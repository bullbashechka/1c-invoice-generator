"""Durable operation journal prototype for one configured Bitrix24 portal."""

from contextlib import contextmanager
import hashlib
import json
import math
import re
import sqlite3


class Conflict(Exception):
    pass


class OperationStore:
    _IDENTIFIER = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
    _MAX_REQUEST_BYTES = 32768

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
                CREATE TABLE IF NOT EXISTS task_requests (
                    operation_id TEXT PRIMARY KEY,
                    initiator_id TEXT NOT NULL,
                    workplace_id TEXT NOT NULL,
                    session_id TEXT NOT NULL DEFAULT 'unbound',
                    correlation_tag TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    delivery_state TEXT NOT NULL CHECK(delivery_state IN
                        ('queued','claimed','opening','opened','unknown','created','acknowledged','cancelled')),
                    lease_owner TEXT,
                    lease_until REAL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(operation_id) REFERENCES operations(operation_id)
                );
                CREATE INDEX IF NOT EXISTS task_request_queue
                    ON task_requests(initiator_id, workplace_id, delivery_state, created_at);
                CREATE TABLE IF NOT EXISTS active_1c_sessions (
                    base_id TEXT NOT NULL,
                    initiator_id TEXT NOT NULL,
                    workplace_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    last_seen REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    PRIMARY KEY(base_id, initiator_id, workplace_id)
                );
                CREATE TABLE IF NOT EXISTS entity_outbox (
                    message_id TEXT PRIMARY KEY,
                    base_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    message_type TEXT NOT NULL,
                    message_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('pending','published','superseded')),
                    remote_item_id INTEGER,
                    created_at REAL NOT NULL,
                    published_at REAL
                );
                CREATE INDEX IF NOT EXISTS entity_outbox_pending
                    ON entity_outbox(state,created_at,message_id);
                CREATE TABLE IF NOT EXISTS entity_inbox (
                    user_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    message_json TEXT NOT NULL,
                    outcome_json TEXT,
                    received_at REAL NOT NULL,
                    processed_at REAL,
                    PRIMARY KEY(user_id,message_id)
                );
                CREATE TABLE IF NOT EXISTS operation_conflicts (
                    operation_id TEXT PRIMARY KEY,
                    conflict_code TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(operation_id) REFERENCES operations(operation_id)
                );
                CREATE TABLE IF NOT EXISTS order_checks (
                    operation_id TEXT PRIMARY KEY REFERENCES operations(operation_id),
                    check_id TEXT NOT NULL UNIQUE,
                    session_id TEXT NOT NULL,
                    instance_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','valid','rejected')),
                    payload_json TEXT,
                    created_at REAL NOT NULL,
                    validated_at REAL,
                    expires_at REAL
                );
            ''')
            columns = {row['name'] for row in connection.execute(
                'PRAGMA table_info(task_requests)').fetchall()}
            if 'session_id' not in columns:
                connection.execute("ALTER TABLE task_requests ADD COLUMN session_id TEXT NOT NULL DEFAULT 'unbound'")

    @contextmanager
    def _transaction(self):
        connection = sqlite3.connect(self.database, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute('PRAGMA foreign_keys=ON')
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

    @classmethod
    def _identifier(cls, value, name):
        if not isinstance(value, str) or not cls._IDENTIFIER.fullmatch(value):
            raise Conflict('Invalid ' + name)

    @staticmethod
    def _task_request(row):
        return dict(operationId=row['operation_id'], initiatorId=row['initiator_id'],
                    workplaceId=row['workplace_id'], sessionId=row['session_id'],
                    correlationTag=row['correlation_tag'],
                    payload=json.loads(row['payload_json']), deliveryState=row['delivery_state'],
                    leaseOwner=row['lease_owner'], leaseUntil=row['lease_until'],
                    createdAt=row['created_at'], updatedAt=row['updated_at'])

    @staticmethod
    def _entity_message_id(prefix, base, operation, revision=''):
        digest = hashlib.sha256((base + '\0' + operation + '\0' + revision).encode('utf-8')).hexdigest()[:32]
        return prefix + '_' + digest

    @staticmethod
    def _enqueue_entity_message(connection, *, base, user, operation, message, now):
        message_id = message.get('messageId')
        if not isinstance(message_id, str) or not OperationStore._IDENTIFIER.fullmatch(message_id):
            raise Conflict('Invalid entity message ID')
        if not isinstance(message, dict):
            raise Conflict('Entity message must be an object')
        try:
            encoded = json.dumps(message, ensure_ascii=False, sort_keys=True,
                                 separators=(',', ':'), allow_nan=False)
            if len(encoded.encode('utf-8')) > OperationStore._MAX_REQUEST_BYTES:
                raise ValueError
        except (TypeError, ValueError, UnicodeError):
            raise Conflict('Invalid entity message') from None
        existing = connection.execute('SELECT * FROM entity_outbox WHERE message_id=?',
                                       (message_id,)).fetchone()
        if existing is not None:
            if (existing['base_id'] != base or existing['user_id'] != user
                    or existing['operation_id'] != operation
                    or existing['message_json'] != encoded):
                raise Conflict('Entity message ID is already assigned')
            return message_id
        connection.execute(
            'INSERT INTO entity_outbox(message_id,base_id,user_id,operation_id,message_type,'
            'message_json,state,created_at) VALUES (?,?,?,?,?,? ,\'pending\',?)',
            (message_id, base, user, operation, message['type'], encoded, now))
        return message_id

    @classmethod
    def _queue_request_message(cls, connection, request, base, order, *, now):
        revision = request['session_id']
        message_id = cls._entity_message_id('request', base, request['operation_id'], revision)
        connection.execute(
            'UPDATE entity_outbox SET state=\'superseded\' WHERE operation_id=? '
            'AND message_type=\'request\' AND state=\'pending\' AND message_id<>?',
            (request['operation_id'], message_id))
        message = {
            'messageId':message_id, 'type':'request',
            'operationId':request['operation_id'], 'baseId':base, 'orderId':order,
            'initiatorId':request['initiator_id'], 'workplaceId':request['workplace_id'],
            'sessionId':request['session_id'], 'payload':json.loads(request['payload_json']),
            'createdAt':request['created_at'],
        }
        return cls._enqueue_entity_message(connection, base=base,
                                           user=request['initiator_id'],
                                           operation=request['operation_id'],
                                           message=message, now=now)

    def enqueue_task_request(self, base, order, operation, initiator, workplace, payload,
                             *, session_id='unbound', now=None):
        """Persist one immutable, employee- and workstation-scoped task request."""
        for value, name in ((base, 'base ID'), (order, 'order ID'),
                            (operation, 'operation ID'), (initiator, 'initiator ID'),
                            (workplace, 'workplace ID')):
            self._identifier(value, name)
        if session_id != 'unbound':
            self._identifier(session_id, '1C session ID')
        if not isinstance(payload, dict):
            raise Conflict('Task payload must be an object')
        try:
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                 separators=(',', ':'), allow_nan=False)
            if len(encoded.encode('utf-8')) > self._MAX_REQUEST_BYTES:
                raise ValueError
        except (TypeError, ValueError, UnicodeError):
            raise Conflict('Invalid task payload') from None
        timestamp = self._timestamp(now)
        correlation_tag = 'КА-' + operation
        with self._transaction() as connection:
            current = connection.execute('SELECT * FROM operations WHERE operation_id=?',
                                         (operation,)).fetchone()
            if current is None:
                connection.execute('INSERT INTO operations VALUES (?,?,?,\'pending\',NULL)',
                                   (operation, base, order))
            else:
                self._context(current, base, order)
            existing = connection.execute('SELECT * FROM task_requests WHERE operation_id=?',
                                          (operation,)).fetchone()
            if existing is not None:
                matches = (existing['initiator_id'] == initiator
                           and existing['workplace_id'] == workplace
                           and existing['correlation_tag'] == correlation_tag
                           and existing['payload_json'] == encoded)
                if not matches:
                    raise Conflict('Operation request cannot be changed')
                if (existing['delivery_state'] == 'queued'
                        and existing['session_id'] != session_id):
                    connection.execute('UPDATE task_requests SET session_id=?,updated_at=? '
                                       'WHERE operation_id=? AND delivery_state=\'queued\'',
                                       (session_id, timestamp, operation))
                    existing = connection.execute(
                        'SELECT * FROM task_requests WHERE operation_id=?',
                        (operation,)).fetchone()
                if existing['delivery_state'] == 'queued':
                    self._queue_request_message(connection, existing, base, order, now=timestamp)
                return self._task_request(existing)
            operation_row = connection.execute('SELECT state FROM operations WHERE operation_id=?',
                                               (operation,)).fetchone()
            if operation_row['state'] != 'pending':
                raise Conflict('Resolved operation cannot be enqueued')
            connection.execute(
                'INSERT INTO task_requests(operation_id,initiator_id,workplace_id,'
                'session_id,correlation_tag,payload_json,delivery_state,created_at,updated_at) '
                'VALUES (?,?,?,?,?,?,\'queued\',?,?)',
                (operation, initiator, workplace, session_id, correlation_tag, encoded,
                 timestamp, timestamp))
            row = connection.execute('SELECT * FROM task_requests WHERE operation_id=?',
                                     (operation,)).fetchone()
            self._queue_request_message(connection, row, base, order, now=timestamp)
            return self._task_request(row)

    @staticmethod
    def _timestamp(value):
        if value is None:
            import time
            return time.time()
        if type(value) not in (int, float) or not math.isfinite(value):
            raise Conflict('Invalid timestamp')
        return float(value)

    def task_request(self, base, operation):
        self._identifier(base, 'base ID')
        self._identifier(operation, 'operation ID')
        with self._transaction() as connection:
            row = connection.execute(
                'SELECT r.* FROM task_requests r JOIN operations o USING(operation_id) '
                'WHERE o.base_id=? AND r.operation_id=?', (base, operation)).fetchone()
            if row is None:
                raise Conflict('Unknown task request')
            return self._task_request(row)

    def request_by_correlation_tag(self, tag):
        if not isinstance(tag, str) or not tag.startswith('КА-'):
            raise Conflict('Invalid task correlation tag')
        with self._transaction() as connection:
            row = connection.execute(
                'SELECT r.*,o.base_id,o.order_id FROM task_requests r '
                'JOIN operations o USING(operation_id) WHERE r.correlation_tag=?',
                (tag,)).fetchone()
            if row is None:
                return None
            result = self._task_request(row)
            result['baseId'] = row['base_id']
            result['orderId'] = row['order_id']
            return result

    def claim_task_request(self, base, initiator, workplace, worker, *, now=None,
                           lease_seconds=30):
        """Claim only the oldest queued request for the exact employee/workstation."""
        for value, name in ((base, 'base ID'), (initiator, 'initiator ID'),
                            (workplace, 'workplace ID'), (worker, 'worker ID')):
            self._identifier(value, name)
        timestamp = self._timestamp(now)
        if (type(lease_seconds) not in (int, float) or not math.isfinite(lease_seconds)
                or not 1 <= lease_seconds <= 300):
            raise Conflict('Invalid lease duration')
        with self._transaction() as connection:
            expired = connection.execute(
                'SELECT r.operation_id FROM task_requests r JOIN operations o USING(operation_id) '
                'WHERE o.base_id=? AND r.initiator_id=? AND r.workplace_id=? '
                'AND r.delivery_state IN (\'claimed\',\'opening\',\'opened\') '
                'AND r.lease_until<=?',
                (base, initiator, workplace, timestamp)).fetchall()
            for item in expired:
                connection.execute(
                    'UPDATE task_requests SET delivery_state=\'unknown\',lease_owner=NULL,'
                    'lease_until=NULL,updated_at=? WHERE operation_id=?',
                    (timestamp, item['operation_id']))
            active = connection.execute(
                'SELECT r.* FROM task_requests r JOIN operations o USING(operation_id) '
                'WHERE o.base_id=? AND r.initiator_id=? AND r.workplace_id=? '
                'AND r.delivery_state IN (\'claimed\',\'opening\',\'opened\') '
                'ORDER BY r.created_at,r.operation_id LIMIT 1',
                (base, initiator, workplace)).fetchone()
            if active is not None:
                if (active['delivery_state'] == 'claimed' and active['lease_owner'] == worker
                        and active['lease_until'] > timestamp):
                    return self._task_request(active)
                return None
            row = connection.execute(
                'SELECT r.* FROM task_requests r JOIN operations o USING(operation_id) '
                'WHERE o.base_id=? AND r.initiator_id=? AND r.workplace_id=? '
                'AND r.delivery_state=\'queued\' ORDER BY r.created_at,r.operation_id LIMIT 1',
                (base, initiator, workplace)).fetchone()
            if row is None:
                return None
            connection.execute(
                'UPDATE task_requests SET delivery_state=\'claimed\',lease_owner=?,lease_until=?,'
                'updated_at=? WHERE operation_id=? AND delivery_state=\'queued\'',
                (worker, timestamp + lease_seconds, timestamp, row['operation_id']))
            claimed = connection.execute('SELECT * FROM task_requests WHERE operation_id=?',
                                         (row['operation_id'],)).fetchone()
            return self._task_request(claimed)

    def _recover_expired_claims(self, connection, base, initiator, workplace, timestamp):
        expired = connection.execute(
            'SELECT r.operation_id,r.delivery_state FROM task_requests r JOIN operations o USING(operation_id) '
            'WHERE o.base_id=? AND r.initiator_id=? AND r.workplace_id=? '
            'AND r.delivery_state IN (\'claimed\',\'opening\',\'opened\') '
            'AND r.lease_until<=?', (base, initiator, workplace, timestamp)).fetchall()
        for item in expired:
            # Only a persisted open confirmation makes an opening possible.
            # A claim without that confirmation can safely be authorized again.
            unopened = (item['delivery_state'] == 'claimed' and connection.execute(
                "SELECT 1 FROM entity_outbox WHERE operation_id=? AND message_type='open'",
                (item['operation_id'],)).fetchone() is None)
            state = 'queued' if unopened else 'unknown'
            connection.execute(
                'UPDATE task_requests SET delivery_state=?,lease_owner=NULL,'
                'lease_until=NULL,updated_at=? WHERE operation_id=?',
                (state,timestamp, item['operation_id']))
            if unopened:
                connection.execute('DELETE FROM order_checks WHERE operation_id=?',
                                   (item['operation_id'],))
                connection.execute("UPDATE entity_outbox SET state='superseded' "
                    "WHERE operation_id=? AND message_type='grant'",(item['operation_id'],))

    def heartbeat_1c_session(self, base, initiator, workplace, session, *, now=None,
                             ttl_seconds=30):
        """Record the active 1C session and rebind only requests still waiting to open."""
        for value, name in ((base, 'base ID'), (initiator, 'initiator ID'),
                            (workplace, 'workplace ID'), (session, '1C session ID')):
            self._identifier(value, name)
        timestamp = self._timestamp(now)
        if type(ttl_seconds) not in (int, float) or not math.isfinite(ttl_seconds) \
                or not 10 <= ttl_seconds <= 300:
            raise Conflict('Invalid 1C session lease')
        with self._transaction() as connection:
            owner = connection.execute('SELECT session_id,expires_at FROM active_1c_sessions '
                'WHERE base_id=? AND initiator_id=? AND workplace_id=?',
                (base,initiator,workplace)).fetchone()
            if owner is not None and owner['session_id'] != session and owner['expires_at'] > timestamp:
                raise Conflict('Workplace already belongs to a live 1C session')
            connection.execute(
                'INSERT INTO active_1c_sessions(base_id,initiator_id,workplace_id,'
                'session_id,last_seen,expires_at) VALUES (?,?,?,?,?,?) '
                'ON CONFLICT(base_id,initiator_id,workplace_id) DO UPDATE SET '
                'session_id=excluded.session_id,last_seen=excluded.last_seen,'
                'expires_at=excluded.expires_at',
                (base, initiator, workplace, session, timestamp, timestamp + ttl_seconds))
            self._recover_expired_claims(connection, base, initiator, workplace, timestamp)
            waiting = connection.execute(
                'SELECT r.*,o.base_id,o.order_id FROM task_requests r '
                'JOIN operations o USING(operation_id) WHERE o.base_id=? '
                'AND r.initiator_id=? AND r.workplace_id=? AND r.delivery_state=\'queued\' '
                'AND r.session_id<>?', (base, initiator, workplace, session)).fetchall()
            for request in waiting:
                connection.execute('UPDATE task_requests SET session_id=?,updated_at=? '
                                   'WHERE operation_id=? AND delivery_state=\'queued\'',
                                   (session, timestamp, request['operation_id']))
                updated = connection.execute('SELECT * FROM task_requests WHERE operation_id=?',
                                             (request['operation_id'],)).fetchone()
                self._queue_request_message(connection, updated, base,
                                            request['order_id'], now=timestamp)
            return {'baseId':base, 'initiatorId':initiator, 'workplaceId':workplace,
                    'sessionId':session, 'lastSeen':timestamp,
                    'expiresAt':timestamp + ttl_seconds}

    def claim_active_task_request(self, base, initiator, workplace, session, instance,
                                  *, now=None, lease_seconds=30, operation=None):
        """Atomically require the live 1C session and claim its request for one worker frame."""
        for value, name in ((base, 'base ID'), (initiator, 'initiator ID'),
                            (workplace, 'workplace ID'), (session, '1C session ID'),
                            (instance, 'worker instance ID')):
            self._identifier(value, name)
        timestamp = self._timestamp(now)
        if operation is not None:
            self._identifier(operation, 'operation ID')
        if type(lease_seconds) not in (int, float) or not math.isfinite(lease_seconds) \
                or not 1 <= lease_seconds <= 300:
            raise Conflict('Invalid lease duration')
        with self._transaction() as connection:
            current = connection.execute(
                'SELECT session_id,expires_at FROM active_1c_sessions WHERE base_id=? '
                'AND initiator_id=? AND workplace_id=?',
                (base, initiator, workplace)).fetchone()
            if (current is None or current['session_id'] != session
                    or current['expires_at'] <= timestamp):
                return None
            self._recover_expired_claims(connection, base, initiator, workplace, timestamp)
            active = connection.execute(
                'SELECT r.* FROM task_requests r JOIN operations o USING(operation_id) '
                'WHERE o.base_id=? AND r.initiator_id=? AND r.workplace_id=? '
                'AND r.session_id=? AND r.delivery_state IN (\'claimed\',\'opening\',\'opened\') '
                'ORDER BY r.created_at,r.operation_id LIMIT 1',
                (base, initiator, workplace, session)).fetchone()
            if active is not None:
                if (active['delivery_state'] == 'claimed'
                        and active['lease_owner'] == instance
                        and active['lease_until'] > timestamp
                        and (operation is None or active['operation_id'] == operation)):
                    return self._task_request(active)
                return None
            row = connection.execute(
                'SELECT r.* FROM task_requests r JOIN operations o USING(operation_id) '
                'WHERE o.base_id=? AND r.initiator_id=? AND r.workplace_id=? '
                'AND r.session_id=? AND r.delivery_state=\'queued\' '
                'ORDER BY r.created_at,r.operation_id LIMIT 1',
                (base, initiator, workplace, session)).fetchone()
            if row is None or (operation is not None and row['operation_id'] != operation):
                return None
            connection.execute(
                'UPDATE task_requests SET delivery_state=\'claimed\',lease_owner=?,lease_until=?,'
                'updated_at=? WHERE operation_id=? AND delivery_state=\'queued\'',
                (instance, timestamp + lease_seconds, timestamp, row['operation_id']))
            claimed = connection.execute('SELECT * FROM task_requests WHERE operation_id=?',
                                         (row['operation_id'],)).fetchone()
            return self._task_request(claimed)

    def queue_entity_message(self, base, user, operation, message, *, now=None):
        for value, name in ((base, 'base ID'), (user, 'Bitrix24 user ID'),
                            (operation, 'operation ID')):
            self._identifier(value, name)
        timestamp = self._timestamp(now)
        with self._transaction() as connection:
            self._row(connection, operation)
            return self._enqueue_entity_message(connection, base=base, user=user,
                                                operation=operation, message=message,
                                                now=timestamp)

    def confirm_task_opening(self, base, operation, instance, permission, *, now=None):
        """Commit opening and its addressed confirmation together, after a live-session check."""
        timestamp = self._timestamp(now)
        with self._transaction() as connection:
            request = connection.execute('SELECT r.*,o.base_id,o.order_id,o.task_id FROM task_requests r '
                'JOIN operations o USING(operation_id) WHERE operation_id=?', (operation,)).fetchone()
            if (request is None or request['base_id'] != base or request['task_id'] is not None
                    or request['lease_owner'] != instance or request['lease_until'] is None
                    or request['lease_until'] <= timestamp
                    or request['delivery_state'] not in ('claimed','opening')
                    or connection.execute('SELECT 1 FROM operation_conflicts WHERE operation_id=?',
                                          (operation,)).fetchone() is not None):
                raise Conflict('Opening is no longer authorized')
            session = connection.execute('SELECT * FROM active_1c_sessions WHERE base_id=? '
                'AND initiator_id=? AND workplace_id=?',
                (base,request['initiator_id'],request['workplace_id'])).fetchone()
            if (session is None or session['session_id'] != request['session_id']
                    or session['expires_at'] <= timestamp):
                raise Conflict('1C session is no longer active')
            check = connection.execute('SELECT * FROM order_checks WHERE operation_id=?',
                                       (operation,)).fetchone()
            if (check is None or check['status'] != 'valid' or check['expires_at'] <= timestamp
                    or check['session_id'] != request['session_id'] or check['instance_id'] != instance):
                raise Conflict('Saved order has no fresh validation')
            grants = connection.execute('SELECT message_json FROM entity_outbox WHERE operation_id=? '
                'AND message_type=\'grant\'', (operation,)).fetchall()
            grants = [json.loads(row['message_json']) for row in grants]
            grant = next((row for row in grants if row.get('permissionId') == permission
                          and row.get('instanceId') == instance
                          and row.get('sessionId') == request['session_id']), None)
            if grant is None or grant.get('expiresAt',0) <= timestamp:
                raise Conflict('Opening permission is missing or expired')
            confirmation = dict(grant, type='open',
                messageId=self._entity_message_id('open',base,operation,permission),
                expiresAt=min(grant['expiresAt'],session['expires_at'],check['expires_at']))
            existing = connection.execute('SELECT message_json FROM entity_outbox WHERE message_id=?',
                                          (confirmation['messageId'],)).fetchone()
            if existing is not None:
                confirmation = json.loads(existing['message_json'])
                if confirmation['expiresAt'] <= timestamp:
                    raise Conflict('Opening confirmation expired')
            connection.execute('UPDATE task_requests SET delivery_state=\'opening\',updated_at=? '
                               'WHERE operation_id=?', (timestamp,operation))
            self._enqueue_entity_message(connection,base=base,user=request['initiator_id'],
                operation=operation,message=confirmation,now=timestamp)
            return confirmation

    def require_order_check(self, base, operation, instance, *, now=None):
        """Persist a challenge for 1C; heartbeat alone cannot authorize a saved order."""
        timestamp = self._timestamp(now)
        with self._transaction() as connection:
            row = connection.execute('SELECT r.*,o.base_id FROM task_requests r '
                'JOIN operations o USING(operation_id) WHERE operation_id=?',(operation,)).fetchone()
            if (row is None or row['base_id'] != base or row['lease_owner'] != instance
                    or row['delivery_state'] != 'claimed' or row['lease_until'] <= timestamp):
                raise Conflict('Order check does not own a live claim')
            check = connection.execute('SELECT * FROM order_checks WHERE operation_id=?',
                                       (operation,)).fetchone()
            replace = check is not None and (
                (check['status'] == 'rejected' and check['validated_at'] + 10 <= timestamp)
                or (check['status'] == 'valid' and check['expires_at'] <= timestamp))
            if replace:
                if connection.execute("SELECT 1 FROM entity_outbox WHERE operation_id=? AND message_type='open'",
                                      (operation,)).fetchone() is not None:
                    raise Conflict('A possible opening must be reconciled before reauthorization')
                connection.execute('DELETE FROM order_checks WHERE operation_id=?',(operation,))
                check = None
            if check is None:
                check_id = self._entity_message_id('check',base,operation,
                    row['session_id']+'_'+instance+'_'+str(timestamp))
                connection.execute('INSERT INTO order_checks(operation_id,check_id,session_id,instance_id,'
                    'status,created_at) VALUES (?,?,?,?,\'pending\',?)',
                    (operation,check_id,row['session_id'],instance,timestamp))
                return None
            if check['session_id'] != row['session_id'] or check['instance_id'] != instance:
                raise Conflict('Order check belongs to another session or worker')
            if check['status'] == 'pending':
                return None
            if check['status'] != 'valid' or check['expires_at'] <= timestamp:
                raise Conflict('Saved order validation rejected or expired')
            return {'payload':json.loads(check['payload_json']), 'expiresAt':check['expires_at'],
                    'checkId':check['check_id']}

    def order_check_id(self, base, operation, instance):
        with self._transaction() as connection:
            row = connection.execute('SELECT c.check_id FROM order_checks c '
                'JOIN operations o USING(operation_id) WHERE o.base_id=? AND c.operation_id=? '
                'AND c.instance_id=?',(base,operation,instance)).fetchone()
            if row is None:
                raise Conflict('Opening has no saved order check')
            return row['check_id']

    def pending_order_checks(self, base, initiator, workplace, session, *, now=None):
        timestamp = self._timestamp(now)
        with self._transaction() as connection:
            rows = connection.execute('SELECT c.check_id,c.operation_id,o.order_id,c.created_at '
                'FROM order_checks c JOIN task_requests r USING(operation_id) '
                'JOIN operations o USING(operation_id) JOIN active_1c_sessions s '
                'ON s.base_id=o.base_id AND s.initiator_id=r.initiator_id AND s.workplace_id=r.workplace_id '
                'WHERE o.base_id=? AND r.initiator_id=? AND r.workplace_id=? AND r.session_id=? '
                'AND s.session_id=? AND s.expires_at>? AND c.status=\'pending\' '
                'AND c.session_id=r.session_id AND c.instance_id=r.lease_owner '
                'AND r.delivery_state=\'claimed\' AND r.lease_until>? '
                'ORDER BY c.created_at,c.operation_id',
                (base,initiator,workplace,session,session,timestamp,timestamp)).fetchall()
            return [{'checkId':row['check_id'],'operationId':row['operation_id'],
                     'orderId':row['order_id'],'baseId':base,'sessionId':session} for row in rows]

    def validate_order_check(self, base, order, operation, initiator, workplace, session,
                             check_id, eligible, payload, *, now=None):
        timestamp = self._timestamp(now)
        if type(eligible) is not bool or not isinstance(payload, dict):
            raise Conflict('Invalid saved-order validation')
        try:
            encoded = json.dumps(payload,ensure_ascii=False,sort_keys=True,allow_nan=False)
            if len(encoded.encode('utf-8')) > self._MAX_REQUEST_BYTES:
                raise ValueError
        except (ValueError,TypeError,UnicodeError):
            raise Conflict('Invalid saved-order snapshot') from None
        with self._transaction() as connection:
            row = connection.execute('SELECT r.*,o.base_id,o.order_id,o.task_id FROM task_requests r '
                'JOIN operations o USING(operation_id) WHERE operation_id=?',(operation,)).fetchone()
            check = connection.execute('SELECT * FROM order_checks WHERE operation_id=?',(operation,)).fetchone()
            current = connection.execute('SELECT * FROM active_1c_sessions WHERE base_id=? '
                'AND initiator_id=? AND workplace_id=?',(base,initiator,workplace)).fetchone()
            if (row is None or check is None or row['base_id'] != base or row['order_id'] != order
                    or row['initiator_id'] != initiator or row['workplace_id'] != workplace
                    or row['session_id'] != session or check['session_id'] != session
                    or check['check_id'] != check_id or check['instance_id'] != row['lease_owner']
                    or row['task_id'] is not None or row['delivery_state'] != 'claimed'
                    or row['lease_until'] <= timestamp or current is None
                    or current['session_id'] != session or current['expires_at'] <= timestamp):
                raise Conflict('Saved-order validation context is stale or mismatched')
            status = 'valid' if eligible else 'rejected'
            if check['status'] != 'pending':
                if check['status'] != status or check['payload_json'] != encoded:
                    raise Conflict('Saved-order validation already has a different result')
                return {'status':status,'expiresAt':check['expires_at']}
            expires_at = min(timestamp+30,row['lease_until'],current['expires_at'])
            connection.execute('UPDATE order_checks SET status=?,payload_json=?,validated_at=?,expires_at=? '
                'WHERE operation_id=?',(status,encoded,timestamp,expires_at,operation))
            return {'status':status,'expiresAt':expires_at}

    def pending_entity_messages(self, *, user=None, limit=100):
        if user is not None:
            self._identifier(user, 'Bitrix24 user ID')
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise Conflict('Invalid entity message limit')
        with self._transaction() as connection:
            if user is None:
                rows = connection.execute('SELECT * FROM entity_outbox WHERE state=\'pending\' '
                                          'ORDER BY created_at,message_id LIMIT ?',
                                          (limit,)).fetchall()
            else:
                rows = connection.execute('SELECT * FROM entity_outbox WHERE state=\'pending\' '
                                          'AND user_id=? ORDER BY created_at,message_id LIMIT ?',
                                          (user, limit)).fetchall()
            return [dict(messageId=row['message_id'], baseId=row['base_id'],
                         userId=row['user_id'], operationId=row['operation_id'],
                         type=row['message_type'], message=json.loads(row['message_json']),
                         state=row['state'], remoteItemId=row['remote_item_id'],
                         createdAt=row['created_at']) for row in rows]

    def mark_entity_message_published(self, message_id, remote_item_id, *, now=None):
        self._identifier(message_id, 'entity message ID')
        if type(remote_item_id) is not int or remote_item_id <= 0:
            raise Conflict('Invalid remote entity item ID')
        timestamp = self._timestamp(now)
        with self._transaction() as connection:
            row = connection.execute('SELECT * FROM entity_outbox WHERE message_id=?',
                                     (message_id,)).fetchone()
            if row is None:
                raise Conflict('Unknown entity message')
            if row['state'] == 'published':
                if row['remote_item_id'] != remote_item_id:
                    raise Conflict('Entity message was published with another remote ID')
                return message_id
            connection.execute('UPDATE entity_outbox SET state=\'published\',remote_item_id=?,'
                               'published_at=? WHERE message_id=? AND state=\'pending\'',
                               (remote_item_id, timestamp, message_id))
            return message_id

    def receive_entity_message(self, user, message_id, message, *, now=None):
        self._identifier(user, 'Bitrix24 user ID')
        self._identifier(message_id, 'entity message ID')
        if not isinstance(message, dict) or message.get('messageId') != message_id:
            raise Conflict('Invalid incoming entity message')
        try:
            encoded = json.dumps(message, ensure_ascii=False, sort_keys=True,
                                 separators=(',', ':'), allow_nan=False)
            if len(encoded.encode('utf-8')) > self._MAX_REQUEST_BYTES:
                raise ValueError
        except (TypeError, ValueError, UnicodeError):
            raise Conflict('Invalid incoming entity message') from None
        timestamp = self._timestamp(now)
        with self._transaction() as connection:
            row = connection.execute('SELECT * FROM entity_inbox WHERE user_id=? AND message_id=?',
                                     (user, message_id)).fetchone()
            if row is None:
                connection.execute('INSERT INTO entity_inbox(user_id,message_id,message_json,'
                                   'received_at) VALUES (?,?,?,?)',
                                   (user, message_id, encoded, timestamp))
                return {'duplicate':False, 'processed':False, 'outcome':None}
            if row['message_json'] != encoded:
                raise Conflict('Entity message ID was reused with changed contents')
            return {'duplicate':True, 'processed':row['processed_at'] is not None,
                    'outcome':json.loads(row['outcome_json']) if row['outcome_json'] else None}

    def mark_entity_message_processed(self, user, message_id, outcome, *, now=None):
        self._identifier(user, 'Bitrix24 user ID')
        self._identifier(message_id, 'entity message ID')
        if not isinstance(outcome, dict):
            raise Conflict('Invalid entity processing outcome')
        try:
            encoded = json.dumps(outcome, ensure_ascii=False, sort_keys=True,
                                 separators=(',', ':'), allow_nan=False)
        except (TypeError, ValueError, UnicodeError):
            raise Conflict('Invalid entity processing outcome') from None
        timestamp = self._timestamp(now)
        with self._transaction() as connection:
            row = connection.execute('SELECT * FROM entity_inbox WHERE user_id=? AND message_id=?',
                                     (user, message_id)).fetchone()
            if row is None:
                raise Conflict('Unknown incoming entity message')
            if row['processed_at'] is not None:
                if row['outcome_json'] != encoded:
                    raise Conflict('Entity message already has another outcome')
                return json.loads(row['outcome_json'])
            connection.execute('UPDATE entity_inbox SET outcome_json=?,processed_at=? '
                               'WHERE user_id=? AND message_id=? AND processed_at IS NULL',
                               (encoded, timestamp, user, message_id))
            return outcome

    def claim_next_task_request(self, base, initiator, worker, *, now=None,
                                lease_seconds=30):
        """Claim one request for a Bitrix user, regardless of the originating 1C workstation."""
        for value, name in ((base, 'base ID'), (initiator, 'initiator ID'),
                            (worker, 'worker ID')):
            self._identifier(value, name)
        timestamp = self._timestamp(now)
        if (type(lease_seconds) not in (int, float) or not math.isfinite(lease_seconds)
                or not 1 <= lease_seconds <= 300):
            raise Conflict('Invalid lease duration')
        with self._transaction() as connection:
            expired = connection.execute(
                'SELECT r.operation_id FROM task_requests r JOIN operations o USING(operation_id) '
                'WHERE o.base_id=? AND r.initiator_id=? '
                'AND r.delivery_state IN (\'claimed\',\'opening\',\'opened\') '
                'AND r.lease_until<=?', (base, initiator, timestamp)).fetchall()
            for item in expired:
                connection.execute(
                    'UPDATE task_requests SET delivery_state=\'unknown\',lease_owner=NULL,'
                    'lease_until=NULL,updated_at=? WHERE operation_id=?',
                    (timestamp, item['operation_id']))
            active = connection.execute(
                'SELECT r.* FROM task_requests r JOIN operations o USING(operation_id) '
                'WHERE o.base_id=? AND r.initiator_id=? '
                'AND r.delivery_state IN (\'claimed\',\'opening\',\'opened\') '
                'ORDER BY r.created_at,r.operation_id LIMIT 1', (base, initiator)).fetchone()
            if active is not None:
                if (active['delivery_state'] == 'claimed' and active['lease_owner'] == worker
                        and active['lease_until'] > timestamp):
                    return self._task_request(active)
                return None
            row = connection.execute(
                'SELECT r.* FROM task_requests r JOIN operations o USING(operation_id) '
                'WHERE o.base_id=? AND r.initiator_id=? AND r.delivery_state=\'queued\' '
                'ORDER BY r.created_at,r.operation_id LIMIT 1', (base, initiator)).fetchone()
            if row is None:
                return None
            connection.execute(
                'UPDATE task_requests SET delivery_state=\'claimed\',lease_owner=?,lease_until=?,'
                'updated_at=? WHERE operation_id=? AND delivery_state=\'queued\'',
                (worker, timestamp + lease_seconds, timestamp, row['operation_id']))
            claimed = connection.execute('SELECT * FROM task_requests WHERE operation_id=?',
                                         (row['operation_id'],)).fetchone()
            return self._task_request(claimed)

    def _mark_request(self, operation, worker, state, *, now=None):
        self._identifier(operation, 'operation ID')
        self._identifier(worker, 'worker ID')
        timestamp = self._timestamp(now)
        expired = False
        result = None
        with self._transaction() as connection:
            row = connection.execute('SELECT * FROM task_requests WHERE operation_id=?',
                                     (operation,)).fetchone()
            if row is None:
                raise Conflict('Task request lease does not match')
            if state == 'unknown' and row['delivery_state'] in ('created','acknowledged'):
                return self._task_request(row)
            if row['lease_owner'] != worker:
                raise Conflict('Task request lease does not match')
            if (state == 'unknown' and row['delivery_state'] == 'unknown'
                    and row['lease_owner'] == worker):
                return self._task_request(row)
            if row['lease_until'] is None or row['lease_until'] <= timestamp:
                connection.execute(
                    'UPDATE task_requests SET delivery_state=\'unknown\',lease_owner=NULL,'
                    'lease_until=NULL,updated_at=? WHERE operation_id=?',
                    (timestamp, operation))
                expired = True
            else:
                allowed = {
                    'opening': ('claimed',),
                    'opened': ('opening',),
                    'unknown': ('opening', 'opened'),
                }
                if (row['delivery_state'] == state and state in ('opening','opened')):
                    return self._task_request(row)
                if row['delivery_state'] not in allowed[state]:
                    raise Conflict('Invalid task request transition')
                owner = worker
                lease = None if state == 'unknown' else row['lease_until']
                connection.execute(
                    'UPDATE task_requests SET delivery_state=?,lease_owner=?,lease_until=?,'
                    'updated_at=? WHERE operation_id=?',
                    (state, owner, lease, timestamp, operation))
                updated = connection.execute('SELECT * FROM task_requests WHERE operation_id=?',
                                             (operation,)).fetchone()
                result = self._task_request(updated)
        if expired:
            raise Conflict('Task request lease expired')
        return result

    def mark_task_request_opening(self, operation, worker, *, now=None):
        return self._mark_request(operation, worker, 'opening', now=now)

    def mark_task_request_opened(self, operation, worker, *, now=None):
        return self._mark_request(operation, worker, 'opened', now=now)

    def mark_task_request_closed(self, operation, worker, *, now=None):
        return self._mark_request(operation, worker, 'unknown', now=now)

    def renew_task_request(self, operation, worker, *, now=None, lease_seconds=30):
        self._identifier(operation, 'operation ID')
        self._identifier(worker, 'worker ID')
        timestamp = self._timestamp(now)
        if (type(lease_seconds) not in (int, float) or not math.isfinite(lease_seconds)
                or not 1 <= lease_seconds <= 300):
            raise Conflict('Invalid lease duration')
        expired = False
        result = None
        with self._transaction() as connection:
            row = connection.execute('SELECT * FROM task_requests WHERE operation_id=?',
                                     (operation,)).fetchone()
            if (row is None or row['lease_owner'] != worker
                    or row['delivery_state'] not in ('claimed','opening','opened')):
                raise Conflict('Task request lease does not match')
            if row['lease_until'] is None or row['lease_until'] <= timestamp:
                connection.execute(
                    'UPDATE task_requests SET delivery_state=\'unknown\',lease_owner=NULL,'
                    'lease_until=NULL,updated_at=? WHERE operation_id=?',
                    (timestamp, operation))
                expired = True
            else:
                connection.execute('UPDATE task_requests SET lease_until=?,updated_at=? '
                                   'WHERE operation_id=?',
                                   (timestamp + lease_seconds, timestamp, operation))
                updated = connection.execute('SELECT * FROM task_requests WHERE operation_id=?',
                                             (operation,)).fetchone()
                result = self._task_request(updated)
        if expired:
            raise Conflict('Task request lease expired')
        return result

    def accept_verified_task(self, base, order, operation, task, observed_tags):
        """Accept a task only after the Bitrix adapter read it and verified its exact tag."""
        self._task(task)
        if not isinstance(observed_tags, (list, tuple, set)) or any(
                not isinstance(tag, str) for tag in observed_tags):
            raise Conflict('Invalid task tags')
        with self._transaction() as connection:
            row = self._row(connection, operation)
            self._context(row, base, order)
            request = connection.execute('SELECT * FROM task_requests WHERE operation_id=?',
                                         (operation,)).fetchone()
            if request is None or request['correlation_tag'] not in observed_tags:
                raise Conflict('Task correlation tag does not match')
            if row['state'] == 'cancelled':
                raise Conflict('Cancelled operation received a result')
            if row['task_id'] is not None and row['task_id'] != task:
                raise Conflict('Operation already has a different task')
            if row['task_id'] is None:
                connection.execute('UPDATE operations SET task_id=?,state=\'created\' '
                                   'WHERE operation_id=?', (task, operation))
            if request['delivery_state'] != 'acknowledged':
                connection.execute('UPDATE task_requests SET delivery_state=\'created\','
                                   'lease_owner=NULL,lease_until=NULL,updated_at=? '
                                   'WHERE operation_id=?', (self._timestamp(None), operation))
            return self._public(self._row(connection, operation))

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
            result = self._public(self._row(connection, operation))
            conflict = connection.execute('SELECT conflict_code FROM operation_conflicts '
                                           'WHERE operation_id=?',(operation,)).fetchone()
            result['conflict'] = conflict['conflict_code'] if conflict else None
            return result

    def mark_operation_conflict(self, operation, code, *, now=None):
        self._identifier(operation, 'operation ID')
        if not isinstance(code, str) or not re.fullmatch(r'[a-z_]{1,48}', code):
            raise Conflict('Invalid operation conflict code')
        timestamp = self._timestamp(now)
        with self._transaction() as connection:
            self._row(connection, operation)
            existing = connection.execute('SELECT * FROM operation_conflicts '
                                           'WHERE operation_id=?',(operation,)).fetchone()
            if existing is not None:
                if existing['conflict_code'] != code:
                    raise Conflict('Operation already has a different conflict')
            else:
                connection.execute('INSERT INTO operation_conflicts VALUES (?,?,?)',
                                    (operation, code, timestamp))
            return {'operationId':operation,'conflict':code}

    def reconciliation_candidates(self, base):
        self._identifier(base, 'base ID')
        with self._transaction() as connection:
            rows = connection.execute(
                'SELECT o.operation_id,o.base_id,o.order_id,o.state,o.task_id,r.* '
                'FROM operations o JOIN task_requests r USING(operation_id) '
                'LEFT JOIN operation_conflicts c USING(operation_id) '
                'WHERE o.base_id=? AND o.state<>\'cancelled\' '
                'AND c.operation_id IS NULL ORDER BY r.created_at,o.operation_id',
                (base,)).fetchall()
            results = []
            for row in rows:
                item = self._task_request(row)
                item.update(baseId=row['base_id'],orderId=row['order_id'],state=row['state'])
                results.append(item)
            return results

    def unresolved_requests(self, base):
        return self.reconciliation_candidates(base)

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
            connection.execute('UPDATE task_requests SET delivery_state=\'acknowledged\','
                               'lease_owner=NULL,lease_until=NULL,updated_at=? '
                               'WHERE operation_id=?', (self._timestamp(None), operation))

    def cancel(self, operation, *, absence_confirmed=False):
        with self._transaction() as connection:
            row = self._row(connection, operation)
            if absence_confirmed is not True or row['task_id'] is not None:
                raise Conflict('Cancellation requires confirmed absence of task')
            connection.execute('UPDATE operations SET state=\'cancelled\' WHERE operation_id=?',
                               (operation,))
            connection.execute('UPDATE task_requests SET delivery_state=\'cancelled\','
                               'lease_owner=NULL,lease_until=NULL,updated_at=? '
                               'WHERE operation_id=?', (self._timestamp(None), operation))
