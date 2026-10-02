from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys
import unittest

SOURCE = Path(__file__).resolve().parents[2] / 'src/stage-1-service'
sys.path.insert(0, str(SOURCE))
from bitrix_task import build_task_fields


class TaskFieldsContract(unittest.TestCase):
    def setUp(self):
        self.order = dict(counterparty='Тестовый контрагент', client='Другой клиент',
                          number='DEMO-7', date='01.10.2026', amount=Decimal('1250.50'),
                          currency='KZT', link='e1cib/data/Документ.ЗаказКлиента?ref=demo')
        self.defaults = dict(creator=101, responsible=102, auditor=103,
                             accomplice=104, project=105)
        self.opened_at = datetime(2026, 10, 1, 20, 10, tzinfo=timezone.utc)

    def test_counterparty_title_and_verified_defaults(self):
        fields = build_task_fields(self.order, self.defaults, self.opened_at)
        self.assertEqual('Тестовый контрагент.', fields['TITLE'])
        self.assertEqual(101, fields['CREATED_BY'])
        self.assertEqual(102, fields['RESPONSIBLE_ID'])
        self.assertEqual([103], fields['AUDITORS'])
        self.assertEqual([104], fields['ACCOMPLICES'])
        self.assertEqual(105, fields['GROUP_ID'])
        self.assertNotIn('Другой клиент', fields['TITLE'])

    def test_description_uses_saved_order_and_original_date(self):
        fields = build_task_fields(self.order, self.defaults, self.opened_at)
        self.assertEqual('Заказ клиента №DEMO-7 от 01.10.2026\n'
                         'Клиент: Другой клиент\nСумма: 1250.50 KZT\n'
                         'Документ 1С: e1cib/data/Документ.ЗаказКлиента?ref=demo',
                         fields['DESCRIPTION'])

    def test_deadline_is_opening_day_in_utc_plus_five(self):
        fields = build_task_fields(self.order, self.defaults, self.opened_at)
        self.assertEqual('2026-10-02T23:59:00+05:00', fields['DEADLINE'])

    def test_prepared_snapshot_is_not_changed_by_later_order_edit(self):
        fields = build_task_fields(self.order, self.defaults, self.opened_at)
        self.order['counterparty'] = 'Новый контрагент'
        self.order['amount'] = Decimal('99')
        self.defaults['auditor'] = 999
        self.assertEqual('Тестовый контрагент.', fields['TITLE'])
        self.assertIn('1250.50 KZT', fields['DESCRIPTION'])
        self.assertEqual([103], fields['AUDITORS'])

    def test_invalid_participant_ids_are_not_sent(self):
        for name in self.defaults:
            for value in (True, 0, -1, '101', None):
                with self.subTest(name=name, value=value):
                    changed = {**self.defaults, name: value}
                    with self.assertRaises(ValueError):
                        build_task_fields(self.order, changed, self.opened_at)

    def test_missing_counterparty_or_naive_time_is_rejected(self):
        with self.assertRaises(ValueError):
            build_task_fields({**self.order, 'counterparty': ' '}, self.defaults,
                              self.opened_at)
        with self.assertRaises(ValueError):
            build_task_fields(self.order, self.defaults, self.opened_at.replace(tzinfo=None))


if __name__ == '__main__':
    unittest.main()
