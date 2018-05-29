import logging
from authlib.specs.rfc5849 import (
    OAuth1Request,
    AuthorizationServer as _AuthorizationServer,
)
from authlib.specs.rfc5849 import TemporaryCredential
from authlib.common.security import generate_token
from authlib.common.urls import url_encode
from django.core.cache import cache
from django.conf import settings
from django.http import HttpResponse
from .nonce import exists_nonce_in_cache
from ..helpers import parse_request_headers

log = logging.getLogger(__name__)


class BaseServer(_AuthorizationServer):
    def __init__(self, client_model, token_model, token_generator):
        self.client_model = client_model
        self.token_model = token_model
        self.token_generator = token_generator
        self._config = getattr(settings, 'AUTHLIB_OAUTH1_PROVIDER', {})
        self._nonce_expires_in = self._config.get('nonce_expires_in', 86400)

    def get_client_by_id(self, client_id):
        return self.client_model.objects.get(client_id=client_id)

    def exists_nonce(self, nonce, request):
        return exists_nonce_in_cache(nonce, request, self._nonce_expires_in)

    def create_token_credential(self, request):
        temporary_credential = request.credential
        token = self.token_generator()
        item = self.token_model(
            oauth_token=token['oauth_token'],
            oauth_token_secret=token['oauth_token_secret'],
            client_id=temporary_credential.get_client_id()
        )
        item.set_user_id(temporary_credential.get_user_id())
        item.save()
        return item

    def check_authorization_request(self, request):
        req = _create_oauth1_request(request)
        self.validate_authorization_request(req)
        return req

    def process_request(self, request):
        return _create_oauth1_request(request)

    def handle_response(self, status_code, payload, headers):
        resp = HttpResponse(url_encode(payload), status=status_code)
        for k in headers:
            resp[k] = headers[k]
        return resp


class CacheAuthorizationServer(BaseServer):
    def __init__(self, client_model, token_model, token_generator):
        super(CacheAuthorizationServer, self).__init__(
            client_model, token_model, token_generator)
        self._temporary_expires_in = self._config.get(
            'temporary_credential_expires_in', 86400)
        self._temporary_credential_key_prefix = self._config.get(
            'temporary_credential_key_prefix', 'temporary_credential:')

    def create_temporary_credential(self, request):
        key_prefix = self._temporary_credential_key_prefix
        token = self.token_generator()

        client_id = request.client_id
        redirect_uri = request.redirect_uri
        key = key_prefix + token['oauth_token']
        token['client_id'] = client_id
        if redirect_uri:
            token['oauth_callback'] = redirect_uri

        cache.set(key, token, timeout=self._temporary_expires_in)
        return TemporaryCredential(token)

    def get_temporary_credential(self, request):
        if not request.token:
            return None

        key_prefix = self._temporary_credential_key_prefix
        key = key_prefix + request.token
        value = cache.get(key)
        if value:
            return TemporaryCredential(value)

    def delete_temporary_credential(self, request):
        if request.token:
            key_prefix = self._temporary_credential_key_prefix
            key = key_prefix + request.token
            cache.delete(key)

    def create_authorization_verifier(self, request):
        key_prefix = self._temporary_credential_key_prefix
        verifier = generate_token(36)
        credential = request.credential
        user = request.user
        key = key_prefix + credential.get_oauth_token()
        credential['oauth_verifier'] = verifier
        credential['user_id'] = user.get_user_id()
        cache.set(key, credential, timeout=self._temporary_expires_in)
        return credential


def _create_oauth1_request(request):
    if request.method == 'POST':
        body = request.POST.dict()
    else:
        body = None

    headers = parse_request_headers(request)
    return OAuth1Request(request.method, request.url, body, headers)