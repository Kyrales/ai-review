# GitFlic Manual AI Review Dispatch Design

## Контекст

В `sppr_gitflic` уже существует доверенный worker-процесс AI review: pipeline на защищённой ветке `ai-review-control` запускается с `AI_REVIEW_REQUESTED=true`, получает зафиксированные MR/head/source/target и выполняет review через подписанный образ `ghcr.io/kyrales/ai-review`. Локальные Windows-сценарии `Start-AiReview.ps1` и `Sync-AiReviewKnowledge.ps1` решают ручные задачи, но не дают оператору выбрать MR непосредственно в интерфейсе GitFlic.

Нужен ручной pipeline GitFlic с одним пользовательским параметром:

- `AI_REVIEW_MR_IDS=79,81,84` — обработать явный список;
- `AI_REVIEW_MR_IDS=all` — обработать все открытые MR;
- явно пустую `AI_REVIEW_MR_IDS` текущий GitFlic создать не позволяет (HTTP 422); внутри Bash launcher пустая строка всё равно защитно трактуется как `all`;
- отсутствующая `AI_REVIEW_MR_IDS` — обычный pipeline, dispatcher job не создаётся.

## Решение

Основная реализация размещается в `ai_review` как новая команда `ai-review gitflic-dispatch`. Команда работает внутри подписанного контейнера и использует уже существующие Python runtime, GitFlic HTTP client, Pydantic-схемы, marker parser v1/v2 и knowledge parser. На Linux runner не устанавливается Python и в `sppr_gitflic` не создаются Python-файлы.

В `sppr_gitflic` остаётся тонкий Bash launcher и declarative GitFlic job. Launcher до чтения секретов проверяет доверенный ref/tag и синтаксис selector, затем запускает закреплённый digest подписанного образа в hardened-контейнере. Контейнер обращается к GitFlic API, определяет `initial`/`followup`/`none` и создаёт отдельный существующий worker-pipeline только для `initial` и `followup`.

```text
GitFlic Create Pipeline
  ref=ai-review-control, AI_REVIEW_MR_IDS=79,81,84 | all
      |
      v
sppr: ai_review_dispatch + thin Bash gate
      |
      v
signed pinned ai-review image: gitflic-dispatch
      |
      +-- GET open MR/details/discussions/protection
      +-- classify with canonical marker/knowledge logic
      +-- POST one existing worker pipeline per actionable MR
      v
sppr: ai_review worker (unchanged contract)
```

## Границы ответственности

### `ai_review`

- CLI и разбор типизированных параметров `--owner`, `--project`, `--control-ref`, `--merge-request-id`, `--all`, `--api-url`.
- Получение открытых MR, деталей, дискуссий, текущего пользователя и branch protection через GitFlic API.
- Чистая классификация состояния review поверх канонических `parse_marker` и `parse_knowledge_block`.
- Формирование и однократный POST существующего worker-pipeline body.
- Агрегированный типизированный отчёт и стабильные exit codes.
- Unit/HTTP/CLI/image tests и публикация подписанного образа.

### `sppr_gitflic`

- GitFlic rule, создающее job только при наличии переменной; массовый запуск использует значение `all`.
- Проверка `ai-review-control`, запрет tag и проверка selector до чтения env-файла.
- Преобразование selector: пусто/`all` в `--all`, CSV в повторяемые `--merge-request-id`.
- Pull и cosign-проверка закреплённого digest, hardened `docker run` без Docker socket и mounts проекта.
- Документация оператора, contract tests и контролируемый live smoke.

## Контракт выбора

- CLI требует ровно один способ выбора: `--all` либо repeated положительные `--merge-request-id`, которые после дедупликации дают 1–10 уникальных ID.
- Bash wrapper принимает только точное lowercase `all`, пустую строку либо regex `[1-9][0-9]*(,[1-9][0-9]*)*`; пробелы, `ALL`, пустые элементы, знаки и leading zero отклоняются до чтения секретов.
- Явный список дедуплицируется с сохранением первого порядка на границах Bash, CLI и service; лимит 10 применяется после дедупликации.
- `--all` получает полный snapshot `OPENED` MR до первого POST, максимум 10 страниц по 100 элементов. Snapshot сортируется по `localId`; если открытых MR больше 10, команда fail-fast предлагает явные последовательные batch-запуски и не делает POST. Порог оставляет запас внутри лимита GitFlic `500 requests/hour` даже при десяти страницах list/protection, двух чтениях detail/discussions, пяти страницах discussions и трёх GET-попытках; официальный лимит: `https://docs.gitflic.ru/latest/en/api/intro/`.
- Пустой snapshot завершает команду успешно и не создаёт worker.
- MR, закрытый между snapshot и повторным detail GET, пропускается в `--all`, но является ошибкой явного выбора.

## Контракт классификации

