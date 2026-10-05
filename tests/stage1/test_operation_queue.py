from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[2] / 'src/stage-1-service'
sys.path.insert(0, str(SOURCE))
from operation_store import Conflict, OperationStore


class TaskRequestQueueContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = OperationStore(Path(self.temp.name) / 'operations.sqlite')
        self.payload = {
            'TITLE': 'Демо контрагент.',
            'DESCRIPTION': 'Данные сохраненного заказа',
            'GROUP_ID': 36,
        }

    def enqueue(self, **changes):
        values = dict(base='base-a', order='order-1', operation='op-1',
                      initiator='user-7', workplace='pc-11',
                      payload=self.payload)
        values.update(changes)
        return self.store.enqueue_task_request(**values)

    def test_request_persists_exact_context_payload_and_correlation_tag(self):
        row = self.enqueue()
        self.assertEqual('КА-op-1', row['correlationTag'])
        self.assertEqual('user-7', row['initiatorId'])
        self.assertEqual('pc-11', row['workplaceId'])
        self.assertEqual(self.payload, row['payload'])
        self.assertEqual('queued', row['deliveryState'])
        reopened = OperationStore(Path(self.temp.name) / 'operations.sqlite')
        self.assertEqual(row, reopened.task_request('base-a', 'op-1'))

    def test_exact_repeat_is_idempotent_but_changed_request_is_conflict(self):
        first = self.enqueue()
        self.assertEqual(first, self.enqueue())
        with self.assertRaises(Conflict):
            self.enqueue(payload={**self.payload, 'TITLE':'Other.'})
        with self.assertRaises(Conflict):
            self.enqueue(initiator='user-8')

    def test_queue_claim_is_scoped_to_user_and_workplace(self):
        self.enqueue()
        self.assertIsNone(self.store.claim_task_request(
            'base-a', 'user-8', 'pc-11', 'window-1', now=100, lease_seconds=20))
        self.assertIsNone(self.store.claim_task_request(
            'base-a', 'user-7', 'pc-12', 'window-1', now=100, lease_seconds=20))
        claimed = self.store.claim_task_request(
            'base-a', 'user-7', 'pc-11', 'window-1', now=100, lease_seconds=20)
        self.assertEqual('claimed', claimed['deliveryState'])
        self.assertIsNone(self.store.claim_task_request(
            'base-a', 'user-7', 'pc-11', 'window-2', now=101, lease_seconds=20))
        self.assertEqual(claimed, self.store.claim_task_request(
            'base-a', 'user-7', 'pc-11', 'window-1', now=101, lease_seconds=20))

    def test_concurrent_windows_claim_only_once(self):
        self.enqueue()
        def claim(window):
            return self.store.claim_task_request(
                'base-a', 'user-7', 'pc-11', window, now=100, lease_seconds=20)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, ('window-1', 'window-2')))
        self.assertEqual(1, sum(result is not None for result in results))

    def test_background_worker_claims_oldest_request_across_workplaces_for_its_user(self):
        first = self.enqueue(workplace='pc-11')
        self.enqueue(order='order-2', operation='op-2', workplace='pc-12')
        self.enqueue(order='order-3', operation='op-3', initiator='user-8', workplace='pc-11')
        claimed = self.store.claim_next_task_request(
            'base-a', 'user-7', 'window-1', now=100, lease_seconds=20)
        self.assertEqual(first['operationId'], claimed['operationId'])
        self.assertIsNone(self.store.claim_next_task_request(
            'base-a', 'user-7', 'window-2', now=101, lease_seconds=20))
        self.assertEqual('op-3', self.store.claim_next_task_request(
            'base-a', 'user-8', 'window-3', now=101, lease_seconds=20)['operationId'])

    def test_worker_queue_claim_is_atomic_across_windows_and_computers(self):
        self.enqueue(workplace='pc-11')
        def claim(worker):
            return self.store.claim_next_task_request(
                'base-a', 'user-7', worker, now=100, lease_seconds=20)
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(claim, ('window-1','window-2','other-computer')))
        self.assertEqual(1, sum(result is not None for result in results))

    def test_entity_claim_requires_the_exact_active_1c_session_and_workplace(self):
        self.enqueue(session_id='1c-session-1')
        self.assertIsNone(self.store.claim_active_task_request(
            'base-a', 'user-7', 'pc-11', '1c-session-1', 'worker-instance-1',
            now=100, lease_seconds=30))
        self.store.heartbeat_1c_session(
            'base-a', 'user-7', 'pc-11', '1c-session-1', now=100, ttl_seconds=30)
        self.assertIsNone(self.store.claim_active_task_request(
            'base-a', 'user-7', 'pc-12', '1c-session-1', 'worker-instance-2',
            now=101, lease_seconds=30))
        self.assertIsNone(self.store.claim_active_task_request(
            'base-a', 'user-7', 'pc-11', 'another-session', 'worker-instance-2',
            now=101, lease_seconds=30))
        claimed = self.store.claim_active_task_request(
            'base-a', 'user-7', 'pc-11', '1c-session-1', 'worker-instance-1',
            now=101, lease_seconds=30)
        self.assertEqual('worker-instance-1', claimed['leaseOwner'])
        self.assertEqual('1c-session-1', claimed['sessionId'])
        self.assertIsNone(self.store.claim_active_task_request(
            'base-a', 'user-7', 'pc-11', '1c-session-1', 'worker-instance-2',
            now=102, lease_seconds=30))

    def test_closed_1c_session_blocks_new_openings_and_new_session_rebinds_only_queued(self):
        queued = self.enqueue(session_id='session-old')
        self.store.heartbeat_1c_session(
            'base-a', 'user-7', 'pc-11', 'session-old', now=100, ttl_seconds=30)
        self.store.heartbeat_1c_session(
            'base-a', 'user-7', 'pc-11', 'session-new', now=131, ttl_seconds=30)
        rebound = self.store.task_request('base-a', queued['operationId'])
        self.assertEqual('session-new', rebound['sessionId'])
        self.assertIsNone(self.store.claim_active_task_request(
            'base-a', 'user-7', 'pc-11', 'session-old', 'worker-1',
            now=132, lease_seconds=30))
        self.assertEqual('session-new', self.store.claim_active_task_request(
            'base-a', 'user-7', 'pc-11', 'session-new', 'worker-1',
            now=132, lease_seconds=30)['sessionId'])
        self.store.mark_task_request_opening('op-1','worker-1',now=133)
        self.store.heartbeat_1c_session(
            'base-a', 'user-7', 'pc-11', 'session-restarted', now=162, ttl_seconds=30)
        self.assertEqual('session-new', self.store.task_request('base-a','op-1')['sessionId'])

    def test_live_workplace_session_cannot_be_replaced_by_second_client(self):
        self.store.heartbeat_1c_session('base-a','user-7','pc-11','session-1',now=100)
        with self.assertRaises(Conflict):
            self.store.heartbeat_1c_session('base-a','user-7','pc-11','session-2',now=110)
        self.store.heartbeat_1c_session('base-a','user-7','pc-11','session-1',now=111)
        self.store.heartbeat_1c_session('base-a','user-7','pc-11','session-2',now=142)

    def test_request_outbox_is_persisted_and_rebound_with_the_1c_session(self):
        self.enqueue(session_id='session-old')
        first = self.store.pending_entity_messages(user='user-7')
        self.assertEqual(1, len(first))
        self.assertEqual('session-old', first[0]['message']['sessionId'])
        self.store.heartbeat_1c_session(
            'base-a', 'user-7', 'pc-11', 'session-new', now=100, ttl_seconds=30)
        pending = self.store.pending_entity_messages(user='user-7')
        self.assertEqual(1, len(pending))
        self.assertEqual('session-new', pending[0]['message']['sessionId'])
        self.store.mark_entity_message_published(pending[0]['messageId'], 57, now=101)
        reopened = OperationStore(Path(self.temp.name) / 'operations.sqlite')
        self.assertEqual([], reopened.pending_entity_messages(user='user-7'))

    def test_entity_inbox_is_idempotent_and_rejects_message_id_content_conflict(self):
        event = {'messageId':'event-1','type':'claim','operationId':'op-1'}
        first = self.store.receive_entity_message('user-7','event-1',event,now=100)
        self.assertEqual({'duplicate':False,'processed':False,'outcome':None},first)
        self.store.mark_entity_message_processed('user-7','event-1',{'granted':True},now=101)
        repeated = self.store.receive_entity_message('user-7','event-1',event,now=102)
        self.assertEqual({'duplicate':True,'processed':True,'outcome':{'granted':True}},repeated)
        with self.assertRaises(Conflict):
            self.store.receive_entity_message(
                'user-7','event-1',{**event,'operationId':'op-2'},now=103)

    def test_open_form_keeps_queue_sequential_until_it_closes(self):
        first = self.enqueue()
        self.enqueue(order='order-2', operation='op-2')
        self.store.claim_task_request('base-a','user-7','pc-11','window-1',
                                      now=100,lease_seconds=20)
        self.store.mark_task_request_opening('op-1','window-1',now=101)
        self.store.mark_task_request_opened('op-1','window-1',now=102)
        self.assertIsNone(self.store.claim_task_request(
            'base-a','user-7','pc-11','window-2',now=101,lease_seconds=20))
        self.store.mark_task_request_closed('op-1','window-1',now=103)
        next_request = self.store.claim_task_request(
            'base-a','user-7','pc-11','window-2',now=104,lease_seconds=20)
        self.assertEqual('op-2', next_request['operationId'])

    def test_heartbeat_extends_open_form_lease(self):
        self.enqueue()
        self.store.claim_task_request('base-a','user-7','pc-11','window-1',
                                      now=100,lease_seconds=20)
        self.store.mark_task_request_opening('op-1','window-1',now=101)
        self.store.mark_task_request_opened('op-1','window-1',now=102)
        renewed = self.store.renew_task_request('op-1','window-1',now=115,
                                                lease_seconds=20)
        self.assertEqual(135, renewed['leaseUntil'])
        self.assertIsNone(self.store.claim_task_request(
            'base-a','user-7','pc-11','window-2',now=130,lease_seconds=20))

    def test_opening_opened_and_slow_heartbeat_keep_a_ninety_second_lease(self):
        self.enqueue()
        self.store.claim_task_request('base-a','user-7','pc-11','window-1',
                                      now=100,lease_seconds=30)
        opening = self.store.mark_task_request_opening('op-1','window-1',now=101)
        self.assertEqual(191,opening['leaseUntil'])
        opened = self.store.mark_task_request_opened('op-1','window-1',now=122)
        self.assertEqual(212,opened['leaseUntil'])
        renewed = self.store.renew_task_request('op-1','window-1',now=133,lease_seconds=90)
        self.assertEqual(223,renewed['leaseUntil'])
        self.assertIsNone(self.store.claim_task_request(
            'base-a','user-7','pc-11','window-2',now=150,lease_seconds=30))

    def test_ninety_second_lease_still_rejects_wrong_owner_and_expired_heartbeat(self):
        self.enqueue()
        self.store.claim_task_request('base-a','user-7','pc-11','window-1',
                                      now=100,lease_seconds=30)
        self.store.mark_task_request_opening('op-1','window-1',now=101)
        self.store.mark_task_request_opened('op-1','window-1',now=102)
        with self.assertRaises(Conflict):
            self.store.renew_task_request('op-1','window-2',now=103,lease_seconds=90)
        with self.assertRaises(Conflict):
            self.store.renew_task_request('op-1','window-1',now=192,lease_seconds=90)
        row = self.store.task_request('base-a','op-1')
        self.assertEqual('unknown',row['deliveryState'])
        self.assertIsNone(row['leaseUntil'])

    def test_expired_dispatch_lease_becomes_unknown_and_is_not_reopened(self):
        self.enqueue()
        self.store.claim_task_request('base-a','user-7','pc-11','window-1',
                                      now=100,lease_seconds=20)
        with self.assertRaises(Conflict):
            self.store.mark_task_request_opening('op-1','window-1',now=121)
        self.assertEqual('unknown', self.store.task_request('base-a','op-1')['deliveryState'])
        self.assertIsNone(self.store.claim_task_request(
            'base-a','user-7','pc-11','window-2',now=121,lease_seconds=20))

    def test_openpath_close_is_unknown_until_tag_reconciliation(self):
        self.enqueue()
        self.store.claim_task_request(
            'base-a', 'user-7', 'pc-11', 'window-1', now=100, lease_seconds=20)
        self.store.mark_task_request_opening('op-1', 'window-1',now=101)
        closed = self.store.mark_task_request_closed('op-1', 'window-1',now=102)
        self.assertEqual('unknown', closed['deliveryState'])
        self.assertIsNone(self.store.claim_task_request(
            'base-a', 'user-7', 'pc-11', 'window-2', now=200, lease_seconds=20))
        with self.assertRaises(Conflict):
            self.store.accept_verified_task('base-a', 'order-1', 'op-1', 42,
                                            ['different-tag'])
        result = self.store.accept_verified_task(
            'base-a', 'order-1', 'op-1', 42, ['КА-op-1'])
        self.assertEqual(42, result['taskId'])
        self.assertEqual('created', self.store.task_request('base-a', 'op-1')['deliveryState'])

    def test_verified_task_id_is_one_to_one_and_exact_repeat_is_safe(self):
        self.enqueue()
        first = self.store.accept_verified_task('base-a', 'order-1', 'op-1', 42,
                                                ['КА-op-1'])
        self.assertEqual(first, self.store.accept_verified_task(
            'base-a', 'order-1', 'op-1', 42, ['КА-op-1']))
        with self.assertRaises(Conflict):
            self.store.accept_verified_task('base-a', 'another-order', 'op-2', 42,
                                            ['КА-op-2'])
        with self.assertRaises(Conflict):
            self.store.accept_verified_task('base-a', 'order-1', 'op-1', 43,
                                            ['КА-op-1'])

    def test_request_must_not_be_acknowledged_before_link_persistence(self):
        self.enqueue()
        with self.assertRaises(Conflict):
            self.store.acknowledge('op-1', 'base-a', 'order-1', 42)
        self.store.accept_verified_task('base-a', 'order-1', 'op-1', 42,
                                        ['КА-op-1'])
        self.store.acknowledge('op-1', 'base-a', 'order-1', 42)
        self.assertEqual('acknowledged', self.store.task_request(
            'base-a', 'op-1')['deliveryState'])


if __name__ == '__main__':
    unittest.main()
