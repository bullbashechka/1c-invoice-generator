"""Read-only live UI contract; refuses databases outside the saved demo profile."""
import argparse
import json
import uuid
from pathlib import Path

from run_register_concurrency import MCP

ROOT = Path(__file__).resolve().parents[2]


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=6004)
    parser.add_argument('--label', default='contract')
    parser.add_argument('--rows', action='store_true', help='Open isolated demo forms and verify existing linked/unlinked rows')
    args = parser.parse_args()
    client = MCP(args.port)
    fingerprint = client.execute('''ХешПроверки = Новый ХешированиеДанных(ХешФункция.SHA256);
ХешПроверки.Добавить(СтрокаСоединенияИнформационнойБазы()); Результат = Строка(ХешПроверки.ХешСумма);''')
    profile = json.loads((ROOT / '.local/stage-1/one-c-profile.json').read_text(encoding='utf-8-sig'))
    if ''.join(fingerprint.split()).lower() != ''.join(profile['baseFingerprint'].split()).lower():
        raise RuntimeError('DEMO_FINGERPRINT_MISMATCH')
    code = Path(__file__).with_name('list_forms_column_contract.bsl').read_text(encoding='utf-8-sig')
    if args.rows:
        fixture = client.execute('''Запрос = Новый Запрос("ВЫБРАТЬ ПЕРВЫЕ 1 Связи.Заказ, Связи.ЗадачаId ИЗ РегистрСведений.Расш1_СвязиЗаказовБ24 КАК Связи ВНУТРЕННЕЕ СОЕДИНЕНИЕ Документ.ЗаказКлиента КАК Заказы ПО Связи.Заказ = Заказы.Ссылка ГДЕ Связи.ЗадачаId <> &Пусто И НЕ Заказы.ПометкаУдаления УПОРЯДОЧИТЬ ПО Заказы.Дата, Заказы.Номер, Заказы.Ссылка");
Запрос.УстановитьПараметр("Пусто", ""); Выборка = Запрос.Выполнить().Выбрать();
Если Не Выборка.Следующий() Тогда ВызватьИсключение "NO_EXISTING_LINKED_DEMO_ORDER"; КонецЕсли;
Результат = Новый Структура("linked,task", Строка(Выборка.Заказ.УникальныйИдентификатор()), Выборка.ЗадачаId);
Запрос = Новый Запрос("ВЫБРАТЬ ПЕРВЫЕ 1 Заказы.Ссылка ИЗ Документ.ЗаказКлиента КАК Заказы ЛЕВОЕ СОЕДИНЕНИЕ РегистрСведений.Расш1_СвязиЗаказовБ24 КАК Связи ПО Заказы.Ссылка = Связи.Заказ ГДЕ Связи.Заказ ЕСТЬ NULL И НЕ Заказы.ПометкаУдаления УПОРЯДОЧИТЬ ПО Заказы.Дата, Заказы.Номер, Заказы.Ссылка");
Выборка = Запрос.Выполнить().Выбрать(); Если Не Выборка.Следующий() Тогда ВызватьИсключение "NO_EXISTING_UNLINKED_DEMO_ORDER"; КонецЕсли;
Результат.Вставить("unlinked", Строка(Выборка.Ссылка.УникальныйИдентификатор()));''')
        if not str(fixture['task']).isdigit():
            raise RuntimeError('INVALID_EXISTING_TASK_ID')
        code = Path(__file__).with_name('list_forms_rows_contract.bsl').read_text(encoding='utf-8-sig')
        code = code.replace('__LINKED_UUID__', str(uuid.UUID(fixture['linked']))).replace('__UNLINKED_UUID__', str(uuid.UUID(fixture['unlinked']))).replace('__TASK_ID__', str(fixture['task']))
    response = client.rpc('tools/call', {'name': 'execute_code',
        'arguments': {'code': code, 'execution_context': 'client'}})
    blocks = [block['text'] for block in response.get('content', []) if block.get('type') == 'text']
    if response.get('isError') or len(blocks) != 1:
        raise RuntimeError('MCP_CONTRACT_RESPONSE_NOT_CONFIRMED')
    result = json.loads(blocks[0])
    output = ROOT / '.local/list-forms-fix'
    output.mkdir(parents=True, exist_ok=True)
    label = ''.join(ch for ch in args.label if ch.isalnum() or ch in '-_')
    (output / f'{label}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    expected = 'LIST_FORMS_ROWS_GREEN' if args.rows else 'LIST_FORMS_COLUMN_GREEN'
    return 0 if result.get('success') and result.get('data', {}).get('status') == expected else 1


if __name__ == '__main__':
    raise SystemExit(run())
