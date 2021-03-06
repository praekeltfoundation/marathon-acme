from testtools import ExpectedException
from testtools.assertions import assert_that
from testtools.matchers import Equals, Is, IsInstance, MatchesStructure
from testtools.twistedsupport import failed, flush_logged_errors

from twisted.internet.defer import inlineCallbacks
from twisted.web.http_headers import Headers

from txfake.fake_connection import wait0

from marathon_acme.clients._base import (
    HTTPClient, HTTPError, get_single_header, raise_for_status)
from marathon_acme.clients.tests.helpers import TestHTTPClientBase
from marathon_acme.clients.tests.matchers import HasRequestProperties
from marathon_acme.tests.helpers import failing_client
from marathon_acme.tests.matchers import HasHeader, WithErrorTypeAndMessage


class TestGetSingleHeader(object):
    def test_single_value(self):
        """
        When a single value is set for a header key and we use
        get_single_header to get that value, the correct value is returned.
        """
        headers = Headers({'Content-Type': ['application/json']})
        content_type = get_single_header(headers, 'Content-Type')

        assert_that(content_type, Equals('application/json'))

    def test_multiple_values(self):
        """
        When multiple values are set for a header key and we use
        get_single_header to get the value, the last value is returned.
        """
        headers = Headers({'Content-Type': [
            'application/json',
            'text/event-stream',
            'text/html'
        ]})
        content_type = get_single_header(headers, 'Content-Type')

        assert_that(content_type, Equals('text/html'))

    def test_value_with_params(self):
        """
        When the value set for a header key include parameters and we use
        get_single_header to get the value, the value without the parameters
        is returned.
        """
        headers = Headers({'Accept': ['application/json; charset=utf-8']})
        accept = get_single_header(headers, 'Accept')

        assert_that(accept, Equals('application/json'))

    def test_value_missing(self):
        """
        When the requested header key is not present in the set of headers,
        get_single_header returns None.
        """
        headers = Headers({'Content-Type': ['application/json']})
        content_type = get_single_header(headers, 'Accept')

        assert_that(content_type, Is(None))


