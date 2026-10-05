"""Live 1C queue reference regression; no posting, HTTP exchange, or retained writes."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.request import Request, urlopen
import uuid

from run_register_concurrency import MCP, PROBE, decode


def run(args):
    expected = json.loads(args.profile.read_text(encoding='utf-8'))
    client = MCP(args.port)
    probe = client.execute(PROBE)
    if (probe['baseFingerprint'] != expected['baseFingerprint']
            or probe['configuration'] != 'КомплекснаяАвтоматизацияДляКазахстана'
            or probe['version'] != '2.4.5.18'):
        raise RuntimeError('AUTHORIZED_DEMO_REQUIRED')
    order = str(uuid.uuid4()) if args.link else str(uuid.UUID(args.order))
    operation = str(uuid.uuid4())
    requests = []
    class Fixture(BaseHTTPRequestHandler):
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
            requests.append({'path': self.path, 'data': data})
            if self.path == '/v1/1c/checks':
                answer = {'checks': []}
            elif self.path == '/v1/acknowledgments':
                if data != {'baseId': expected['baseId'], 'orderId': order,
                            'operationId': operation, 'taskId': 2100000103}:
                    self.send_error(400)
                    return
                answer = {'accepted': True}
            else:
                self.send_error(404)
                return
            self.respond(answer)

        def do_GET(self):
            requests.append({'path': self.path})
            if self.path != '/v1/results':
                self.send_error(404)
                return
            self.respond({'results': [{'baseId': expected['baseId'], 'orderId': order,
                         'operationId': operation, 'taskId': 2100000103}]})

        def respond(self, answer):
            body = json.dumps(answer).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    if args.link:
        thread.start()
    filename = 'returned_link_reference_contract.bsl' if args.link else 'queue_order_reference_contract.bsl'
    code = Path(__file__).with_name(filename).read_text(encoding='utf-8')
    for key, value in {'ORDER': order, 'COMPUTER': expected['computer'], 'OPERATION': operation,
                       'PORT': str(server.server_port), 'SESSION': str(uuid.uuid4())}.items():
        if '"' in value or '\n' in value:
            raise ValueError('INVALID_CONTRACT_PARAMETER')
        code = code.replace('__' + key + '__', value)
    client.request_id += 1
    payload = {'jsonrpc': '2.0', 'id': client.request_id, 'method': 'tools/call',
               'params': {'name': 'execute_code', 'arguments': {'code': code, 'execution_context': 'server'}}}
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'}
    if client.session:
        headers['Mcp-Session-Id'] = client.session
    try:
        with urlopen(Request(client.url, json.dumps(payload, ensure_ascii=False).encode('utf-8'), headers), timeout=600) as result:
            envelope = decode(result.read().decode('utf-8'))
    finally:
        if args.link:
            server.shutdown()
            thread.join(timeout=2)
        server.server_close()
    answer = json.loads(envelope['result']['content'][0]['text'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'answer': answer, 'httpRequests': requests}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(answer, ensure_ascii=False))
    marker = 'LINK_REFERENCE_CONTRACT_GREEN' if args.link else 'QUEUE_REFERENCE_CONTRACT_GREEN'
    passed = answer.get('success') and answer.get('data') == marker
    if args.link and passed and [r['path'] for r in requests] != ['/v1/1c/checks', '/v1/results', '/v1/acknowledgments']:
        raise RuntimeError('EXPECTED_HTTP_SEQUENCE_NOT_OBSERVED')
    return passed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=6004)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--order')
    parser.add_argument('--link', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    if not options.link and not options.order:
        parser.error('--order is required for queue contract')
    raise SystemExit(0 if run(options) else 1)
