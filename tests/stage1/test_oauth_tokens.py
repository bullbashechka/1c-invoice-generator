from pathlib import Path
import json
import sys
import tempfile
import threading
import unittest

SOURCE = Path(__file__).resolve().parents[2] / 'src/stage-1-service'
sys.path.insert(0, str(SOURCE))
from oauth_tokens import OAuthTokenStore, OAuthRefreshError


class OAuthTokenStoreContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'private' / 'b24-oauth.json'
        self.initial = {
            'clientId':'local-app-id', 'clientSecret':'private-client-secret',
            'accessToken':'access-old', 'refreshToken':'refresh-old', 'expiresAt':1100,
        }

    def test_one_time_import_refuses_to_replace_existing_credentials(self):
        OAuthTokenStore.initialize(self.path, self.initial)
        original = self.path.read_bytes()
        with self.assertRaises(FileExistsError):
            OAuthTokenStore.initialize(self.path, {**self.initial, 'refreshToken':'other'})
        self.assertEqual(original, self.path.read_bytes())

    def test_valid_access_token_is_reused_without_refresh(self):
        OAuthTokenStore.initialize(self.path, self.initial)
        store = OAuthTokenStore(self.path, now=lambda:1000,
                                refresh_transport=lambda *args: self.fail('unexpected refresh'))
        self.assertEqual('access-old', store.access_token())

    def test_refresh_rotates_both_tokens_atomically_and_survives_restart(self):
        OAuthTokenStore.initialize(self.path, {**self.initial, 'expiresAt':1000})
        calls = []
        def refresh(url, timeout):
            calls.append((url, timeout))
            return {'access_token':'access-new','refresh_token':'refresh-new','expires_in':3600}
        store = OAuthTokenStore(self.path, now=lambda:2000, refresh_transport=refresh)
        self.assertEqual('access-new', store.access_token())
        persisted = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(('access-new','refresh-new',5600),
                         (persisted['accessToken'],persisted['refreshToken'],persisted['expiresAt']))
        restarted = OAuthTokenStore(self.path, now=lambda:2001,
                                    refresh_transport=lambda *args: self.fail('unexpected refresh'))
        self.assertEqual('access-new', restarted.access_token())
        self.assertEqual(1, len(calls))

    def test_failed_refresh_keeps_the_previous_complete_token_pair(self):
        expired = {**self.initial, 'expiresAt':1000}
        OAuthTokenStore.initialize(self.path, expired)
        before = self.path.read_bytes()
        store = OAuthTokenStore(self.path, now=lambda:2000,
                                refresh_transport=lambda *args: {'error':'invalid_grant'})
        with self.assertRaises(OAuthRefreshError):
            store.access_token()
        self.assertEqual(before, self.path.read_bytes())

    def test_parallel_refreshes_share_one_rotated_pair(self):
        OAuthTokenStore.initialize(self.path, {**self.initial, 'expiresAt':1000})
        entered = threading.Event()
        release = threading.Event()
        calls = []
        def refresh(*args):
            calls.append(args)
            entered.set()
            release.wait(2)
            return {'access_token':'access-new','refresh_token':'refresh-new','expires_in':3600}
        store = OAuthTokenStore(self.path, now=lambda:2000, refresh_transport=refresh)
        results = []
        threads = [threading.Thread(target=lambda: results.append(store.access_token()))
                   for _ in range(3)]
        for thread in threads: thread.start()
        self.assertTrue(entered.wait(2))
        release.set()
        for thread in threads: thread.join(3)
        self.assertEqual(['access-new'] * 3, sorted(results))
        self.assertEqual(1, len(calls))


if __name__ == '__main__':
    unittest.main()
