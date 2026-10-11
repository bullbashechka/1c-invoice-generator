"""One explicitly authorized native REST add at TITLE=250; resume only reads."""
import argparse
import json
from pathlib import Path
import uuid

from run_register_concurrency import MCP, PROBE

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--create-one-boundary-task', action='store_true')
    choice.add_argument('--resume', type=Path)
    args = parser.parse_args()
    c = MCP(6004)
    profile = json.loads((ROOT / '.local/stage-1/one-c-profile.json').read_text())
    assert c.execute(PROBE)['baseFingerprint'] == profile['baseFingerprint'], 'DEMO_MISMATCH'
    if args.resume:
        folder = args.resume.resolve()
        intent = json.loads((folder / 'intent.json').read_text())
    else:
        folder = ROOT / '.local/stage-4/title-boundary' / str(uuid.uuid4())
        folder.mkdir(parents=True)
        intent = {'marker': 'KA2:' + str(uuid.uuid4()), 'phase': 'not-sent'}
        (folder / 'intent.json').write_text(json.dumps(intent))
    marker = intent['marker']
    assert marker.startswith('KA2:') and len(marker) == 40
    prelude = '''Если Не ПравоДоступа("Администрирование", Метаданные) Или СтрНайти(НРег(СтрокаСоединенияИнформационнойБазы()), "ка демо") = 0 Тогда ВызватьИсключение "DEMO_ONLY"; КонецЕсли;
Ж = РегистрыСведений.Расш1_ЖурналОперацийБ24.СоздатьМенеджерЗаписи(); Ж.ОперацияId = "f3b5f0bb-7e70-463d-874f-d0239bbb4880"; Ж.Прочитать();
Если Не Ж.Выбран() Или Ж.ЗадачаId <> "10074" Или Ж.Состояние <> "Завершена" Или Лев(Ж.Заказ.Комментарий,14) <> "STAGE3_NATIVE_" Тогда ВызватьИсключение "OWN_SOURCE_REQUIRED"; КонецЕсли;
НазваниеТеста = "ТЕСТ ЭТАП 4 — граница названия "; Пока СтрДлина(НазваниеТеста) < 250 Цикл НазваниеТеста = НазваниеТеста + "Я"; КонецЦикла;
'''
    if args.create_one_boundary_task:
        # Persist sending intent BEFORE the only add. Never retry an uncertain result.
        intent['phase'] = 'sending-do-not-repeat'
        (folder / 'intent.json').write_text(json.dumps(intent))
        code = prelude + '''Снимок = Расш1_RESTБ24Сервер.ДанныеJSON(Ж.СнимокJSON); Поля = Снимок.Получить("fields");
Поля.Вставить("TITLE",НазваниеТеста); Поля.Вставить("GROUP_ID",36); Поля.Вставить("XML_ID","__MARKER__");
Поля.Вставить("DESCRIPTION","ТЕСТ ЭТАП 4. Проверка сохранности названия длиной 250 символов.");
Ответ = Расш1_RESTБ24Сервер.СоздатьПоСнимку(Ж.Портал, Расш1_RESTБ24Сервер.JSONСтрока(Снимок));
Результат = Новый Структура("state,taskId",Ответ.Состояние,Ответ.ЗадачаId);'''.replace('__MARKER__', marker)
        answer = c.execute(code)
        intent.update({'phase': 'add-returned', 'answer': answer})
        (folder / 'intent.json').write_text(json.dumps(intent))
    code = prelude + '''Найдено = Расш1_RESTБ24Сервер.НайтиПоМаркеру(Ж.Портал,"__MARKER__");
Если Найдено.Состояние <> "ЗадачаИзвестна" Тогда ВызватьИсключение "EXACT_ONE_BOUNDARY_TASK_REQUIRED"; КонецЕсли;
Ответ = Расш1_RESTБ24Сервер.ПрочитатьЗадачуREST(Число(Найдено.ЗадачаId));
Если Ответ.Состояние <> "Результат" Тогда ВызватьИсключение "BOUNDARY_READ_NOT_CONFIRMED"; КонецЕсли;
Т = Ответ.Данные.Получить("task");
Если Т.Получить("title") <> НазваниеТеста Или СтрДлина(Т.Получить("title")) <> 250 Или Число(Т.Получить("groupId")) <> 36 Или Т.Получить("xmlId") <> "__MARKER__" Тогда ВызватьИсключение "BOUNDARY_TITLE_NOT_PRESERVED"; КонецЕсли;
Результат = Новый Структура("passed,taskId,length,projectId",Истина,Найдено.ЗадачаId,СтрДлина(Т.Получить("title")),36);'''.replace('__MARKER__', marker)
    proof = c.execute(code)
    (folder / 'result.json').write_text(json.dumps(proof))
    print(json.dumps({'result': proof, 'evidence': str(folder.relative_to(ROOT))}), flush=True)


if __name__ == '__main__':
    main()
