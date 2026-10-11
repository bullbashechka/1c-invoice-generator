"""Ordinary-session read/secret check on6004; never creates a task or changes roles."""
import json
from pathlib import Path

from run_register_concurrency import MCP

ROOT = Path(__file__).resolve().parents[2]


def main():
    c = MCP(6004)
    # Ordinary staff must not read protected integration registers for preflight.
    probe = c.execute('''Х = Новый ХешированиеДанных(ХешФункция.SHA256); Х.Добавить(СтрокаСоединенияИнформационнойБазы());
Результат = Новый Структура("baseFingerprint,admin,privileged,user",Строка(Х.ХешСумма),ПравоДоступа("Администрирование",Метаданные),ПривилегированныйРежим(),ПользователиИнформационнойБазы.ТекущийПользователь().Имя);''')
    profile = json.loads((ROOT / '.local/stage-1/one-c-profile.json').read_text())
    assert probe['baseFingerprint'] == profile['baseFingerprint'], 'DEMO_MISMATCH'
    assert not probe['admin'] and not probe['privileged'], 'ORDINARY_EMPLOYEE_REQUIRED'
    code = Path(__file__).with_name('stage4_employee_read_contract.bsl').read_text()
    result = c.execute(code)
    assert result == 'STAGE4_EMPLOYEE_READ_AND_SECRET_GREEN', 'EMPLOYEE_CHECK_NOT_CONFIRMED'
    output = ROOT / '.local/stage-4/completion/employee-read-proof.json'
    output.write_text(json.dumps({'result': result, 'user': probe['user']}, ensure_ascii=False))
    print(result, flush=True)


if __name__ == '__main__':
    main()
