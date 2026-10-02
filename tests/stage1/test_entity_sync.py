from pathlib import Path
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[2] / 'src/stage-1-service'
sys.path.insert(0, str(SOURCE))
from operation_store import OperationStore
from entity_sync import BitrixEntitySync
from bitrix_entities import EntitySetupError
from bitrix_reconcile import ResultUnknown


def as_item(message, item_id):
    fields = {
        'MESSAGE_ID':message['messageId'], 'MESSAGE_TYPE':message['type'],
        'OPERATION_ID':message['operationId'], 'BASE_ID':message['baseId'],
        'ORDER_ID':message['orderId'], 'INITIATOR_ID':message['initiatorId'],
        'WORKPLACE_ID':message['workplaceId'], 'SESSION_ID':message['sessionId'],
        'INSTANCE_ID':message.get('instanceId',''),
        'PERMISSION_ID':message.get('permissionId',''),
        'TASK_ID':message.get('taskId',''), 'PAYLOAD_JSON':__import__('json').dumps(
            message.get('payload',{}),ensure_ascii=False), 'CREATED_AT':'100',
    }
    return {'ID':item_id,'NAME':message['messageId'],'PROPERTY_VALUES':fields}


class FakeEntityClient:
    def __init__(self):
        self.incoming = {'30':[]}
        self.outgoing = {'30':[]}
        self.next_id = 1
        self.fail_after_publish = False

    def read_messages(self, user, direction):
        return list((self.incoming if direction == 'incoming' else self.outgoing).get(user,[]))

    def add_message(self, user, direction, message):
        target = self.outgoing if direction == 'outgoing' else self.incoming
        existing = [item for item in target.get(user, [])
                    if item.get('NAME') == message['messageId']]
        if existing:
            return existing[0]['ID']
        self.next_id += 1
        target.setdefault(user,[]).append(as_item(message,self.next_id))
        if self.fail_after_publish:
            self.fail_after_publish = False
            raise RuntimeError('simulated response lost after remote commit')
        return self.next_id


class FakeTaskReader:
    def __init__(self, matches=None, *, fail=False):
        self.matches = [] if matches is None else matches
        self.fail = fail
        self.reads = []

    def find_task_ids_by_correlation_tag(self, tag):
        self.reads.append(tag)
        if self.fail:
            raise ResultUnknown('test transport failure')
        return list(self.matches)

    def reconcile_task_add(self, task_id, store):
        return store.accept_verified_task('base-a','order-1','op-1',task_id,['КА-op-1'])


class ProvisioningClient:
    def __init__(self, current_user_id):
        self.user_id = current_user_id
        self.provisioned = []
        self.identity_reads = 0

    def current_user_id(self):
        self.identity_reads += 1
        return self.user_id

    def provision_user(self, user_id, *, owner_user_id):
        self.provisioned.append((user_id, owner_user_id))
        return {'outgoing':'Q_' + user_id,'incoming':'R_' + user_id}

    def ensure_worker_placement(self, *args, **kwargs):
        return 'verified'


class BitrixEntitySyncContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = OperationStore(Path(self.temp.name) / 'operations.sqlite')
        self.entities = FakeEntityClient()
        self.sync = BitrixEntitySync(self.store,self.entities,'base-a',['30'])
        self.store.enqueue_task_request(
            'base-a','order-1','op-1','30','pc-1',
            {'TITLE':'Контрагент.','DESCRIPTION':'Заказ 1','GROUP_ID':36},
            session_id='1c-session-1',now=100)
        self.store.heartbeat_1c_session(
            'base-a','30','pc-1','1c-session-1',now=100,ttl_seconds=30)

    def claim(self, instance, *, workplace='pc-1', session='1c-session-1', message_id=None):
        message_id = message_id or 'claim_' + instance
        self.entities.incoming['30'].append(as_item({
            'messageId':message_id,'type':'claim','operationId':'op-1',
            'baseId':'base-a','orderId':'order-1','initiatorId':'30',
            'workplaceId':workplace,'sessionId':session,'instanceId':instance,
            'payload':{},
        },100+len(self.entities.incoming['30'])))

    def test_claim_is_granted_only_to_one_worker_instance_on_the_active_workplace(self):
        self.sync.sync_once(now=101)
        requests = [item for item in self.entities.outgoing['30']
                    if item['PROPERTY_VALUES']['MESSAGE_TYPE'] == 'request']
        self.assertEqual(1,len(requests))
        self.claim('window-1')
        self.claim('window-2')
        self.sync.sync_once(now=102)
        grants = [item for item in self.entities.outgoing['30']
                  if item['PROPERTY_VALUES']['MESSAGE_TYPE'] == 'grant']
        self.assertEqual(1,len(grants))
        grant = grants[0]['PROPERTY_VALUES']
        self.assertEqual('window-1',grant['INSTANCE_ID'])
        self.assertEqual('1c-session-1',grant['SESSION_ID'])
        self.assertEqual('window-1',self.store.task_request('base-a','op-1')['leaseOwner'])

    def test_wrong_computer_and_stale_1c_session_get_no_open_permission(self):
        self.claim('window-other-pc',workplace='pc-2')
        self.claim('window-stale-session',session='old-session')
        self.store.heartbeat_1c_session(
            'base-a','30','pc-1','1c-session-1',now=60,ttl_seconds=30)
        self.sync.sync_once(now=102)
        grants = [item for item in self.entities.outgoing['30']
                  if item['PROPERTY_VALUES']['MESSAGE_TYPE'] == 'grant']
        self.assertEqual([],grants)
        self.assertEqual('queued',self.store.task_request('base-a','op-1')['deliveryState'])

    def test_duplicate_claim_event_and_service_restart_do_not_change_lease_owner(self):
        self.claim('window-1',message_id='claim-same')
        self.sync.sync_once(now=101)
        owner = self.store.task_request('base-a','op-1')['leaseOwner']
        restarted = BitrixEntitySync(self.store,self.entities,'base-a',['30'])
        restarted.sync_once(now=102)
        self.assertEqual('window-1',owner)
        self.assertEqual(owner,self.store.task_request('base-a','op-1')['leaseOwner'])
        grants = [item for item in self.entities.outgoing['30']
                  if item['PROPERTY_VALUES']['MESSAGE_TYPE'] == 'grant']
        self.assertEqual(1,len(grants))

    def test_outbox_retry_after_remote_commit_does_not_duplicate_entity_message(self):
        self.entities.fail_after_publish = True
        first = self.sync.sync_once(now=101)
        pending = self.store.pending_entity_messages()
        self.assertEqual(0,first['messagesPublished'])
        self.assertEqual(1,len(pending))
        second = self.sync.sync_once(now=102)
        self.assertEqual(1,second['messagesPublished'])
        requests = [item for item in self.entities.outgoing['30']
                    if item['PROPERTY_VALUES']['MESSAGE_TYPE'] == 'request']
        self.assertEqual(1,len(requests))
        self.assertEqual([],self.store.pending_entity_messages())

    def test_open_and_close_messages_advance_state_without_acknowledging_task(self):
        self.claim('window-1')
        self.sync.sync_once(now=101)
        for index, event_type in enumerate(('opening','opened','closed'),start=1):
            self.entities.incoming['30'].append(as_item({
                'messageId':'event-' + str(index),'type':event_type,
                'operationId':'op-1','baseId':'base-a','orderId':'order-1',
                'initiatorId':'30','workplaceId':'pc-1','sessionId':'1c-session-1',
                'instanceId':'window-1','payload':{},
            },200+index))
        self.sync.sync_once(now=102)
        self.assertEqual('unknown',self.store.task_request('base-a','op-1')['deliveryState'])
        self.assertEqual('pending',self.store.get('op-1')['state'])

    def test_tag_reconciliation_accepts_one_reread_candidate_and_repeats_safely(self):
        reader = FakeTaskReader([42])
        self.sync._task_reader = reader
        first = self.sync.reconcile_once(now=110)
        second = self.sync.reconcile_once(now=111)
        self.assertEqual({'accepted':1,'unknown':0,'conflicts':0},first)
        self.assertEqual({'accepted':1,'unknown':0,'conflicts':0},second)
        self.assertEqual(42,self.store.get('op-1')['taskId'])
        self.assertEqual(['КА-op-1','КА-op-1'],reader.reads)

    def test_duplicate_exact_tags_are_recorded_as_conflict_without_replacing_link(self):
        reader = FakeTaskReader([42,43])
        self.sync._task_reader = reader
        result = self.sync.reconcile_once(now=110)
        self.assertEqual({'accepted':0,'unknown':0,'conflicts':1},result)
        self.assertEqual('duplicate_task_tag',self.store.get('op-1')['conflict'])
        self.assertIsNone(self.store.get('op-1')['taskId'])

    def test_empty_or_unknown_search_does_not_cancel_or_conflict_the_operation(self):
        self.sync._task_reader = FakeTaskReader([])
        result = self.sync.reconcile_once(now=110)
        self.assertEqual({'accepted':0,'unknown':1,'conflicts':0},result)
        self.sync._task_reader = FakeTaskReader(fail=True)
        result = self.sync.reconcile_once(now=111)
        self.assertEqual({'accepted':0,'unknown':1,'conflicts':0},result)
        self.assertIsNone(self.store.get('op-1')['conflict'])
        self.assertEqual('pending',self.store.get('op-1')['state'])

    def test_provisioning_refuses_oauth_identity_mismatch_before_any_remote_write(self):
        client = ProvisioningClient('8')
        sync = BitrixEntitySync(
            self.store, client, 'base-a', ['30'],
            worker_handler_url='https://app.example/worker.html',
            error_handler_url='https://app.example/error.html')

        with self.assertRaises(EntitySetupError):
            sync.provision('7')

        self.assertEqual(1, client.identity_reads)
        self.assertEqual([], client.provisioned)

    def test_provisioning_verifies_owner_then_creates_each_private_employee_channel(self):
        client = ProvisioningClient('7')
        sync = BitrixEntitySync(
            self.store, client, 'base-a', ['30'],
            worker_handler_url='https://app.example/worker.html',
            error_handler_url='https://app.example/error.html')

        result = sync.provision('7')

        self.assertEqual(1, client.identity_reads)
        self.assertEqual([('30','7')], client.provisioned)
        self.assertEqual('verified', result['30']['workerPlacement'])


if __name__ == '__main__':
    unittest.main()
