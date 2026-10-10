"""Controlled recreation on one own demo order after physical deletion of its link.

The user edits a goods quantity in the native form, confirms Toolkit enable,
then answers the native recreation question. Never retries the command.
"""
import argparse
import json
import time
import uuid
from pathlib import Path

from run_register_concurrency import MCP, PROBE, UnknownOutcome
from run_stage3_native_create import READ, GATE

ROOT = Path(__file__).resolve().parents[2]
CLIENT = '''
НайденаФормаПроверки = Неопределено; КоличествоФормПроверки = 0;
Для Каждого ОкноПроверки Из ПолучитьОкна() Цикл
    Для Каждого ФормаПроверки Из ОкноПроверки.Содержимое Цикл
        Если ФормаПроверки.ИмяФормы = "Документ.ЗаказКлиента.Форма.ФормаДокумента" Тогда
            Если ЗначениеЗаполнено(ФормаПроверки.Объект.Ссылка) Тогда
                Если Строка(ФормаПроверки.Объект.Ссылка.УникальныйИдентификатор()) = "__UUID__"
                    И ФормаПроверки.Заголовок = "__TITLE__"
                    И СтрНайти(ФормаПроверки.Объект.Комментарий, "STAGE3_RECREATE_CANCEL") > 0 Тогда
                    НайденаФормаПроверки = ФормаПроверки; КоличествоФормПроверки = КоличествоФормПроверки + 1;
                КонецЕсли;
            КонецЕсли;
        КонецЕсли;
    КонецЦикла;
КонецЦикла;
Если КоличествоФормПроверки <> 1 Или Не НайденаФормаПроверки.Модифицированность Тогда
    ВызватьИсключение "ONE_OWN_MODIFIED_FORM_REQUIRED";
КонецЕсли;
СуммаФормыПроверки = НайденаФормаПроверки.Объект.СуммаДокумента;
ИтогТоваровПроверки = НайденаФормаПроверки.Объект.Товары.Итог("Сумма");
__COMMAND__
Результат = Новый Структура("formAmount,goodsTotal,commandRequested", СуммаФормыПроверки, ИтогТоваровПроверки, __REQUESTED__);
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--order', required=True, type=uuid.UUID)
    parser.add_argument('--deleted-task', required=True)
    parser.add_argument('--preflight-only', action='store_true')
    parser.add_argument('--retry-not-sent', action='store_true', help='Require a confirmed unsent previous attempt; never retry unknown outcomes')
    args = parser.parse_args()
    assert args.deleted_task.isascii() and args.deleted_task.isdigit(), 'INVALID_TASK_ID'
    c = MCP(6004)
    profile = json.loads((ROOT / '.local/stage-1/one-c-profile.json').read_text(encoding='utf-8'))
    assert c.execute(PROBE)['baseFingerprint'] == profile['baseFingerprint'], 'DEMO_MISMATCH'
    read = READ.replace('__UUID__', str(args.order))
    before = c.execute(read)
    expected_state = 'НеОтправлена' if args.retry_not_sent else 'Нет'
    assert before['own'] and before['posted'] and before['state'] == expected_state and not before['taskId'], 'OWN_UNLINKED_POSTED_ORDER_REQUIRED'
    assert not before['creationEnabled'], 'REST_MUST_START_DISABLED'
    if args.retry_not_sent:
        assert before['operationId'], 'PREVIOUS_UNSENT_OPERATION_REQUIRED'
        previous = c.execute('''
ОперацияПроверки = РегистрыСведений.Расш1_ЖурналОперацийБ24.СоздатьМенеджерЗаписи();
ОперацияПроверки.ОперацияId = "__OPERATION__"; ОперацияПроверки.Прочитать();
Результат = Новый Структура("exists,state,sent", ОперацияПроверки.Выбран(), ОперацияПроверки.Состояние, ОперацияПроверки.ОтправкаНачата);
'''.replace('__OPERATION__', str(uuid.UUID(before['operationId']))))
        assert previous['exists'] and previous['state'] == 'НеОтправлена' and not previous['sent'], 'CONFIRMED_UNSENT_ONLY'
    detail = c.execute('''
ЗаказПроверки = Документы.ЗаказКлиента.ПолучитьСсылку(Новый УникальныйИдентификатор("__UUID__"));
СостояниеПроверки = Расш1_СервисRESTБ24.СостояниеЗаказа(ЗаказПроверки);
ОбъектПроверки = ЗаказПроверки.ПолучитьОбъект();
Результат = Новый Структура("deletedTask,amount,goodsTotal,linkVersion", СостояниеПроверки.ПоследняяУдаленнаяЗадача,
    ОбъектПроверки.СуммаДокумента, ОбъектПроверки.Товары.Итог("Сумма"), СостояниеПроверки.ВерсияСвязи);
