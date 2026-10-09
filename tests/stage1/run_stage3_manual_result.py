"""Verify the native manual test against its saved baseline, without writes."""
import base64
import json
from pathlib import Path
from run_register_concurrency import MCP, PROBE

ROOT = Path(__file__).resolve().parents[2]


def main():
    c = MCP(6004)
    profile = json.loads((ROOT / '.local/stage-1/one-c-profile.json').read_text(encoding='utf-8'))
    assert c.execute(PROBE)['baseFingerprint'] == profile['baseFingerprint'], 'DEMO_MISMATCH'
    before = json.loads((ROOT / '.local/stage-3/manual-10058-before.json').read_text(encoding='utf-8'))
    assert before['taskId'] == '10058' and type(before['linkVersion']) is int, 'BASELINE_REJECTED'
    base64.b64decode(before['documentVersion'], validate=True)
    code = (ROOT / 'tests/stage1/stage3_manual_result_contract.bsl').read_text(encoding='utf-8')
    code = code.replace('__DOCUMENT_VERSION__', before['documentVersion']).replace('__LINK_VERSION__', str(before['linkVersion']))
    result = c.rpc('tools/call', {'name': 'execute_code', 'arguments': {'code': code, 'execution_context': 'server'}})
    (ROOT / '.local/stage-3/manual-10058-after.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    answers = [json.loads(b['text']) for b in result.get('content', []) if b.get('type') == 'text']
    passed = len(answers) == 1 and answers[0].get('success') is True and answers[0].get('data') == 'STAGE3_MANUAL_RESULT_GREEN'
    print(json.dumps({'passed': passed, 'orderUnchangedAuditAndHistoryVerified': passed}))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