class TestHTTPClient(TestHTTPClientBase):
    def get_client(self, client):
        return HTTPClient('http://localhost:8000', client=client)

    @inlineCallbacks
    def test_request(self):
        """
        When a request is made, it should be made with the correct method,
        address and headers, and should contain an empty body. The response
        should be returned.
        """
        d = self.cleanup_d(self.client.request('GET', path='/hello'))

        request = yield self.requests.get()
        self.assertThat(request, HasRequestProperties(
            method='GET', url=self.uri('/hello')))
        self.assertThat(request.content.read(), Equals(b''))

        request.setResponseCode(200)
        request.write(b'hi\n')
        request.finish()

        response = yield d
        text = yield response.text()
        self.assertThat(text, Equals('hi\n'))

    @inlineCallbacks
    def test_request_debug_log(self):
        """
        When a request is made in debug mode, things should run smoothly.
        (Don't really want to check the log output here, just that things don't
        break.)
        """
        self.client.debug = True
        d = self.cleanup_d(self.client.request('GET', path='/hello'))

        request = yield self.requests.get()
        self.assertThat(request, HasRequestProperties(
            method='GET', url=self.uri('/hello')))
        self.assertThat(request.content.read(), Equals(b''))

        request.setResponseCode(200)
        request.write(b'hi\n')
        request.finish()

        response = yield d
        text = yield response.text()
        self.assertThat(text, Equals('hi\n'))

    @inlineCallbacks
    def test_request_url(self):
        """
        When a request is made with the url parameter set, that parameter
        should be used as the base URL.
        """
        self.cleanup_d(self.client.request(
            'GET', path='/hello', url='http://localhost:9000'))

        request = yield self.requests.get()
        self.assertThat(request, HasRequestProperties(
            method='GET', url='http://localhost:9000/hello'))

        request.setResponseCode(200)
        request.finish()

    def test_request_no_url(self):
        """
        When a request is made without the url parameter and the client doesn't
        have a url, an error should be raised
        """
        self.client.url = None
        with ExpectedException(
            ValueError,
                r'url not provided and this client has no url attribute'):
            self.client.request('GET', path='/hello')

    @inlineCallbacks
    def test_client_error_response(self):
        """
        When a request is made and the raise_for_status callback is added and a
        4xx response code is returned, a HTTPError should be raised to indicate
        a client error.
        """
        d = self.cleanup_d(self.client.request('GET', path='/hello'))
        d.addCallback(raise_for_status)

        request = yield self.requests.get()
        self.assertThat(request, HasRequestProperties(
            method='GET', url=self.uri('/hello')))

        request.setResponseCode(403)
        request.write(b'Unauthorized\n')
        request.finish()

        yield wait0()
        self.assertThat(d, failed(WithErrorTypeAndMessage(
            HTTPError, '403 Client Error for url: %s' % self.uri('/hello'))))

    @inlineCallbacks
    def test_server_error_response(self):
        """
        When a request is made and the raise_for_status callback is added and a
        5xx response code is returned, a HTTPError should be raised to indicate
        a server error.
        """
        d = self.cleanup_d(self.client.request('GET', path='/hello'))
        d.addCallback(raise_for_status)

        request = yield self.requests.get()
        self.assertThat(request, HasRequestProperties(
            method='GET', url=self.uri('/hello')))

        request.setResponseCode(502)
        request.write(b'Bad gateway\n')
        request.finish()

        yield wait0()
        self.assertThat(d, failed(WithErrorTypeAndMessage(
            HTTPError, '502 Server Error for url: %s' % self.uri('/hello'))))

    @inlineCallbacks
    def test_params(self):
        """
        When query parameters are specified as the params kwarg, those
        parameters are reflected in the request.
        """
        self.cleanup_d(self.client.request(
            'GET', path='/hello', params={'from': 'earth'}))

        request = yield self.requests.get()
        self.assertThat(request, HasRequestProperties(
            method='GET', url=self.uri('/hello'), query={'from': ['earth']}))

        request.setResponseCode(200)
        request.finish()

    @inlineCallbacks
    def test_url_query_as_params(self):
        """
        When query parameters are specified in the URL, those parameters are
        reflected in the request.
        """
        self.cleanup_d(self.client.request(
            'GET', self.uri('/hello?from=earth')))

        request = yield self.requests.get()
        self.assertThat(request, HasRequestProperties(
            method='GET', url=self.uri('/hello'), query={'from': ['earth']}))

        request.setResponseCode(200)
        request.finish()

    @inlineCallbacks
    def test_params_precedence_over_url_query(self):
        """
        When query parameters are specified as both the params kwarg and in the
        URL, the params kwarg takes precedence.
        """
        self.cleanup_d(self.client.request(
            'GET', self.uri('/hello?from=mars'), params={'from': 'earth'}))

        request = yield self.requests.get()
        self.assertThat(request, HasRequestProperties(
            method='GET', url=self.uri('/hello'), query={'from': ['earth']}))

        request.setResponseCode(200)
        request.finish()

    @inlineCallbacks
    def test_auth(self):
        """
        When basic auth credentials are specified as the auth kwarg, the
        encoded credentials are present in the request headers.
        """
        self.cleanup_d(self.client.request(
            'GET', path='/hello', auth=('user', 'pa$$word')))

        request = yield self.requests.get()
        self.assertThat(request, HasRequestProperties(
            method='GET', url=self.uri('/hello')))
        self.assertThat(
            request.requestHeaders,
            HasHeader('Authorization', ['Basic dXNlcjpwYSQkd29yZA==']))

        request.setResponseCode(200)
        request.finish()

    @inlineCallbacks
    def test_url_userinfo_as_auth(self):
        """
        When basic auth credentials are specified in the URL, the encoded
        credentials are present in the request headers.
        """
        self.cleanup_d(self.client.request(
            'GET', 'http://user:pa$$word@localhost:8000/hello'))

        request = yield self.requests.get()
        self.assertThat(request, HasRequestProperties(
            method='GET', url=self.uri('/hello')))
        self.assertThat(
            request.requestHeaders,
            HasHeader('Authorization', ['Basic dXNlcjpwYSQkd29yZA==']))

        request.setResponseCode(200)
        request.finish()

    @inlineCallbacks
    def test_auth_precedence_over_url_userinfo(self):
        """
        When basic auth credentials are specified as both the auth kwarg and in
        the URL, the credentials in the auth kwarg take precedence.
        """
        self.cleanup_d(self.client.request(
            'GET', 'http://usernator:password@localhost:8000/hello',
            auth=('user', 'pa$$word')))

        request = yield self.requests.get()
        self.assertThat(request, HasRequestProperties(
            method='GET', url=self.uri('/hello')))
        self.assertThat(
            request.requestHeaders,
            HasHeader('Authorization', ['Basic dXNlcjpwYSQkd29yZA==']))

        request.setResponseCode(200)
        request.finish()

    @inlineCallbacks
    def test_url_overrides(self):
        """
        When URL parts are overridden via keyword arguments, those overrides
        should be reflected in the request.
        """
        self.cleanup_d(self.client.request(
            'GET', 'http://example.com:8000/hello#section1',
            scheme='https', host='example2.com', port='9000', path='/goodbye',
            fragment='section2'))

        request = yield self.requests.get()
        self.assertThat(request, HasRequestProperties(
            method='GET', url='https://example2.com:9000/goodbye#section2'))

        request.setResponseCode(200)
        request.finish()

    def test_failure_during_request(self):
        """
        When a failure occurs during a request, the exception is propagated
        to the request's deferred.
        """
        client = self.get_client(failing_client)

        d = client.request('GET', path='/hello')
        self.assertThat(d, failed(MatchesStructure(
            value=IsInstance(RuntimeError))))

        flush_logged_errors(RuntimeError)
