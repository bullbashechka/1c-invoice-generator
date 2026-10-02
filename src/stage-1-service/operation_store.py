"""Durable operation journal prototype for one configured Bitrix24 portal."""

from contextlib import contextmanager
import sqlite3


class Conflict(Exception):
    pass


class OperationStore:
    def __init__(self, database):
        self.database = str(database)
        with self._transaction() as connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS operations (
                    operation_id TEXT PRIMARY KEY,
                    base_id TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN
                        ('pending','unknown','created','acknowledged','cancelled')),
                    task_id INTEGER UNIQUE CHECK(task_id > 0)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS active_order
                    ON operations(base_id, order_id) WHERE state != 'cancelled';
            ''')

    @contextmanager
    def _transaction(self):
        connection = sqlite3.connect(self.database, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('PRAGMA synchronous=FULL')
            connection.execute('BEGIN IMMEDIATE')
            yield connection
            connection.commit()
        except sqlite3.IntegrityError as error:
            connection.rollback()
            raise Conflict('Operation or task is already assigned') from error
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _row(connection, operation):
        row = connection.execute('SELECT * FROM operations WHERE operation_id=?',
                                 (operation,)).fetchone()
        if row is None:
            raise Conflict('Unknown operation')
        return row

    @staticmethod
    def _public(row):
        return dict(operationId=row['operation_id'], baseId=row['base_id'],
                    orderId=row['order_id'], state=row['state'], taskId=row['task_id'],
                    linkAcknowledged=row['state'] == 'acknowledged')

    @staticmethod
    def _context(row, base, order):
        if row['base_id'] != base or row['order_id'] != order:
            raise Conflict('Operation context does not match')

    @staticmethod
    def _task(task):
        if type(task) is not int or task <= 0 or task > 2**63 - 1:
            raise Conflict('Invalid task ID')

    def begin(self, base, order, operation):
        if any(not isinstance(value, str) or not value.strip()
               for value in (base, order, operation)):
            raise Conflict('Missing operation context')
        with self._transaction() as connection:
            existing = connection.execute('SELECT * FROM operations WHERE operation_id=?',
                                          (operation,)).fetchone()
            if existing is not None:
                self._context(existing, base, order)
                return self._public(existing)
            connection.execute('INSERT INTO operations VALUES (?,?,?,\'pending\',NULL)',
                               (operation, base, order))
            return self._public(self._row(connection, operation))

    def get(self, operation):
        with self._transaction() as connection:
            return self._public(self._row(connection, operation))

    def mark_unknown(self, operation):
        with self._transaction() as connection:
            row = self._row(connection, operation)
            if row['state'] not in ('pending', 'unknown'):
                raise Conflict('Cannot change resolved operation to unknown')
            connection.execute('UPDATE operations SET state=\'unknown\' WHERE operation_id=?',
                               (operation,))

    def claim_creation(self, operation, base, order):
        """Reserve the single outbound create before dispatch, durably and atomically."""
        with self._transaction() as connection:
            row = self._row(connection, operation)
            self._context(row, base, order)
            if row['state'] != 'pending':
                return False
            connection.execute('UPDATE operations SET state=\'unknown\' WHERE operation_id=?',
                               (operation,))
            return True

    def accept_result(self, operation, base, order, task):
        self._task(task)
        with self._transaction() as connection:
            row = self._row(connection, operation)
            self._context(row, base, order)
            if row['state'] == 'cancelled':
                raise Conflict('Cancelled operation received a result')
            if row['task_id'] is not None:
                if row['task_id'] != task:
                    raise Conflict('Operation already has a different task')
                return self._public(row)
            connection.execute('UPDATE operations SET task_id=?, state=\'created\' '
                               'WHERE operation_id=?', (task, operation))
            return self._public(self._row(connection, operation))

    def pending(self, base):
        with self._transaction() as connection:
            return [self._public(row) for row in connection.execute(
                'SELECT * FROM operations WHERE base_id=? AND state=\'created\' '
                'ORDER BY operation_id', (base,))]

    def acknowledge(self, operation, base, order, task):
        self._task(task)
        with self._transaction() as connection:
            row = self._row(connection, operation)
            self._context(row, base, order)
            if row['state'] not in ('created', 'acknowledged') or row['task_id'] != task:
                raise Conflict('Acknowledgment does not match a created task')
            connection.execute('UPDATE operations SET state=\'acknowledged\' '
                               'WHERE operation_id=?', (operation,))

    def cancel(self, operation, *, absence_confirmed=False):
        with self._transaction() as connection:
            row = self._row(connection, operation)
            if absence_confirmed is not True or row['task_id'] is not None:
                raise Conflict('Cancellation requires confirmed absence of task')
            connection.execute('UPDATE operations SET state=\'cancelled\' WHERE operation_id=?',
                               (operation,))
