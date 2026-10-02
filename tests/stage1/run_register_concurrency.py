"""Live two-session register check. This is not a mocked or offline unit test."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import time
from urllib.error import URLError
from urllib.request import Request, urlopen
import uuid

ROOT = Path(__file__).resolve().parent
PROBE = '''ХешБазы = Новый ХешированиеДанных(ХешФункция.SHA256);
ХешБазы.Добавить(СтрокаСоединенияИнформационнойБазы());
Запрос = Новый Запрос("ВЫБРАТЬ КОЛИЧЕСТВО(*) КАК Количество ИЗ РегистрСведений.Расш1_СвязиЗаказовБ24");
Выборка = Запрос.Выполнить().Выбрать(); Выборка.Следующий();
Результат = Новый Структура("connectionId,sessionId,configuration,version,baseFingerprint,rows",
    НомерСоединенияИнформационнойБазы(), НомерСеансаИнформационнойБазы(),
    Метаданные.Имя, Метаданные.Версия, Строка(ХешБазы.ХешСумма), Выборка.Количество);'''


def decode(raw: str) -> dict:
    if not raw.lstrip().startswith('{'):
        raw = '\n'.join(line[5:].lstrip() for line in raw.splitlines()
                        if line.startswith('data:'))
    return json.loads(raw)


class MCP:
    def __init__(self, port: int):
        self.url = f'http://127.0.0.1:{port}/mcp'
        self.session = None
        self.request_id = 0
        self.rpc('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
                               'clientInfo': {'name': 'stage1-concurrency', 'version': '1'}})

    def rpc(self, method: str, params: dict) -> dict:
        self.request_id += 1
        headers = {'Content-Type': 'application/json',
                   'Accept': 'application/json, text/event-stream'}
        if self.session:
            headers['Mcp-Session-Id'] = self.session
        payload = {'jsonrpc': '2.0', 'id': self.request_id,
                   'method': method, 'params': params}
        try:
            with urlopen(Request(self.url, json.dumps(payload, ensure_ascii=False).encode(),
                                 headers), timeout=90) as response:
                self.session = response.headers.get('Mcp-Session-Id', self.session)
                result = decode(response.read().decode())
        except (URLError, OSError, ValueError) as error:
            raise UnknownOutcome('MCP_RESPONSE_NOT_CONFIRMED') from error
        if 'error' in result:
            raise UnknownOutcome('MCP_RPC_ERROR')
        return result['result']

    def execute(self, code: str) -> dict:
        result = self.rpc('tools/call', {'name': 'execute_code',
                                       'arguments': {'code': code, 'execution_context': 'server'}})
        blocks = [block['text'] for block in result.get('content', [])
                  if block.get('type') == 'text']
        if result.get('isError') or len(blocks) != 1:
            raise UnknownOutcome('MCP_TOOL_ERROR')
        try:
            answer = json.loads(blocks[0])
        except ValueError as error:
            raise UnknownOutcome('MCP_TOOL_RESPONSE_INVALID') from error
        if not answer.get('success'):
            # Keep the original error private; it can contain local paths.
            raise ScenarioFailed('ONEC_SCENARIO_FAILED')
        return answer['data']


class UnknownOutcome(RuntimeError):
    pass


class ScenarioFailed(RuntimeError):
    pass


def make_attempts(conflict: str) -> dict:
    if conflict not in ('task', 'order', 'operation'):
        raise ValueError('UNKNOWN_CONFLICT_CASE')
    attempts = {
        'orderIds': [str(uuid.uuid4()), str(uuid.uuid4())],
        'operationIds': [str(uuid.uuid4()), str(uuid.uuid4())],
        'taskIds': [],
    }
    first_task = 2100000100 + secrets.randbelow(10000)
    attempts['taskIds'] = [str(first_task), str(first_task + 1)]
    shared = {'task': 'taskIds', 'order': 'orderIds', 'operation': 'operationIds'}[conflict]
    attempts[shared][1] = attempts[shared][0]
    return attempts


def run(port_a: int, port_b: int, output: Path, expected_fingerprint: str,
        conflict: str = 'task') -> dict:
    attempts = make_attempts(conflict)
    if port_a == port_b:
        raise ValueError('TWO_DISTINCT_MCP_PORTS_REQUIRED')
    clients = [MCP(port_a), MCP(port_b)]
    probes = [client.execute(PROBE) for client in clients]
    if any(p['configuration'] != 'КомплекснаяАвтоматизацияДляКазахстана'
           or p['version'] != '2.4.5.18' or p['rows'] != 0 for p in probes):
        raise RuntimeError('DEMO_CONFIGURATION_AND_EMPTY_REGISTER_REQUIRED')
    if probes[0]['baseFingerprint'] != probes[1]['baseFingerprint']:
        raise RuntimeError('SAME_DATABASE_REQUIRED')
    if probes[0]['baseFingerprint'] != expected_fingerprint:
        raise RuntimeError('AUTHORIZED_DEMO_DATABASE_REQUIRED')
    if (probes[0]['sessionId'] == probes[1]['sessionId']
            or probes[0]['connectionId'] == probes[1]['connectionId']):
        raise RuntimeError('TWO_DISTINCT_ONEC_SESSIONS_REQUIRED')

    run_id = uuid.uuid4().hex
    orders = attempts['orderIds']
    operations = attempts['operationIds']
    task_ids = attempts['taskIds']
    holder_ready, contender_ready, release = (threading.Event() for _ in range(3))
    aborted = threading.Event()
    timeline = {}
    timeline_lock = threading.Lock()

    class Barrier(BaseHTTPRequestHandler):
        def do_GET(self):
            suffix = self.path.removeprefix('/' + run_id)
            if self.path != '/' + run_id + suffix or suffix not in ('/holder', '/contender'):
                self.send_error(404)
                return
            with timeline_lock:
                timeline[suffix[1:]] = time.monotonic()
            if suffix == '/holder':
                holder_ready.set()
                permitted = release.wait(25) and not aborted.is_set()
            else:
                contender_ready.set()
                permitted = not aborted.is_set()
            self.send_response(200 if permitted else 409)
            self.send_header('Content-Length', '0')
            self.end_headers()

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Barrier)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    prelude = f'''Заказ1 = Документы.ЗаказКлиента.ПолучитьСсылку(Новый УникальныйИдентификатор("{orders[0]}"));
Заказ2 = Документы.ЗаказКлиента.ПолучитьСсылку(Новый УникальныйИдентификатор("{orders[1]}"));
ЗадачаIdТеста = "{task_ids[0]}"; ЗадачаIdВторой = "{task_ids[1]}";
Операция1 = "{operations[0]}"; Операция2 = "{operations[1]}";
ПортБарьера = {server.server_port}; ПутьБарьера = "/{run_id}";
'''
    report = {'runId': run_id, 'conflict': conflict, 'orderIds': orders,
              'operationIds': operations, 'syntheticTaskId': task_ids[0],
              'syntheticTaskIds': task_ids, 'sessions': probes, 'status': 'started'}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    scenario_error = None
    started = False
    actors = []
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            started = True
            holder = pool.submit(clients[0].execute, prelude +
                                 ROOT.joinpath('register_concurrency_holder.bsl').read_text(encoding='utf-8'))
            actors.append(holder)
            try:
                if not holder_ready.wait(60):
                    raise RuntimeError('HOLDER_NOT_READY')
                contender = pool.submit(clients[1].execute, prelude +
                                        ROOT.joinpath('register_concurrency_contender.bsl').read_text(encoding='utf-8'))
                actors.append(contender)
                if not contender_ready.wait(15):
                    raise RuntimeError('CONTENDER_NOT_READY')
                time.sleep(1)
                if holder.done() or contender.done():
                    raise RuntimeError('OVERLAP_NOT_OBSERVED')
                report['overlapObserved'] = True
                with timeline_lock:
                    timeline['release'] = time.monotonic()
                release.set()
                holder_result, contender_result = holder.result(), contender.result()
                if (holder_result != {'status': 'holder_committed', 'sessionId': probes[0]['sessionId']}
                        or contender_result != {'status': 'contender_conflict', 'sessionId': probes[1]['sessionId']}):
                    raise RuntimeError('UNEXPECTED_ACTOR_RESULT')
                report['actors'] = [holder_result, contender_result]
            except Exception as error:
                scenario_error = type(error).__name__ + ': ' + (
                    str(error) if isinstance(error, RuntimeError) else 'transport failure')
                aborted.set()
                release.set()
        # A transport failure is an unknown outcome, even when its Future is done.
        if any(actor.exception() is not None
               and not isinstance(actor.exception(), ScenarioFailed) for actor in actors):
            raise UnknownOutcome('ACTOR_OUTCOME_UNKNOWN: cleanup deferred')
        cleanup = clients[0].execute(prelude +
                                    ROOT.joinpath('register_concurrency_cleanup.bsl').read_text(encoding='utf-8'))
        report['cleanup'] = cleanup
        if scenario_error:
            raise RuntimeError(scenario_error)
        if cleanup != {'firstLinkPreserved': True, 'testRowsRemaining': 0}:
            raise RuntimeError('FIRST_LINK_NOT_PRESERVED')
        report['status'] = 'STAGE1_REGISTER_CONCURRENCY_GREEN'
    except Exception as error:
        report['status'] = 'not_passed'
        report['failure'] = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        report['cleanupRequired'] = started and 'cleanup' not in report
    finally:
        aborted.set()
        release.set()
        server.shutdown()
        server.server_close()
        server_thread.join()
        report['timeline'] = timeline
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ports', nargs=2, type=int, default=[6003, 6004])
    parser.add_argument('--conflict', choices=['task', 'order', 'operation'], default='task',
                        help='The one key shared by the two competing attempts')
    parser.add_argument('--output', type=Path, default=Path('.local/stage-1/register-concurrency-private.json'))
    parser.add_argument('--demo-proof', type=Path, required=True,
                        help='Private read-only execute_code result with baseFingerprint for the authorized demo')
    args = parser.parse_args()
    if any(port < 1024 or port > 65535 for port in args.ports):
        parser.error('ports must be in 1024..65535')
    try:
        proof = json.loads(args.demo_proof.read_text(encoding='utf-8'))['response']['result']
        expected_fingerprint = json.loads(proof['content'][0]['text'])['data']['baseFingerprint']
        report = run(*args.ports, args.output, expected_fingerprint, args.conflict)
    except Exception as error:
        # Preflight failures occur before register writes or a barrier server start.
        print(json.dumps({'status': 'preflight_failed', 'errorType': type(error).__name__,
                          'registerWritesStarted': False}))
        raise SystemExit(1)
    print(json.dumps({'status': report['status'], 'conflict': report['conflict'],
                      'cleanup': report.get('cleanup'),
                      'cleanupRequired': report.get('cleanupRequired', False)}))
    raise SystemExit(0 if report['status'] == 'STAGE1_REGISTER_CONCURRENCY_GREEN' else 1)


if __name__ == '__main__':
    main()