- `initial`: после проверки followup-состояния нет доверенного terminal `summary` для текущего head. Finding текущего head без terminal summary означает восстановительный `initial`; markers старого head не блокируют новое initial-review.
- `followup`: есть непокрытые человеческие ответы либо последний валидный followup требует повторного resolve (`fixed`, `withdrawn` или валидный knowledge block при незакрытой дискуссии).
- `none`: review уже был, новых непокрытых ответов нет и повторный resolve не требуется.
- Marker v1/v2 разбирается только существующим `ai_review.services.vcs.markers.parse_marker`.
- Knowledge block разбирается только существующим `ai_review.services.knowledge.block.parse_knowledge_block`.
- Нормализация opaque IDs, учёт v2 continuation threads, covered replies и выбор unresolved threads для повторного resolve используют общий pure helper, который также вызывает `FollowupReviewRunner`; отдельная копия алгоритма для dispatcher запрещена.
- UUID trusted author канонизируется и сравнивается без учёта регистра внутри общего `parse_marker`; не-UUID opaque author IDs нормализуются NFC и сравниваются строго.

## GitFlic API и worker contract

Перед первым POST команда получает все branch-protection rules и применяет wildcard-семантику GitFlic (`*` без `/`, `?` один символ без `/`, `**` включая `/`). Требуется ровно одно совпадающее правило — exact `branchTemplate == control_ref` с `allowedToPush` в `ADMINS|NO_ONE` и `allowForcePush == false`. Любое второе совпадающее wildcard/exact rule считается неоднозначным и блокирует запуск независимо от priority; это консервативнее серверного выбора по весу. Контракт основан на `https://docs.gitflic.ru/latest/en/project/branch_settings/`.

Worker pipeline запускается на `control_ref` с переменными:

```text
AI_REVIEW_REQUESTED=true
AI_REVIEW_MODE=initial|followup
AI_REVIEW_MR_ID=<positive integer>
AI_REVIEW_HEAD_SHA=<40 lowercase hex>
AI_REVIEW_SOURCE_BRANCH=<validated ref>
AI_REVIEW_TARGET_BRANCH=<validated ref>
```

`AI_REVIEW_MR_IDS` в child body не передаётся. Поэтому child pipeline не создаёт новый dispatcher.

POST `/cicd/pipeline/start` не повторяется ни transport retry, ни fallback-token retry: timeout, 401/403 или 5xx после отправки имеет неоднозначный результат и повтор может создать дубликат. Fallback применяется только к идемпотентным GET. Ошибка одного MR не прекращает обработку остальных, но итоговый exit code становится ненулевым.

Непосредственно перед POST команда повторно читает и details, и discussions, заново классифицирует общий snapshot с актуальным head и использует только актуальные mode/head/source/target. Переход в `none` означает skip; переход `initial ↔ followup` использует новый актуальный mode и отражается в результате.

## Безопасность

- API token `AI_REVIEW_GITFLIC_TOKEN` и fallback `AI_REVIEW_GITFLIC_TOKEN2` читаются только из environment и не принимаются CLI-аргументами.
- Ответы API, токены и произвольные headers не попадают в exception text, stdout, stderr или artifacts. Это относится и к malformed JSON/schema при HTTP 2xx: наружу выходит только allowlisted endpoint/error code.
- GitFlic GET/POST не следуют redirect; URL строятся из проверенного HTTPS base URL и percent-encoded owner/project.
- Dispatch-specific list/discussion pagination требует согласованных nonnegative `number/totalPages/totalElements`, ожидаемого номера страницы, неизменных totals, явного status каждого MR и точного cumulative count; malformed snapshot завершается до POST.
- `/user/me` используется только при отсутствии доверенного author ID; результат должен быть canonical UUID.
- Container запускается `--read-only`, `--cap-drop=ALL`, `--security-opt=no-new-privileges`, `--user 65532:65532`, с tmpfs `/tmp`, без Docker socket и без mount checkout.
- Образ задаётся точным `ghcr.io/kyrales/ai-review@sha256:...` и проверяется cosign по существующим issuer/workflow identity.
- Ветка/tag/selector проверяются до source `SPPR_CI_ENV_FILE`.

## Совместимость

Новая команда additive. Не изменяются интерфейсы существующих CLI-команд `run*`, `clear*`, `show-config`, `sync-knowledge` и worker contract. В соседнем репозитории остаются без изменений `Start-AiReview.ps1`, `Sync-AiReviewKnowledge.ps1` и `AiReview.psm1`; их regression tests обязательны перед завершением rollout.

## Rollout

1. Реализовать и протестировать `gitflic-dispatch` в ветке `feature/gitflic-dispatch` проекта `ai_review`.
2. Собрать Windows release согласно `AGENTS.md`, проверить `ai-review.exe --help`; Linux binary и container проверяет Linux CI.
3. Merge/publish подписанный container image, получить точный digest и проверить наличие `gitflic-dispatch --help` по digest.
4. Только после этого внести digest, Bash launcher и job в `sppr_gitflic:ai-review-control`.
5. До подключения mutation-capable launcher отдельным no-secret/no-POST CI probe проверить absent/empty contract. Проверка выполнена: pipelines без variable не содержат job, а явно пустую variable GitFlic API отклоняет с HTTP 422. Поэтому документированный массовый selector — `all`; launcher-side empty parsing остаётся защитным.
6. Сначала провести fake/contract tests, затем с отдельного согласия — live smoke одного MR и контролируемый `all`.

## Не входит в эту итерацию

- `AI_REVIEW_MODE=auto` внутри worker.
- Автоматический повтор worker после `MR state changed before checkout`.
- Resumable/idempotent массовые batches больше 10 MR; оператор запускает несколько явных batch до появления отдельного протокола resume.
- Установка Python или PowerShell на Linux runner.
- Замена либо удаление локальных PowerShell-launcher.
