import http.client
import json
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import unittest

SOURCE = Path(__file__).resolve().parents[2] / 'src/stage-1-service'
sys.path.insert(0, str(SOURCE))
from operation_http import create_server


class TaskRequestHTTPContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tokens = {'base-a': secrets.token_urlsafe(32),
                       'base-b': secrets.token_urlsafe(32)}
        self.server = create_server(Path(self.temp.name) / 'operations.sqlite', self.tokens,
                                    secrets.token_urlsafe(32))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.payload = {'TITLE':'Контрагент.', 'DESCRIPTION':'Заказ 7', 'GROUP_ID':36}
        self.request_data = {'baseId':'base-a','orderId':'order-7','operationId':'op-7',
                             'initiatorId':'user-9','workplaceId':'pc-2',
                             'sessionId':'session-1',
                             'payload':self.payload}

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, method, path, data=None, token=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        headers = {'Content-Type':'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        body = None if data is None else json.dumps(data, ensure_ascii=False).encode('utf-8')
        try:
            connection.request(method, path, body, headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def enqueue(self, data=None):
        return self.request('POST','/v1/task-requests',
                            self.request_data if data is None else data,
                            self.tokens['base-a'])

    def test_request_endpoint_is_idempotent_and_base_scoped(self):
        status, request = self.enqueue()
        self.assertEqual(200, status)
        self.assertEqual('queued', request['deliveryState'])
        self.assertEqual('КА-op-7', request['correlationTag'])
        self.assertEqual(self.request_data['payload'], request['payload'])
        self.assertEqual((200, request), self.enqueue())
        changed = dict(self.request_data, payload={**self.payload, 'TITLE':'Changed.'})
        self.assertEqual(409, self.enqueue(changed)[0])
        self.assertEqual(200, self.request('GET','/v1/task-requests/op-7',
                                            token=self.tokens['base-a'])[0])
        self.assertEqual(404, self.request('GET','/v1/task-requests/op-7',
                                            token=self.tokens['base-b'])[0])

    def test_request_requires_exact_schema_and_authenticated_base(self):
        self.assertEqual(401, self.request('POST','/v1/task-requests',
                            self.request_data)[0])
        self.assertEqual(400, self.enqueue(dict(self.request_data, unexpected='value'))[0])
        invalid = dict(self.request_data, initiatorId='../other')
        self.assertEqual(400, self.enqueue(invalid)[0])
        foreign = dict(self.request_data, baseId='base-b')
        self.assertEqual(403, self.enqueue(foreign)[0])

    def test_1c_session_heartbeat_is_base_scoped_and_enables_the_claim_gate(self):
        heartbeat = {'baseId':'base-a','initiatorId':'user-9','workplaceId':'pc-2',
                     'sessionId':'session-1'}
        status, result = self.request('POST','/v1/1c/heartbeat',heartbeat,
                                      self.tokens['base-a'])
        self.assertEqual(200,status)
        self.assertEqual('session-1',result['sessionId'])
        status, request = self.enqueue()
        self.assertEqual(200,status)
        self.assertEqual('session-1',request['sessionId'])
        self.assertEqual(403,self.request('POST','/v1/1c/heartbeat',
                            dict(heartbeat,baseId='base-b'),
                            self.tokens['base-a'])[0])


if __name__ == '__main__':
    unittest.main()
