"""Controller failure test; it does not simulate or verify native 1C forms."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import run_stage3_state_forms as runner


class PendingClient:
    def __init__(self):
        self.fixture_exists = False
        self.cleanup_calls = 0

    def execute(self, code):
        if code == runner.PROBE:
            return {'baseFingerprint': 'test-base'}
        if 'Скопировать()' in code:
            self.fixture_exists = True
            return {'order': '00000000-0000-0000-0000-000000000001', 'number': 'test'}
        if 'СостояниеПроверки.Удалить()' in code:
            self.cleanup_calls += 1
            self.fixture_exists = False
            return True
        if 'Результат = СтрокиПроверки.Количество;' in code:
            return 7 + int(self.fixture_exists)
        if 'ХешСнимка' in code:
            return {'dataHash': 'unchanged', 'posted': False}
        return True

    def rpc(self, *_):
        # Lost client response: execution may still continue on the remote side.
        raise runner.UnknownOutcome('client-response-lost')


class StateFormsControllerTests(unittest.TestCase):
    def test_lost_client_response_leaves_fixture_for_reconciliation(self):
        client = PendingClient()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / '.local/stage-1/one-c-profile.json'
            profile.parent.mkdir(parents=True)
            profile.write_text(json.dumps({'baseFingerprint': 'test-base'}))
            contracts = root / 'tests/stage1'
            contracts.mkdir(parents=True)
            for name in ['order_data_fingerprint.bsl', 'stage3_state_form_contract.bsl']:
                shutil.copyfile(runner.ROOT / 'tests/stage1' / name, contracts / name)
            with patch.object(runner, 'ROOT', root), patch.object(runner, 'MCP', return_value=client):
                with self.assertRaises(runner.UnknownOutcome):
                    runner.main()
            self.assertEqual(client.cleanup_calls, 0, 'Do not remove state while the client may still use it')
            self.assertTrue(client.fixture_exists)


if __name__ == '__main__':
    unittest.main()
