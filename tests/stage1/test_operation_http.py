import http.client
import json
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import unittest

SOURCE = Path(__file__).resolve().parents[2] / 'src/stage-1-service'
sys.path.insert(0, str(SOURCE))
from operation_http import create_server
from operation_store import OperationStore


class OperationHTTPContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / 'operations.sqlite'
        self.tokens = {'base-a': secrets.token_urlsafe(32),
                       'base-b': secrets.token_urlsafe(32)}
        self.result_token = secrets.token_urlsafe(32)
        self.server = create_server(self.database, self.tokens, self.result_token)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=5)
            self.server = None

    def request(self, method, path, data=None, token=None, port=None):
        connection = http.client.HTTPConnection('127.0.0.1', port or self.server.server_port,
                                                timeout=5)
        headers = {'Content-Type': 'application/json'}
        if token is not None:
            headers['Authorization'] = 'Bearer ' + token
        body = None if data is None else json.dumps(data).encode()
        try:
            connection.request(method, path, body, headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def begin(self, operation='op-1', order='order-1', base='base-a'):
        status, data = self.request('POST', '/v1/operations',
                                    {'baseId':base, 'orderId':order, 'operationId':operation},
                                    self.tokens[base])
        self.assertEqual(200, status)
        return data

    def result(self, operation='op-1', order='order-1', task=42, base='base-a'):
        return self.request('POST', '/v1/results',
                            {'baseId':base, 'orderId':order, 'operationId':operation,
                             'taskId':task}, self.result_token)

    def test_result_redelivered_until_exact_ack_and_duplicates_preserve_first(self):
        self.begin()
        self.assertEqual(200, self.result()[0])
        for _ in range(2):
            status, rows = self.request('GET', '/v1/results', token=self.tokens['base-a'])
            self.assertEqual(200, status)
            self.assertEqual(42, rows['results'][0]['taskId'])
        ack = {'baseId':'base-a', 'orderId':'order-1', 'operationId':'op-1', 'taskId':43}
        self.assertEqual(409, self.request('POST', '/v1/acknowledgments', ack,
                                          self.tokens['base-a'])[0])
        self.assertEqual(409, self.result(task=43)[0])
        ack['taskId'] = 42
        for _ in range(2):
            self.assertEqual(200, self.request('POST', '/v1/acknowledgments', ack,
                                              self.tokens['base-a'])[0])
        self.assertEqual(200, self.result()[0])
        self.assertEqual([], self.request('GET', '/v1/results',
                                         token=self.tokens['base-a'])[1]['results'])

    def test_unknown_blocks_new_attempt_and_clients_cannot_submit_results_or_cancel(self):
        self.begin()
        context = {'baseId':'base-a','orderId':'order-1','operationId':'op-1'}
        self.assertEqual(200, self.request('POST', '/v1/unknown', context,
                                          self.tokens['base-a'])[0])
        context['operationId'] = 'op-2'
        self.assertEqual(409, self.request('POST', '/v1/operations', context,
                                          self.tokens['base-a'])[0])
        context.update(operationId='op-1', taskId=42)
        self.assertEqual(403, self.request('POST', '/v1/results', context,
                                          self.tokens['base-a'])[0])
        self.assertEqual(404, self.request('POST', '/v1/cancel', context,
                                          self.tokens['base-a'])[0])
        self.assertEqual('unknown', self.request('GET', '/v1/operations/op-1',
                          token=self.tokens['base-a'])[1]['state'])

    def test_auth_and_base_scope_reject_reads_and_writes_without_data_leak(self):
        for token in (None, 'incorrect'):
            self.assertEqual(401, self.request('GET', '/v1/results', token=token)[0])
        self.begin()
        self.assertEqual(200, self.result()[0])
        self.assertEqual([], self.request('GET', '/v1/results',
                          token=self.tokens['base-b'])[1]['results'])
        self.assertEqual(404, self.request('GET', '/v1/operations/op-1',
                                           token=self.tokens['base-b'])[0])
        status, error = self.request('POST', '/v1/acknowledgments',
                                     {'baseId':'base-a','orderId':'order-1',
                                      'operationId':'op-1','taskId':42}, self.tokens['base-b'])
        self.assertEqual(403, status)
        self.assertNotIn('order-1', json.dumps(error))

    def test_lost_http_response_does_not_lose_result(self):
        self.begin()
        body = json.dumps({'baseId':'base-a','orderId':'order-1',
                           'operationId':'op-1','taskId':42}).encode()
        packet = (f'POST /v1/results HTTP/1.0\r\nHost: 127.0.0.1\r\n'
                  f'Authorization: Bearer {self.result_token}\r\n'
                  f'Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n').encode()+body
        with socket.create_connection(self.server.server_address, timeout=5) as connection:
            connection.sendall(packet)
            connection.shutdown(socket.SHUT_WR)
            # Server headers prove it accepted the request; deliberately discard the body.
            headers = b''
            while b'\r\n\r\n' not in headers:
                chunk = connection.recv(1)
                self.assertTrue(chunk, 'Server closed before response headers')
                headers += chunk
            self.assertIn(b'200', headers.split(b'\r\n')[0])
        self.assertEqual(200, self.result()[0])
        self.assertEqual(42, self.request('GET', '/v1/results',
                          token=self.tokens['base-a'])[1]['results'][0]['taskId'])

    def test_invalid_requests_do_not_resolve_or_acknowledge_operation(self):
        self.begin()
        for task in (None, True, 0, -1, '42', 2**63):
            self.assertEqual(409, self.result(task=task)[0])
        context = {'baseId':'base-a','orderId':'order-1','operationId':'op-1'}
        for change in ({'operationId':'../op-1'}, {'operationId':''},
                       {'orderId':False}, {'extra':'value'}):
            invalid = dict(context, **change)
            self.assertEqual(400, self.request('POST','/v1/operations', invalid,
                                              self.tokens['base-a'])[0])
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        try:
            connection.request('POST', '/v1/operations',
                               b'{"baseId":"base-a","baseId":"base-b"}',
                               {'Content-Type':'application/json',
                                'Authorization':'Bearer '+self.tokens['base-a']})
            response = connection.getresponse()
            self.assertEqual(400, response.status)
            self.assertEqual('invalid_json', json.loads(response.read())['error'])
        finally:
            connection.close()
        self.assertEqual('pending', self.request('GET','/v1/operations/op-1',
                          token=self.tokens['base-a'])[1]['state'])

    def test_empty_or_reused_credentials_cannot_start_server(self):
        for clients, result in (({'base-a':''}, self.result_token),
                                (self.tokens, self.tokens['base-a']),
                                ({'base-a':self.result_token,'base-b':self.result_token},
                                 secrets.token_urlsafe(32))):
            with self.assertRaises(ValueError):
                create_server(self.database, clients, result)

    def test_1c_reads_addressed_order_challenge_and_submits_saved_snapshot(self):
        context={'baseId':'base-a','initiatorId':'30','workplaceId':'pc-1','sessionId':'session-1'}
        token=self.tokens['base-a']
        self.assertEqual(200,self.request('POST','/v1/1c/heartbeat',context,token)[0])
        request={**context,'orderId':'order-1','operationId':'op-1','payload':{'TITLE':'Old.','GROUP_ID':36}}
        self.assertEqual(200,self.request('POST','/v1/task-requests',request,token)[0])
        store=OperationStore(self.database)
        store.claim_active_task_request('base-a','30','pc-1','session-1','instance-1',operation='op-1')
        store.require_order_check('base-a','op-1','instance-1')
        status,response=self.request('POST','/v1/1c/checks',context,token)
        self.assertEqual(200,status)
        check=response['checks'][0]
        validation={**context,'orderId':'order-1','operationId':'op-1','checkId':check['checkId'],
                    'eligible':True,'payload':{'TITLE':'Saved.','GROUP_ID':36}}
        for _ in range(2):
            self.assertEqual(200,self.request('POST','/v1/1c/validate',validation,token)[0])
        self.assertEqual([],self.request('POST','/v1/1c/checks',context,token)[1]['checks'])
        self.assertEqual(403,self.request('POST','/v1/1c/validate',validation,self.tokens['base-b'])[0])
        self.assertEqual('Saved.',store.require_order_check('base-a','op-1','instance-1')['payload']['TITLE'])

    def test_non_loopback_listener_requires_an_explicit_tls_context(self):
        with self.assertRaisesRegex(ValueError, 'TLS'):
            create_server(self.database, self.tokens, self.result_token, host='0.0.0.0')

    def test_result_survives_actual_service_process_restart(self):
        self.begin()
        self.assertEqual(200, self.result()[0])
        self.stop_server()
        config = Path(self.temp.name) / 'settings.json'
        config.write_text(json.dumps({'database':str(self.database), 'port':0,
                                     'clientTokens':self.tokens, 'resultToken':self.result_token}))
        process = subprocess.Popen([sys.executable, str(SOURCE/'operation_http.py'),
                                    '--config', str(config)], stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop_process, process)
        # A background reader prevents a failed startup from hanging this test.
        output = []
        ready = threading.Event()
        def read_ready():
            output.append(process.stdout.readline())
            ready.set()
        threading.Thread(target=read_ready, daemon=True).start()
        self.assertTrue(ready.wait(5), 'Service did not report readiness')
        self.assertTrue(output[0], 'Service exited before readiness')
        port = json.loads(output[0])['port']
        self.assertEqual(42, self.request('GET', '/v1/results', token=self.tokens['base-a'],
                                         port=port)[1]['results'][0]['taskId'])

    @staticmethod
    def stop_process(process):
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main()
