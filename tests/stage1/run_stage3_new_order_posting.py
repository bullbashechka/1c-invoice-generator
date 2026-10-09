"""One native command on an unsaved own demo copy; REST is disabled by default."""
import argparse
import json
import time
import uuid
from pathlib import Path
from run_register_concurrency import MCP, PROBE, UnknownOutcome
from run_stage3_native_create import READ, GATE

ROOT = Path(__file__).resolve().parents[2]


def run_attempt(c, args, before, read_source):
    marker = 'STAGE3_NATIVE_NEW_' + str(args.resume_marker or uuid.uuid4())
    output = ROOT / '.local/stage-3/new-order-posting' / marker
    if args.resume_marker:
        attempt = json.loads((output / 'attempt.json').read_text(encoding='utf-8'))
        assert attempt['source'] == str(args.source) and attempt['marker'] == marker, 'ATTEMPT_MISMATCH'
        response = json.loads((output / 'client.json').read_text(encoding='utf-8'))
    else:
        output.mkdir(parents=True)
        (output / 'attempt.json').write_text(json.dumps({'source': str(args.source), 'marker': marker, 'createTask': args.create_task}), encoding='utf-8')
        code = (ROOT / 'tests/stage1/stage3_new_order_posting_contract.bsl').read_text(encoding='utf-8')
        code = code.replace('__SOURCE_UUID__', str(args.source)).replace('__MARKER__', marker)
        response = c.rpc('tools/call', {'name': 'execute_code', 'arguments': {'code': code, 'execution_context': 'client'}})
        (output / 'client.json').write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding='utf-8')
    blocks = [json.loads(b['text']) for b in response.get('content', []) if b.get('type') == 'text']
    returned = len(blocks) == 1 and blocks[0].get('success') is True and blocks[0].get('data') == 'STAGE3_NEW_ORDER_COMMAND_RETURNED'
    find = '''
Запрос = Новый Запрос("ВЫБРАТЬ Ссылка ИЗ Документ.ЗаказКлиента ГДЕ ВЫРАЗИТЬ(Комментарий КАК СТРОКА(80)) = &Маркер");
Запрос.УстановитьПараметр("Маркер", "__MARKER__");
Строки = Запрос.Выполнить().Выбрать();
ID = ""; Количество = 0;
Пока Строки.Следующий() Цикл
    Если Строки.Ссылка.Комментарий = "__MARKER__" Тогда
        ID = Строка(Строки.Ссылка.УникальныйИдентификатор()); Количество = Количество + 1;
    КонецЕсли;
КонецЦикла;
Результат = Новый Структура("count,id", Количество, ID);
'''.replace('__MARKER__', marker)
    found = c.execute(find)
    deadline = time.monotonic() + 45
    while returned and found['count'] == 0 and time.monotonic() < deadline:
        time.sleep(1)
        found = c.execute(find)
    assert found['count'] == 1, 'NO_UNIQUE_POSTED_ORDER; INSPECT_BEFORE_RETRY'
    order_id = str(uuid.UUID(found['id']))
    read_order = READ.replace('__UUID__', order_id)
    state = c.execute(read_order)
    if args.create_task and returned:
        deadline = time.monotonic() + 45
        while state['state'] in ('Нет', 'Резерв', 'ОтправкаНачата', 'ЗадачаИзвестна') and time.monotonic() < deadline:
            time.sleep(1)
            state = c.execute(read_order)
    after_source = c.execute(read_source)
    unchanged = {k: v for k, v in before.items() if k != 'creationEnabled'} == {k: v for k, v in after_source.items() if k != 'creationEnabled'}
    expected_state = state['state'] == 'Связана' and bool(state['operationId']) and bool(state['taskId']) if args.create_task else state['state'] == 'Нет' and not state['operationId'] and not state['taskId'] and not state['creationEnabled']
    passed = returned and unchanged and state['own'] and state['posted'] and expected_state
    cleanup_confirmed = False
    if passed:
        cleanup = '''
ФормыПроверки = Новый Массив;
Для Каждого ОкноПроверки Из ПолучитьОкна() Цикл
    Для Каждого ФормаПроверки Из ОкноПроверки.Содержимое Цикл
        Если ФормаПроверки.ИмяФормы = "Документ.ЗаказКлиента.Форма.ФормаДокумента" Тогда
            Если Строка(ФормаПроверки.Объект.Ссылка.УникальныйИдентификатор()) = "__ORDER_UUID__" Тогда
                Если ФормаПроверки.Модифицированность Тогда ВызватьИсключение "OWN_FORM_HAS_UNSAVED_EDITS"; КонецЕсли;
                ФормыПроверки.Добавить(ФормаПроверки);
            КонецЕсли;
        КонецЕсли;
    КонецЦикла;
КонецЦикла;
Для Каждого ФормаПроверки Из ФормыПроверки Цикл ФормаПроверки.Закрыть(); КонецЦикла;
Результат = "OWN_POSTING_FORMS_CLOSED";
'''.replace('__ORDER_UUID__', order_id)
        cleanup_response = c.rpc('tools/call', {'name': 'execute_code', 'arguments': {'code': cleanup, 'execution_context': 'client'}})
        (output / 'cleanup.json').write_text(json.dumps(cleanup_response, ensure_ascii=False, indent=2), encoding='utf-8')
        answers = [json.loads(b['text']) for b in cleanup_response.get('content', []) if b.get('type') == 'text']
        cleanup_confirmed = len(answers) == 1 and answers[0].get('success') is True and answers[0].get('data') == 'OWN_POSTING_FORMS_CLOSED'
        passed = passed and cleanup_confirmed
    result = {'passed': passed, 'order': order_id, 'sourceIntegrationUnchanged': unchanged, 'resumedWithoutCommand': bool(args.resume_marker), 'testFormClosed': cleanup_confirmed, 'state': state}
    (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result, output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True, type=uuid.UUID)
    parser.add_argument('--resume-marker', type=uuid.UUID, help='Read an existing attempt; never execute the create command again')
    parser.add_argument('--create-task', action='store_true', help='Explicitly enable REST for one own demo attempt; disable in finally')
    args = parser.parse_args()
    c = MCP(6004)
    profile = json.loads((ROOT / '.local/stage-1/one-c-profile.json').read_text(encoding='utf-8'))
    assert c.execute(PROBE)['baseFingerprint'] == profile['baseFingerprint'], 'DEMO_MISMATCH'
    read_source = READ.replace('__UUID__', str(args.source))
    before = c.execute(read_source)
    assert before['own'] and not before['creationEnabled'], 'OWN_SOURCE_AND_REST_DISABLED_REQUIRED'
    enable_allowed = args.create_task and not args.resume_marker
    enable_requested = False
    try:
        if enable_allowed:
            pending = c.execute('''
Запрос = Новый Запрос("ВЫБРАТЬ ОперацияId, Состояние ИЗ РегистрСведений.Расш1_ЖурналОперацийБ24");
Строки = Запрос.Выполнить().Выбрать(); Количество = 0;
Пока Строки.Следующий() Цикл
    // Документированный старый тест с обрезанным маркером задачи10046.
    // Его неизвестность сохраняется; восстановление не вызывает повторный add.
    ИзвестнаяСтарая = Строки.ОперацияId = "a75ba748-104a-4735-951d-8554c96e2b9b" И Строки.Состояние = "Неизвестно";
    Если Расш1_ОперацииRESTБ24.Незавершена(Строки.Состояние) И Не ИзвестнаяСтарая Тогда Количество = Количество + 1; КонецЕсли;
КонецЦикла;
Результат = Количество;
''')
            assert pending == 0, 'UNFINISHED_ATTEMPTS_INSPECT_BEFORE_ENABLE'
            enable_requested = True
            print('AWAITING_TOOLKIT_CONFIRMATION_ENABLE_TRUE', flush=True)
            c.execute(GATE.replace('__ENABLED__', 'true'))
        result, output = run_attempt(c, args, before, read_source)
    finally:
        if enable_requested:
            print('AWAITING_TOOLKIT_CONFIRMATION_DISABLE_FALSE', flush=True)
            c.execute(GATE.replace('__ENABLED__', 'false'))
        assert not c.execute(read_source)['creationEnabled'], 'REST_DISABLE_NOT_CONFIRMED'
    result['state'] = c.execute(READ.replace('__UUID__', result['order']))
    result['restDisabledConfirmed'] = not result['state']['creationEnabled']
    result['passed'] = result['passed'] and result['restDisabledConfirmed']
    (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except UnknownOutcome:
        print('UNKNOWN_OUTCOME_INSPECT_PRIVATE_ATTEMPT_BEFORE_RETRY')
        raise SystemExit(2)
