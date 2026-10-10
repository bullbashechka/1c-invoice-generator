"""Two real client commands on one fresh own posted demo order; one REST task."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
import time
import uuid
from run_register_concurrency import MCP, PROBE
from run_stage3_native_create import READ, GATE

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--order', required=True, type=uuid.UUID)
    args = parser.parse_args()
    clients = [MCP(6003), MCP(6004)]
    profile = json.loads((ROOT / '.local/stage-1/one-c-profile.json').read_text())
    probes = [c.execute(PROBE) for c in clients]
    assert all(p['baseFingerprint'] == profile['baseFingerprint'] for p in probes), 'DEMO_MISMATCH'
    assert probes[0]['connectionId'] != probes[1]['connectionId'], 'DISTINCT_CONNECTIONS_REQUIRED'
    read = READ.replace('__UUID__', str(args.order))
    states = [c.execute(read) for c in clients]
    assert all(s['own'] and s['posted'] and s['state'] == 'Нет' and not s['creationEnabled'] for s in states), 'FRESH_OWN_POSTED_ORDER_REQUIRED'
    user_ids = [c.execute('Результат = Расш1_ОперацииRESTБ24.ПользовательId();') for c in clients]
    admin = clients[1]
    output = ROOT / '.local/stage-4' / ('concurrent-' + str(uuid.uuid4()))
    output.mkdir()
    (output / 'attempt.json').write_text(json.dumps({'order': str(args.order), 'connections': [p['connectionId'] for p in probes]}))
    labels = ['STAGE4_CONCURRENT_' + str(uuid.uuid4()) for _ in clients]
    opened = [False, False]
    enabled_requested = False

    def native(index, code, phase):
        response = clients[index].rpc('tools/call', {'name': 'execute_code', 'arguments': {'code': code, 'execution_context': 'client'}})
        (output / (phase + '-' + str(index) + '.json')).write_text(json.dumps(response, ensure_ascii=False, indent=2))
        answers = [json.loads(b['text']) for b in response.get('content', []) if b.get('type') == 'text']
        assert len(answers) == 1 and answers[0].get('success') and not response.get('isError'), 'NATIVE_RESULT_NOT_CONFIRMED_INSPECT_BEFORE_RETRY'
        return answers[0].get('data')

    def lookup(index):
        return '''
ФормаКонкурентнойПроверки = Неопределено;
Для Каждого ОкноКонкурентнойПроверки Из ПолучитьОкна() Цикл
 Для Каждого КандидатКонкурентнойПроверки Из ОкноКонкурентнойПроверки.Содержимое Цикл
  Если КандидатКонкурентнойПроверки.ИмяФормы = "Документ.ЗаказКлиента.Форма.ФормаДокумента" Тогда
   Если КандидатКонкурентнойПроверки.Заголовок = "__LABEL__" Тогда ФормаКонкурентнойПроверки = КандидатКонкурентнойПроверки; КонецЕсли;
  КонецЕсли;
 КонецЦикла;
КонецЦикла;
Если ФормаКонкурентнойПроверки = Неопределено Тогда ВызватьИсключение "OWN_FORM_NOT_FOUND"; КонецЕсли;
'''.replace('__LABEL__', labels[index])

    settings = (ROOT / 'tests/stage1/stage4_settings_update.bsl').read_text()
    try:
        admin.execute('ProjectId = 36;\n' + settings)
        for index in range(2):
            native(index, '''
ЗаказКонкурентнойПроверки = ПолучитьСсылкуПоТипуИUUID("Документ", "ЗаказКлиента", "__ORDER__");
ФормаКонкурентнойПроверки = ПолучитьФорму("Документ.ЗаказКлиента.Форма.ФормаДокумента", Новый Структура("Ключ", ЗаказКонкурентнойПроверки), , Новый УникальныйИдентификатор);
ФормаКонкурентнойПроверки.Заголовок = "__LABEL__"; ФормаКонкурентнойПроверки.Открыть();
Результат = "OWN_FORM_READY";
'''.replace('__ORDER__', str(args.order)).replace('__LABEL__', labels[index]), 'open')
            opened[index] = True
        enabled_requested = True
        admin.execute(GATE.replace('__ENABLED__', 'true'))
        barrier = threading.Barrier(2)

        def invoke(index):
            barrier.wait(timeout=10)
            started = time.monotonic()
            data = native(index, lookup(index) + '''
ПередКонкурентнойКомандой = Расш1_СервисRESTБ24.ДоСоздания(ФормаКонкурентнойПроверки.Объект.Ссылка, Ложь, Ложь);
ФормаКонкурентнойПроверки.Расш1_СоздатьЗадачу(Неопределено);
Результат = Новый Структура("beforeAction,commandReturned", ПередКонкурентнойКомандой.Действие, Истина);
''', 'command')
            return {'started': started, 'finished': time.monotonic(), 'userId': user_ids[index], **data}

        with ThreadPoolExecutor(2) as executor:
            commands = list(executor.map(invoke, range(2)))
        deadline = time.monotonic() + 60
        state = admin.execute(read)
        while state['state'] in ('Нет', 'Резерв', 'ОтправкаНачата', 'ЗадачаИзвестна') and time.monotonic() < deadline:
            time.sleep(1)
            state = admin.execute(read)
        assert state['state'] == 'Связана' and state['taskId'], 'RESULT_NOT_LINKED_INSPECT_BEFORE_RETRY'
        evidence = admin.execute('''
ЗаказКонкурентнойПроверки = Документы.ЗаказКлиента.ПолучитьСсылку(Новый УникальныйИдентификатор("__ORDER__"));
ЗапросКонкурентнойПроверки = Новый Запрос("ВЫБРАТЬ ОперацияId, ИнициаторId, СнимокJSON, Портал ИЗ РегистрСведений.Расш1_ЖурналОперацийБ24 ГДЕ Заказ = &Заказ");
ЗапросКонкурентнойПроверки.УстановитьПараметр("Заказ", ЗаказКонкурентнойПроверки); СтрокиКонкурентнойПроверки = ЗапросКонкурентнойПроверки.Выполнить().Выгрузить();
Если СтрокиКонкурентнойПроверки.Количество() <> 1 Тогда ВызватьИсключение "MORE_THAN_ONE_OPERATION"; КонецЕсли;
ОперацияКонкурентнойПроверки = СтрокиКонкурентнойПроверки[0];
МаркерКонкурентнойПроверки = Расш1_RESTБ24Сервер.ДанныеJSON(ОперацияКонкурентнойПроверки.СнимокJSON).Получить("fields").Получить("XML_ID");
ПоискКонкурентнойПроверки = Расш1_RESTБ24Сервер.НайтиПоМаркеру(ОперацияКонкурентнойПроверки.Портал, МаркерКонкурентнойПроверки);
Если ПоискКонкурентнойПроверки.Состояние <> "ЗадачаИзвестна" Тогда ВызватьИсключение "UNIQUE_TASK_NOT_CONFIRMED"; КонецЕсли;
ЗапросКонкурентнойПроверки = Новый Запрос("ВЫБРАТЬ ИнициаторId, СообщениеId ИЗ РегистрСведений.Расш1_УведомленияБ24 ГДЕ ОперацияId = &ID");
ЗапросКонкурентнойПроверки.УстановитьПараметр("ID", ОперацияКонкурентнойПроверки.ОперацияId); АдресатыКонкурентнойПроверки = ЗапросКонкурентнойПроверки.Выполнить().Выбрать(); ЧислоАдресатовКонкурентнойПроверки = 0; ЧислоУспеховКонкурентнойПроверки = 0;
Пока АдресатыКонкурентнойПроверки.Следующий() Цикл
 Если АдресатыКонкурентнойПроверки.ИнициаторId <> ОперацияКонкурентнойПроверки.ИнициаторId Тогда ВызватьИсключение "OTHER_USER_NOTIFIED"; КонецЕсли;
 ЧислоАдресатовКонкурентнойПроверки = ЧислоАдресатовКонкурентнойПроверки + 1;
 Если АдресатыКонкурентнойПроверки.СообщениеId = ОперацияКонкурентнойПроверки.ОперацияId + ":success" Тогда ЧислоУспеховКонкурентнойПроверки = ЧислоУспеховКонкурентнойПроверки + 1; КонецЕсли;
КонецЦикла;
Результат = Новый Структура("operationId,initiatorId,taskId,notificationCount,successNotificationCount", ОперацияКонкурентнойПроверки.ОперацияId, ОперацияКонкурентнойПроверки.ИнициаторId, ПоискКонкурентнойПроверки.ЗадачаId, ЧислоАдресатовКонкурентнойПроверки, ЧислоУспеховКонкурентнойПроверки);
'''.replace('__ORDER__', str(args.order)))
        assert evidence['taskId'] == state['taskId'] and evidence['operationId'] == state['operationId']
        (output / 'evidence.json').write_text(json.dumps({'commands': commands, 'evidence': evidence}, ensure_ascii=False, indent=2))
        assert evidence['initiatorId'] in [c['userId'] for c in commands] and evidence['successNotificationCount'] == 1
        overlaps = max(c['started'] for c in commands) < min(c['finished'] for c in commands)
        result = {'passed': True, 'overlappingRequests': overlaps, 'beforeActions': [c['beforeAction'] for c in commands], 'oneOperationAndTask': True, **evidence}
        (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        if enabled_requested:
            admin.execute(GATE.replace('__ENABLED__', 'false'))
        assert not admin.execute(read)['creationEnabled'], 'REST_DISABLE_NOT_CONFIRMED'
        admin.execute('ProjectId = 6;\n' + settings)
        for index in range(2):
            if opened[index]:
                native(index, lookup(index) + 'Если ФормаКонкурентнойПроверки.Модифицированность Тогда ВызватьИсключение "UNSAVED_EDITS_PRESERVED"; КонецЕсли; ФормаКонкурентнойПроверки.Закрыть(); Результат = "OWN_FORM_CLOSED";', 'close')
        print('REST_DISABLED_TARGET_PROJECT_6_RESTORED', flush=True)


if __name__ == '__main__':
    main()
