"""Task request preparation; not connected to a user interface or live portal."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import math
import re
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


def build_task_fields(order, defaults, opened_at):
    """Capture defaults once at opening; callers may edit fields before submitting."""
    if not isinstance(opened_at, datetime) or opened_at.utcoffset() is None:
        raise ValueError('Opening time must include a timezone')
    for name in ('creator', 'responsible', 'auditor', 'accomplice', 'project'):
        value = defaults.get(name)
        if type(value) is not int or value <= 0:
            raise ValueError('Verified positive participant and project IDs are required')
    for name in ('counterparty', 'client', 'number', 'date', 'currency', 'link'):
        value = order.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError('Saved order text fields are required')
    try:
        amount = Decimal(str(order.get('amount')))
    except InvalidOperation:
        raise ValueError('Invalid order amount') from None
    if not amount.is_finite():
        raise ValueError('Invalid order amount')
    opening_day = opened_at.astimezone(timezone(timedelta(hours=5)))
    deadline = opening_day.replace(hour=23, minute=59, second=0, microsecond=0)
    return {
        'TITLE': order['counterparty'] + '.',
        'DESCRIPTION': (f"Заказ клиента №{order['number']} от {order['date']}\n"
                        f"Клиент: {order['client']}\nСумма: {amount} {order['currency']}\n"
                        f"Документ 1С: {order['link']}"),
        'CREATED_BY': defaults['creator'],
        'RESPONSIBLE_ID': defaults['responsible'],
        'AUDITORS': [defaults['auditor']],
        'ACCOMPLICES': [defaults['accomplice']],
        'GROUP_ID': defaults['project'],
        'DEADLINE': deadline.isoformat(),
    }


class CreationBlocked(Exception):
    pass


class ResultUnknown(Exception):
    pass


class APIRejected(Exception):
    pass


class CreatedButNotRecorded(Exception):
    def __init__(self, task_id, operation_id):
        self.task_id, self.operation_id = task_id, operation_id
        super().__init__('Task was created, but its result could not be recorded; do not retry')


def _payload(fields):
    if not isinstance(fields, dict):
        raise ValueError('Task fields must be an object')
    title, responsible = fields.get('TITLE'), fields.get('RESPONSIBLE_ID')
    if not isinstance(title, str) or not title.strip():
        raise ValueError('Task title is required')
    if type(responsible) is not int or responsible <= 0:
        raise ValueError('Positive responsible ID is required')
    if 'CREATED_BY' in fields:
        creator = fields['CREATED_BY']
        if type(creator) is not int or creator <= 0:
            raise ValueError('Positive creator ID is required')
    if 'GROUP_ID' in fields:
        group = fields['GROUP_ID']
        if type(group) is not int or group < 0:
            raise ValueError('Nonnegative project ID is required')
    for name in ('AUDITORS', 'ACCOMPLICES'):
        if name in fields:
            ids = fields[name]
            if not isinstance(ids, list) or any(type(value) is not int or value <= 0 for value in ids):
                raise ValueError('Participant lists must contain positive IDs')
    if 'DESCRIPTION' in fields and not isinstance(fields['DESCRIPTION'], str):
        raise ValueError('Description must be text')
    deadline = fields.get('DEADLINE')
    if deadline is not None and deadline != '':
        try:
            parsed_deadline = datetime.fromisoformat(deadline)
            if parsed_deadline.utcoffset() is None:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError('Deadline must be an ISO date-time with timezone or empty') from None
    try:
        return json.dumps({'fields': fields}, ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, UnicodeError):
        raise ValueError('Task fields must be valid JSON') from None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def _post(url, body, timeout):
    request = Request(url, data=body, headers={'Content-Type': 'application/json',
                                             'Accept': 'application/json'}, method='POST')
    opener = build_opener(_NoRedirect())
    try:
        response = opener.open(request, timeout=timeout)
    except HTTPError as error:
        response = error
    with response:
        data = response.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise ResultUnknown('Task creation response is too large')
        if 300 <= response.code < 400 or response.code >= 500:
            raise ResultUnknown('Task creation result is unknown')
        if response.code >= 400:
            try:
                error_body = json.loads(data)
            except (ValueError, UnicodeError):
                raise ResultUnknown('Task creation result is unknown') from None
            if not isinstance(error_body, dict) or 'error' not in error_body:
                raise ResultUnknown('Task creation result is unknown')
        return data


class TaskAPIClient:
    def __init__(self, webhook_base, *, transport=None, timeout=15):
        try:
            parsed = urlsplit(webhook_base)
            valid = (parsed.scheme == 'https' and parsed.hostname and
                     parsed.username is None and parsed.password is None and
                     parsed.port in (None, 443) and not parsed.query and not parsed.fragment and
                     re.fullmatch(r'/rest/[1-9]\d*/[A-Za-z0-9_-]+/?', parsed.path))
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise ValueError('A configured HTTPS incoming webhook is required')
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('Positive finite timeout is required')
        self._url = webhook_base.rstrip('/') + '/tasks.task.add.json'
        self._transport = _post if transport is None else transport
        self._timeout = timeout

    def add(self, fields):
        body = _payload(fields)
        try:
            data = json.loads(self._transport(self._url, body, self._timeout))
        except Exception:
            raise ResultUnknown('Task creation result is unknown; do not retry') from None
        if not isinstance(data, dict):
            raise ResultUnknown('Invalid task creation response; do not retry')
        if 'error' in data:
            code = data['error']
            if not isinstance(code, str) or not re.fullmatch(r'[A-Z0-9_.]{1,80}', code):
                code = 'API_ERROR'
            raise APIRejected(code)
        result = data.get('result')
        task = result.get('task') if isinstance(result, dict) else None
        task_id = task.get('id') if isinstance(task, dict) else None
        if isinstance(task_id, str) and re.fullmatch(r'[0-9]{1,19}', task_id):
            task_id = int(task_id)
        if type(task_id) is not int or not 0 < task_id <= 2**63 - 1:
            raise ResultUnknown('Invalid task creation response; do not retry')
        return task_id


def submit_task(store, client, base, order, operation, fields):
    """Submit only on an explicitly chosen user action; no automatic retries."""
    # Freeze the employee's submitted values before reserving the operation.
    snapshot = json.loads(_payload(fields))['fields']
    current = store.begin(base, order, operation)
    if current['state'] in ('created', 'acknowledged'):
        return current
    if not store.claim_creation(operation, base, order):
        raise CreationBlocked('Operation cannot be submitted again; check the original result')
    task_id = client.add(snapshot)
    try:
        return store.accept_result(operation, base, order, task_id)
    except Exception:
        # Retain the known remote ID for explicit reconciliation by the caller.
        raise CreatedButNotRecorded(task_id, operation) from None
