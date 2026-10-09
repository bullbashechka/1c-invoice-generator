"""Run a full installed BSL contract, after checking the demo fingerprint."""
import argparse
import json
from pathlib import Path
from run_register_concurrency import MCP, PROBE, UnknownOutcome

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('contract')
    parser.add_argument('--context', choices=['server', 'client'], default='server')
    parser.add_argument('--port', type=int, default=6004)
    args = parser.parse_args()
    path = (ROOT / 'tests/stage1' / args.contract).resolve()
    if path.parent != ROOT / 'tests/stage1' or path.suffix != '.bsl':
        raise SystemExit('CONTRACT_PATH_REJECTED')
    output = ROOT / '.local/stage-3'
    output.mkdir(parents=True, exist_ok=True)
    try:
        client = MCP(args.port)
        probe = client.execute(PROBE)
        profile = json.loads((ROOT / '.local/stage-1/one-c-profile.json').read_text(encoding='utf-8'))
        if probe['baseFingerprint'] != profile['baseFingerprint']:
            raise SystemExit('DEMO_FINGERPRINT_MISMATCH')
        result = client.rpc('tools/call', {'name': 'execute_code', 'arguments': {
            'code': path.read_text(encoding='utf-8'), 'execution_context': args.context}})
        (output / (path.stem + '-result.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        answers = [json.loads(b['text']) for b in result.get('content', []) if b.get('type') == 'text']
        passed = len(answers) == 1 and answers[0].get('success') is True and not result.get('isError')
        print(json.dumps({'contract': path.name, 'passed': passed, 'session': probe['sessionId'],
                          'result': answers[0].get('data') if passed else 'FAILED_SEE_PRIVATE_RESULT'}, ensure_ascii=False))
        return 0 if passed else 1
    except UnknownOutcome:
        print('ONEC_ENVIRONMENT_UNAVAILABLE; no confirmed Red or Green')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
