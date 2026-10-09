"""Exercise the installed form without saving the linked demo order."""
import json
import argparse
from pathlib import Path
from run_register_concurrency import MCP, PROBE

ROOT = Path(__file__).resolve().parents[2]
SELECT = '''
Запрос = Новый Запрос("ВЫБРАТЬ ПЕРВЫЕ 1 Заказ ИЗ РегистрСведений.Расш1_СвязиЗаказовБ24 ГДЕ ЗадачаId = &ID");
Запрос.УстановитьПараметр("ID", "10050");
Строки = Запрос.Выполнить().Выбрать();
Если Не Строки.Следующий() Тогда ВызватьИсключение "Нет связанного демозаказа"; КонецЕсли;
Заказ = Строки.Заказ;
Результат = Новый Структура("id", Строка(Заказ.УникальныйИдентификатор()));
'''
SNAPSHOT = '''
Заказ = Документы.ЗаказКлиента.ПолучитьСсылку(Новый УникальныйИдентификатор("__UUID__"));
Объект = Заказ.ПолучитьОбъект();
Результат = Новый Структура("version,posted,comment,state",
    Base64Строка(Объект.ВерсияДанных), Объект.Проведен, Объект.Комментарий,
    Расш1_СервисRESTБ24.СостояниеЗаказа(Заказ));
'''

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--contract', choices=['stage3_existing_link_form_contract.bsl', 'stage3_list_contract.bsl'], default='stage3_existing_link_form_contract.bsl')
    args = parser.parse_args()
    c = MCP(6004)
    probe = c.execute(PROBE)
    profile = json.loads((ROOT / '.local/stage-1/one-c-profile.json').read_text(encoding='utf-8'))
    assert probe['baseFingerprint'] == profile['baseFingerprint'], 'DEMO_MISMATCH'
    selected = c.execute(SELECT)
    import uuid
    order_id = str(uuid.UUID(selected['id']))
    snapshot = SNAPSHOT.replace('__UUID__', order_id)
    before = c.execute(snapshot)
    code = (ROOT / 'tests/stage1' / args.contract).read_text(encoding='utf-8')
    code = code.replace('__ORDER_UUID__', order_id)
    if args.contract == 'stage3_list_contract.bsl':
        unlinked = c.execute('''
Запрос = Новый Запрос("ВЫБРАТЬ ПЕРВЫЕ 1 Ссылка ИЗ Документ.ЗаказКлиента ГДЕ НЕ ПометкаУдаления И Ссылка НЕ В (ВЫБРАТЬ Заказ ИЗ РегистрСведений.Расш1_СвязиЗаказовБ24) УПОРЯДОЧИТЬ ПО Дата УБЫВ");
Строки = Запрос.Выполнить().Выбрать();
Если Не Строки.Следующий() Тогда ВызватьИсключение "Нет заказа без связи для проверки"; КонецЕсли;
Результат = Строка(Строки.Ссылка.УникальныйИдентификатор());
''')
        code = code.replace('__UNLINKED_UUID__', str(uuid.UUID(unlinked)))
    response = c.rpc('tools/call', {'name':'execute_code', 'arguments': {'code':code,'execution_context':'client'}})
    output = ROOT / '.local/stage-3' / (Path(args.contract).stem + '-result.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding='utf-8')
    after = c.execute(snapshot)
    assert before == after, 'LINKED_ORDER_CHANGED'
    blocks = [json.loads(b['text']) for b in response.get('content',[]) if b.get('type')=='text']
    expected = 'STAGE3_LIST_GREEN' if args.contract == 'stage3_list_contract.bsl' else 'STAGE3_EXISTING_LINK_FORM_GREEN'
    passed = len(blocks)==1 and blocks[0].get('success') is True and blocks[0].get('data')==expected
    print(json.dumps({'passed':passed,'persistedOrderUnchanged':before==after}, ensure_ascii=False))
    return 0 if passed else 1

if __name__ == '__main__':
    raise SystemExit(main())
