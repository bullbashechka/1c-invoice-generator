"""Exercise actual posting/closing continuations; physical question input is separate."""
import argparse
import json
import time
import uuid
from pathlib import Path
from run_register_concurrency import MCP, PROBE
from run_stage3_native_create import READ

ROOT = Path(__file__).resolve().parents[2]
CHECK = '''
Открытых = 0;
Для Каждого ОкноПроверки Из ПолучитьОкна() Цикл
    Для Каждого ПроверяемаяФорма Из ОкноПроверки.Содержимое Цикл
        Если ПроверяемаяФорма.ИмяФормы = "Документ.ЗаказКлиента.Форма.ФормаДокумента"
            И Лев(ПроверяемаяФорма.Заголовок, СтрДлина("__FORM_PREFIX__")) = "__FORM_PREFIX__" Тогда Открытых = Открытых + 1; КонецЕсли;
    КонецЦикла;
КонецЦикла;
Результат = Открытых;
'''

def client(c, code):
    result = c.rpc('tools/call', {'name':'execute_code','arguments':{'code':code,'execution_context':'client'}})
    (ROOT/'.local/stage-3/reminder-last-client.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    answers = [json.loads(b['text']) for b in result.get('content',[]) if b.get('type') == 'text']
    assert len(answers) == 1 and answers[0].get('success'), 'CLIENT_RESULT_NOT_CONFIRMED'
    return answers[0]['data']

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--order', required=True, type=uuid.UUID)
    args = p.parse_args()
    c = MCP(6004)
    profile = json.loads((ROOT/'.local/stage-1/one-c-profile.json').read_text(encoding='utf-8'))
    assert c.execute(PROBE)['baseFingerprint'] == profile['baseFingerprint'], 'DEMO_MISMATCH'
    read = READ.replace('__UUID__', str(args.order))
    before = c.execute(read)
    assert before['own'] and before['posted'] and not before['creationEnabled'], 'OWN_POSTED_ORDER_REST_DISABLED_REQUIRED'
    prefix = 'STAGE3_REMINDER_' + str(uuid.uuid4()) + '_'
    code = (ROOT/'tests/stage1/stage3_reminder_continuation_contract.bsl').read_text(encoding='utf-8').replace('__ORDER_UUID__', str(args.order)).replace('__FORM_PREFIX__', prefix)
    assert client(c, code) == 'STAGE3_REMINDER_CONTINUATIONS_REQUESTED'
    check = CHECK.replace('__FORM_PREFIX__', prefix)
    deadline = time.monotonic() + 15
    opened = client(c, check)
    while opened and time.monotonic() < deadline:
        time.sleep(1)
        opened = client(c, check)
    after = c.execute(read)
    passed = opened == 0 and after == before
    report = {'passed':passed, 'remainingForms':opened, 'integrationUnchanged':after == before, 'physicalDialogInputVerified':False}
    target = ROOT/'.local/stage-3/reminder-continuation-result.json'
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report))
    return 0 if passed else 1

if __name__ == '__main__':
    raise SystemExit(main())
