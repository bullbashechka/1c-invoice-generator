"""Read and correlate manually-created Bitrix24 tasks; this module never creates tasks."""

from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import json
import math
import re

from operation_store import Conflict


class ResultUnknown(Exception):
    pass


class BitrixAPIRejected(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__('Bitrix24 rejected the task read: ' + code)


class CorrelationUnavailable(Exception):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def _post_json(url, body, timeout):
    request = Request(url, data=body, headers={
        'Content-Type':'application/json', 'Accept':'application/json'}, method='POST')
    opener = build_opener(_NoRedirect())
    try:
        response = opener.open(request, timeout=timeout)
    except HTTPError as error:
        response = error
    with response:
        data = response.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024 or response.code >= 500 or 300 <= response.code < 400:
            raise ResultUnknown('Bitrix24 task read result is unknown')
        return data


class BitrixRESTClient:
    _MAX_TAG_SEARCH_PAGES = 100

    def __init__(self, portal, access_token, *, transport=None, timeout=15):
        try:
            parsed = urlsplit(portal)
            valid_portal = (parsed.scheme == 'https' and parsed.hostname
                            and parsed.username is None and parsed.password is None
                            and parsed.port in (None, 443) and parsed.path in ('', '/')
                            and not parsed.query and not parsed.fragment)
        except (TypeError, ValueError):
            valid_portal = False
        if not valid_portal:
            raise ValueError('A configured HTTPS Bitrix24 portal origin is required')
        if not callable(access_token) and (not isinstance(access_token, str)
                or not access_token.isascii() or not 1 <= len(access_token) <= 4096
                or any(ch.isspace() for ch in access_token)):
            raise ValueError('A valid Bitrix24 access token is required')
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('Positive finite timeout is required')
        self._origin = f'https://{parsed.netloc}'
        self._access_token = access_token
        self._transport = _post_json if transport is None else transport
        self._timeout = timeout

    def _call(self, method, parameters):
        if method not in ('tasks.task.get', 'tasks.task.list', 'user.current'):
            raise ValueError('Only configured Bitrix24 identity and task reads are supported')
        try:
            access_token = self._access_token() if callable(self._access_token) else self._access_token
            if (not isinstance(access_token, str) or not access_token.isascii()
                    or not 1 <= len(access_token) <= 4096
                    or any(ch.isspace() for ch in access_token)):
                raise ResultUnknown('Bitrix24 access token is unavailable')
            payload = json.dumps({**parameters, 'auth':access_token},
                                 ensure_ascii=False, allow_nan=False).encode('utf-8')
            raw = self._transport(self._origin + '/rest/' + method + '.json', payload,
                                  self._timeout)
            data = raw if isinstance(raw, dict) else json.loads(raw)
        except BitrixAPIRejected:
            raise
        except ResultUnknown:
            raise
        except Exception:
            raise ResultUnknown('Bitrix24 task read result is unknown') from None
        if not isinstance(data, dict):
            raise ResultUnknown('Bitrix24 task read response is invalid')
        if 'error' in data:
            code = data['error']
            if not isinstance(code, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', code):
                code = 'API_ERROR'
            raise BitrixAPIRejected(code)
        return data.get('result')

    @staticmethod
    def _task_id(value, *, allow_string=True):
        if type(value) is int and 0 < value <= 2**63 - 1:
            return value
        if (allow_string and isinstance(value, str)
                and re.fullmatch(r'[0-9]{1,19}', value)):
            number = int(value)
            if 0 < number <= 2**63 - 1:
                return number
        raise ValueError('Positive task ID is required')

    @staticmethod
    def _tags(task):
        tags = task.get('tags', task.get('TAGS'))
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise CorrelationUnavailable('Task response does not contain readable tags')
        return tags

    def read_task(self, task_id):
        wanted = self._task_id(task_id, allow_string=False)
        result = self._call('tasks.task.get', {'id':wanted, 'select':['id','tags']})
        task = result.get('task') if isinstance(result, dict) else None
        if not isinstance(task, dict):
            task = result.get('item') if isinstance(result, dict) else None
        if not isinstance(task, dict):
            raise ResultUnknown('Bitrix24 task read response is invalid')
        returned_id = self._task_id(task.get('id', task.get('ID')))
        if returned_id != wanted:
            raise CorrelationUnavailable('Bitrix24 returned a different task')
        self._tags(task)
        return task

    def current_user_id(self):
        """Verify a browser access token against the configured portal, then return its user ID."""
        result = self._call('user.current', {})
        if (not isinstance(result, dict) or not isinstance(result.get('ID'), str)
                or not re.fullmatch(r'[1-9][0-9]{0,9}', result['ID'])):
            raise ResultUnknown('Bitrix24 user identity response is invalid')
        return result['ID']

    def find_task_ids_by_correlation_tag(self, tag):
        """Search task tags with bounded pagination; an empty result is not proof of absence."""
        if not isinstance(tag, str) or not re.fullmatch(r'КА-[A-Za-z0-9_-]{1,128}', tag):
            raise ValueError('A valid operation correlation tag is required')
        matches = set()
        start = 0
        for _ in range(self._MAX_TAG_SEARCH_PAGES):
            result = self._call('tasks.task.list', {
                'filter': {'TAG':tag}, 'select':['ID','TAGS'], 'start':start,
            })
            tasks = result.get('tasks') if isinstance(result, dict) else None
            if not isinstance(tasks, list) or len(tasks) > 50:
                raise ResultUnknown('Bitrix24 tag search response is invalid')
            for task in tasks:
                if not isinstance(task, dict):
                    raise ResultUnknown('Bitrix24 tag search response is invalid')
                task_id = self._task_id(task.get('id', task.get('ID')))
                if tag in self._tags(task):
                    matches.add(task_id)
            next_start = result.get('next')
            if next_start is None:
                return sorted(matches)
            if type(next_start) is not int or next_start <= start:
                raise ResultUnknown('Bitrix24 tag search pagination is invalid')
            start = next_start
        raise ResultUnknown('Bitrix24 tag search exceeded the recovery limit')

    def reconcile_correlation_tag(self, tag, store):
        """Recover a task only when the exact correlation tag identifies one candidate."""
        task_ids = self.find_task_ids_by_correlation_tag(tag)
        if len(task_ids) != 1:
            raise CorrelationUnavailable('Tag search did not identify exactly one task')
        return self.reconcile_task_add(task_ids[0], store)

    def reconcile_task_add(self, task_id, store):
        task = self.read_task(task_id)
        tags = self._tags(task)
        matching = [store.request_by_correlation_tag(tag) for tag in set(tags)]
        matching = [request for request in matching if request is not None]
        unique = {request['operationId']:request for request in matching}
        if len(unique) != 1:
            raise CorrelationUnavailable('Task does not identify exactly one queued operation')
        request = next(iter(unique.values()))
        try:
            return store.accept_verified_task(request['baseId'], request['orderId'],
                                              request['operationId'], self._task_id(task_id), tags)
        except Conflict:
            raise CorrelationUnavailable('Task result conflicts with the saved operation') from None
