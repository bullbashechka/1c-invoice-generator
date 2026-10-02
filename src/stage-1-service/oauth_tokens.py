"""Private, atomically rotated Bitrix24 OAuth token storage for the local service."""

from http.client import HTTPException
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


_TOKEN_ENDPOINT = 'https://oauth.bitrix24.tech/oauth/token/'
_TOKEN_FIELDS = {'clientId', 'clientSecret', 'accessToken', 'refreshToken', 'expiresAt'}


class OAuthRefreshError(Exception):
    """A sanitized error that never carries tokens or server response bodies."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def _request_refresh(url, timeout):
    request = Request(url, headers={'Accept': 'application/json'}, method='GET')
    opener = build_opener(_NoRedirect())
    try:
        response = opener.open(request, timeout=timeout)
    except HTTPError as error:
        response = error
    with response:
        body = response.read(65537)
        if response.code != 200 or len(body) > 65536:
            raise OAuthRefreshError('Bitrix24 OAuth refresh was rejected')
        try:
            value = json.loads(body.decode('utf-8'))
        except (ValueError, UnicodeError):
            raise OAuthRefreshError('Bitrix24 OAuth response was invalid') from None
        if not isinstance(value, dict):
            raise OAuthRefreshError('Bitrix24 OAuth response was invalid')
        return value


def _secret(value, field):
    if (not isinstance(value, str) or not value.isascii() or not 1 <= len(value) <= 4096
            or any(character.isspace() for character in value)):
        raise ValueError('Invalid OAuth ' + field)
    return value


def _token_record(value):
    if not isinstance(value, dict) or set(value) != _TOKEN_FIELDS:
        raise OAuthRefreshError('OAuth token file has an invalid schema')
    record = {
        'clientId': _secret(value['clientId'], 'client ID'),
        'clientSecret': _secret(value['clientSecret'], 'client secret'),
        'accessToken': _secret(value['accessToken'], 'access token'),
        'refreshToken': _secret(value['refreshToken'], 'refresh token'),
        'expiresAt': value['expiresAt'],
    }
    if (type(record['expiresAt']) not in (int, float)
            or not math.isfinite(record['expiresAt']) or record['expiresAt'] <= 0):
        raise OAuthRefreshError('OAuth token file has an invalid expiry')
    record['expiresAt'] = float(record['expiresAt'])
    return record


class OAuthTokenStore:
    """Read a local token pair and rotate it under one lock with atomic file replacement."""

    def __init__(self, path, *, refresh_transport=None, now=None, timeout=15,
                 refresh_skew=60):
        self.path = Path(path).expanduser().resolve()
        self._refresh_transport = _request_refresh if refresh_transport is None else refresh_transport
        self._now = time.time if now is None else now
        if not callable(self._refresh_transport) or not callable(self._now):
            raise ValueError('OAuth transport and clock must be callable')
        if (type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0
                or type(refresh_skew) not in (int, float) or not math.isfinite(refresh_skew)
                or refresh_skew < 0):
            raise ValueError('OAuth timeout and refresh skew must be valid')
        self._timeout = float(timeout)
        self._refresh_skew = float(refresh_skew)
        self._lock = threading.RLock()

    @staticmethod
    def initialize(path, credentials):
        """Import the first token pair once; never replace an existing credential file."""
        target = Path(path).expanduser().resolve()
        record = _token_record(credentials)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open('x', encoding='utf-8', newline='\n') as output:
                json.dump(record, output, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False)
                output.write('\n')
                output.flush()
                os.fsync(output.fileno())
        except FileExistsError:
            raise
        except OSError:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        if os.name != 'nt':
            os.chmod(target, 0o600)
        return target

    def _read(self):
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
            return _token_record(value)
        except OAuthRefreshError:
            raise
        except (OSError, ValueError, UnicodeError):
            raise OAuthRefreshError('OAuth token file is unavailable or invalid') from None

    def _write_atomic(self, record):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', newline='\n',
                                             dir=self.path.parent,
                                             prefix=self.path.name + '.', suffix='.tmp',
                                             delete=False) as output:
                temporary = Path(output.name)
                json.dump(record, output, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False)
                output.write('\n')
                output.flush()
                os.fsync(output.fileno())
            if os.name != 'nt':
                os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        except OSError:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            raise OAuthRefreshError('OAuth token pair could not be saved atomically') from None

    def access_token(self):
        with self._lock:
            current = self._read()
            now = self._now()
            if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
                raise OAuthRefreshError('System clock is invalid')
            if current['expiresAt'] > now + self._refresh_skew:
                return current['accessToken']
            query = urlencode({
                'grant_type':'refresh_token',
                'client_id':current['clientId'],
                'client_secret':current['clientSecret'],
                'refresh_token':current['refreshToken'],
            })
            try:
                response = self._refresh_transport(_TOKEN_ENDPOINT + '?' + query, self._timeout)
            except OAuthRefreshError:
                raise
            except Exception:
                raise OAuthRefreshError('Bitrix24 OAuth refresh is unavailable') from None
            if not isinstance(response, dict) or 'error' in response:
                raise OAuthRefreshError('Bitrix24 OAuth refresh was rejected')
            access = response.get('access_token')
            refresh = response.get('refresh_token')
            expires = response.get('expires_in')
            try:
                access = _secret(access, 'access token')
                refresh = _secret(refresh, 'refresh token')
            except ValueError:
                raise OAuthRefreshError('Bitrix24 OAuth response was invalid') from None
            if type(expires) not in (int, float) or not math.isfinite(expires) or expires <= 0:
                raise OAuthRefreshError('Bitrix24 OAuth response was invalid')
            updated = {**current, 'accessToken':access, 'refreshToken':refresh,
                       'expiresAt':float(now) + float(expires)}
            self._write_atomic(updated)
            return access
