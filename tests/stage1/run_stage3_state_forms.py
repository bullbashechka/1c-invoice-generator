"""Real native form rendering over isolated persisted UI fixtures, without REST.

These are not real uncertain REST outcomes. A future recovery date prevents
background handling; only this test's identifiable records are removed.
The unposted fixture order remains in the demo database for inspection.
"""
import json
from pathlib import Path
import time
import uuid

from run_register_concurrency import MCP, PROBE, UnknownOutcome

ROOT = Path(__file__).resolve().parents[2]
CHECK_OFF = '''
НастройкаПроверки = РегистрыСведений.Расш1_НастройкиОбменаБ24.СоздатьМенеджерЗаписи();
НастройкаПроверки.Ключ = "stage2Enabled"; НастройкаПроверки.Прочитать();
Если НастройкаПроверки.Выбран() И НастройкаПроверки.Значение = "true" Тогда ВызватьИсключение "REST_OFF_REQUIRED"; КонецЕсли;
'''
COUNT = 'ЗапросПроверки = Новый Запрос("ВЫБРАТЬ КОЛИЧЕСТВО(*) КАК Количество ИЗ РегистрСведений.Расш1_ЖурналОперацийБ24"); СтрокиПроверки = ЗапросПроверки.Выполнить().Выбрать(); СтрокиПроверки.Следующий(); Результат = СтрокиПроверки.Количество;'
GUARD = '''
ЗаказПроверки = Документы.ЗаказКлиента.ПолучитьСсылку(Новый УникальныйИдентификатор("__ORDER__"));
Если ЗаказПроверки.Комментарий <> "__TOKEN__" Или ЗаказПроверки.Проведен Тогда ВызватьИсключение "OWN_UNPOSTED_FIXTURE_REQUIRED"; КонецЕсли;
ОперацияПроверки = РегистрыСведений.Расш1_ЖурналОперацийБ24.СоздатьМенеджерЗаписи();
ОперацияПроверки.ОперацияId = "__OPERATION__"; ОперацияПроверки.Прочитать();
Если Не ОперацияПроверки.Выбран() Или ОперацияПроверки.Заказ <> ЗаказПроверки
    Или ОперацияПроверки.СнимокJSON <> "__TOKEN__" Или ОперацияПроверки.ОтправкаНачата Тогда ВызватьИсключение "ONLY_UI_FIXTURE_NO_SEND"; КонецЕсли;
ЗапросПроверки = Новый Запрос("ВЫБРАТЬ Заказ ИЗ РегистрСведений.Расш1_СвязиЗаказовБ24 ГДЕ Заказ = &Заказ");
ЗапросПроверки.УстановитьПараметр("Заказ", ЗаказПроверки);
Если Не ЗапросПроверки.Выполнить().Пустой() Тогда ВызватьИсключение "FIXTURE_MUST_HAVE_NO_LINK"; КонецЕсли;
'''


