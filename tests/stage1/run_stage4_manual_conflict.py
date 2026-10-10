"""Two-session stale manual confirmation on own task10074; no task creation/update."""
import json
from pathlib import Path
import uuid
from run_register_concurrency import MCP, PROBE

ROOT = Path(__file__).resolve().parents[2]
ORDER = '238b75de-c495-11f1-9a07-7ef9832e8e54'
TASK = '10074'


def main():
    stale, first = MCP(6003), MCP(6004)
    profile = json.loads((ROOT / '.local/stage-1/one-c-profile.json').read_text())
    probes = [c.execute(PROBE) for c in (stale, first)]
    assert all(p['baseFingerprint'] == profile['baseFingerprint'] for p in probes), 'DEMO_MISMATCH'
    assert probes[0]['connectionId'] != probes[1]['connectionId'], 'DISTINCT_CONNECTIONS_REQUIRED'
    output = ROOT / '.local/stage-4' / ('manual-conflict-' + str(uuid.uuid4()))
    output.mkdir()
    label = 'STAGE4_MANUAL_' + str(uuid.uuid4())
    ref = 'Документы.ЗаказКлиента.ПолучитьСсылку(Новый УникальныйИдентификатор("' + ORDER + '"))'
    snapshot_code = 'ЗаказРучнойПроверки = ' + ref + '; ОбъектРучнойПроверки = ЗаказРучнойПроверки.ПолучитьОбъект(); Результат = Новый Структура("comment,posted,version", ОбъектРучнойПроверки.Комментарий, ОбъектРучнойПроверки.Проведен, Base64Строка(ОбъектРучнойПроверки.ВерсияДанных));'
    before = first.execute(snapshot_code)
    state = first.execute('Результат = Расш1_СервисRESTБ24.СостояниеЗаказа(' + ref + ');')
    assert before['comment'].startswith('STAGE3_NATIVE_') and state['ЗадачаId'] == TASK, 'OWN_LINK_REQUIRED'
    version = state['ВерсияСвязи']
    lookup = '''
ФормаРучнойПроверки = Неопределено;
Для Каждого ОкноРучнойПроверки Из ПолучитьОкна() Цикл
 Для Каждого КандидатРучнойПроверки Из ОкноРучнойПроверки.Содержимое Цикл
  Если КандидатРучнойПроверки.ИмяФормы = "Документ.ЗаказКлиента.Форма.ФормаДокумента" Тогда
   Если КандидатРучнойПроверки.Заголовок = "__LABEL__" Тогда ФормаРучнойПроверки = КандидатРучнойПроверки; КонецЕсли;
  КонецЕсли;
 КонецЦикла;
КонецЦикла;
Если ФормаРучнойПроверки = Неопределено Тогда ВызватьИсключение "OWN_FORM_NOT_FOUND"; КонецЕсли;
'''.replace('__LABEL__', label)

    def client(code, phase):
        result = stale.rpc('tools/call', {'name': 'execute_code', 'arguments': {'code': code, 'execution_context': 'client'}})
        (output / (phase + '.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2))
        answers = [json.loads(b['text']) for b in result.get('content', []) if b.get('type') == 'text']
        assert len(answers) == 1 and answers[0].get('success') and not result.get('isError'), 'NATIVE_CHECK_FAILED'
        return answers[0].get('data')

    opened = False
    removed = False
    try:
        client('''
СсылкаРучнойПроверки = ПолучитьСсылкуПоТипуИUUID("Документ", "ЗаказКлиента", "__ORDER__");
ФормаРучнойПроверки = ПолучитьФорму("Документ.ЗаказКлиента.Форма.ФормаДокумента", Новый Структура("Ключ", СсылкаРучнойПроверки), , Новый УникальныйИдентификатор);
ФормаРучнойПроверки.Заголовок = "__LABEL__"; ФормаРучнойПроверки.Открыть();
Если ФормаРучнойПроверки.Расш1_НомерЗадачиБ24 <> "10074" Тогда ВызватьИсключение "OWN_LINK_REQUIRED"; КонецЕсли;
ФормаРучнойПроверки.Объект.Комментарий = ФормаРучнойПроверки.Объект.Комментарий + " UNSAVED_STAGE4_CONFLICT";
ФормаРучнойПроверки.Модифицированность = Истина;
Результат = "STALE_FORM_READY";
'''.replace('__ORDER__', ORDER).replace('__LABEL__', label), 'open')
        opened = True
        first.execute('Расш1_СервисRESTБ24.ИзменитьСвязь(' + ref + ', "", ' + str(version) + '); Результат = "FIRST_UNLINK_COMMITTED";')
        removed = True
        after_first = first.execute('Результат = Расш1_СервисRESTБ24.СостояниеЗаказа(' + ref + ');')
        assert not after_first['ЗадачаId'] and after_first['ВерсияСвязи'] == version + 1
        result = client(lookup + '''
КомментарийРучнойПроверки = ФормаРучнойПроверки.Объект.Комментарий;
ФормаРучнойПроверки.Расш1_ПодтверждениеУдаления(КодВозвратаДиалога.Да, Новый Структура("Заказ,ВерсияСвязи", ФормаРучнойПроверки.Объект.Ссылка, __VERSION__));
Если Не ФормаРучнойПроверки.Модифицированность Или ФормаРучнойПроверки.Объект.Комментарий <> КомментарийРучнойПроверки
 Или ФормаРучнойПроверки.Расш1_ЗадачаСоздана Или ФормаРучнойПроверки.Расш1_НомерЗадачиБ24 <> ""
 Или ФормаРучнойПроверки.Расш1_ВерсияСвязи <> __NEW_VERSION__ Тогда ВызватьИсключение "STALE_ACTION_CHANGED_LINK_OR_EDITS"; КонецЕсли;
Результат = "STAGE4_TWO_SESSION_MANUAL_CONFLICT_GREEN";
'''.replace('__VERSION__', str(version)).replace('__NEW_VERSION__', str(version + 1)), 'stale-confirmation')
        assert first.execute('Результат = Расш1_СервисRESTБ24.СостояниеЗаказа(' + ref + ');') == after_first
        assert first.execute(snapshot_code) == before
        print(result, flush=True)
    finally:
        if removed:
            first.execute('СостояниеРучнойПроверки = Расш1_СервисRESTБ24.СостояниеЗаказа(' + ref + '); Если Не ПустаяСтрока(СостояниеРучнойПроверки.ЗадачаId) Или СостояниеРучнойПроверки.ВерсияСвязи <> ' + str(version + 1) + ' Тогда ВызватьИсключение "RESTORE_REFUSED_PRESERVE_NEWER_ACTION"; КонецЕсли; КандидатРучнойПроверки = Расш1_СервисRESTБ24.ПроверитьКандидата(' + ref + ', "10074", СостояниеРучнойПроверки.ВерсияСвязи); Расш1_СервисRESTБ24.ПодтвердитьПривязку(' + ref + ', КандидатРучнойПроверки, Истина); Результат = "OWN_LINK_RESTORED";')
        if opened:
            client(lookup + 'ФормаРучнойПроверки.Объект.Комментарий = Лев(ФормаРучнойПроверки.Объект.Комментарий, СтрДлина(ФормаРучнойПроверки.Объект.Комментарий) - СтрДлина(" UNSAVED_STAGE4_CONFLICT")); ФормаРучнойПроверки.Модифицированность = Ложь; ФормаРучнойПроверки.Закрыть(); Результат = "OWN_FORM_CLOSED_WITHOUT_SAVE";', 'close')
        final_state = first.execute('Результат = Расш1_СервисRESTБ24.СостояниеЗаказа(' + ref + ');')
        assert final_state['ЗадачаId'] == TASK and first.execute(snapshot_code) == before
        print('OWN_LINK_RESTORED_DOCUMENT_UNCHANGED', flush=True)


if __name__ == '__main__':
    main()
