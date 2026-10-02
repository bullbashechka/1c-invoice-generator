from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import unittest

SOURCE = Path(__file__).resolve().parents[2] / 'src/stage-1-service'
sys.path.insert(0, str(SOURCE))
from bitrix_task import ResultUnknown, _post


class TaskTransportContract(unittest.TestCase):
    def setUp(self):
        owner = self
        self.status = 200
        self.body = b'{"result":{"task":{"id":"42"}}}'
        self.requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                owner.requests.append((self.command, self.path,
                                       self.headers.get('Content-Type'),
                                       self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(owner.status)
                if owner.status == 302:
                    self.send_header('Location', '/redirect-target')
                self.send_header('Content-Length', str(len(owner.body)))
                self.end_headers()
                self.wfile.write(owner.body)

            def do_GET(self):
                owner.requests.append((self.command, self.path, None, None))
                self.send_response(200)
                self.end_headers()

            def log_message(self, format, *args):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.url = f'http://127.0.0.1:{self.server.server_port}/rest/7/TEST_SECRET/tasks.task.add.json'

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def test_post_sends_json_without_get_or_retries(self):
        payload = b'{"fields":{"TITLE":"test","RESPONSIBLE_ID":7}}'
        self.assertEqual(self.body, _post(self.url, payload, 2))
        self.assertEqual([('POST', '/rest/7/TEST_SECRET/tasks.task.add.json',
                           'application/json', payload)], self.requests)

    def test_redirect_is_not_followed(self):
        self.status = 302
        with self.assertRaises(ResultUnknown):
            _post(self.url, b'{}', 2)
        self.assertEqual(1, len(self.requests))

    def test_http_error_cannot_be_accepted_as_success(self):
        self.status = 400
        with self.assertRaises(ResultUnknown):
            _post(self.url, b'{}', 2)
        self.assertEqual(1, len(self.requests))

    def test_structured_api_error_can_be_read_without_retry(self):
        self.status = 403
        self.body = b'{"error":"ACCESS_DENIED"}'
        self.assertEqual(self.body, _post(self.url, b'{}', 2))
        self.assertEqual(1, len(self.requests))


if __name__ == '__main__':
    unittest.main()
