"""One controlled native create on a previously prepared own demo order."""
import argparse
import json
import time
import uuid
from pathlib import Path
from run_register_concurrency import MCP, PROBE, UnknownOutcome

ROOT = Path(__file__).resolve().parents[2]
READ = '''
Заказ = Документы.ЗаказКлиента.ПолучитьСсылку(Новый УникальныйИдентификатор("__UUID__"));
Объект = Заказ.ПолучитьОбъект();
Состояние = Расш1_СервисRESTБ24.СостояниеЗаказа(Заказ);
Настройка = РегистрыСведений.Расш1_НастройкиОбменаБ24.СоздатьМенеджерЗаписи();
Настройка.Ключ = "stage2Enabled"; Настройка.Прочитать();
Результат = Новый Структура("own,posted,state,taskId,operationId,creationEnabled",
    Лев(Объект.Комментарий, 14) = "STAGE3_NATIVE_", Объект.Проведен,
    Состояние.Состояние, Состояние.ЗадачаId, Состояние.ОперацияId,
    Настройка.Выбран() И Настройка.Значение = "true");
'''
GATE = '''
Настройка = РегистрыСведений.Расш1_НастройкиОбменаБ24.СоздатьМенеджерЗаписи();
Настройка.Ключ = "stage2Enabled"; Настройка.Значение = "__ENABLED__"; Настройка.Записать();
Результат = "GATE_SET";
'''

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--order', required=True, type=uuid.UUID)
    parser.add_argument('--posting-only', action='store_true', help='Keep REST disabled and verify native posting only')
    parser.add_argument('--allow-posted', action='store_true', help='Explicitly test an already posted own order without an existing attempt')
    parser.add_argument('--close-form', action='store_true', help='Close only after a durable pending operation; fast completion is inconclusive')
    args = parser.parse_args()
    assert not (args.close_form and args.posting_only), 'CLOSE_TEST_REQUIRES_CREATION'
    c = MCP(6004)
    profile = json.loads((ROOT / '.local/stage-1/one-c-profile.json').read_text(encoding='utf-8'))
    assert c.execute(PROBE)['baseFingerprint'] == profile['baseFingerprint'], 'DEMO_MISMATCH'
    read = READ.replace('__UUID__', str(args.order))
    before = c.execute(read)
    assert before['own'] and (not before['posted'] or args.allow_posted), 'OWN_UNPOSTED_ORDER_REQUIRED'
    assert before['state'] == 'Нет' and not before['operationId'] and not before['taskId'], 'EXISTING_ATTEMPT_REFUSED'
    assert not before['creationEnabled'], 'REST_MUST_START_DISABLED'
    output = ROOT / '.local/stage-3/native-contract'
    output.mkdir(parents=True, exist_ok=True)
    stamp = str(time.time_ns())
    def progress(phase):
        (output / (stamp + '-progress.json')).write_text(json.dumps({'order':str(args.order),'phase':phase}), encoding='utf-8')
    try:
        if not args.posting_only:
            progress('awaiting_enable_confirmation')
            print('AWAITING_TOOLKIT_CONFIRMATION_ENABLE', flush=True)
            c.execute(GATE.replace('__ENABLED__', 'true'))
        progress('native_command_requested')
        contract = 'stage3_close_during_create.bsl' if args.close_form else 'stage3_native_create_form_contract.bsl'
        code = (ROOT / 'tests/stage1' / contract).read_text(encoding='utf-8').replace('__ORDER_UUID__', str(args.order))
        response = c.rpc('tools/call', {'name':'execute_code', 'arguments':{'code':code,'execution_context':'client'}})
        (output / (stamp + '-client.json')).write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding='utf-8')
        blocks = [json.loads(b['text']) for b in response.get('content',[]) if b.get('type') == 'text']
        data = blocks[0].get('data') if len(blocks) == 1 and blocks[0].get('success') is True else None
        returned = data.get('commandReturned') is True if args.close_form and isinstance(data, dict) else data == 'STAGE3_NATIVE_CREATE_COMMAND_RETURNED'
        state = c.execute(read)
        if returned and not args.posting_only:
            deadline = time.monotonic() + 45
            while state['state'] in ('Нет', 'Резерв', 'ОтправкаНачата', 'ЗадачаИзвестна') and time.monotonic() < deadline:
                time.sleep(1)
                state = c.execute(read)
        (output / (stamp + '-state.json')).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        passed = returned and state['posted'] and (
            state['state'] == 'Нет' and not state['taskId'] and not state['operationId']
            if args.posting_only else state['state'] == 'Связана' and bool(state['taskId']))
        if args.close_form:
            passed = passed and isinstance(data, dict) and data.get('closedWhilePending') is True and data.get('operationId') == state['operationId']
            print(json.dumps({'closeEvidence': data}, ensure_ascii=False), flush=True)
        print(json.dumps({'passed':passed,'postingOnly':args.posting_only,'commandReturned':returned,'posted':state['posted'],'state':state['state'],'taskId':state['taskId']}, ensure_ascii=False), flush=True)
        return 0 if passed else 1
    except UnknownOutcome:
        progress('unknown_inspect_before_retry')
        print('UNKNOWN_OUTCOME_INSPECT_BEFORE_RETRY', flush=True)
        return 2
    finally:
        if not args.posting_only:
            progress('awaiting_disable_confirmation')
            print('AWAITING_TOOLKIT_CONFIRMATION_DISABLE', flush=True)
            c.execute(GATE.replace('__ENABLED__', 'false'))
        assert not c.execute(read)['creationEnabled'], 'REST_DISABLE_NOT_CONFIRMED'
        progress('finished_rest_disabled')
        print('STAGE2_ENABLED_FALSE', flush=True)

if __name__ == '__main__':
    raise SystemExit(main())
