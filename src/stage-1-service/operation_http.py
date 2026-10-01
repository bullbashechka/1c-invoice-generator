"""Loopback transport prototype; no Bitrix24 or 1C adapter is included."""

import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sqlite3

from operation_store import Conflict, OperationStore

IDENTIFIER = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
MAX_BODY = 4096


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


def create_server(database, client_tokens, result_token, port=0):
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

            paths = {'/v1/operations', '/v1/unknown', '/v1/results', '/v1/acknowledgments'}
            if self.path not in paths:
                raise RequestError(404, 'not_found')
            is_result = self.path == '/v1/results'
            if is_result != (base is None):
                raise RequestError(403, 'forbidden')
            keys = {'baseId', 'orderId', 'operationId'}
            if is_result or self.path == '/v1/acknowledgments':
                keys.add('taskId')
            if set(data) != keys:
                raise RequestError(400, 'invalid_fields')
            context = tuple(identifier(data[key]) for key in ('baseId','orderId','operationId'))
            request_base, order, operation = context
            if request_base not in client_tokens or (base is not None and request_base != base):
                raise RequestError(403, 'forbidden')
            if self.path == '/v1/operations':
                return store.begin(*context)
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

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text(encoding='utf-8'), object_pairs_hook=unique_object)
        if not isinstance(config, dict) or set(config) != {'database','port','clientTokens','resultToken'}:
            raise ValueError('Invalid configuration')
        database = Path(config['database'])
        if not database.is_absolute():
            database = args.config.resolve().parent / database
        server = create_server(database, config['clientTokens'], config['resultToken'], config['port'])
    except (OSError, ValueError, TypeError, RequestError, sqlite3.Error):
        raise SystemExit('Invalid or unavailable local service configuration') from None
    with server:
        print(json.dumps({'host':'127.0.0.1','port':server.server_port}), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
