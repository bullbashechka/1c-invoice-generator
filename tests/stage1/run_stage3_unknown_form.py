"""Read-only actual form check on the documented legacy unknown demo operation."""
import json
from pathlib import Path
from run_register_concurrency import MCP, PROBE

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = '''
ЗаказПроверки = Документы.ЗаказКлиента.ПолучитьСсылку(Новый УникальныйИдентификатор("109e05b5-fdd2-11e6-b139-0050568b35ac"));
Результат = Новый Структура("version,state", Base64Строка(ЗаказПроверки.ПолучитьОбъект().ВерсияДанных),
    Расш1_СервисRESTБ24.СостояниеЗаказа(ЗаказПроверки));
'''


def main():
    c = MCP(6004)
    profile = json.loads((ROOT / '.local/stage-1/one-c-profile.json').read_text(encoding='utf-8'))
    assert c.execute(PROBE)['baseFingerprint'] == profile['baseFingerprint'], 'DEMO_MISMATCH'
    before = c.execute(SNAPSHOT)
    assert before['state']['Состояние'] == 'Неизвестно', 'UNKNOWN_DEMO_OPERATION_REQUIRED'
    code = (ROOT / 'tests/stage1/stage3_unknown_form_contract.bsl').read_text(encoding='utf-8')
    response = c.rpc('tools/call', {'name': 'execute_code', 'arguments': {'code': code, 'execution_context': 'client'}})
    output = ROOT / '.local/stage-3/unknown-form-result.json'
    output.write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding='utf-8')
    unchanged = before == c.execute(SNAPSHOT)
    answers = [json.loads(b['text']) for b in response.get('content', []) if b.get('type') == 'text']
    passed = unchanged and len(answers) == 1 and answers[0].get('success') is True and answers[0].get('data') == 'STAGE3_UNKNOWN_FORM_GREEN'
    print(json.dumps({'passed': passed, 'persistedOrderAndStateUnchanged': unchanged, 'reopenVerified': passed}))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
