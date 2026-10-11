"""Guarded cleanup of the own partial-close fixture; no Bitrix24 requests."""
import argparse
import json
from pathlib import Path

from run_register_concurrency import MCP, PROBE

ROOT = Path(__file__).resolve().parents[2]


def literal(value):
    return '"' + value.replace('"', '""') + '"'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=6004)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    fixture = json.loads((ROOT / '.local/stage-4/completion/partial-visual-fixture.json').read_text())
    profile = json.loads((ROOT / '.local/stage-1/one-c-profile.json').read_text())
    client = MCP(args.port)
    assert client.execute(PROBE)['baseFingerprint'] == profile['baseFingerprint'], 'DEMO_MISMATCH'
    prelude = (
        'ЗаказКопии = Документы.ЗаказКлиента.ПолучитьСсылку(Новый УникальныйИдентификатор(' + literal(fixture['order']) + '));\n'
        'РеализацияКопии = Документы.РеализацияТоваровУслуг.ПолучитьСсылку(Новый УникальныйИдентификатор(' + literal(fixture['realization']) + '));\n'
        'ТестовыйМаркер = ' + literal(fixture['token']) + '; СообщениеКопии = ' + literal(fixture['message']) + ';\n'
    )
    contract = (Path(__file__).with_name('stage4_partial_fixture_cleanup.bsl')).read_text()
    guarded = client.execute(prelude + 'ВыполнитьУборку = Ложь;\n' + contract)
    assert guarded == 'OWN_PARTIAL_FIXTURE_CLEANUP_GUARDS_PASSED', 'GUARDS_NOT_CONFIRMED'
    print(guarded, flush=True)
    if args.apply:
        # Recheck every guard inside the mutation's transaction; preserve on error.
        result = client.execute(prelude + 'ВыполнитьУборку = Истина;\n' + contract)
        assert result == 'OWN_PARTIAL_FIXTURE_CLEANED', 'CLEANUP_NOT_CONFIRMED'
        (ROOT / '.local/stage-4/completion/partial-cleanup-result.json').write_text(
            json.dumps({'result': result, 'order': fixture['order'], 'realization': fixture['realization']}))
        print(result, flush=True)


if __name__ == '__main__':
    main()
