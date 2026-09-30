# SyntaxHelpReader — 1C Native API Component

C++ компонента для 1С:Предприятие. Читает встроенную справку по синтаксису BSL из `.hbk` файлов платформы 1С. Используется инструментом `get_bsl_syntax_help` — позволяет агенту искать описания функций, методов, типов и конструкций языка запросов.

Зависимость — [miniz](https://github.com/richgel999/miniz) v3.1.1 (ZIP-чтение + raw deflate).

## Требования

- Windows (x86 или x64)
- CMake 3.16+
- Visual Studio 2022 (MSVC)
- Установленная платформа 1С:Предприятие (для `.hbk` файлов)

## Сборка

Собирать нужно под разрядность платформы 1С, на которой будет работать компонента.

```bash
cd native_components/SyntaxHelpReader

# x64
cmake -B build -A x64
cmake --build build --config Release
# Результат: build/Release/SyntaxHelpReader.dll

# x86
cmake -B build_x86 -A Win32
cmake --build build_x86 --config Release
# Результат: build_x86/Release/SyntaxHelpReader.dll
```

miniz скачивается автоматически через CMake FetchContent при первой сборке.

## Сборка под Linux (через Docker)

Требования: [Docker Desktop](https://www.docker.com/products/docker-desktop/) на Windows.

```
native_components\SyntaxHelpReader\build_linux.bat
```

Результат: `build_linux/SyntaxHelpReader.so`

База образа — Ubuntu 20.04 (glibc 2.31). Подходит для RedOS 8.x и большинства Linux-серверов 1С. Для RedOS 7.x замените базу в `Dockerfile.linux` на `debian:buster`.

### Установка на Linux-сервер 1С

```bash
cp build_linux/SyntaxHelpReader.so \
   ../../1c/MCPToolkit/MCPToolkit/Templates/SyntaxHelpReader/Ext/Template.bin
```

---

## Установка в обработку

Скопируйте DLL нужной разрядности в макет обработки под именем `Template.bin`:

```bash
# x64
cp build/Release/SyntaxHelpReader.dll \
   ../../1c/MCPToolkit/MCPToolkit/Templates/SyntaxHelpReader/Ext/Template.bin

# x86
cp build_x86/Release/SyntaxHelpReader.dll \
   ../../1c/MCPToolkit/MCPToolkit/Templates/SyntaxHelpReader/Ext/Template.bin
```

После обновления Template.bin нужно пересохранить обработку в конфигураторе 1С.

## Нативные тесты

```bash
cmake -B build -A x64 -DSYNTAX_HELP_BUILD_TESTS=ON
cmake --build build --config Release
./build/Release/test_syntax_help.exe "C:/Program Files/1cv8/<версия>/bin"

# или через ctest (требует -DSYNTAX_HELP_HBK_DIR=... при конфигурировании):
cmake -B build -A x64 -DSYNTAX_HELP_BUILD_TESTS=ON \
      -DSYNTAX_HELP_HBK_DIR="C:/Program Files/1cv8/<версия>/bin"
cmake --build build --config Release
ctest --test-dir build -C Release
```

Тесты проверяют загрузку `.hbk` из `C:/Program Files/1cv8/<версия>/bin`, поиск по ключевым словам, разрешение ссылок и протокол `topic:`.

## Файлы справки

Компонента читает только два файла из `КаталогПрограммы()`:

| Файл | Содержимое |
|------|-----------|
| `shcntx_ru.hbk` | Контекстная справка BSL (функции, типы, методы) |
| `shquery_ru.hbk` | Справка по языку запросов |

Если файл не найден — пропускается без ошибки. Если найден, но повреждён — `Initialize` возвращает отрицательное число.

## API

### Методы

| Метод (EN) | Метод (RU) | Параметры | Возврат | Описание |
|-----------|-----------|-----------|---------|----------|
| `Initialize(dir)` | `Инициализировать` | Строка | Число | Загрузить `.hbk` из каталога; >0 — кол-во тем, 0 — файлы не найдены, <0 — ошибка парсинга |
| `Search(keywordsJson, matchAll)` | `Поиск` | Строка JSON, Булево | Строка JSON | Поиск тем по ключевым словам |
| `GetTopic(breadcrumb)` | `ПолучитьТему` | Строка | Строка JSON | Точный поиск по полному пути (breadcrumb) |
| `Version()` | `Версия` | — | Строка | Версия компоненты |

### Search / Поиск

**keywordsJson** — JSON-массив строк:
```json
["Найти", "Массив"]
```

**matchAll** — `true` = все ключевые слова должны присутствовать в breadcrumb (AND), `false` = любое (OR).

Если передан единственный keyword с префиксом `topic:` — выполняется точный поиск через `GetTopic`:
```json
["topic:Массив/Методы/Найти"]
```

**Возвращает** JSON:
```json
{
  "candidates": ["Массив/Методы/Найти"],
  "content": "# Найти\n\n**Синтаксис:** `Найти(<Что>)`\n\n..."
}
```

- `candidates` — уникальные breadcrumb-пути совпавших тем
- `content` — Markdown-содержимое, если совпал ровно один уникальный breadcrumb с содержимым; иначе `null`
- если совпавший breadcrumb является навигационным разделом (нет содержимого, есть подтемы), `candidates` содержит его прямые подтемы
- Ссылки в Markdown резолвятся в breadcrumb-пути с префиксом `topic:`, например `[Добавить](topic:Массив/Методы/Добавить)`

### GetTopic / ПолучитьТему

Точный поиск по полному breadcrumb (нечувствителен к регистру, `ё` = `е`):
```json
{"candidates": ["Массив/Методы/Найти"], "content": "# Найти\n\n..."}
```

Если тема не найдена — `{"candidates": [], "content": null}`.
