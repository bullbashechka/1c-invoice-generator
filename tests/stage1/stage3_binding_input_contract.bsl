// Проверяет рабочий парсер модуля, а не REST-заглушку. Нет сети/записи.
Портал = "demo.bitrix24.kz";
Если Расш1_СервисRESTБ24.IDИзВвода("123", Портал) <> "123" Тогда ВызватьИсключение "STAGE3_RED: числовой ID отклонён"; КонецЕсли;
Для Каждого Адрес Из СтрРазделить("https://demo.bitrix24.kz/company/personal/user/12/tasks/task/view/123/|https://demo.bitrix24.kz/workgroups/group/36/tasks/task/view/123/?x=1", "|") Цикл
    Если Расш1_СервисRESTБ24.IDИзВвода(Адрес, Портал) <> "123" Тогда ВызватьИсключение "STAGE3_RED: штатная ссылка отклонена"; КонецЕсли;
КонецЦикла;
Для Каждого Ввод Из СтрРазделить("0|-1|1.5|abc|https://other.bitrix24.kz/tasks/task/view/123/|https://demo.bitrix24.kz.evil.test/tasks/task/view/123/|https://user@demo.bitrix24.kz/tasks/task/view/123/|https://demo.bitrix24.kz/tasks/task/edit/123/|https://demo.bitrix24.kz/tasks/task/view/123/456/", "|") Цикл
    Отклонен = Ложь;
    Попытка ID = Расш1_СервисRESTБ24.IDИзВвода(Ввод, Портал); Исключение Отклонен = Истина; КонецПопытки;
    Если Не Отклонен Тогда ВызватьИсключение "STAGE3_RED: неверный ввод принят"; КонецЕсли;
КонецЦикла;
Результат = "STAGE3_BINDING_INPUT_CONTRACT_GREEN";
