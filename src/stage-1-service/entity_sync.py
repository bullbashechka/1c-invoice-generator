"""Durable SQLite ↔ per-user Bitrix24 entity exchange for the stage-one demo."""

import hashlib
import json
import re
import threading
import time

from operation_store import Conflict
from bitrix_reconcile import BitrixAPIRejected, CorrelationUnavailable, ResultUnknown
from bitrix_entities import EntitySetupError


_IDENTIFIER = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
_WORKER_MESSAGES = {'claim','opening','opened','heartbeat','closed'}


class BitrixEntitySync:
    def __init__(self, store, entities, base_id, user_ids, *,
                 worker_handler_url=None, error_handler_url=None,
                 task_reader=None, clock=None, poll_seconds=5):
        if not isinstance(base_id, str) or not _IDENTIFIER.fullmatch(base_id):
            raise ValueError('A valid base identifier is required')
        if (not isinstance(user_ids, (list, tuple, set)) or not user_ids
                or any(not isinstance(value, str) or not re.fullmatch(r'[1-9][0-9]{0,9}', value)
                       for value in user_ids)):
            raise ValueError('Configured employee IDs are required')
        if (worker_handler_url is None) != (error_handler_url is None):
            raise ValueError('Both worker package URLs must be configured together')
        if type(poll_seconds) not in (int, float) or poll_seconds < 1:
            raise ValueError('A positive entity polling interval is required')
        self._store = store
        self._entities = entities
        self._base = base_id
        self._users = tuple(sorted(set(user_ids), key=int))
        self._worker_handler_url = worker_handler_url
        self._error_handler_url = error_handler_url
        self._task_reader = task_reader
        self._clock = time.time if clock is None else clock
        self._poll_seconds = float(poll_seconds)
        self._stop = threading.Event()
        self._thread = None

    @staticmethod
    def _property_values(item):
        if not isinstance(item, dict):
            raise Conflict('Invalid entity item')
        properties = item.get('PROPERTY_VALUES', item.get('PROPERTY_VALUE'))
        if isinstance(properties, list):
            result = {}
            for row in properties:
                if not isinstance(row, dict):
                    raise Conflict('Invalid entity properties')
                code = row.get('PROPERTY', row.get('CODE', row.get('NAME')))
                value = row.get('VALUE')
                if not isinstance(code, str) or code in result:
                    raise Conflict('Invalid entity properties')
                result[code] = value
            properties = result
        if not isinstance(properties, dict):
            raise Conflict('Entity item has no readable properties')
        return properties

    @classmethod
    def _decode_worker_message(cls, item):
        properties = cls._property_values(item)
        message_id = properties.get('MESSAGE_ID', item.get('NAME'))
        if not isinstance(message_id, str) or not _IDENTIFIER.fullmatch(message_id):
            raise Conflict('Entity message ID is invalid')
        raw_payload = properties.get('PAYLOAD_JSON', '{}')
        try:
            payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
        except (ValueError, TypeError):
            raise Conflict('Entity message payload is invalid') from None
        if not isinstance(payload, dict):
            raise Conflict('Entity message payload is invalid')
        message = {
            'messageId':message_id,
            'type':properties.get('MESSAGE_TYPE'),
            'operationId':properties.get('OPERATION_ID'),
            'baseId':properties.get('BASE_ID'),
            'orderId':properties.get('ORDER_ID'),
            'initiatorId':properties.get('INITIATOR_ID'),
            'workplaceId':properties.get('WORKPLACE_ID'),
            'sessionId':properties.get('SESSION_ID'),
            'instanceId':properties.get('INSTANCE_ID'),
            'permissionId':properties.get('PERMISSION_ID'),
            'taskId':properties.get('TASK_ID'),
            'payload':payload,
        }
        for key in ('operationId','baseId','orderId','initiatorId','workplaceId',
                    'sessionId','instanceId'):
            if not isinstance(message[key], str) or not _IDENTIFIER.fullmatch(message[key]):
                raise Conflict('Entity message context is invalid')
        if message['type'] not in _WORKER_MESSAGES:
            raise Conflict('Entity message type is invalid')
        return message

    @staticmethod
    def _stable_id(prefix, *parts):
        encoded = '\0'.join(parts).encode('utf-8')
        return prefix + '_' + hashlib.sha256(encoded).hexdigest()[:32]

    def _grant(self, request, claim, *, now):
        instance_id = claim['instanceId']
        grant_id = self._stable_id('permit', self._base, request['operationId'],
                                  request['sessionId'], instance_id, request['checkId'])
        grant = {
            'messageId':self._stable_id('grant', self._base, request['operationId'],
                                        request['sessionId'], instance_id, request['checkId']),
            'type':'grant', 'operationId':request['operationId'], 'baseId':self._base,
            'orderId':claim['orderId'], 'initiatorId':request['initiatorId'],
            'workplaceId':request['workplaceId'], 'sessionId':request['sessionId'],
            'instanceId':instance_id, 'permissionId':grant_id,
            'payload':request['payload'], 'createdAt':request['updatedAt'],
            'expiresAt':request['leaseUntil'],
        }
        self._store.queue_entity_message(self._base, claim['initiatorId'],
                                         request['operationId'], grant, now=now)

    def _process_worker_message(self, user, message, *, now):
        if (message['baseId'] != self._base or message['initiatorId'] != user):
            raise Conflict('Worker message identity does not match its employee channel')
        operation = self._store.get(message['operationId'])
        request = self._store.task_request(self._base, message['operationId'])
        if (operation['baseId'] != self._base or operation['orderId'] != message['orderId']
                or request['initiatorId'] != user
                or request['workplaceId'] != message['workplaceId']
                or request['sessionId'] != message['sessionId']):
            raise Conflict('Worker message does not match the saved request context')

        kind = message['type']
        if kind == 'claim':
            claimed = self._store.claim_active_task_request(
                self._base, user, message['workplaceId'], message['sessionId'],
                message['instanceId'], now=now, lease_seconds=30,
                operation=message['operationId'])
            if claimed is None:
                return {'accepted':True,'granted':False}
            validation = self._store.require_order_check(self._base,message['operationId'],
                                                         message['instanceId'],now=now)
            if validation is None:
                return {'accepted':True,'granted':False,'deferred':True}
            claimed['payload'] = validation['payload']
            claimed['checkId'] = validation['checkId']
            claimed['leaseUntil'] = min(claimed['leaseUntil'],validation['expiresAt'])
            self._grant(claimed, message, now=now)
            return {'accepted':True,'granted':True}

        if request['leaseOwner'] != message['instanceId']:
            raise Conflict('Worker instance does not own the opening lease')
        expected_permission = self._stable_id('permit', self._base, request['operationId'],
            request['sessionId'], message['instanceId'],
            self._store.order_check_id(self._base,request['operationId'],message['instanceId']))
        if message['permissionId'] != expected_permission:
            raise Conflict('Worker opening permission does not match')
        if kind == 'opening':
            self._store.confirm_task_opening(self._base,message['operationId'],
                message['instanceId'],message['permissionId'],now=now)
        elif kind == 'opened':
            self._store.mark_task_request_opened(message['operationId'],
                                                 message['instanceId'],now=now)
        elif kind == 'heartbeat':
            self._store.renew_task_request(message['operationId'],
                                           message['instanceId'],now=now,
                                           lease_seconds=30)
        elif kind == 'closed':
            self._store.mark_task_request_closed(message['operationId'],
                                                 message['instanceId'],now=now)
        return {'accepted':True,'granted':False}

    def _consume_responses(self, *, now):
        consumed = 0
        for user in self._users:
            for item in self._entities.read_messages(user,'incoming'):
                try:
                    message = self._decode_worker_message(item)
                    receipt = self._store.receive_entity_message(
                        user, message['messageId'], message, now=now)
                    if receipt['processed']:
                        continue
                    try:
                        outcome = self._process_worker_message(user,message,now=now)
                    except Conflict:
                        outcome = {'accepted':False,'granted':False}
                    if outcome.get('deferred'):
                        continue
                    self._store.mark_entity_message_processed(
                        user,message['messageId'],outcome,now=now)
                    consumed += 1
                except Conflict:
                    # Malformed or changed remote rows remain visible for diagnosis, but
                    # cannot authorize an opening or alter the SQLite lease.
                    continue
        return consumed

    def _publish(self, *, now):
        published = 0
        for row in self._store.pending_entity_messages(limit=1000):
            if row['baseId'] != self._base or row['userId'] not in self._users:
                continue
            try:
                remote_id = self._entities.add_message(row['userId'],'outgoing',row['message'])
                self._store.mark_entity_message_published(row['messageId'],remote_id,now=now)
                published += 1
            except Exception:
                # The durable SQLite outbox remains pending and is retried next cycle.
                continue
        return published

    def sync_once(self, *, now=None):
        timestamp = self._clock() if now is None else now
        consumed = self._consume_responses(now=timestamp)
        published = self._publish(now=timestamp)
        return {'responsesProcessed':consumed,'messagesPublished':published}

    def reconcile_once(self, *, now=None):
        """Search exact tags and re-read every candidate before accepting a task ID."""
        if self._task_reader is None:
            return {'accepted':0,'unknown':0,'conflicts':0}
        timestamp = self._clock() if now is None else now
        accepted = unknown = conflicts = 0
        for request in self._store.reconciliation_candidates(self._base):
            try:
                task_ids = self._task_reader.find_task_ids_by_correlation_tag(
                    request['correlationTag'])
            except (BitrixAPIRejected, ResultUnknown):
                unknown += 1
                continue
            if not task_ids:
                unknown += 1
                continue
            if len(task_ids) > 1:
                self._store.mark_operation_conflict(
                    request['operationId'],'duplicate_task_tag',now=timestamp)
                conflicts += 1
                continue
            try:
                self._task_reader.reconcile_task_add(task_ids[0],self._store)
                accepted += 1
            except (BitrixAPIRejected, ResultUnknown):
                unknown += 1
            except CorrelationUnavailable:
                self._store.mark_operation_conflict(
                    request['operationId'],'tag_or_link_conflict',now=timestamp)
                conflicts += 1
        return {'accepted':accepted,'unknown':unknown,'conflicts':conflicts}

    def _verify_oauth_owner(self, owner_user_id):
        owner_user_id = str(owner_user_id)
        if not re.fullmatch(r'[1-9][0-9]{0,9}', owner_user_id):
            raise EntitySetupError('A positive technical-owner Bitrix24 user ID is required')
        identity_reader = getattr(self._entities, 'current_user_id', None)
        if not callable(identity_reader):
            raise EntitySetupError('Bitrix24 OAuth identity cannot be verified')
        if identity_reader() != owner_user_id:
            raise EntitySetupError('Bitrix24 OAuth user does not match the configured technical owner')
        return owner_user_id

    def provision(self, owner_user_id):
        owner_user_id = self._verify_oauth_owner(owner_user_id)
        results = {}
        for user in self._users:
            channels = self._entities.provision_user(user,owner_user_id=owner_user_id)
            if self._worker_handler_url is not None:
                placement = self._entities.ensure_worker_placement(
                    user,self._worker_handler_url,self._error_handler_url)
            else:
                placement = 'not_checked'
            results[user] = {'channels':channels,'workerPlacement':placement}
        return results

    def verify_worker_placements(self, owner_user_id):
        self._verify_oauth_owner(owner_user_id)
        if self._worker_handler_url is None:
            raise ValueError('Worker package URLs are required for placement verification')
        results = {}
        for user in self._users:
            results[user] = self._entities.ensure_worker_placement(
                user,self._worker_handler_url,self._error_handler_url,
                register_if_missing=False)
        return results

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name='stage1-entity-sync',daemon=True)
        self._thread.start()

    def stop(self, timeout=5):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self):
        while not self._stop.is_set():
            try:
                self.sync_once()
            except Exception:
                # Keep the worker alive; exact operational errors are recorded by
                # the service health endpoint without request content or credentials.
                pass
            self._stop.wait(self._poll_seconds)
