from pathlib import Path
import sys
import unittest

SOURCE = Path(__file__).resolve().parents[2] / 'src/stage-1-service'
sys.path.insert(0, str(SOURCE))
from bitrix_entities import BitrixEntityClient, EntitySetupError


class BitrixEntityClientContract(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.client = BitrixEntityClient(
            'https://toofirmagem.bitrix24.kz', lambda:'private-token',
            transport=self.transport)

    def transport(self, url, body, timeout):
        import json
        request = json.loads(body)
        method = url.rsplit('/', 1)[-1].removesuffix('.json')
        self.calls.append((method, request))
        if method == 'entity.add': return {'result':True}
        if method == 'entity.rights': return {'result':request.get('ACCESS', {
            'U30':'R', 'U7':'X'})}
        if method == 'entity.item.property.add': return {'result':True}
        if method == 'placement.get': return {'result':self.placements}
        if method == 'placement.bind':
            self.placements.append({
                'placement':request['PLACEMENT'], 'handler':request['HANDLER'],
                'userId':request['USER_ID'], 'options':request['OPTIONS'],
            })
            return {'result':True}
        if method == 'entity.item.add':
            self._items = getattr(self, '_items', []) + [{
                'ID':71, 'NAME':request['NAME'],
                'PROPERTY_VALUES':request['PROPERTY_VALUES'],
            }]
            return {'result':71}
        if method == 'entity.item.get': return {'result':self.items}
        if method == 'user.current': return {'result':{'ID':self.oauth_user_id}}
        raise AssertionError(method)

    @property
    def placements(self):
        if not hasattr(self, '_placements'):
            self._placements = []
        return self._placements

    @property
    def items(self):
        return getattr(self, '_items', [])

    @property
    def oauth_user_id(self):
        return getattr(self, '_oauth_user_id', '7')

    def test_provisioning_creates_private_employee_channels_without_au(self):
        result = self.client.provision_user('30', owner_user_id='7')
        self.assertEqual({'outgoing':'Q_30','incoming':'R_30'}, result)
        creates = [params for method, params in self.calls if method == 'entity.add']
        self.assertEqual(2, len(creates))
        expected = {'U7':'X','U30':'R'}
        self.assertEqual(expected, creates[0]['ACCESS'])
        self.assertEqual({'U7':'X','U30':'W'}, creates[1]['ACCESS'])
        self.assertTrue(all('AU' not in params['ACCESS'] for params in creates))
        self.assertTrue(all('U30' in params['ACCESS'] for params in creates))

    def test_entity_scan_reads_rest_top_level_pagination(self):
        import json
        starts=[]
        def transport(url,body,timeout):
            start=json.loads(body).get('start',0);starts.append(start)
            return {'result':[{'ID':start+1}], **({'next':50} if start==0 else {})}
        client=BitrixEntityClient('https://portal.example.test',lambda:'private-token',transport=transport)
        self.assertEqual([{'ID':1},{'ID':51}],client.read_messages('30','outgoing'))
        self.assertEqual([0,50],starts)

    def test_current_user_id_reads_the_rest_oauth_principal(self):
        self.assertEqual('7', self.client.current_user_id())
        self.assertEqual('user.current', self.calls[-1][0])

    def test_current_user_id_rejects_missing_or_invalid_identity(self):
        self._oauth_user_id = ''
        with self.assertRaises(EntitySetupError):
            self.client.current_user_id()

    def test_worker_registration_is_personal_and_read_back_after_bind(self):
        result = self.client.ensure_worker_placement(
            '30', 'https://app.example/worker.html', 'https://app.example/error.html')
        self.assertEqual('bound', result)
        method, params = [item for item in self.calls if item[0] == 'placement.bind'][0]
        self.assertEqual('PAGE_BACKGROUND_WORKER', params['PLACEMENT'])
        self.assertEqual(30, params['USER_ID'])
        self.assertEqual({'errorHandlerUrl':'https://app.example/error.html'},params['OPTIONS'])
        self.assertEqual('verified', self.client.ensure_worker_placement(
            '30','https://app.example/worker.html','https://app.example/error.html'))

    def test_conflicting_global_or_other_user_registration_is_not_replaced(self):
        self._placements = [{'placement':'PAGE_BACKGROUND_WORKER','handler':'https://other/',
                             'userId':0,'options':{'errorHandlerUrl':'https://other/error'}}]
        with self.assertRaises(EntitySetupError):
            self.client.ensure_worker_placement(
                '30','https://app.example/worker.html','https://app.example/error.html')
        self.assertFalse(any(method == 'placement.bind' for method, _ in self.calls))

    def test_channel_item_writes_use_message_id_and_employee_store(self):
        message = {
            'messageId':'grant_op_1_instance_1','type':'grant','operationId':'op-1',
            'baseId':'base-a','orderId':'order-1','initiatorId':'30',
            'workplaceId':'pc-1','sessionId':'session-1','instanceId':'instance-1',
            'permissionId':'permission-1','payload':{'TITLE':'ТОО.'},
        }
        item_id = self.client.add_message('30','outgoing',message)
        duplicate_id = self.client.add_message('30','outgoing',message)
        self.assertEqual(71,item_id)
        self.assertEqual(item_id,duplicate_id)
        method, params = [item for item in self.calls if item[0] == 'entity.item.add'][0]
        self.assertEqual('Q_30',params['ENTITY'])
        self.assertEqual('grant_op_1_instance_1',params['NAME'])
        self.assertEqual('grant',params['PROPERTY_VALUES']['MESSAGE_TYPE'])
        self.assertEqual(1,len([item for item in self.calls if item[0] == 'entity.item.add']))

    def test_reused_message_id_with_changed_contents_is_a_conflict(self):
        message = {
            'messageId':'grant_op_1_instance_1','type':'grant','operationId':'op-1',
            'baseId':'base-a','orderId':'order-1','initiatorId':'30',
            'workplaceId':'pc-1','sessionId':'session-1','instanceId':'instance-1',
            'permissionId':'permission-1','payload':{'TITLE':'ТОО.'},
        }
        self.client.add_message('30','outgoing',message)
        changed = {**message,'payload':{'TITLE':'Changed.'}}
        with self.assertRaises(EntitySetupError):
            self.client.add_message('30','outgoing',changed)
        self.assertEqual(1,len([item for item in self.calls if item[0] == 'entity.item.add']))


if __name__ == '__main__':
    unittest.main()
