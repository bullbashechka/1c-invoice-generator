"""One authorized demo add; discard response, hold failed reads >5min, recover.
Only the prepare phase may call add. Resume uses the persisted intent and never sends.
Only the nonsecret portal setting is temporarily made inconsistent with the webhook.
No secret is copied or changed. This simulates unavailable configured reads, not TCP loss.
"""
import argparse
import json
import time
import uuid
from pathlib import Path
from run_register_concurrency import MCP, PROBE

ROOT = Path(__file__).resolve().parents[2]

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--order', type=uuid.UUID, required=True)
    p.add_argument('--create-one-task', action='store_true')
    p.add_argument('--resume', type=Path)
    args = p.parse_args()
    c = MCP(6004)
    assert c.execute(PROBE)['baseFingerprint'] == json.loads((ROOT/'.local/stage-1/one-c-profile.json').read_text())['baseFingerprint'], 'DEMO_MISMATCH'
    out = args.resume or ROOT/'.local/stage-4/response-loss'/str(uuid.uuid4())
    out.mkdir(parents=True, exist_ok=True)
    if args.resume:
        intent = json.loads((out/'intent.json').read_text())
        assert intent['order'] == str(args.order), 'ORDER_MISMATCH'
    else:
        assert args.create_one_task, 'EXPLICIT_ONE_TASK_AUTHORIZATION_REQUIRED'
        intent = {'order':str(args.order),'phase':'before-prepare'}
        (out/'intent.json').write_text(json.dumps(intent))
    intent.setdefault('settingsKey','stage4-json-'+str(uuid.uuid4()))
    (out/'intent.json').write_text(json.dumps(intent))
    guard = '''
Если Не ПравоДоступа("Администрирование", Метаданные) Или СтрНайти(НРег(СтрокаСоединенияИнформационнойБазы()), "ка демо") = 0 Тогда ВызватьИсключение "DEMO_ONLY"; КонецЕсли;
ЗаказПроверки = Документы.ЗаказКлиента.ПолучитьСсылку(Новый УникальныйИдентификатор("__ORDER__"));
Если Лев(ЗаказПроверки.Комментарий, 14) <> "STAGE3_NATIVE_" Или Не ЗаказПроверки.Проведен Тогда ВызватьИсключение "OWN_POSTED_ORDER_REQUIRED"; КонецЕсли;
Настройка = РегистрыСведений.Расш1_НастройкиОбменаБ24.СоздатьМенеджерЗаписи(); Настройка.Ключ = "rest"; Настройка.Прочитать();
Допуск = РегистрыСведений.Расш1_НастройкиОбменаБ24.СоздатьМенеджерЗаписи(); Допуск.Ключ = "stage2Enabled"; Допуск.Прочитать();
РезервJSON = РегистрыСведений.Расш1_НастройкиОбменаБ24.СоздатьМенеджерЗаписи(); РезервJSON.Ключ = "__JSONKEY__"; РезервJSON.Прочитать();
'''.replace('__ORDER__', str(args.order)).replace('__JSONKEY__',intent['settingsKey'])
    def server(name, code):
        (out/(name+'-request.bsl')).write_text(guard+code,encoding='utf-8')
        raw = c.rpc('tools/call', {'name':'execute_code','arguments':{'code':guard+code,'execution_context':'server'}})
        (out/(name+'.json')).write_text(json.dumps(raw,ensure_ascii=False))
        a=[json.loads(b['text']) for b in raw.get('content',[]) if b.get('type')=='text']
        assert len(a)==1 and a[0].get('success'), 'NATIVE_'+name+'_NOT_CONFIRMED_INSPECT_PRIVATE_RESULT'
        return a[0]['data']
    def client(name, code):
        (out/(name+'-request.bsl')).write_text(code,encoding='utf-8')
        raw=c.rpc('tools/call',{'name':'execute_code','arguments':{'code':code,'execution_context':'client'}})
        (out/(name+'.json')).write_text(json.dumps(raw,ensure_ascii=False))
        a=[json.loads(b['text']) for b in raw.get('content',[]) if b.get('type')=='text']
        assert len(a)==1 and a[0].get('success'), 'CLIENT_'+name+'_NOT_CONFIRMED'
        return a[0]['data']
    if not args.resume:
        prep=server('prepare', '''
Если Допуск.Значение = "true" Или Расш1_ОперацииRESTБ24.ПрочитатьСостояние(ЗаказПроверки).Состояние <> "Нет" Тогда ВызватьИсключение "FREE_ORDER_REST_OFF_REQUIRED"; КонецЕсли;
НачатьТранзакцию();
Попытка
    РезервJSON.Ключ = "__JSONKEY__"; РезервJSON.Значение = Настройка.Значение; РезервJSON.Секрет = ""; РезервJSON.Записать();
    Данные = Расш1_RESTБ24Сервер.ДанныеJSON(Настройка.Значение); Данные.Вставить("groupId", 36);
    Настройка.Значение = Расш1_RESTБ24Сервер.JSONСтрока(Данные); Настройка.Записать();
    Допуск.Значение = "true"; Допуск.Записать();
    О = Расш1_ОперацииRESTБ24.Зарезервировать(ЗаказПроверки, "stage4-response-loss");
    Ж = РегистрыСведений.Расш1_ЖурналОперацийБ24.СоздатьМенеджерЗаписи(); Ж.ОперацияId = О.ОперацияId; Ж.Прочитать();
    Ж.СледующаяПроверкаUTC = ТекущаяУниверсальнаяДата() + 86400; Ж.Записать();
    ЗафиксироватьТранзакцию();
Исключение ОтменитьТранзакцию(); ВызватьИсключение; КонецПопытки;
Результат = Новый Структура("operation,token", О.ОперацияId, ЗаказПроверки.Комментарий);
'''.replace('__JSONKEY__',intent['settingsKey']))
        intent.update(prep); intent['phase']='reserved-no-send-yet'
        (out/'intent.json').write_text(json.dumps(intent))
    if intent['phase']=='reserved-no-send-yet':
        assert args.create_one_task, 'EXPLICIT_SEND_RESERVED_REQUIRED'
        discarded=server('send-once', '''
О = Расш1_ОперацииRESTБ24.ПрочитатьСостояние(ЗаказПроверки);
Если О.Состояние <> "Резерв" Или О.ОперацияId <> "__OP__" Тогда ВызватьИсключение "ONLY_UNSENT_RESERVATION"; КонецЕсли;
Если Не РезервJSON.Выбран() Тогда
РезервJSON.Ключ = "__JSONKEY__"; РезервJSON.Значение = Настройка.Значение; РезервJSON.Секрет = ""; РезервJSON.Записать();
КонецЕсли;
Если Не ПустаяСтрока(РезервJSON.Секрет) Тогда ВызватьИсключение "NONSECRET_JSON_ONLY"; КонецЕсли;
Допуск.Значение = "true"; Допуск.Записать();
Сбой = Ложь;
Попытка Расш1_ОперацииRESTБ24.ОтправитьОперацию(О.ОперацияId, "after-response"); Исключение Сбой = СтрНайти(ОписаниеОшибки(), "KA2_DIAGNOSTIC_RESPONSE_DISCARDED") > 0; КонецПопытки;
Допуск.Значение = "false"; Допуск.Записать();
Если Не Сбой Тогда ВызватьИсключение "REAL_RESPONSE_NOT_DISCARDED_INSPECT_BEFORE_RETRY"; КонецЕсли;
// Несовпадение портала блокирует чтение до сети; секрет не меняется.
Данные = Расш1_RESTБ24Сервер.ДанныеJSON(Настройка.Значение); Данные.Вставить("portal", "https://stage4-unavailable.invalid");
Настройка.Значение = Расш1_RESTБ24Сервер.JSONСтрока(Данные); Настройка.Записать();
Расш1_ОперацииRESTБ24.ВосстановитьОперацию(О.ОперацияId);
С = Расш1_ОперацииRESTБ24.ПрочитатьСостояние(ЗаказПроверки);
Если С.Состояние <> "Неизвестно" Или Не ПустаяСтрока(С.ЗадачаId) Тогда ВызватьИсключение "UNKNOWN_WITHOUT_LINK_REQUIRED"; КонецЕсли;
Результат = Истина;
'''.replace('__OP__',intent['operation']).replace('__JSONKEY__',intent['settingsKey']))
        assert discarded
        intent['phase']='response-discarded-reads-disabled';intent['holdStart']=time.time()
        (out/'intent.json').write_text(json.dumps(intent))
    assert intent['phase']=='response-discarded-reads-disabled','RESUME_HOLD_PHASE_REQUIRED_NO_SEND'
    fingerprint=(ROOT/'tests/stage1/order_data_fingerprint.bsl').read_text().replace('__ORDER_UUID__',str(args.order))
    before_hash=c.execute(fingerprint)
    check='''
Если Допуск.Значение = "true" Или Расш1_RESTБ24Сервер.ДанныеJSON(Настройка.Значение).Получить("portal") <> "https://stage4-unavailable.invalid" Тогда ВызватьИсключение "HOLD_GUARD_FAILED"; КонецЕсли;
С = Расш1_ОперацииRESTБ24.ПрочитатьСостояние(ЗаказПроверки);
Ж = РегистрыСведений.Расш1_ЖурналОперацийБ24.СоздатьМенеджерЗаписи(); Ж.ОперацияId = "__OP__"; Ж.Прочитать();
З = Новый Запрос("ВЫБРАТЬ КОЛИЧЕСТВО(*) КАК К ИЗ РегистрСведений.Расш1_ЖурналОперацийБ24 ГДЕ Заказ = &Заказ"); З.УстановитьПараметр("Заказ", ЗаказПроверки); В = З.Выполнить().Выбрать(); В.Следующий();
Если С.ОперацияId <> Ж.ОперацияId Или С.Состояние <> "Неизвестно" Или Не ПустаяСтрока(С.ЗадачаId) Или В.К <> 1 Или Не Ж.ОтправкаНачата Тогда ВызватьИсключение "HOLD_STATE_CHANGED"; КонецЕсли;
Результат = Новый Структура("state,operationCount,snapshot", С.Состояние, В.К, Ж.СнимокJSON);
'''.replace('__OP__',intent['operation'])
    before=server('hold-before',check)
    ui=(ROOT/'tests/stage1/stage3_state_form_contract.bsl').read_text()
    for key,value in {'__ORDER_UUID__':str(args.order),'__TOKEN__':intent['token'],'__OPERATION__':intent['operation'],'__STATE__':'Неизвестно','__TEXT__':'Проверяем, создана ли задача'}.items(): ui=ui.replace(key,value)
    client('unknown-form',ui)
    while time.time()-intent['holdStart']<310:
        now=server('hold-read',check)
        assert now==before,'SNAPSHOT_CHANGED'
        assert c.execute(fingerprint)==before_hash,'ORDER_CHANGED_DURING_HOLD'
        print(json.dumps({'holdingUnknownSeconds':round(time.time()-intent['holdStart']),'operationCount':1}),flush=True)
        time.sleep(30)
    assert server('hold-after',check)==before
    # Close only the clean own form; the durable attempt remains independent.
    close='''
Для Каждого О Из ПолучитьОкна() Цикл Для Каждого Ф Из О.Содержимое Цикл
Если Ф.ИмяФормы = "Документ.ЗаказКлиента.Форма.ФормаДокумента" Тогда
Если ЗначениеЗаполнено(Ф.Объект.Ссылка) И Строка(Ф.Объект.Ссылка.УникальныйИдентификатор()) = "__ORDER__" Тогда
Если Ф.Модифицированность Тогда ВызватьИсключение "OWN_FORM_DIRTY"; КонецЕсли; Ф.Закрыть();
КонецЕсли; КонецЕсли; КонецЦикла; КонецЦикла;
Результат = "OWN_FORM_CLOSED";
'''.replace('__ORDER__',str(args.order))
    client('close-pending-form',close)
    restore=server('restore-auth-and-known', '''
Ж = РегистрыСведений.Расш1_ЖурналОперацийБ24.СоздатьМенеджерЗаписи(); Ж.ОперацияId = "__OP__"; Ж.Прочитать();
Ж.СледующаяПроверкаUTC = ТекущаяУниверсальнаяДата() + 86400; Ж.Записать();
Если Не РезервJSON.Выбран() Или Не ПустаяСтрока(РезервJSON.Секрет) Тогда ВызватьИсключение "NONSECRET_JSON_BACKUP_REQUIRED"; КонецЕсли;
Настройка.Значение = РезервJSON.Значение; Настройка.Записать(); РезервJSON.Удалить();
СнимокДо = Ж.СнимокJSON; Маркер = Расш1_RESTБ24Сервер.ДанныеJSON(Ж.СнимокJSON).Получить("fields").Получить("XML_ID");
Ответ = Расш1_RESTБ24Сервер.НайтиПоМаркеру(Ж.Портал, Маркер);
Если Ответ.Состояние <> "ЗадачаИзвестна" Тогда ВызватьИсключение "EXACT_ONE_TASK_NOT_FOUND"; КонецЕсли;
Расш1_ОперацииRESTБ24.СохранитьОтвет(Ж.ОперацияId, Ответ);
Ж.Прочитать(); Ж.СледующаяПроверкаUTC = ТекущаяУниверсальнаяДата() + 86400; Ж.Записать();
// Отказ внешней транзакции откатывает только запись связи, уже известный ID устойчив.
НачатьТранзакцию();
Попытка Расш1_ОперацииRESTБ24.ЗаписатьСвязь(Ж.ОперацияId); ОтменитьТранзакцию();
Исключение ОтменитьТранзакцию(); ВызватьИсключение; КонецПопытки;
С = Расш1_ОперацииRESTБ24.ПрочитатьСостояние(ЗаказПроверки); Ж.Прочитать();
Если С.Состояние <> "ЗадачаИзвестна" Или С.ЗадачаId <> Ответ.ЗадачаId Или Ж.СнимокJSON <> СнимокДо Тогда ВызватьИсключение "KNOWN_ID_NOT_DURABLE"; КонецЕсли;
Результат = Новый Структура("taskId,operationId", Ответ.ЗадачаId, Ж.ОперацияId);
'''.replace('__OP__',intent['operation']))
    intent.update(restore);intent['phase']='known-before-link';(out/'intent.json').write_text(json.dumps(intent))
    print(json.dumps({'passedUnknownHold':True,'taskId':restore['taskId'],'knownIdSurvivedLinkRollback':True,'settingsRestored':True,'next':'restart-client-to-measure-recovery'}),flush=True)

if __name__=='__main__':
    main()
