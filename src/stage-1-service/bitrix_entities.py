"""Per-user Bitrix24 entity channels and personal background-worker registration."""

from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import json
import re


class EntitySetupError(Exception):
    """A sanitized entity/placement setup or REST contract failure."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


_USER_ID = re.compile(r'[1-9][0-9]{0,9}\Z')
_MESSAGE_ID = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
_OPERATION_ID = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
_MESSAGE_TYPES = {
    'request', 'claim', 'grant', 'claim_denied', 'opening', 'opened',
    'heartbeat', 'closed', 'result', 'acknowledged', 'open',
}
_PROPERTIES = (
    ('MESSAGE_ID','Message ID'), ('MESSAGE_TYPE','Message type'),
    ('OPERATION_ID','Operation ID'), ('BASE_ID','Base ID'),
    ('ORDER_ID','Order ID'), ('INITIATOR_ID','Initiator ID'),
    ('WORKPLACE_ID','Workplace ID'), ('SESSION_ID','1C session ID'),
    ('INSTANCE_ID','Worker instance ID'), ('PERMISSION_ID','Opening permission ID'),
    ('TASK_ID','Task ID'), ('PAYLOAD_JSON','Payload JSON'), ('CREATED_AT','Created at'),
    ('EXPIRES_AT','Permission expires at'),
)


def _user(value):
    if not isinstance(value, str) or not _USER_ID.fullmatch(value):
        raise EntitySetupError('A positive Bitrix24 user ID is required')
    return value


def _https_url(value, name):
    try:
        parsed = urlsplit(value)
        valid = (isinstance(value, str) and parsed.scheme == 'https' and parsed.hostname
                 and parsed.username is None and parsed.password is None
                 and parsed.port in (None, 443) and not parsed.fragment)
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise EntitySetupError(name + ' must be an HTTPS URL')
    return value


def entity_names(user_id):
    user_id = _user(user_id)
    outgoing = 'Q_' + user_id
    incoming = 'R_' + user_id
    if len(outgoing) > 13 or len(incoming) > 13:
        raise EntitySetupError('Bitrix24 entity identifier is too long')
    return {'outgoing':outgoing, 'incoming':incoming}


def _default_transport(url, body, timeout):
    request = Request(url, data=body, headers={
        'Content-Type':'application/json', 'Accept':'application/json'}, method='POST')
    opener = build_opener(_NoRedirect())
    try:
        response = opener.open(request, timeout=timeout)
    except HTTPError as error:
        response = error
    with response:
        payload = response.read(1024 * 1024 + 1)
        if len(payload) > 1024 * 1024 or response.code >= 500 or 300 <= response.code < 400:
            raise EntitySetupError('Bitrix24 REST result is unknown')
        try:
            return json.loads(payload.decode('utf-8'))
        except (ValueError, UnicodeError):
            raise EntitySetupError('Bitrix24 REST response is invalid') from None


class BitrixEntityClient:
    """REST adapter using a refreshable service OAuth token; no browser-service calls."""

    def __init__(self, portal, access_token, *, transport=None, timeout=15):
        try:
            parsed = urlsplit(portal)
            valid = (parsed.scheme == 'https' and parsed.hostname
                     and parsed.username is None and parsed.password is None
                     and parsed.port in (None, 443) and parsed.path in ('', '/')
                     and not parsed.query and not parsed.fragment)
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise ValueError('A configured HTTPS Bitrix24 portal origin is required')
        if not callable(access_token):
            raise ValueError('An OAuth access-token provider is required')
        if type(timeout) not in (int, float) or timeout <= 0:
            raise ValueError('A positive REST timeout is required')
        self._origin = 'https://' + parsed.netloc
        self._access_token = access_token
        self._transport = _default_transport if transport is None else transport
        self._timeout = float(timeout)

    def call(self, method, parameters, *, pagination=False):
        if method not in {
            'entity.add','entity.rights','entity.item.property.add','entity.item.add',
            'entity.item.get','placement.get','placement.bind','user.current',
        }:
            raise ValueError('REST method is not enabled by the entity adapter')
        token = self._access_token()
        if not isinstance(token, str) or not token or not token.isascii():
            raise EntitySetupError('Bitrix24 OAuth token is unavailable')
        try:
            body = json.dumps({**parameters,'auth':token}, ensure_ascii=False,
                              allow_nan=False).encode('utf-8')
            response = self._transport(self._origin + '/rest/' + method + '.json',
                                       body, self._timeout)
        except EntitySetupError:
            raise
        except Exception:
            raise EntitySetupError('Bitrix24 REST request is unavailable') from None
        if not isinstance(response, dict):
            raise EntitySetupError('Bitrix24 REST response is invalid')
        error = response.get('error')
        if error is not None:
            if not isinstance(error, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', error):
                error = 'API_ERROR'
            raise EntitySetupError('Bitrix24 REST rejected ' + method + ': ' + error)
        if 'result' not in response:
            raise EntitySetupError('Bitrix24 REST response is incomplete')
        return (response['result'],response.get('next')) if pagination else response['result']

    def current_user_id(self):
        result = self.call('user.current', {})
        if not isinstance(result, dict):
            raise EntitySetupError('Bitrix24 OAuth identity is unavailable')
        try:
            return _user(str(result.get('ID', '')))
        except EntitySetupError:
            raise EntitySetupError('Bitrix24 OAuth identity is unavailable') from None

    def _create_entity(self, code, name, rights):
        try:
            result = self.call('entity.add', {'ENTITY':code,'NAME':name,'ACCESS':rights})
            if result is not True:
                raise EntitySetupError('Bitrix24 entity creation was not confirmed')
        except EntitySetupError as error:
            if 'ERROR_ENTITY_ALREADY_EXISTS' not in str(error):
                raise
        actual = self.call('entity.rights', {'ENTITY':code,'ACCESS':rights})
        if not isinstance(actual, dict) or actual != rights or 'AU' in actual:
            raise EntitySetupError('Bitrix24 entity rights did not match the private channel contract')
        for sort, (property_code, title) in enumerate(_PROPERTIES, start=100):
            try:
                added = self.call('entity.item.property.add', {
                    'ENTITY':code, 'PROPERTY':property_code, 'NAME':title,
                    'TYPE':'S', 'SORT':sort,
                })
                if added is not True:
                    raise EntitySetupError('Bitrix24 entity property was not confirmed')
            except EntitySetupError as error:
                if 'ERROR_PROPERTY_ALREADY_EXISTS' not in str(error):
                    raise

    def provision_user(self, user_id, *, owner_user_id):
        user_id = _user(user_id)
        owner_user_id = _user(str(owner_user_id))
        if user_id == owner_user_id:
            raise EntitySetupError('The technical owner must differ from the employee')
        names = entity_names(user_id)
        self._create_entity(names['outgoing'], 'КА: очередь сотрудника ' + user_id,
                            {'U' + owner_user_id:'X','U' + user_id:'R'})
        self._create_entity(names['incoming'], 'КА: ответы сотрудника ' + user_id,
                            {'U' + owner_user_id:'X','U' + user_id:'W'})
        return names

    def ensure_worker_placement(self, user_id, handler_url, error_handler_url, *,
                                register_if_missing=True):
        user_id = _user(user_id)
        handler_url = _https_url(handler_url, 'Worker handler')
        error_handler_url = _https_url(error_handler_url, 'Worker error handler')
        placements = self.call('placement.get', {})
        if not isinstance(placements, list):
            raise EntitySetupError('Bitrix24 placement list is invalid')
        worker_rows = [row for row in placements
                       if isinstance(row, dict)
                       and row.get('placement') == 'PAGE_BACKGROUND_WORKER']
        expected_options = {'errorHandlerUrl':error_handler_url}

        def exact(row):
            return (str(row.get('userId', '')) == user_id
                    and row.get('handler') == handler_url
                    and row.get('options') == expected_options)

        if any(exact(row) for row in worker_rows):
            return 'verified'
        if worker_rows:
            raise EntitySetupError('A conflicting PAGE_BACKGROUND_WORKER registration exists')
        if not register_if_missing:
            raise EntitySetupError('PAGE_BACKGROUND_WORKER is not registered for this employee')
        parameters = {
            'PLACEMENT':'PAGE_BACKGROUND_WORKER', 'HANDLER':handler_url,
            'TITLE':'КА: очередь задачи', 'USER_ID':int(user_id),
            'OPTIONS':expected_options,
        }
        if self.call('placement.bind', parameters) is not True:
            raise EntitySetupError('Bitrix24 did not confirm worker registration')
        readback = self.call('placement.get', {})
        if (not isinstance(readback, list)
                or not any(isinstance(row, dict) and row.get('placement') == 'PAGE_BACKGROUND_WORKER'
                           and exact(row) for row in readback)):
            raise EntitySetupError('Bitrix24 worker registration was not confirmed by readback')
        return 'bound'

    def add_message(self, user_id, direction, message):
        user_id = _user(user_id)
        names = entity_names(user_id)
        if direction not in names:
            raise EntitySetupError('Unknown Bitrix24 employee channel')
        if not isinstance(message, dict) or set(message) - {
            'messageId','type','operationId','baseId','orderId','initiatorId',
            'workplaceId','sessionId','instanceId','permissionId','taskId','payload','createdAt','expiresAt',
        }:
            raise EntitySetupError('Invalid Bitrix24 channel message')
        message_id = message.get('messageId')
        message_type = message.get('type')
        operation_id = message.get('operationId')
        if (not isinstance(message_id, str) or not _MESSAGE_ID.fullmatch(message_id)
                or message_type not in _MESSAGE_TYPES
                or not isinstance(operation_id, str) or not _OPERATION_ID.fullmatch(operation_id)
                or str(message.get('initiatorId')) != user_id):
            raise EntitySetupError('Invalid Bitrix24 channel message identity')
        payload = message.get('payload', {})
        if not isinstance(payload, dict):
            raise EntitySetupError('Invalid Bitrix24 channel payload')
        try:
            payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                      separators=(',', ':'), allow_nan=False)
        except (TypeError, ValueError):
            raise EntitySetupError('Invalid Bitrix24 channel payload') from None
        if len(payload_json.encode('utf-8')) > 32768:
            raise EntitySetupError('Bitrix24 channel payload is too large')
        property_values = {
            'MESSAGE_ID':message_id, 'MESSAGE_TYPE':message_type,
            'OPERATION_ID':operation_id, 'BASE_ID':str(message.get('baseId','')),
            'ORDER_ID':str(message.get('orderId','')), 'INITIATOR_ID':user_id,
            'WORKPLACE_ID':str(message.get('workplaceId','')),
            'SESSION_ID':str(message.get('sessionId','')),
            'INSTANCE_ID':str(message.get('instanceId','')),
            'PERMISSION_ID':str(message.get('permissionId','')),
            'TASK_ID':str(message.get('taskId','')), 'PAYLOAD_JSON':payload_json,
            'CREATED_AT':str(message.get('createdAt','')),
            'EXPIRES_AT':str(message.get('expiresAt','')),
        }
        if any(len(value.encode('utf-8')) > 32768 for value in property_values.values()):
            raise EntitySetupError('Bitrix24 channel field is too large')
        # entity.item.add has no idempotency key. Recover an earlier successful
        # remote write before retrying a pending SQLite outbox row after a crash.
        matches = []
        for item in self.read_messages(user_id, direction):
            try:
                properties = self._item_properties(item)
            except EntitySetupError:
                continue
            if item.get('NAME') == message_id or properties.get('MESSAGE_ID') == message_id:
                matches.append(item)
        if len(matches) > 1:
            raise EntitySetupError('Bitrix24 contains duplicate entity message IDs')
        if matches:
            existing = matches[0]
            properties = self._item_properties(existing)
            for key, expected in property_values.items():
                actual = properties.get(key, '')
                if isinstance(actual, dict) and set(actual) <= {'VALUE','DESCRIPTION'}:
                    actual = actual.get('VALUE', '')
                if str(actual) != expected:
                    raise EntitySetupError('Bitrix24 message ID exists with changed contents')
            remote_id = existing.get('ID', existing.get('id'))
            if isinstance(remote_id, str) and remote_id.isdigit():
                remote_id = int(remote_id)
            if type(remote_id) is not int or remote_id <= 0:
                raise EntitySetupError('Bitrix24 channel item ID is invalid')
            return remote_id
        entity_id = self.call('entity.item.add', {
            'ENTITY':names[direction], 'NAME':message_id,
            'PROPERTY_VALUES':property_values,
        })
        if type(entity_id) not in (int, str) or not str(entity_id).isdigit() or int(entity_id) <= 0:
            raise EntitySetupError('Bitrix24 channel item ID is invalid')
        return int(entity_id)

    @staticmethod
    def _item_properties(item):
        if not isinstance(item, dict):
            raise EntitySetupError('Bitrix24 entity item is invalid')
        properties = item.get('PROPERTY_VALUES', item.get('PROPERTY_VALUE'))
        if isinstance(properties, list):
            parsed = {}
            for row in properties:
                if not isinstance(row, dict):
                    raise EntitySetupError('Bitrix24 entity item properties are invalid')
                key = row.get('PROPERTY', row.get('CODE', row.get('NAME')))
                if not isinstance(key, str) or key in parsed:
                    raise EntitySetupError('Bitrix24 entity item properties are invalid')
                parsed[key] = row.get('VALUE')
            properties = parsed
        if not isinstance(properties, dict):
            raise EntitySetupError('Bitrix24 entity item properties are unavailable')
        return properties

    def read_messages(self, user_id, direction):
        user_id = _user(user_id)
        names = entity_names(user_id)
        if direction not in names:
            raise EntitySetupError('Unknown Bitrix24 employee channel')
        items = []
        start = 0
        for _ in range(200):
            result, next_start = self.call('entity.item.get', {'ENTITY':names[direction],
                'SORT':{'ID':'ASC'},'start':start},pagination=True)
            if isinstance(result, dict) and isinstance(result.get('items'), list):
                result = result['items']
            if not isinstance(result, list) or len(result) > 50:
                raise EntitySetupError('Bitrix24 channel item list is invalid')
            items.extend(result)
            if next_start is None:
                return items
            if type(next_start) is not int or next_start <= start:
                raise EntitySetupError('Bitrix24 channel pagination is invalid')
            start = next_start
        raise EntitySetupError('Bitrix24 channel item list exceeded the scan limit')
