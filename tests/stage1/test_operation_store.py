import concurrent.futures
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[2] / 'src/stage-1-service/operation_store.py'


class OperationStoreContract(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('operation_store', MODULE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.Store, self.Conflict = module.OperationStore, module.Conflict
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / 'operations.sqlite'
        self.store = self.Store(self.database)

    def test_unknown_result_prevents_second_attempt(self):
        self.store.begin('base', 'order', 'op1')
        self.store.mark_unknown('op1')
        with self.assertRaises(self.Conflict):
            self.store.begin('base', 'order', 'op2')
        self.assertEqual('unknown', self.store.begin('base', 'order', 'op1')['state'])

    def test_result_survives_process_restart_before_acknowledgment(self):
        self.store.begin('base', 'order', 'op1')
        code = ('import importlib.util,sys;'
                's=importlib.util.spec_from_file_location("store",sys.argv[1]);'
                'm=importlib.util.module_from_spec(s);s.loader.exec_module(m);'
                'm.OperationStore(sys.argv[2]).accept_result("op1","base","order",42)')
        subprocess.run([sys.executable, '-c', code, str(MODULE), str(self.database)], check=True)
        restarted = self.Store(self.database)
        self.assertEqual(42, restarted.pending('base')[0]['taskId'])
        restarted.acknowledge('op1', 'base', 'order', 42)
        restarted.acknowledge('op1', 'base', 'order', 42)
        self.assertEqual([], self.Store(self.database).pending('base'))

    def test_duplicate_delivery_and_conflicts_preserve_original(self):
        self.store.begin('base', 'order', 'op1')
        self.store.accept_result('op1', 'base', 'order', 42)
        self.store.accept_result('op1', 'base', 'order', 42)
        for operation, base, order, task in [('op1', 'base', 'order', 43),
                                             ('op1', 'base', 'other', 42),
                                             ('op1', 'other', 'order', 42)]:
            with self.assertRaises(self.Conflict):
                self.store.accept_result(operation, base, order, task)
        self.store.begin('base', 'other', 'op2')
        with self.assertRaises(self.Conflict):
            self.store.accept_result('op2', 'base', 'other', 42)
        self.assertEqual(42, self.store.get('op1')['taskId'])

    def test_cancel_requires_confirmed_absence_of_task(self):
        self.store.begin('base', 'order', 'op1')
        self.store.mark_unknown('op1')
        with self.assertRaises(self.Conflict):
            self.store.cancel('op1')
        self.store.cancel('op1', absence_confirmed=True)
        self.store.begin('base', 'order', 'op2')
        self.store.accept_result('op2', 'base', 'order', 42)
        with self.assertRaises(self.Conflict):
            self.store.cancel('op2', absence_confirmed=True)

    def test_concurrent_attempts_have_one_winner(self):
        def begin(number):
            try:
                self.Store(self.database).begin('base', 'order', f'op{number}')
                return True
            except self.Conflict:
                return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(1, sum(pool.map(begin, range(8))))

    def test_ack_requires_exact_saved_link(self):
        self.store.begin('base', 'order', 'op1')
        with self.assertRaises(self.Conflict):
            self.store.acknowledge('op1', 'base', 'order', 42)
        self.store.accept_result('op1', 'base', 'order', 42)
        with self.assertRaises(self.Conflict):
            self.store.acknowledge('op1', 'base', 'order', 43)
        self.assertEqual(1, len(self.store.pending('base')))

    def test_late_result_cannot_reassign_cancelled_operation(self):
        self.store.begin('base', 'order', 'op1')
        self.store.cancel('op1', absence_confirmed=True)
        self.store.begin('base', 'order', 'op2')
        with self.assertRaises(self.Conflict):
            self.store.accept_result('op1', 'base', 'order', 42)
        self.assertEqual('pending', self.store.get('op2')['state'])

    def test_duplicate_result_after_ack_does_not_request_delivery_again(self):
        self.store.begin('base', 'order', 'op1')
        self.store.accept_result('op1', 'base', 'order', 42)
        self.store.acknowledge('op1', 'base', 'order', 42)
        self.store.accept_result('op1', 'base', 'order', 42)
        with self.assertRaises(self.Conflict):
            self.store.mark_unknown('op1')
        self.assertEqual([], self.store.pending('base'))

    def test_invalid_task_ids_never_become_results(self):
        self.store.begin('base', 'order', 'op1')
        for task in (None, True, 0, -1, '42', 2**63):
            with self.assertRaises(self.Conflict):
                self.store.accept_result('op1', 'base', 'order', task)
        self.assertEqual('pending', self.store.get('op1')['state'])

    def test_concurrent_task_assignment_has_one_winner(self):
        self.store.begin('base', 'order1', 'op1')
        self.store.begin('base', 'order2', 'op2')

        def assign(number):
            try:
                self.Store(self.database).accept_result(
                    f'op{number}', 'base', f'order{number}', 42)
                return True
            except self.Conflict:
                return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(1, sum(pool.map(assign, (1, 2))))
        self.assertEqual(1, len(self.store.pending('base')))


if __name__ == '__main__':
    unittest.main()
