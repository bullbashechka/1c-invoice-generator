"""Validate that each live race targets exactly one shared key, without mocks."""
import unittest
import uuid

from run_register_concurrency import make_attempts


class ConflictAttemptsTest(unittest.TestCase):
    def check_case(self, conflict, shared):
        attempts = make_attempts(conflict)
        for key in ('orderIds', 'operationIds', 'taskIds'):
            values = attempts[key]
            self.assertEqual(len(values), 2)
            if key == shared:
                self.assertEqual(values[0], values[1])
            else:
                self.assertNotEqual(values[0], values[1])
        for key in ('orderIds', 'operationIds'):
            for value in attempts[key]:
                self.assertEqual(str(uuid.UUID(value)), value)
        for value in attempts['taskIds']:
            self.assertGreaterEqual(int(value), 2100000100)
            self.assertLess(int(value), 2147483647)

    def test_task_conflict_has_distinct_orders_and_operations(self):
        self.check_case('task', 'taskIds')

    def test_order_conflict_has_distinct_tasks_and_operations(self):
        self.check_case('order', 'orderIds')

    def test_operation_conflict_has_distinct_orders_and_tasks(self):
        self.check_case('operation', 'operationIds')

    def test_unknown_case_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'UNKNOWN_CONFLICT_CASE'):
            make_attempts('unknown')


if __name__ == '__main__':
    unittest.main()
