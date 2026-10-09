"""Native save/post helpers on one own unlinked demo order, with REST disabled."""
import argparse
import json
import time
import uuid
from pathlib import Path
from run_register_concurrency import MCP, PROBE
from run_stage3_native_create import READ

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--order', required=True, type=uuid.UUID)
    args = parser.parse_args()
    c = MCP(6004)
    profile = json.loads((ROOT / '.local/stage-1/one-c-profile.json').read_text(encoding='utf-8'))
    assert c.execute(PROBE)['baseFingerprint'] == profile['baseFingerprint'], 'DEMO_MISMATCH'
    read = READ.replace('__UUID__', str(args.order))
    before = c.execute(read)
    assert before['own'] and before['posted'] and before['state'] == 'Нет' and not before['operationId'] and not before['creationEnabled'], 'OWN_POSTED_UNLINKED_ORDER_REST_DISABLED_REQUIRED'
    output = ROOT / '.local/stage-3/ordinary-commands' / str(uuid.uuid4())
    output.mkdir(parents=True)
    template = (ROOT / 'tests/stage1/stage3_ordinary_commands_contract.bsl').read_text(encoding='utf-8')
    for method in ('Записать', 'Провести'):
        marker = 'STAGE3_NATIVE_ORDINARY_' + str(uuid.uuid4())
        code = template.replace('__ORDER_UUID__', str(args.order)).replace('__METHOD__', method).replace('__MARKER__', marker)
        response = c.rpc('tools/call', {'name': 'execute_code', 'arguments': {'code': code, 'execution_context': 'client'}})
        (output / (method + '-client.json')).write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding='utf-8')
        answers = [json.loads(b['text']) for b in response.get('content', []) if b.get('type') == 'text']
        assert len(answers) == 1 and answers[0].get('success') and answers[0].get('data') == 'STAGE3_ORDINARY_COMMAND_RETURNED', 'NATIVE_COMMAND_NOT_CONFIRMED'
        check = '''
ЗаказПроверки = Документы.ЗаказКлиента.ПолучитьСсылку(Новый УникальныйИдентификатор("__UUID__"));
Результат = ЗаказПроверки.Комментарий = "__MARKER__";
'''.replace('__UUID__', str(args.order)).replace('__MARKER__', marker)
        deadline = time.monotonic() + 15
        saved = c.execute(check)
        while not saved and time.monotonic() < deadline:
            time.sleep(1)
            saved = c.execute(check)
        assert saved and c.execute(read) == before, 'SAVE_OR_INTEGRATION_STATE_NOT_CONFIRMED'
        # The form must be clean after real saving; never discard edits.
        close = '''
ФормыПроверки = Новый Массив;
Для Каждого ОкноПроверки Из ПолучитьОкна() Цикл
    Для Каждого ФормаПроверки Из ОкноПроверки.Содержимое Цикл
        Если ФормаПроверки.ИмяФормы = "Документ.ЗаказКлиента.Форма.ФормаДокумента" Тогда
            Если ФормаПроверки.Объект.Комментарий = "__MARKER__" Тогда
                Если ФормаПроверки.Модифицированность Тогда ВызватьИсключение "OWN_FORM_HAS_UNSAVED_EDITS"; КонецЕсли;
                ФормыПроверки.Добавить(ФормаПроверки);
            КонецЕсли;
        КонецЕсли;
    КонецЦикла;
КонецЦикла;
Для Каждого ФормаПроверки Из ФормыПроверки Цикл ФормаПроверки.Закрыть(); КонецЦикла;
Результат = "OWN_ORDINARY_FORMS_CLOSED";
'''.replace('__MARKER__', marker)
        closed = c.rpc('tools/call', {'name': 'execute_code', 'arguments': {'code': close, 'execution_context': 'client'}})
        (output / (method + '-cleanup.json')).write_text(json.dumps(closed, ensure_ascii=False, indent=2), encoding='utf-8')
        closed_answers = [json.loads(b['text']) for b in closed.get('content', []) if b.get('type') == 'text']
        assert len(closed_answers) == 1 and closed_answers[0].get('success') and closed_answers[0].get('data') == 'OWN_ORDINARY_FORMS_CLOSED', 'CLEANUP_NOT_CONFIRMED'
    result = {'passed': True, 'nativeSaveAndPostVerified': True, 'integrationUnchanged': True, 'physicalButtonsVerified': False}
    (output / 'result.json').write_text(json.dumps(result), encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
