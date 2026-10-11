"""Read only today's guarded manual employee event from admin6004."""
import json
from pathlib import Path
from run_register_concurrency import MCP, PROBE

ROOT = Path(__file__).resolve().parents[2]


def main():
    c = MCP(6004)
    assert c.execute(PROBE)['baseFingerprint'] == json.loads((ROOT / '.local/stage-1/one-c-profile.json').read_text())['baseFingerprint'], 'DEMO_MISMATCH'
    code = '''Если Не ПравоДоступа("Администрирование",Метаданные) Тогда ВызватьИсключение "ADMIN_REQUIRED"; КонецЕсли;
Т = Новый ТаблицаЗначений; Отбор = Новый Структура("Событие,ДатаНачала","STAGE4_EMPLOYEE_READ",НачалоДня(ТекущаяДата()));
ВыгрузитьЖурналРегистрации(Т,Отбор, , ,100);
Подтверждения = Новый Массив;
Для Каждого С Из Т Цикл
// Журнал возвращает UUID пользователя, а не отображаемое имя.
ПользовательИБ = ПользователиИнформационнойБазы.НайтиПоУникальномуИдентификатору(Новый УникальныйИдентификатор(Строка(С.Пользователь)));
Если ПользовательИБ = Неопределено Тогда Продолжить; КонецЕсли;
Если СтрЗаменить(НРег(ПользовательИБ.Имя)," ","") = "продажи(гладилинавм)"
 И СтрНайти(С.Комментарий,"STAGE4_EMPLOYEE_READ_AND_SECRET_GREEN;") = 1 Тогда
Подтверждения.Добавить(Новый Структура("session,comment,date",С.Сеанс,С.Комментарий,Формат(С.Дата,"ДФ=yyyy-MM-dd HH:mm:ss")));
КонецЕсли; КонецЦикла;
Результат = Новый Структура("eventCount,employeeConfirmed,proofs",Т.Количество(),Подтверждения.Количество() > 0,Подтверждения);'''
    result = c.execute(code)
    (ROOT / '.local/stage-4/completion/employee-log-proof.json').write_text(json.dumps(result, ensure_ascii=False))
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
