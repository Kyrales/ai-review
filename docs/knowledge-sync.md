# Синхронизация знаний AI Review для проектов 1С

Функция превращает подтверждённые ответы доверенных ревьюверов в предложения
правил, а затем по отдельной команде обновляет управляемую часть проектного
файла правил. Она отключена по умолчанию, не делает commit или push и не меняет
исходный код проекта 1С.

## Что добавить в проект

Для стандартного размещения достаточно четырёх элементов:

1. Секция `knowledge` в корневом YAML-файле AI Review.
2. Файл `ai-review/prompts/project-rules.md` с управляемыми marker.
3. Подключение `project-rules.md` ко всем используемым режимам review и
   followup через секцию `prompt`.
4. Тонкий `tools/ai-review/Sync-AiReviewKnowledge.ps1`, который только находит
   корень репозитория и передаёт аргументы команде `ai-review sync-knowledge`.

Рекомендуемый layout одинаков для любого проекта 1С:

```text
<root>/
├── .ai-review-ones.yaml
├── ai-review/prompts/
│   ├── inline.md
│   ├── context.md
│   ├── summary.md
│   ├── followup.md
│   └── project-rules.md
└── tools/ai-review/
    ├── run-ai-review.sh
    └── Sync-AiReviewKnowledge.ps1
```

Python-пакет `ai_review` копировать или изменять для нового проекта не нужно.

## Почему в конфигурации два вида путей

| Назначение | Путь |
|---|---|
| В репозитории | `<root>/ai-review/prompts` |
| Доверенный staging на CI-host | `$runtime_root/control/ai-review-${CI_PIPELINE_ID}/prompts` |
| В review-контейнере | `/control/ai-review/prompts` |

`run-ai-review.sh` копирует конфигурацию и prompts из репозитория в защищённый
staging, затем монтирует его read-only как `/control/ai-review`. Именно launcher
задаёт это сопоставление; YAML его не создаёт.

Поэтому пути в `prompt.*_prompt_files` абсолютные и относятся к файловой системе
review-контейнера:

```yaml
prompt:
  inline_prompt_files:
    - /control/ai-review/prompts/inline.md
    - /control/ai-review/prompts/project-rules.md
  context_prompt_files:
    - /control/ai-review/prompts/context.md
    - /control/ai-review/prompts/project-rules.md
  summary_prompt_files:
    - /control/ai-review/prompts/summary.md
    - /control/ai-review/prompts/project-rules.md
  inline_reply_prompt_files:
    - /control/ai-review/prompts/followup.md
    - /control/ai-review/prompts/project-rules.md
```

Добавьте файл правил в каждый реально используемый список prompt, включая
reply-режимы, если они настроены отдельно. В отличие от них,
`knowledge.sync.rules_file` — безопасный локальный путь относительно каталога
YAML-файла. Он используется локальным синхронизатором и должен оставаться внутри
репозитория:

```yaml
knowledge:
  sync:
    rules_file: ai-review/prompts/project-rules.md
```

При нестандартном layout согласованно измените источник копирования, staging и
mount в `run-ai-review.sh`, runtime-пути в `prompt`, относительный `rules_file`
и contract tests launcher. Менять Python core из-за расположения файлов не
следует.

## Настройки knowledge

Минимальная конфигурация GitFlic:

```yaml
knowledge:
  enabled: true
  trusted_reviewers:
    gitflic:
      - "reviewer-login"
  max_rules_per_reply: 3
  sync:
    rules_file: ai-review/prompts/project-rules.md
```

| Поле | Назначение |
|---|---|
| `enabled` | Включает дополнительное выделение знаний в followup и разрешает sync. По умолчанию `false`. |
| `trusted_reviewers.gitflic` | GitFlic usernames людей, чьи ответы можно использовать; регистр не учитывается. Пустой список никому не доверяет. |
| `trusted_reviewers.gitlab` | Резерв настройки для будущего GitLab adapter. |
| `max_rules_per_reply` | Лимит предложений из одного ответа: от 1 до 3. Это не общий лимит MR. |
| `sync.rules_file` | Markdown-файл правил, относительный к YAML и находящийся внутри репозитория. |

Чтобы выключить функцию без удаления настроек:

```yaml
knowledge:
  enabled: false
```

В этом режиме followup не вызывает отдельный knowledge-extractor, а
`sync-knowledge` завершается до обращения к GitFlic и LLM.

## Файл правил

Ручной текст можно редактировать свободно. Синхронизатор меняет только одну
секцию между standalone-marker:

```markdown
## Автоматически накопленные правила

<!-- ai-review-knowledge:start -->
- Проверять один конкретный переиспользуемый случай.

<!-- ai-review-knowledge:end -->
```

