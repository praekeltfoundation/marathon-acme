import json
import os

from requests.exceptions import RequestException

from twisted.web.http import BAD_REQUEST, NOT_FOUND

from marathon_acme.clients._base import HTTPClient, get_single_header
from marathon_acme.clients._tx_util import ClientPolicyForHTTPS, default_client


class VaultError(RequestException):
    """
    Exception type for Vault response errors. The ``errors`` parameter contains
    a list of error messages. Roughly copies hvac's ``VaultError`` type:
    https://github.com/hvac/hvac/blob/v0.6.4/hvac/exceptions.py#L1-L8
    """
    def __init__(self, message=None, errors=None, response=None):
        if errors:
            message = ', '.join(errors)

        self.errors = errors

        super(VaultError, self).__init__(message, response=response)


class CasError(VaultError):
    """Exception type to indicate a Check-And-Set mismatch error. """


class VaultClient(HTTPClient):
    """
    A very simple Vault client that can read and write to paths.
    """

    def __init__(self, url, token, *args, **kwargs):
        """
        :param url: the URL for Vault
        :param token: the Vault auth token
        """
        super(VaultClient, self).__init__(*args, url=url, **kwargs)
        self._token = token

    @classmethod
    def from_env(cls, reactor=None, env=os.environ):
        """
        Create a Vault client with configuration from the environment. Supports
        a limited number of the available config options:
        https://www.vaultproject.io/docs/commands/index.html#environment-variables
        https://github.com/hashicorp/vault/blob/v0.11.3/api/client.go#L28-L40

        Supported:
        - ``VAULT_ADDR``
        - ``VAULT_CACERT``
        - ``VAULT_CLIENT_CERT``
        - ``VAULT_CLIENT_KEY``
        - ``VAULT_TLS_SERVER_NAME``
        - ``VAULT_TOKEN``

        Not currently supported:
        - ``VAULT_CAPATH``
        - ``VAULT_CLIENT_TIMEOUT``
        - ``VAULT_MAX_RETRIES``
        - ``VAULT_MFA``
        - ``VAULT_RATE_LIMIT``
        - ``VAULT_SKIP_VERIFY``
        - ``VAULT_WRAP_TTL``
        """
        address = env.get('VAULT_ADDR', 'https://127.0.0.1:8200')
        # This seems to be what the Vault CLI defaults to
        token = env.get('VAULT_TOKEN', 'TEST')

        ca_cert = env.get('VAULT_CACERT')
        tls_server_name = env.get('VAULT_TLS_SERVER_NAME')
        client_cert = env.get('VAULT_CLIENT_CERT')
        client_key = env.get('VAULT_CLIENT_KEY')
        cf = ClientPolicyForHTTPS.from_pem_files(
            caKey=ca_cert, privateKey=client_key, certKey=client_cert,
            tls_server_name=tls_server_name
        )
        client, reactor = default_client(reactor, contextFactory=cf)

        return cls(address, token, client=client, reactor=reactor)

    def request(self, method, path, *args, **kwargs):
        headers = kwargs.pop('headers', {})
        headers['X-Vault-Token'] = self._token
        return super(VaultClient, self).request(
            method, *args, path=path, headers=headers, **kwargs)

    def _handle_response(self, response, check_cas=False):
        if 400 <= response.code < 600:
            return self._handle_error(response, check_cas)

        return response.json()

    def _handle_error(self, response, check_cas):
        # Decode as utf-8. treq's text() method uses ISO-8859-1 which is
        # correct for random text over HTTP, but not for JSON. Cross fingers
        # that we don't receive anything non-utf-8.
        d = response.text(encoding='utf-8')

        def to_error(text):
            # This logic is inspired by hvac as well:
            # https://github.com/hvac/hvac/blob/v0.6.4/hvac/adapters.py#L227-L233
            exc_type = VaultError
            errors = None
            if get_single_header(
                    response.headers, 'Content-Type') == 'application/json':
                errors = json.loads(text).get('errors')

            # Special case for 404s without extra errors: return None (hvac
            # doesn't do this)
            if response.code == NOT_FOUND and errors == []:
                return None

            # Special case for CAS mismatch errors: raise a CasError
            # Unfortunately, Vault doesn't make it easy to differentiate
            # between CAS errors and other errors so we have to check a few
            # things.
            if (check_cas and response.code == BAD_REQUEST and
                    errors and 'check-and-set' in errors[0]):
                exc_type = CasError

            # hvac returns more specific errors that are subclasses of its
            # VaultError. For simplicity we just return fewer error types.
            raise exc_type(text, errors=errors, response=response)

        return d.addCallback(to_error)

    def read(self, path, **params):
        """
        Read data from Vault. Returns the JSON-decoded response.
        """
        d = self.request('GET', '/v1/' + path, params=params)
        return d.addCallback(self._handle_response)

    def write(self, path, **data):
        """
        Write data to Vault. Returns the JSON-decoded response.
        """
        d = self.request('PUT', '/v1/' + path, json=data)
        return d.addCallback(self._handle_response, check_cas=True)

    def read_kv2(self, path, version=None, mount_path='secret'):
        """
        Read some data from a key/value version 2 secret engine.
        """
        params = {}
        if version is not None:
            params['version'] = version

        read_path = '{}/data/{}'.format(mount_path, path)
        return self.read(read_path, **params)

    def create_or_update_kv2(self, path, data, cas=None, mount_path='secret'):
        """
        Create or update some data in a key/value version 2 secret engine.

        :raises CasError:
            Raises an error if the ``cas`` value, when provided, doesn't match
            Vault's version for the key.
        """
        params = {
            'options': {},
            'data': data
        }
        if cas is not None:
            params['options']['cas'] = cas

        write_path = '{}/data/{}'.format(mount_path, path)
        return self.write(write_path, **params)
