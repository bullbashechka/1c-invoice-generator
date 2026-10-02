from pathlib import Path
import json
import sys
import unittest

SOURCE = Path(__file__).resolve().parents[2] / 'src/stage-1-service'
sys.path.insert(0, str(SOURCE))
from bitrix_reconcile import (BitrixAPIRejected, BitrixRESTClient,
                              CorrelationUnavailable, ResultUnknown)
from operation_store import OperationStore
import tempfile


class BitrixTaskReconciliationContract(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.task = {'id':'42','tags':['КА-op-1'], 'title':'Edited title',
                     'description':'Edited by employee'}
        def transport(url, body, timeout):
            self.requests.append((url, body, timeout))
            return {'result':{'task':self.task}}
        self.client = BitrixRESTClient('https://portal.example.test', 'access-token-test',
                                       transport=transport)

    def test_reads_one_real_task_with_explicit_fields_and_correlates_exact_tag(self):
        task = self.client.read_task(42)
        self.assertEqual('42', task['id'])
        self.assertEqual(1, len(self.requests))
        url, body, timeout = self.requests[0]
        body = json.loads(body)
        self.assertEqual('https://portal.example.test/rest/tasks.task.get.json', url)
        self.assertEqual('access-token-test', body['auth'])
        self.assertEqual(42, body['id'])
        self.assertIn('tags', body['select'])
        self.assertNotIn('TITLE', body['select'])

        with tempfile.TemporaryDirectory() as temp:
            store = OperationStore(Path(temp) / 'operations.sqlite')
            store.enqueue_task_request('base', 'order', 'op-1', 'user', 'pc',
                                       {'TITLE':'Original.'})
            result = self.client.reconcile_task_add(42, store)
            self.assertEqual('created', result['state'])
            self.assertEqual(42, result['taskId'])

    def test_missing_or_modified_tag_never_creates_a_link(self):
        for tags in ([], ['КА-op-10'], ['КА-op-2','КА-op-1-extra']):
            with self.subTest(tags=tags):
                self.task = {'id':42, 'tags':tags}
                with tempfile.TemporaryDirectory() as temp:
                    store = OperationStore(Path(temp) / 'operations.sqlite')
                    store.enqueue_task_request('base','order','op-1','user','pc',
                                               {'TITLE':'Original.'})
                    with self.assertRaises(CorrelationUnavailable):
                        self.client.reconcile_task_add(42, store)
                    self.assertEqual([], store.pending('base'))
                    self.assertEqual('pending', store.get('op-1')['state'])

    def test_task_id_and_tag_shapes_are_strict(self):
        for task_id in (True, 0, -1, '42', 2**63):
            with self.subTest(task_id=task_id):
                with self.assertRaises(ValueError):
                    self.client.read_task(task_id)
        for invalid in ({'id':41,'tags':['КА-op-1']},
                        {'id':42,'tags':[{'name':'КА-op-1'}]},
                        {'id':42,'tags':'КА-op-1'}):
            self.task = invalid
            with self.assertRaises(CorrelationUnavailable):
                self.client.read_task(42)

    def test_http_or_api_uncertainty_is_not_treated_as_absence(self):
        for response in (TimeoutError('private'), b'not-json',
                         {'error':'expired_token','error_description':'private'}):
            with self.subTest(response=response):
                client = BitrixRESTClient('https://portal.example.test', 'access-token-test',
                                          transport=lambda *_: response)
                expected = BitrixAPIRejected if isinstance(response, dict) else ResultUnknown
                with self.assertRaises(expected) as raised:
                    client.read_task(42)
                self.assertNotIn('private', str(raised.exception))
                self.assertNotIn('access-token-test', str(raised.exception))

    def test_portal_and_token_configuration_fail_closed(self):
        for portal in ('http://portal.example.test', 'https://user:pw@portal.example.test',
                       'https://portal.example.test/path?token=value'):
            with self.subTest(portal=portal), self.assertRaises(ValueError):
                BitrixRESTClient(portal,'access-token-test')
        for token in ('', 'contains whitespace', None):
            with self.subTest(token=token), self.assertRaises(ValueError):
                BitrixRESTClient('https://portal.example.test',token)

    def test_current_user_id_is_read_from_the_portal_using_the_supplied_user_token(self):
        calls = []
        def transport(url, body, timeout):
            calls.append((url, json.loads(body), timeout))
            return {"result":{"ID":"30","ACTIVE":True}}
        client = BitrixRESTClient('https://portal.example.test', 'user-access-token',
                                  transport=transport)
        self.assertEqual('30', client.current_user_id())
        self.assertEqual('https://portal.example.test/rest/user.current.json', calls[0][0])
        self.assertEqual({'auth':'user-access-token'}, calls[0][1])

    def test_current_user_requires_a_valid_positive_portal_user_id(self):
        for result in ({"ID":"0"}, {"ID":"4.5"}, {"ID":True}, {"not_id":"30"}):
            client = BitrixRESTClient('https://portal.example.test', 'user-access-token',
                                      transport=lambda *_args, item=result: {"result":item})
            with self.subTest(result=result), self.assertRaises(ResultUnknown):
                client.current_user_id()

    def test_recovery_search_filters_exact_tag_and_reads_candidate_before_linking(self):
        calls = []
        def transport(url, body, timeout):
            request = json.loads(body)
            calls.append((url, request, timeout))
            if url.endswith('/tasks.task.list.json'):
                return {'result':{'tasks':[{'id':'42','tags':['КА-op-1','other']}],
                                  'total':1}}
            return {'result':{'task':self.task}}
        client = BitrixRESTClient('https://portal.example.test', 'access-token-test',
                                  transport=transport)
        with tempfile.TemporaryDirectory() as temp:
            store = OperationStore(Path(temp) / 'operations.sqlite')
            store.enqueue_task_request('base','order','op-1','user','pc',{'TITLE':'Original.'})
            linked = client.reconcile_correlation_tag('КА-op-1', store)
            self.assertEqual(('created',42),(linked['state'],linked['taskId']))
        self.assertEqual('tasks.task.list', calls[0][0].rsplit('/rest/',1)[1].removesuffix('.json'))
        self.assertEqual({'TAG':'КА-op-1'}, calls[0][1]['filter'])
        self.assertIn('TAGS', calls[0][1]['select'])
        self.assertTrue(calls[1][0].endswith('/tasks.task.get.json'))

    def test_recovery_does_not_pick_between_duplicate_tag_matches(self):
        calls = []
        def transport(url, body, timeout):
            request = json.loads(body)
            calls.append(request)
            if url.endswith('/tasks.task.list.json'):
                return {'result':{'tasks':[
                    {'id':'42','tags':['КА-op-1']}, {'id':'43','tags':['КА-op-1']}],
                    'total':2}}
            return {'result':{'task':self.task}}
        client = BitrixRESTClient('https://portal.example.test', 'access-token-test',
                                  transport=transport)
        with tempfile.TemporaryDirectory() as temp:
            store = OperationStore(Path(temp) / 'operations.sqlite')
            store.enqueue_task_request('base','order','op-1','user','pc',{'TITLE':'Original.'})
            with self.assertRaises(CorrelationUnavailable):
                client.reconcile_correlation_tag('КА-op-1', store)
            self.assertEqual('pending', store.get('op-1')['state'])
            self.assertEqual(1, len(calls))


if __name__ == '__main__':
    unittest.main()