def main():
    c = MCP(6004)
    assert c.execute(PROBE)['baseFingerprint'] == json.loads((ROOT / '.local/stage-1/one-c-profile.json').read_text())['baseFingerprint'], 'DEMO_MISMATCH'
    before_count = c.execute(CHECK_OFF + COUNT)
    token = 'STAGE3_UI_FIXTURE_' + str(uuid.uuid4())
    operation = str(uuid.uuid4())
    out = ROOT / '.local/stage-3/state-forms' / str(time.time_ns())
    out.mkdir(parents=True)
    (out / 'intent.json').write_text(json.dumps({'token': token, 'operation': operation, 'beforeCount': before_count}))
    # No real operation has this marker, portal, or snapshot. Never use the
    # fixture to infer REST recovery correctness or a real task's existence.
    setup = CHECK_OFF + '''
ИсточникПроверки = Документы.ЗаказКлиента.ПолучитьСсылку(Новый УникальныйИдентификатор("cbbf15da-c47c-11f1-9797-7ef9832e8e54")).ПолучитьОбъект();
Если Лев(ИсточникПроверки.Комментарий, 20) <> "STAGE3_NATIVE_CLOSE_" Тогда ВызватьИсключение "OWN_SOURCE_REQUIRED"; КонецЕсли;
НачатьТранзакцию();
Попытка
    КопияПроверки = ИсточникПроверки.Скопировать(); КопияПроверки.Номер = "";
    КопияПроверки.Дата = ТекущаяДатаСеанса(); КопияПроверки.Комментарий = "__TOKEN__";
    КопияПроверки.Записать(РежимЗаписиДокумента.Запись);
    ОперацияПроверки = РегистрыСведений.Расш1_ЖурналОперацийБ24.СоздатьМенеджерЗаписи();
    ОперацияПроверки.ОперацияId = "__OPERATION__"; ОперацияПроверки.Заказ = КопияПроверки.Ссылка;
    ОперацияПроверки.СнимокJSON = "__TOKEN__"; ОперацияПроверки.Портал = "stage3-ui-fixture.invalid";
    ОперацияПроверки.ИнициаторId = Строка(ПользователиИнформационнойБазы.ТекущийПользователь().УникальныйИдентификатор);
    ОперацияПроверки.СеансId = Строка(НомерСеансаИнформационнойБазы());
    ОперацияПроверки.НачалоUTC = ТекущаяУниверсальнаяДата() - 86400;
    ОперацияПроверки.СледующаяПроверкаUTC = ТекущаяУниверсальнаяДата() + 604800;
    ОперацияПроверки.Состояние = "Резерв"; ОперацияПроверки.ОтправкаНачата = Ложь; ОперацияПроверки.Записать();
    СостояниеПроверки = РегистрыСведений.Расш1_СостояниеЗаказовБ24.СоздатьМенеджерЗаписи();
    СостояниеПроверки.Заказ = КопияПроверки.Ссылка; СостояниеПроверки.ОперацияId = ОперацияПроверки.ОперацияId; СостояниеПроверки.Записать();
    ЗафиксироватьТранзакцию();
Исключение ОтменитьТранзакцию(); ВызватьИсключение; КонецПопытки;
Результат = Новый Структура("order,number", Строка(КопияПроверки.Ссылка.УникальныйИдентификатор()), КопияПроверки.Номер);
'''
    setup = setup.replace('__TOKEN__', token).replace('__OPERATION__', operation)
    fixture = c.execute(setup)
    (out / 'fixture.json').write_text(json.dumps(fixture, ensure_ascii=False))
    order = str(uuid.UUID(fixture['order']))
    guard = CHECK_OFF + GUARD.replace('__ORDER__', order).replace('__TOKEN__', token).replace('__OPERATION__', operation)
    fingerprint = (ROOT / 'tests/stage1/order_data_fingerprint.bsl').read_text().replace('__ORDER_UUID__', order)
    before_hash = c.execute(fingerprint)
    passed = False
    cleanup_safe = True
    try:
        for state, text in [('Резерв', 'Создаём задачу'), ('ОтправкаНачата', 'Создаём задачу'), ('Неизвестно', 'Проверяем, создана ли задача'), ('ЗадачаИзвестна', 'Задача №900000001 создана. Связь с заказом ещё не сохранена')]:
            update = guard + '''
ОперацияПроверки.Состояние = "__STATE__";
ОперацияПроверки.ЗадачаId = "__TASK__"; ОперацияПроверки.Записать();
Результат = Истина;
'''
            c.execute(update.replace('__STATE__', state).replace('__TASK__', '900000001' if state == 'ЗадачаИзвестна' else ''))
            client = (ROOT / 'tests/stage1/stage3_state_form_contract.bsl').read_text()
            for key, value in {'__ORDER_UUID__': order, '__TOKEN__': token, '__OPERATION__': operation, '__STATE__': state, '__TEXT__': text}.items():
                client = client.replace(key, value)
            raw = c.rpc('tools/call', {'name': 'execute_code', 'arguments': {'code': client, 'execution_context': 'client'}})
            (out / (state + '.json')).write_text(json.dumps(raw, ensure_ascii=False))
            answers = [json.loads(b['text']) for b in raw.get('content', []) if b.get('type') == 'text']
            assert len(answers) == 1 and answers[0].get('success') is True and answers[0].get('data') == 'STAGE3_STATE_FORM_GREEN:' + state, 'UI_STATE_NOT_GREEN'
            if state == 'Неизвестно':
                print('UNKNOWN_FORM_OPEN_WAITING_32_SECONDS', flush=True)
                time.sleep(32)
                check = '''
КоличествоФормПроверки = 0;
Для Каждого ОкноПроверки Из ПолучитьОкна() Цикл
    Для Каждого ФормаПроверки Из ОкноПроверки.Содержимое Цикл
        Если ФормаПроверки.ИмяФормы = "Документ.ЗаказКлиента.Форма.ФормаДокумента" Тогда
            Если ЗначениеЗаполнено(ФормаПроверки.Объект.Ссылка) Тогда
                Если Строка(ФормаПроверки.Объект.Ссылка.УникальныйИдентификатор()) = "__ORDER__" Тогда
                    КоличествоФормПроверки = КоличествоФормПроверки + 1;
                    Если ФормаПроверки.Расш1_ТекстБ24 <> "Проверяем, создана ли задача" Или ФормаПроверки.Расш1_ЗадачаСоздана Тогда ВызватьИсключение "UNKNOWN_STATE_CHANGED_BY_TIMER"; КонецЕсли;
                    ФормаПроверки.Расш1_ОбновитьБ24(Неопределено);
                    Если ФормаПроверки.Расш1_ТекстБ24 <> "Проверяем, создана ли задача" Или ФормаПроверки.Расш1_НомерЗадачиБ24 <> "" Тогда ВызватьИсключение "UNKNOWN_REFRESH_CHANGED_STATE"; КонецЕсли;
                    ФормаПроверки.Закрыть();
                КонецЕсли;
            КонецЕсли;
        КонецЕсли;
    КонецЦикла;
КонецЦикла;
Если КоличествоФормПроверки <> 1 Тогда ВызватьИсключение "ONE_UNKNOWN_FIXTURE_FORM_REQUIRED"; КонецЕсли;
Результат = "UNKNOWN_TIMER_STATE_GREEN";
'''.replace('__ORDER__', order)
                timed = c.rpc('tools/call', {'name': 'execute_code', 'arguments': {'code': check, 'execution_context': 'client'}})
                (out / 'unknown-timer.json').write_text(json.dumps(timed, ensure_ascii=False))
                timed_answers = [json.loads(b['text']) for b in timed.get('content', []) if b.get('type') == 'text']
                assert len(timed_answers) == 1 and timed_answers[0].get('success') is True and timed_answers[0].get('data') == 'UNKNOWN_TIMER_STATE_GREEN', 'UNKNOWN_TIMER_NOT_GREEN'
            assert c.execute(guard + COUNT) == before_count + 1, 'NEW_OPERATION_CREATED'
            print('STATE_FORM_GREEN:' + state, flush=True)
        assert c.execute(fingerprint) == before_hash, 'FIXTURE_ORDER_CHANGED'
        passed = True
    except UnknownOutcome:
        # The remote client/server may still be executing the request. Removing
        # its state now would change the meaning of a late native command.
        cleanup_safe = False
        raise
    finally:
        # Only artificial, never-sent records with our exact marker; never
        # delete real unknown operations or links, even on a test failure.
        cleanup = guard + '''
НачатьТранзакцию();
Попытка
    СостояниеПроверки = РегистрыСведений.Расш1_СостояниеЗаказовБ24.СоздатьМенеджерЗаписи();
    СостояниеПроверки.Заказ = ЗаказПроверки; СостояниеПроверки.Прочитать();
    Если СостояниеПроверки.ОперацияId <> ОперацияПроверки.ОперацияId Тогда ВызватьИсключение "FIXTURE_STATE_CHANGED"; КонецЕсли;
    СостояниеПроверки.Удалить(); ОперацияПроверки.Удалить(); ЗафиксироватьТранзакцию();
Исключение ОтменитьТранзакцию(); ВызватьИсключение; КонецПопытки;
Результат = Истина;
'''
        if cleanup_safe:
            c.execute(cleanup)
            clean = c.execute(CHECK_OFF + COUNT) == before_count
            (out / 'result.json').write_text(json.dumps({'passed': passed, 'fixtureRecordsRemoved': clean, 'fixtureOrder': fixture}, ensure_ascii=False))
            assert clean, 'FIXTURE_CLEANUP_NOT_CONFIRMED'
            print('UI_FIXTURE_RECORDS_REMOVED_REST_OFF', flush=True)
        else:
            (out / 'result.json').write_text(json.dumps({'passed': False, 'fixtureRecordsRemoved': False, 'requiresReconciliation': True, 'fixtureOrder': fixture}, ensure_ascii=False))
            print('UI_FIXTURE_LEFT_FOR_RECONCILIATION_NO_RETRY', flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except UnknownOutcome:
        print('UNKNOWN_MCP_OUTCOME_INSPECT_PRIVATE_INTENT_BEFORE_RETRY', flush=True)
        raise SystemExit(2)