Если marker ещё нет, секция создаётся при первой подтверждённой записи. Не
создавайте вторую пару marker и не помещайте внутрь секции ручной Markdown.
Перед записью всегда показываются решения модели и полный diff; существующая
ручная часть файла сохраняется.

## Переменные окружения GitFlic и LLM

Репозиторий обычно определяется по `origin`. Для CI или явного переопределения
доступны:

```text
VCS__PROVIDER=GITFLIC
VCS__PIPELINE__OWNER=<owner>
VCS__PIPELINE__PROJECT=<project>
VCS__HTTP_CLIENT__API_URL=https://api.gitflic.ru
VCS__HTTP_CLIENT__API_TOKEN=<token>
AI_REVIEW_GITFLIC_USER_ID=<id пользователя AI Review>
```

Также принимаются `AI_REVIEW_GITFLIC_TOKEN` и `GITFLIC_TOKEN`. Для публичного
`gitflic.ru` URL API определяется автоматически; для self-hosted GitFlic его
нужно задать явно. Если `AI_REVIEW_GITFLIC_USER_ID` не указан при sync, клиент
получает ID владельца токена через API. Секреты удобно хранить в переменных CI
или локальном `.env`, но не в YAML и не в launcher.

Отдельный вызов компилятора правил сначала использует четыре явные переменные:

```text
AI_REVIEW_API_URL=<Responses API base URL>
AI_REVIEW_API_TOKEN=<token>
AI_REVIEW_MODEL=<model>
AI_REVIEW_REASONING_EFFORT=<none|minimal|low|medium|high|xhigh|max>
```

Если они не заданы, локальный sync использует активный провайдер из
`$CODEX_HOME/config.toml` (по умолчанию `~/.codex/config.toml`) и его токен из
указанной провайдером переменной окружения либо локальной Codex-авторизации.
Явные `AI_REVIEW_*` имеют приоритет, поэтому их можно использовать для отдельной
модели или endpoint без изменения настроек Codex.

## Рабочий процесс GitFlic

1. Обычный `ai-review run-followup` анализирует только новые ответы. Для ответа
   доверенного ревьювера он может добавить до настроенного числа правил с тегом
   `#ai-review-knowledge`. Обычный текст без выделенного правила не запоминается;
   явная просьба вроде «AI-ревьювер, зафиксируй в правилах это» учитывается.
2. Локальный sync показывает только открытые MR, где уже есть проверенные
   followup-блоки знаний. Источник, автор AI marker, username ревьювера и цитата
   проверяются повторно перед LLM-вызовом. Открытый MR без такого блока корректно
   не попадает в список: сначала нужен новый запуск followup после подходящего
   ответа доверенного ревьювера.
3. После выбора MR отдельный LLM-вызов сопоставляет предложения с текущими
   правилами. Конфликты автоматически не добавляются. Один запуск принимает не
   более 20 предложений; при превышении выберите меньше MR.
4. Пользователь проверяет diff и подтверждает локальную запись. Затем файл можно
   отредактировать и закоммитить обычным процессом проекта.

Для проверки текущей ветки `ai_review` установите именно её из checkout в
активное Python-окружение:

```powershell
Set-Location <path-to-ai_review>
py -m pip install --editable .
ai-review sync-knowledge --help
```

После этого запускайте тонкий PowerShell launcher из корня 1С-проекта:

```powershell
# Интерактивный выбор из подходящих открытых MR
pwsh tools/ai-review/Sync-AiReviewKnowledge.ps1

# Все показанные MR, подтверждение записи остаётся
pwsh tools/ai-review/Sync-AiReviewKnowledge.ps1 -All

# Явный список и неинтерактивное подтверждение; diff всё равно выводится
pwsh tools/ai-review/Sync-AiReviewKnowledge.ps1 -MergeRequestId 42,57 -Yes

# Без установки entry point: передать исполняемый файл окружения явно
pwsh tools/ai-review/Sync-AiReviewKnowledge.ps1 -AiReviewExecutable '..\ai_review\.venv\Scripts\ai-review.exe'
```

Прямой эквивалент доступен через `ai-review sync-knowledge --help`. Пустой Enter
отменяет интерактивный выбор; также принимаются `all` и `все`.

## Подключение GitLab позднее

Knowledge domain и sync не зависят от формата GitFlic. Новый adapter должен
реализовать `KnowledgeReviewSourceProtocol` из `ai_review.services.vcs.types`:
получение списка открытых review, одного review и его дискуссий. После этого
provider подключается в CLI bootstrap; extractor, compiler, writer управляемой
секции и механизм выбора MR менять не требуется. До появления adapter команда
sync поддерживает только GitFlic.