'''.replace('__UUID__', str(args.order)))
    assert detail['deletedTask'] == args.deleted_task, 'DELETED_LINK_MISMATCH'
    form_title = ('Проверка повтора после неотправки — №' if args.retry_not_sent
                  else 'Проверка нового создания — прежняя задача №') + args.deleted_task
    client = CLIENT.replace('__UUID__', str(args.order)).replace('__TITLE__', form_title)

    def call_client(command):
        code = client.replace('__COMMAND__', command).replace('__REQUESTED__', 'Истина' if command else 'Ложь')
        response = c.rpc('tools/call', {'name': 'execute_code', 'arguments': {'code': code, 'execution_context': 'client'}})
        blocks = [json.loads(b['text']) for b in response.get('content', []) if b.get('type') == 'text']
        assert len(blocks) == 1 and blocks[0].get('success') is True, 'CLIENT_SCENARIO_FAILED'
        return blocks[0]['data']

    form = call_client('')
    # In this native form, the document total updates on save; edited goods
    # already have recalculated sums. Verify those before the posting command.
    assert form['goodsTotal'] != detail['goodsTotal'], 'GOODS_TOTAL_NOT_CHANGED'
    if args.preflight_only:
        print('RECREATE_CHANGED_AMOUNT_PREFLIGHT_GREEN', flush=True)
        return 0
    pending = c.execute('''
ЗапросПроверки = Новый Запрос("ВЫБРАТЬ Состояние ИЗ РегистрСведений.Расш1_ЖурналОперацийБ24");
СтрокиПроверки = ЗапросПроверки.Выполнить().Выбрать(); Результат = 0;
Пока СтрокиПроверки.Следующий() Цикл
    Если Расш1_ОперацииRESTБ24.Незавершена(СтрокиПроверки.Состояние) Тогда Результат = Результат + 1; КонецЕсли;
КонецЦикла;
''')
    assert pending == 0, 'UNFINISHED_ATTEMPTS_INSPECT_BEFORE_ENABLE'
    output = ROOT / '.local/stage-3/recreate-attempts' / str(time.time_ns())
    output.mkdir(parents=True)
    (output / 'before.json').write_text(json.dumps({'order': str(args.order), 'state': before, 'detail': detail, 'form': form}, ensure_ascii=False, indent=2), encoding='utf-8')
    enable_requested = False
    try:
        enable_requested = True
        print('AWAITING_TOOLKIT_CONFIRMATION_ENABLE_TRUE', flush=True)
        c.execute(GATE.replace('__ENABLED__', 'true'))
        command = call_client('НайденаФормаПроверки.Расш1_СоздатьЗадачу(Неопределено);')
        (output / 'command.json').write_text(json.dumps(command, ensure_ascii=False), encoding='utf-8')
        print('AWAITING_NATIVE_RECREATE_YES', flush=True)
        deadline = time.monotonic() + 180
        state = c.execute(read)
        while time.monotonic() < deadline:
            new_attempt = state['operationId'] != before['operationId']
            if state['taskId'] or (new_attempt and state['state'] in ('НеОтправлена', 'ОтказAPI', 'Конфликт', 'Неизвестно')):
                break
            time.sleep(1)
            state = c.execute(read)
        passed = state['posted'] and state['state'] == 'Связана' and bool(state['taskId']) and state['taskId'] != args.deleted_task
        (output / 'state.json').write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'passed': passed, 'state': state['state'], 'taskId': state['taskId']}, ensure_ascii=False), flush=True)
        return 0 if passed else 1
    except UnknownOutcome:
        print('UNKNOWN_OUTCOME_INSPECT_BEFORE_RETRY', flush=True)
        return 2
    finally:
        if enable_requested:
            print('AWAITING_TOOLKIT_CONFIRMATION_DISABLE_FALSE', flush=True)
            c.execute(GATE.replace('__ENABLED__', 'false'))
        assert not c.execute(read)['creationEnabled'], 'REST_DISABLE_NOT_CONFIRMED'
        print('STAGE2_ENABLED_FALSE', flush=True)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except UnknownOutcome:
        print('MCP_RESPONSE_NOT_CONFIRMED_INSPECT_BEFORE_RETRY', flush=True)
        raise SystemExit(2)
