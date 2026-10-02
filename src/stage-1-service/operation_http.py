"""Loopback transport prototype; no Bitrix24 or 1C adapter is included."""

import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import re
import sqlite3
import ssl
import threading

from bitrix_reconcile import BitrixRESTClient
from bitrix_reconcile import BitrixAPIRejected, ResultUnknown
from operation_store import Conflict, OperationStore
from bitrix_entities import BitrixEntityClient, EntitySetupError
from entity_sync import BitrixEntitySync
from oauth_tokens import OAuthRefreshError, OAuthTokenStore

IDENTIFIER = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
MAX_BODY = 65536


class RequestError(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code


def identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise RequestError(400, 'invalid_context')
    return value


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def create_server(database, client_tokens, result_token, port=0, *,
                  host='127.0.0.1', ssl_context=None):
    if not isinstance(client_tokens, dict) or not client_tokens:
        raise ValueError('Client credentials required')
    credentials = []
    for base, token in client_tokens.items():
        identifier(base)
        credentials.append((token, base))
    credentials.append((result_token, None))
    tokens = [token for token, _ in credentials]
    if (any(not isinstance(token, str) or not token.isascii()
            or len(token) < 32 or any(c.isspace() for c in token) for token in tokens)
            or len(set(tokens)) != len(tokens)):
        raise ValueError('Distinct nonempty credentials required')
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError('Invalid port')
    if not isinstance(host, str) or not host or any(character.isspace() for character in host):
        raise ValueError('Invalid bind host')
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = False
    if ssl_context is not None and not isinstance(ssl_context, ssl.SSLContext):
        raise ValueError('TLS listener requires an SSL context')
    if not is_loopback and ssl_context is None:
        raise ValueError('TLS is required for non-loopback listeners')
    store = OperationStore(database)

    class Handler(BaseHTTPRequestHandler):
        server_version = 'Stage1Loopback'
        sys_version = ''

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def reply(self, status, data):
            body = json.dumps(data, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)

        def authenticate(self):
            values = self.headers.get_all('Authorization', [])
            if len(values) != 1 or not values[0].startswith('Bearer '):
                raise RequestError(401, 'unauthorized')
            candidate = values[0][7:]
            if not candidate.isascii():
                raise RequestError(401, 'unauthorized')
            for token, base in credentials:
                if hmac.compare_digest(candidate, token):
                    return base
            raise RequestError(401, 'unauthorized')

        def body(self):
            lengths = self.headers.get_all('Content-Length', [])
            if self.headers.get('Transfer-Encoding') or len(lengths) > 1:
                raise RequestError(400, 'invalid_framing')
            if self.command == 'POST' and not lengths:
                raise RequestError(411, 'length_required')
            try:
                length = int(lengths[0]) if lengths else 0
            except ValueError:
                raise RequestError(400, 'invalid_framing') from None
            if not 0 <= length <= MAX_BODY:
                raise RequestError(413, 'body_too_large')
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise RequestError(400, 'incomplete_body')
            if self.command == 'GET':
                if length:
                    raise RequestError(400, 'unexpected_body')
                return None
            if self.headers.get_content_type() != 'application/json':
                raise RequestError(415, 'json_required')
            try:
                result = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object)
            except (ValueError, UnicodeError):
                raise RequestError(400, 'invalid_json') from None
            if not isinstance(result, dict):
                raise RequestError(400, 'invalid_json')
            return result

        def dispatch(self, base, data):
            if self.command == 'GET':
                if base is None:
                    raise RequestError(403, 'forbidden')
                if self.path == '/v1/results':
                    return {'results': store.pending(base)}
                request_prefix = '/v1/task-requests/'
                if self.path.startswith(request_prefix):
                    operation = identifier(self.path[len(request_prefix):])
                    try:
                        return store.task_request(base, operation)
                    except Conflict:
                        raise RequestError(404, 'not_found') from None
                prefix = '/v1/operations/'
                if self.path.startswith(prefix):
                    operation = identifier(self.path[len(prefix):])
                    try:
                        row = store.get(operation)
                    except Conflict:
                        raise RequestError(404, 'not_found') from None
                    if row['baseId'] != base:
                        raise RequestError(404, 'not_found')
                    return row
                raise RequestError(404, 'not_found')

            paths = {'/v1/operations', '/v1/unknown', '/v1/results', '/v1/acknowledgments',
                     '/v1/task-requests', '/v1/1c/heartbeat'}
            if self.path not in paths:
                raise RequestError(404, 'not_found')
            is_result = self.path == '/v1/results'
            if is_result != (base is None):
                raise RequestError(403, 'forbidden')
            if self.path == '/v1/task-requests':
                keys = {'baseId', 'orderId', 'operationId', 'initiatorId',
                        'workplaceId', 'sessionId', 'payload'}
            elif self.path == '/v1/1c/heartbeat':
                keys = {'baseId','initiatorId','workplaceId','sessionId'}
            else:
                keys = {'baseId', 'orderId', 'operationId'}
            if is_result or self.path == '/v1/acknowledgments':
                keys.add('taskId')
            if set(data) != keys:
                raise RequestError(400, 'invalid_fields')
            if self.path == '/v1/1c/heartbeat':
                request_base = identifier(data['baseId'])
                if request_base not in client_tokens or request_base != base:
                    raise RequestError(403, 'forbidden')
                initiator = identifier(data['initiatorId'])
                workplace = identifier(data['workplaceId'])
                session = identifier(data['sessionId'])
                heartbeat = store.heartbeat_1c_session(
                    request_base, initiator, workplace, session, ttl_seconds=30)
                return {'sessionId':heartbeat['sessionId'],
                        'expiresAt':heartbeat['expiresAt']}
            context = tuple(identifier(data[key]) for key in ('baseId','orderId','operationId'))
            request_base, order, operation = context
            if request_base not in client_tokens or (base is not None and request_base != base):
                raise RequestError(403, 'forbidden')
            if self.path == '/v1/operations':
                return store.begin(*context)
            if self.path == '/v1/task-requests':
                initiator = identifier(data['initiatorId'])
                workplace = identifier(data['workplaceId'])
                session = identifier(data['sessionId'])
                if not isinstance(data['payload'], dict):
                    raise RequestError(400, 'invalid_fields')
                return store.enqueue_task_request(request_base, order, operation,
                                                  initiator, workplace, data['payload'],
                                                  session_id=session)
            if is_result:
                return store.accept_result(operation, request_base, order, data['taskId'])
            # Check all context fields before either state transition.
            row = store.get(operation)
            if row['baseId'] != request_base or row['orderId'] != order:
                raise Conflict('Context mismatch')
            if self.path == '/v1/unknown':
                store.mark_unknown(operation)
            else:
                store.acknowledge(operation, request_base, order, data['taskId'])
            return store.get(operation)

        def handle_request(self):
            try:
                data = self.body()
                base = self.authenticate()
                response = self.dispatch(base, data)
                self.reply(200, response)
            except RequestError as error:
                self.reply(error.status, {'error':error.code})
            except Conflict:
                self.reply(409, {'error':'conflict'})
            except sqlite3.Error:
                self.reply(503, {'error':'storage_unavailable'})
            except (ConnectionError, TimeoutError):
                # The committed result remains pending even if the caller disconnects.
                pass

        do_GET = handle_request
        do_POST = handle_request

        def log_message(self, *args):
            # Request paths/bodies and credentials must not enter diagnostics.
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    if ssl_context is not None:
        try:
            server.socket = ssl_context.wrap_socket(server.socket, server_side=True)
        except Exception:
            server.server_close()
            raise
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text(encoding='utf-8'), object_pairs_hook=unique_object)
        if (not isinstance(config, dict) or not {'database','port','clientTokens','resultToken'}
                <= set(config) or set(config) - {'database','port','clientTokens','resultToken',
                                                 'bindHost','tls','bitrixEntity'}):
            raise ValueError('Invalid configuration')
        database = Path(config['database'])
        if not database.is_absolute():
            database = args.config.resolve().parent / database
        bind_host = config.get('bindHost', '127.0.0.1')
        tls_context = None
        if 'tls' in config:
            tls_settings = config['tls']
            if (not isinstance(tls_settings, dict)
                    or set(tls_settings) != {'certificate','privateKey'}
                    or any(not isinstance(tls_settings[key], str) or not tls_settings[key]
                           for key in ('certificate','privateKey'))):
                raise ValueError('Invalid TLS configuration')
            certificate = Path(tls_settings['certificate'])
            private_key = Path(tls_settings['privateKey'])
            if not certificate.is_absolute():
                certificate = args.config.resolve().parent / certificate
            if not private_key.is_absolute():
                private_key = args.config.resolve().parent / private_key
            tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls_context.minimum_version = ssl.TLSVersion.TLSv1_2
            tls_context.load_cert_chain(certfile=certificate, keyfile=private_key)
        entity_sync = None
        reconciliation_seconds = 10
        if 'bitrixEntity' in config:
            settings = config['bitrixEntity']
            required = {'portal','baseId','employeeIds','technicalOwnerUserId',
                        'oauthTokenFile','workerHandlerUrl','errorHandlerUrl',
                        'provisionOnStartup'}
            optional = {'pollSeconds','reconciliationSeconds'}
            if (not isinstance(settings, dict) or not required <= set(settings)
                    or set(settings) - required - optional
                    or type(settings['provisionOnStartup']) is not bool
                    or not isinstance(settings['employeeIds'], list)):
                raise ValueError('Invalid Bitrix entity configuration')
            if settings['baseId'] not in config['clientTokens']:
                raise ValueError('Bitrix entity base is not configured for the 1C client')
            token_path = Path(settings['oauthTokenFile'])
            if not token_path.is_absolute():
                token_path = args.config.resolve().parent / token_path
            token_store = OAuthTokenStore(token_path)
            entity_client = BitrixEntityClient(settings['portal'], token_store.access_token)
            task_reader = BitrixRESTClient(settings['portal'], token_store.access_token)
            poll_seconds = settings.get('pollSeconds',5)
            reconciliation_seconds = settings.get('reconciliationSeconds',10)
            if (type(poll_seconds) not in (int,float) or not 1 <= poll_seconds <= 60
                    or type(reconciliation_seconds) not in (int,float)
                    or not 5 <= reconciliation_seconds <= 300):
                raise ValueError('Invalid Bitrix polling interval')
            entity_sync = BitrixEntitySync(
                OperationStore(database), entity_client, settings['baseId'],
                settings['employeeIds'], task_reader=task_reader,
                worker_handler_url=settings['workerHandlerUrl'],
                error_handler_url=settings['errorHandlerUrl'],
                poll_seconds=poll_seconds)
            if settings['provisionOnStartup']:
                entity_sync.provision(settings['technicalOwnerUserId'])
            else:
                entity_sync.verify_worker_placements(settings['technicalOwnerUserId'])
        server = create_server(database, config['clientTokens'], config['resultToken'], config['port'],
                               host=bind_host, ssl_context=tls_context)
    except (OSError, ValueError, TypeError, RequestError, sqlite3.Error,
            EntitySetupError, OAuthRefreshError):
        raise SystemExit('Invalid or unavailable local service configuration') from None
    with server:
        print(json.dumps({'host':bind_host,'port':server.server_port,
                          'scheme':'https' if tls_context else 'http'}), flush=True)
        reconciliation_stop = threading.Event()
        reconciliation_thread = None
        if entity_sync is not None:
            entity_sync.start()
            def reconcile_loop():
                while not reconciliation_stop.is_set():
                    try:
                        entity_sync.reconcile_once()
                    except Exception:
                        pass
                    reconciliation_stop.wait(reconciliation_seconds)
            reconciliation_thread = threading.Thread(
                target=reconcile_loop,name='stage1-tag-reconciliation',daemon=True)
            reconciliation_thread.start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            reconciliation_stop.set()
            if entity_sync is not None:
                entity_sync.stop()
            if reconciliation_thread is not None:
                reconciliation_thread.join(timeout=5)


if __name__ == '__main__':
    main()
