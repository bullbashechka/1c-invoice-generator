import concurrent.futures
from contextlib import closing
import json
import sqlite3
from pathlib import Path
import sys
import tempfile
import threading
import unittest

SOURCE = Path(__file__).resolve().parents[2] / 'src/stage-1-service'
sys.path.insert(0, str(SOURCE))
from bitrix_task import (APIRejected, CreatedButNotRecorded, CreationBlocked,
                        ResultUnknown, TaskAPIClient, submit_task)
from operation_store import OperationStore


class TaskSubmissionContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / 'operations.sqlite'
        self.store = OperationStore(self.database)
        self.calls = []
        self.webhook = 'https://example.bitrix24.kz/rest/7/TEST_SECRET/'
        self.fields = dict(TITLE='Ручная правка.', RESPONSIBLE_ID=202,
                           CREATED_BY=201, AUDITORS=[203], ACCOMPLICES=[204],
                           DEADLINE='2026-10-03T18:00:00+05:00', GROUP_ID=205)

    def client(self, response=b'{"result":{"task":{"id":"42"}}}'):
        def transport(url, body, timeout):
            self.calls.append((url, json.loads(body), timeout))
            if isinstance(response, Exception):
                raise response
            return response
        return TaskAPIClient(self.webhook, transport=transport)

    def submit(self, client, fields=None):
        return submit_task(self.store, client, 'base', 'order', 'op',
                           self.fields if fields is None else fields)

    def test_employee_fields_sent_unchanged_and_result_is_persisted(self):
        result = self.submit(self.client())
        self.assertEqual('created', result['state'])
        self.assertEqual(42, result['taskId'])
        self.assertEqual(self.fields, self.calls[0][1]['fields'])
        self.assertEqual(self.webhook + 'tasks.task.add.json', self.calls[0][0])
        self.assertEqual(42, OperationStore(self.database).pending('base')[0]['taskId'])

    def test_duplicate_after_creation_or_ack_never_calls_api_again(self):
        client = self.client()
        self.submit(client)
        self.submit(client)
        self.store.acknowledge('op', 'base', 'order', 42)
        self.assertEqual('acknowledged', self.submit(client)['state'])
        self.assertEqual(1, len(self.calls))

    def test_timeout_leaves_unknown_and_restart_cannot_resubmit(self):
        with self.assertRaises(ResultUnknown) as error:
            self.submit(self.client(TimeoutError(self.webhook)))
        self.assertNotIn('TEST_SECRET', str(error.exception))
        self.assertEqual('unknown', self.store.get('op')['state'])
        self.store = OperationStore(self.database)
        with self.assertRaises(CreationBlocked):
            self.submit(self.client())
        self.assertEqual(1, len(self.calls))
        self.store.accept_result('op', 'base', 'order', 42)
        self.assertEqual(42, self.submit(self.client())['taskId'])
        self.assertEqual(1, len(self.calls))

    def test_api_rejection_keeps_context_and_does_not_retry(self):
        with self.assertRaises(APIRejected) as error:
            self.submit(self.client(b'{"error":"ACCESS_DENIED","error_description":"TEST_SECRET"}'))
        self.assertEqual('ACCESS_DENIED', str(error.exception))
        self.assertEqual('unknown', self.store.get('op')['state'])
        with self.assertRaises(CreationBlocked):
            self.submit(self.client())
        self.assertEqual(1, len(self.calls))

    def test_invalid_response_never_becomes_task_id(self):
        for response in (b'not json', b'{}', b'{"result":{"task":{"id":true}}}',
                         b'{"result":{"task":{"id":0}}}',
                         b'{"result":{"task":{"id":"-1"}}}'):
            with self.subTest(response=response):
                client = self.client(response)
                with self.assertRaises(ResultUnknown):
                    client.add(self.fields)

    def test_concurrent_submit_dispatches_once_before_response(self):
        entered, release = threading.Event(), threading.Event()
        count = []
        def transport(url, body, timeout):
            count.append(1)
            entered.set()
            if not release.wait(5):
                raise TimeoutError()
            return b'{"result":{"task":{"id":42}}}'
        client = TaskAPIClient(self.webhook, transport=transport)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.submit, client)
            try:
                self.assertTrue(entered.wait(5))
                with self.assertRaises(CreationBlocked):
                    self.submit(client)
            finally:
                release.set()
            self.assertEqual(42, first.result(timeout=5)['taskId'])
        self.assertEqual(1, len(count))

    def test_unknown_is_durable_before_network_call(self):
        def transport(url, body, timeout):
            self.assertEqual('unknown', OperationStore(self.database).get('op')['state'])
            return b'{"result":{"task":{"id":42}}}'
        self.submit(TaskAPIClient(self.webhook, transport=transport))

    def test_invalid_fields_do_not_reserve_an_operation(self):
        client = self.client()
        for fields in ({'TITLE':'', 'RESPONSIBLE_ID':202},
                       {'TITLE':'x', 'RESPONSIBLE_ID':True}):
            with self.assertRaises(ValueError):
                self.submit(client, fields)
        self.assertEqual([], self.calls)
        self.assertEqual('pending', self.store.begin('base', 'order', 'op')['state'])

    def test_cancelled_operation_cannot_be_dispatched(self):
        self.store.begin('base', 'order', 'op')
        self.store.cancel('op', absence_confirmed=True)
        with self.assertRaises(CreationBlocked):
            self.submit(self.client())
        self.assertEqual([], self.calls)

    def test_non_https_or_credential_in_authority_webhook_is_rejected(self):
        for url in ('http://example.bitrix24.kz/rest/7/TEST_SECRET/',
                    'https://user:pass@example.bitrix24.kz/rest/7/TEST_SECRET/',
                    'https://example.bitrix24.kz/rest/7/TEST_SECRET/?extra=1'):
            with self.assertRaises(ValueError):
                TaskAPIClient(url)

    def test_known_task_id_is_returned_to_caller_when_journal_write_fails(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("CREATE TRIGGER fail_result BEFORE UPDATE OF task_id ON operations "
                               "BEGIN SELECT RAISE(ABORT,'SIMULATED_RESULT_WRITE_FAILURE'); END")
        with self.assertRaises(CreatedButNotRecorded) as error:
            self.submit(self.client())
        self.assertEqual(42, error.exception.task_id)
        self.assertEqual('op', error.exception.operation_id)
        self.assertNotIn('TEST_SECRET', str(error.exception))
        self.assertEqual('unknown', self.store.get('op')['state'])
        with self.assertRaises(CreationBlocked):
            self.submit(self.client())
        self.assertEqual(1, len(self.calls))
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute('DROP TRIGGER fail_result')
        self.store.accept_result('op', 'base', 'order', error.exception.task_id)
        self.assertEqual(42, self.submit(self.client())['taskId'])
        self.assertEqual(1, len(self.calls))

    def test_invalid_edited_fields_do_not_dispatch_or_reserve(self):
        for field, value in (('CREATED_BY', True), ('CREATED_BY', -1),
                             ('GROUP_ID', -1), ('GROUP_ID', True),
                             ('AUDITORS', [True]), ('AUDITORS', '203'),
                             ('ACCOMPLICES', [0]), ('ACCOMPLICES', ['204']),
                             ('DEADLINE', 'not a date'), ('DESCRIPTION', 17)):
            with self.subTest(field=field, value=value):
                with self.assertRaises(ValueError):
                    self.submit(self.client(), {**self.fields, field:value})
        self.assertEqual([], self.calls)
        self.assertEqual('pending', self.store.begin('base', 'order', 'op')['state'])

    def test_employee_can_clear_optional_fields_without_default_restoration(self):
        fields = {**self.fields, 'GROUP_ID':0, 'AUDITORS':[], 'ACCOMPLICES':[],
                  'DEADLINE':'', 'DESCRIPTION':''}
        self.submit(self.client(), fields)
        self.assertEqual(fields, self.calls[0][1]['fields'])

    def test_edited_deadline_requires_explicit_time_and_timezone(self):
        for value in ('2026-10-03', '2026-10-03T18:00:00'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.submit(self.client(), {**self.fields, 'DEADLINE':value})
        self.assertEqual([], self.calls)


if __name__ == '__main__':
    unittest.main()
