# GitFlic Manual AI Review Dispatch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить в `ai_review` контейнерную команду ручного GitFlic-dispatch и подключить её тонким CI-launcher из `sppr_gitflic`, чтобы оператор мог запустить review выбранных либо всех открытых MR без Python на host runner.

**Architecture:** `ai_review gitflic-dispatch` переиспользует канонические GitFlic client, marker/knowledge parsing и создаёт существующие worker pipelines только для состояний `initial`/`followup`. Соседний `sppr_gitflic` валидирует доверенный ref и selector, запускает закреплённый подписанный image digest и не содержит классификации или Python runtime.

**Tech Stack:** Python 3.11+, Typer, Pydantic, httpx `MockTransport`, pytest/pytest-asyncio, Docker, Cosign, Bash 4+, GitFlic CI/API.

**Spec:** `docs/superpowers/specs/2026-09-23-gitflic-dispatch-design.md`

## Global Constraints

- Основная реализация находится в `ai_review`; Python на GitFlic runner не устанавливается.
- Рабочая ветка `ai_review` — `feature/gitflic-dispatch`; интеграционная ветка `sppr_gitflic` — `ai-review-control`.
- Новая CLI-команда называется `gitflic-dispatch`; существующие команды и worker variables не меняются.
- Секреты принимаются только через environment; CLI options и JSON summary секретов не содержат.
- `AI_REVIEW_MR_IDS` отсутствует — dispatcher job отсутствует; пусто/`all` — все `OPENED`; CSV — 1–10 уникальных положительных ID.
- Массовый snapshot получает до 10 страниц по 100 MR, полностью валидируется до первого POST и fail-fast отклоняет больше 10 открытых MR согласно worst-case request budget лимита GitFlic 500/hour.
- Pipeline POST выполняется один раз без transport/fallback retry; GET сохраняет существующий bounded retry и fallback token только после 401/403.
- API redirect запрещён; response body и произвольные headers не включаются в ошибки.
- До первого POST подтверждается единственное matching branch-protection rule: exact `ai-review-control`, `ADMINS|NO_ONE`, `allowForcePush=false`; любое wildcard overlap блокирует запуск.
- `Start-AiReview.ps1`, `Sync-AiReviewKnowledge.ps1`, `AiReview.psm1` и их пользовательские сценарии сохраняются.
- После изменения CLI обязательны Windows release rebuild/`--help` из `AGENTS.md`; Linux binary/container собираются только Linux CI.
- Подключение `sppr_gitflic` начинается только после публикации, подписи и фиксации точного image digest с новой командой.

## Review Focus

- Redirect с Authorization должен завершаться ошибкой без второго запроса и утечки token.
- 5xx/timeout pipeline POST не должен повторяться, даже если GET retry включён.
- Поздняя malformed-страница `--all` не должна дать частичный запуск уже найденных MR.
- V2 continuation marker должен покрывать reply исходной дискуссии так же в dispatcher и worker.
- Явно пустая GitFlic variable должна создавать dispatcher job, а отсутствующая — нет; это проверяется live contract smoke.
- Stale/current-head markers должны приводить к тому же initial recovery, followup или none, что существующий worker lifecycle.

---

## File Map

### Repository `ai_review`

- Create: `ai_review/services/review/followup_state.py` — общий pure-анализ marker/covered/pending/resolve.
- Create: `ai_review/services/dispatch/__init__.py` — package marker.
- Create: `ai_review/services/dispatch/models.py` — selection, decision, result/report и exit semantics.
- Create: `ai_review/services/dispatch/service.py` — orchestration list/detail/classify/protection/start.
- Create: `ai_review/cli/commands/gitflic_dispatch.py` — runtime factory и CLI command body.
- Create: `ai_review/tests/suites/services/review/test_followup_state.py`.
- Create: `ai_review/tests/suites/services/dispatch/test_service.py`.
- Create: `ai_review/tests/suites/cli/test_gitflic_dispatch.py`.
- Modify: `ai_review/services/review/runner/followup.py` — делегирование общей state-логике.
- Modify: `ai_review/services/vcs/markers.py` — canonical trusted-author comparison.
- Modify: `ai_review/clients/gitflic/schema.py` — branch protection и pipeline request/response models.
- Modify: `ai_review/clients/gitflic/client.py` — strict redirects, protection listing и pipeline start.
- Modify: `ai_review/services/vcs/gitflic/adapter.py` — public conversion discussion → neutral thread.
- Modify: `ai_review/services/vcs/gitflic/client.py` — использовать public conversion.
- Modify: `ai_review/services/knowledge/gitflic_source.py` — использовать тот же public conversion.
- Modify: `ai_review/cli/main.py` — зарегистрировать `gitflic-dispatch`.
- Modify: `ai_review/tests/suites/clients/gitflic/test_client.py`.
- Modify: `ai_review/tests/suites/services/vcs/gitflic/test_adapter.py`.
- Modify: `ai_review/tests/suites/services/knowledge/test_source.py`.
- Modify: `ai_review/tests/suites/image/test_image_smoke.py`.
- Modify: `.github/workflows/workflow-publish.yml` — smoke новой команды и выдача точного digest.
- Modify: `docs/cli/README.md`, `docs/ci/README.md`, `README.md` — публичный контракт.

### Repository `../sppr_gitflic`

- Create: `tools/ai-review/dispatch-ai-review.sh` — trusted gate и hardened container launcher.
- Create: `tools/ai-review/tests/test-dispatch-ai-review.sh` — fake docker/cosign contract.
- Modify: `gitflic-ci_linux.yaml` — `ai_review_dispatch`.
- Modify: `tools/ai-review/tests/test-ci-contract.ps1`.
- Modify: `tools/ci/tests/test-gitflic-ci.sh`, `tools/ci/tests/README.md`.
- Modify: `tools/ci/README.md`, `doc/specs/24_ai_review_gitflic_design.md`.
- Preserve: `tools/ai-review/Start-AiReview.ps1`, `Sync-AiReviewKnowledge.ps1`, `AiReview.psm1`.

### Task 1: Вынести общую pure-логику followup state

**Files:**
- Create: `ai_review/services/review/followup_state.py`
- Create: `ai_review/tests/suites/services/review/test_followup_state.py`
- Modify: `ai_review/services/review/runner/followup.py`
- Modify: `ai_review/services/vcs/markers.py`
- Test: `ai_review/tests/suites/services/review/runner/test_followup.py`
- Test: `ai_review/tests/suites/services/vcs/test_markers.py`

**Interfaces:**
- Consumes: `ReviewThreadSchema`, `ReviewCommentSchema`, `parse_marker`, `parse_knowledge_block`, trusted author ID.
- Produces: `FollowupStateAnalyzer(trusted_author_id: str)` с методами `marker`, `pending_comments`, `pending_ids`, `continuations`, `last_followup_requires_resolve`, `resolve_thread_ids`.

- [x] **Step 1: Написать failing unit tests общего анализатора**

Добавить fixtures для v1 covered UUID, v2 opaque Unicode ID/NFC, v2 continuation thread, damaged marker, terminal knowledge block и незакрытого `fixed`/`withdrawn`. Основной контракт:

```python
analyzer = FollowupStateAnalyzer(TRUSTED)
assert [item.id for item in analyzer.pending_comments(origin, (continuation,))] == []
assert analyzer.last_followup_requires_resolve(unresolved_fixed) is True
assert analyzer.resolve_thread_ids(unresolved_fixed, ()) == (str(unresolved_fixed.id),)
assert analyzer.resolve_thread_ids(resolved_fixed, ()) == ()
```

Отдельно проверить, что marker от чужого author остаётся human reply, canonical UUID trusted-author comparison case-insensitive, а не-UUID author ID сравнивается после NFC normalization строго. Parser-level test вызывает `parse_marker` с uppercase/lowercase представлениями одного UUID и с двумя различающимися opaque IDs.

- [x] **Step 2: Запустить новый test module и подтвердить RED**

Run:

```bash
pytest -q ai_review/tests/suites/services/review/test_followup_state.py
```

Expected: FAIL с `ModuleNotFoundError` для `followup_state`.

- [x] **Step 3: Реализовать `FollowupStateAnalyzer`**

Создать immutable helper без settings/env/IO. Сигнатуры:

Определить `FollowupStateAnalyzer.__init__(trusted_author_id: str)`, `marker(comment: ReviewCommentSchema) -> ReviewMarker | None`, `pending_comments(thread, related_threads=()) -> tuple[ReviewCommentSchema, ...]`, `pending_ids(thread, related_threads=()) -> tuple[str, ...]`, `continuations(thread, related_threads) -> tuple[ReviewThreadSchema, ...]`, `last_followup_requires_resolve(thread) -> bool` и `resolve_thread_ids(thread, related_threads) -> tuple[str, ...]`. Последний метод возвращает unresolved continuation IDs и ID исходной unresolved-дискуссии, если её последний followup marker имеет `fixed`/`withdrawn` либо валидный knowledge block.

Перенести только pure-ветвления из `FollowupReviewRunner`; использовать существующие parsers, ограничение pending `[:50]` и UTF-8/NFC canonical IDs. В `parse_marker` централизованно канонизировать UUID author IDs, а opaque IDs — NFC без case-fold. Не читать `AI_REVIEW_*` внутри helper.

- [x] **Step 4: Перевести `FollowupReviewRunner` на helper**

Создать `self.state = FollowupStateAnalyzer(self.author_id)` после чтения trusted ID. Сохранить private wrapper methods `_marker`, `_pending_comments`, `_pending`, `_continuations`, `_last_followup_requires_resolve`, делегирующие helper, чтобы не расширять diff остальных runner methods за пределы механической замены. В resolve-only ветке использовать `resolve_thread_ids` для выбора тех же unresolved original/continuation threads, сохранив существующую повторную загрузку перед mutation.

- [x] **Step 5: Запустить state и полный followup regression suites**

Run:

```bash
pytest -q ai_review/tests/suites/services/review/test_followup_state.py
pytest -q ai_review/tests/suites/services/review/runner/test_followup.py
pytest -q ai_review/tests/suites/services/vcs/test_markers.py
```

Expected: PASS; число и смысл существующих followup tests не изменены.

- [x] **Step 6: Commit**

```bash
git add ai_review/services/review/followup_state.py ai_review/services/review/runner/followup.py ai_review/services/vcs/markers.py ai_review/tests/suites/services/review/test_followup_state.py ai_review/tests/suites/services/vcs/test_markers.py
git commit -m "refactor: share followup state analysis"
```

### Task 2: Добавить типизированные GitFlic dispatch API

**Files:**
- Modify: `ai_review/clients/gitflic/schema.py`
- Modify: `ai_review/clients/gitflic/client.py`
- Modify: `ai_review/tests/suites/clients/gitflic/test_client.py`

**Interfaces:**
- Consumes: существующий `GitFlicHTTPClient`, `_get`, `_post`, pagination/fallback transport.
- Produces: `list_branch_protections`, `start_pipeline`, `GitFlicBranchProtection`, `GitFlicPipelineStartRequest`, `GitFlicPipelineStartResponse`.

- [x] **Step 1: Написать failing schema/client tests**

Добавить tests:

```python
protections = await client.list_branch_protections("rt-vt", "sppr")
assert protections[0].branchTemplate == "ai-review-control"

response = await client.start_pipeline(
    "rt-vt",
    "sppr",
    GitFlicPipelineStartRequest(
        refName="ai-review-control",
        variables=(GitFlicPipelineVariable(key="AI_REVIEW_REQUESTED", value="true"),),
    ),
)
assert response.display_id == "3310"
```

HTTP fixtures обязаны проверить percent-encoded aliases, pagination второй страницы обычного клиента, missing `_embedded` при `totalElements>0`, duplicate protection records, integer `localId`, canonical UUID fallback и отклонение control characters. Для POST отдельно вернуть 401, 403, 500 и `httpx.ReadTimeout`; каждый case подтверждает ровно один request и отсутствие fallback.

Добавить dispatch-strict list/discussion fixtures: negative metadata; `totalElements>0,totalPages=0`; неправильный `page.number`; totals меняются между страницами; cumulative raw count не равен `totalElements`; duplicate MR `localId`; отсутствующий/null status; более пяти discussion pages; более десяти list pages/1000 raw elements. Каждый case завершается typed protocol error; service-level test подтверждает zero POST.

Для каждого response model добавить malformed HTTP 200 fixture с canary в JSON value и private header. Ожидаемая типизированная ошибка содержит только `endpoint_code`, `status_code=200`, безопасный request ID и `error_code=invalid_response`; canary отсутствует в `str(error)`, attributes, CLI JSON и captured logs.

Добавить redirect test: GET возвращает 302 с `Location: https://attacker.invalid/`; ожидается `GitFlicHTTPClientError`, handler видит ровно один запрос и attacker endpoint не вызывается.

- [x] **Step 2: Запустить client tests и подтвердить RED**

Run:

```bash
pytest -q ai_review/tests/suites/clients/gitflic/test_client.py -k 'protection or pipeline or redirect'
```

Expected: FAIL из-за отсутствующих моделей/методов и текущего `follow_redirects=True`.

- [x] **Step 3: Добавить strict Pydantic models**

В `schema.py` определить exact fields и validators:

```python
class GitFlicBranchProtection(GitFlicModel):
    branchTemplate: str
    allowedToPush: str
    allowForcePush: bool
    priority: int = 0

class GitFlicPipelineVariable(GitFlicModel):
    key: str
    value: str

class GitFlicPipelineStartRequest(GitFlicModel):
    refName: str
    isTag: bool = False
    variables: tuple[GitFlicPipelineVariable, ...]

class GitFlicPipelineStartResponse(GitFlicModel):
    localId: int | None = None
    pipeline_uuid: str | None = None
    id: str | None = None

    @property
```

Добавить property `display_id(self) -> str`: он принимает положительный integer `localId`, иначе canonical UUID v1–v5 из `pipeline_uuid`/`id`; лишние и управляющие символы отклоняются.

- [x] **Step 4: Реализовать API methods и запрет redirect**

Удалить `follow_redirects=True` из `_get`. Расширить `_request(method, url, *, allow_fallback: bool = True, **kwargs)` и вызвать `_post` с `allow_fallback=False` плюс `NO_RETRY`, чтобы POST не повторялся ни transport, ни token fallback. Реализовать paginated `list_branch_protections` с embedded property `branchProtectionApiModelList`, max 10 страниц/1000 элементов. Добавить dispatch-specific strict readers: list MR — максимум 10 страниц size=100, discussions — максимум 5 страниц size=100; они валидируют expected page number, stable totals, cumulative count, unique MR IDs и обязательный status. Реализовать POST `project/{owner}/{project}/cicd/pipeline/start` с `request.model_dump()` и strict response validation.

Добавить `GitFlicProtocolError`, который принимает только allowlisted `endpoint_code`, `status_code`, `error_code="invalid_response"` и safe request ID. Все `model_validate_json`/JSON decode errors в public client methods перехватывать и преобразовывать в него без `str(ValidationError)` и response content.

- [x] **Step 5: Запустить весь GitFlic client suite**

Run:

```bash
pytest -q ai_review/tests/suites/clients/gitflic/test_client.py
```

Expected: PASS; существующие token fallback/retry/error-redaction tests остаются зелёными.

- [x] **Step 6: Commit**

```bash
git add ai_review/clients/gitflic/schema.py ai_review/clients/gitflic/client.py ai_review/tests/suites/clients/gitflic/test_client.py
git commit -m "feat: add GitFlic dispatch API"
```

### Task 3: Реализовать neutral thread adapter и dispatch classifier

**Files:**
- Modify: `ai_review/services/vcs/gitflic/adapter.py`
- Modify: `ai_review/services/vcs/gitflic/client.py`
- Modify: `ai_review/services/knowledge/gitflic_source.py`
- Modify: `ai_review/tests/suites/services/vcs/gitflic/test_adapter.py`
- Modify: `ai_review/tests/suites/services/knowledge/test_source.py`
- Create: `ai_review/services/dispatch/__init__.py`
- Create: `ai_review/services/dispatch/models.py`
- Create: `ai_review/tests/suites/services/dispatch/test_service.py`

**Interfaces:**
- Consumes: `GitFlicDiscussion`, `ReviewThreadSchema`, `FollowupStateAnalyzer`.
- Produces: `to_review_thread`, `DispatchMode`, `DispatchDecision`, `classify_dispatch`.

- [x] **Step 1: Написать failing adapter и classifier tests**

Проверить преобразование general/inline discussion, `resolved`, порядок root/replies и IDs. Таблица classifier cases должна содержать:

```text
no trusted root marker                 -> initial
stale finding/summary, current head new -> initial
current finding without summary        -> initial recovery
current terminal summary               -> none
stale finding plus new human reply      -> followup
finding marker in summary thread       -> initial
summary marker in inline thread        -> initial
untrusted fake root marker              -> initial
one uncovered human reply               -> followup, pending=1
v1/v2 covered reply                     -> none
v2 continuation covers origin reply     -> none
fixed/withdrawn unresolved              -> followup, pending=0
valid knowledge unresolved              -> followup, pending=0
damaged knowledge unresolved            -> none
resolved terminal followup              -> none
```

- [x] **Step 2: Запустить tests и подтвердить RED**

Run:

```bash
pytest -q ai_review/tests/suites/services/vcs/gitflic/test_adapter.py
pytest -q ai_review/tests/suites/services/dispatch/test_service.py -k classify
```

Expected: FAIL на отсутствующих `to_review_thread`/`classify_dispatch`.

- [x] **Step 3: Вынести public `to_review_thread`**

Перенести тело `GitFlicVCSClient._thread` в adapter function `to_review_thread(discussion: GitFlicDiscussion) -> ReviewThreadSchema`; существующий client и `GitFlicKnowledgeSource.get_review_threads` вызывают её и не меняют внешний VCS/knowledge behavior. Parity test подаёт одну discussion в оба пути и сравнивает одинаковые `model_dump()`.

- [x] **Step 4: Добавить dispatch models и pure classifier**

В `models.py` определить frozen/extra-forbid модели:

```python
class DispatchMode(StrEnum):
    INITIAL = "initial"
    FOLLOWUP = "followup"
    NONE = "none"

class DispatchDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    mode: DispatchMode
    reason: str
    pending_count: int = Field(ge=0)

```

Добавить `classify_dispatch(threads: Sequence[ReviewThreadSchema], trusted_author_id: str, current_head: str) -> DispatchDecision`.

Сначала для исторических и текущих trusted started threads проверить через `FollowupStateAnalyzer` pending replies и `resolve_thread_ids`; это даёт `followup` независимо от marker head. Если followup не требуется, trusted terminal summary в `ThreadKind.SUMMARY` с `marker.head == current_head` даёт `none`. Иначе вернуть `initial`: сюда входят новый head, stale markers и current-head finding без terminal summary, который worker завершит через existing partial-recovery path. Finding учитывается только в `ThreadKind.INLINE`, summary — только в `ThreadKind.SUMMARY`.

- [x] **Step 5: Запустить adapter/classifier и VCS regression tests**

Run:

```bash
pytest -q ai_review/tests/suites/services/vcs/gitflic ai_review/tests/suites/services/knowledge/test_source.py
pytest -q ai_review/tests/suites/services/dispatch/test_service.py -k classify
```

Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add ai_review/services/vcs/gitflic/adapter.py ai_review/services/vcs/gitflic/client.py ai_review/services/knowledge/gitflic_source.py ai_review/services/dispatch ai_review/tests/suites/services/vcs/gitflic/test_adapter.py ai_review/tests/suites/services/knowledge/test_source.py ai_review/tests/suites/services/dispatch/test_service.py
git commit -m "feat: classify GitFlic dispatch state"
```

### Task 4: Реализовать dispatch orchestration service

**Files:**
- Create: `ai_review/services/dispatch/service.py`
- Modify: `ai_review/services/dispatch/models.py`
- Modify: `ai_review/tests/suites/services/dispatch/test_service.py`

**Interfaces:**
- Consumes: `GitFlicHTTPClient`, `DispatchSelection`, `classify_dispatch`.
- Produces: `GitFlicDispatchService.run(selection) -> DispatchReport`.

- [x] **Step 1: Написать failing service tests с fake gateway**

Определить `FakeGitFlicDispatchClient` с recorded calls и проверить:

- explicit IDs дедуплицируются с сохранением первого порядка; max 10 применяется после дедупликации;
- `all` сначала полностью получает/валидирует/sorts snapshot, затем вызывает detail/discussions;
- `all` с 11+ открытыми MR даёт fatal `request_budget_exceeded` до detail/protection/POST;
- empty all возвращает successful report без protection/POST;
- exact protection отсутствует, дублируется, разрешает DEVELOPERS или force push — zero POST и fatal report;
- exact safe protection плюс совпадающий `*`, `**`, `ai-review-*` или любой иной wildcard — ambiguity и zero POST независимо от priority;
- `none` не вызывает POST;
- actionable MR непосредственно перед POST повторно получает detail и discussions, заново классифицируется и использует актуальные mode/head/source/target;
- race `initial→none` даёт skip без POST, `initial↔followup` запускает актуальный mode;
- MR закрылся перед POST: skip в all, failure в explicit;
- 404/500/invalid response одного explicit MR не мешает следующему, но report получает `status="partial"`, `failure_scope="item"`;
- pipeline variables exact и не содержат selector/token;
- ambiguous POST error записывается один раз и не повторяется.
- 429 на snapshot/protection даёт fatal zero POST; 429 одного MR до какого-либо POST даёт item failure с продолжением, а request-budget cap гарантирует, что собственный worst-case dispatcher не планирует более 433 HTTP attempts.

- [x] **Step 2: Запустить service tests и подтвердить RED**

Run:

```bash
pytest -q ai_review/tests/suites/services/dispatch/test_service.py -k service
```

Expected: FAIL на отсутствующих `DispatchSelection`, `DispatchReport`, `GitFlicDispatchService`.

- [x] **Step 3: Реализовать immutable selection/result models**

Точные модели:

```python
class DispatchSelection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    all_open: bool = False
    merge_request_ids: tuple[int, ...] = ()

class DispatchItemResult(BaseModel):
    mr_id: int
    mode: DispatchMode | None
    outcome: Literal["started", "skipped", "failed"]
    reason: str
    pipeline_id: str | None = None

class DispatchReport(BaseModel):
    status: Literal["success", "partial", "fatal"]
    failure_scope: Literal["none", "item", "source", "security"]
    error_code: str | None = None
    selected_count: int
    started_count: int
    skipped_count: int
    failed_count: int
    items: tuple[DispatchItemResult, ...]
```

Before-validator `DispatchSelection` дедуплицирует IDs с сохранением порядка, затем требует XOR: `all_open` либо 1–10 положительных IDs.

- [x] **Step 4: Реализовать `GitFlicDispatchService`**

Constructor принимает client, owner/project/control_ref/trusted_author_id. `run` выполняет:

1. Для all через strict pagination (до 10 страниц size=100) полностью получает snapshot, требует не более 10 `OPENED`, dedupe/sort; для explicit берёт validated tuple. Превышение даёт fatal `request_budget_exceeded` до остальных GET/POST.
2. При пустом snapshot возвращает success.
3. Получает все branch protections, сопоставляет `control_ref` с `*`/`?`/`**` по семантике GitFlic и требует единственное matching exact safe rule; любое overlap блокирует запуск независимо от priority.
4. Для каждого ID получает detail и strict discussions, конвертирует threads, классифицирует с `current_head=detail.sourceBranch.hash`.
5. Для `none` пишет skipped.
6. Для actionable повторно получает detail и strict discussions, проверяет `localId`, `OPENED`, lowercase 40-hex head и safe branch refs без absolute/`..`, затем заново классифицирует с актуальным head. Актуальный `none` становится skip; изменившийся actionable mode используется в body.
7. Создаёт exact `GitFlicPipelineStartRequest` и вызывает `start_pipeline` один раз.
8. Локальная ошибка элемента становится failed со стабильным allowlisted `error_code`, без `str(error)`; цикл продолжается. Protection/snapshot/API preflight error даёт fatal report со scope `source|security` до POST.

- [x] **Step 5: Запустить service suite**

Run:

```bash
pytest -q ai_review/tests/suites/services/dispatch/test_service.py
```

Expected: PASS; recorded calls подтверждают отсутствие POST для `none` и отсутствие частичного POST при malformed all snapshot.

- [x] **Step 6: Commit**

```bash
git add ai_review/services/dispatch/models.py ai_review/services/dispatch/service.py ai_review/tests/suites/services/dispatch/test_service.py
git commit -m "feat: orchestrate GitFlic AI dispatch"
```

### Task 5: Добавить `ai-review gitflic-dispatch`

**Files:**
- Create: `ai_review/cli/commands/gitflic_dispatch.py`
- Create: `ai_review/tests/suites/cli/test_gitflic_dispatch.py`
- Modify: `ai_review/cli/main.py`
- Modify: `ai_review/tests/suites/image/test_image_smoke.py`

**Interfaces:**
- Consumes: CLI options, environment secrets, `GitFlicDispatchService`.
- Produces: JSON `DispatchReport` на stdout и единый exit mapping: success→`0`, partial item→`1`, usage/config→`2`, fatal source/security/preflight→`3`.

- [ ] **Step 1: Написать failing CLI tests**

Через `CliRunner` и monkeypatched `build_runtime` проверить `--all`, repeated `--merge-request-id`, mutual exclusion, no selection, >10 unique IDs, duplicate IDs deduped в первом порядке, zero ID, invalid control ref/API URL. Проверить, что token options отсутствуют в `gitflic-dispatch --help`.

Runtime tests устанавливают environment:

```text
AI_REVIEW_GITFLIC_TOKEN=primary
AI_REVIEW_GITFLIC_TOKEN2=reserve
AI_REVIEW_GITFLIC_USER_ID=12345678-1234-4234-9234-123456789abc
```

При отсутствии author ID fake `/user/me` задаёт canonical UUID; malformed ID даёт exit 2 до branch protection/POST. Успех печатает ровно один JSON object, error text не содержит token/response body.

- [ ] **Step 2: Запустить CLI tests и подтвердить RED**

Run:

```bash
pytest -q ai_review/tests/suites/cli/test_gitflic_dispatch.py
```

Expected: FAIL: команда не зарегистрирована.

- [ ] **Step 3: Реализовать runtime factory**

В `gitflic_dispatch.py` определить:

Определить `DispatchExitCode(IntEnum)` со значениями `OK=0`, `PARTIAL=1`, `USAGE=2`, `SOURCE=3` и coroutine `run_gitflic_dispatch_command(*, owner: str, project: str, control_ref: str, api_url: str, merge_request_ids: Sequence[int], all_open: bool) -> int`.

Factory валидирует HTTPS URL без credentials/query/fragment, обязательный primary token, optional distinct `AI_REVIEW_GITFLIC_TOKEN2`, trusted author env либо `/user/me`; создаёт `HTTPClientWithTokenConfig`/`GitFlicHTTPClient`, всегда закрывает client в `finally`, печатает `report.model_dump_json()`. Единственная parametrized таблица закрепляет mapping: success→0, partial/item→1, usage/config→2, fatal source/security/preflight→3.

- [ ] **Step 4: Зарегистрировать Typer command**

Сигнатура в `main.py`:

Зарегистрировать `@app.command("gitflic-dispatch")` с required options `owner`, `project`, `control_ref`, optional repeated `merge_request_id: list[int] | None`, flag `all_open: bool` и `api_url: str = "https://api.gitflic.ru"`. Точные option names: `--owner`, `--project`, `--control-ref`, `--merge-request-id`, `--all`, `--api-url`.

Полученный nonzero code преобразовать в `typer.Exit`; secrets не добавлять в signature.

- [ ] **Step 5: Добавить image smoke contract**

В `test_image_smoke.py` вызвать `CliRunner().invoke(app, ["gitflic-dispatch", "--help"])`, потребовать exit 0 и options `--all`, `--merge-request-id`, `--control-ref`; убедиться, что `token` отсутствует в help.

- [ ] **Step 6: Запустить CLI/image tests**

Run:

```bash
pytest -q ai_review/tests/suites/cli/test_gitflic_dispatch.py
pytest -q ai_review/tests/suites/image/test_image_smoke.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add ai_review/cli/main.py ai_review/cli/commands/gitflic_dispatch.py ai_review/tests/suites/cli/test_gitflic_dispatch.py ai_review/tests/suites/image/test_image_smoke.py
git commit -m "feat: add GitFlic dispatch command"
```

### Task 6: Документировать и выпустить подписанный image

**Files:**
- Modify: `docs/cli/README.md`
- Modify: `docs/ci/README.md`
- Modify: `README.md`
- Modify: `.github/workflows/workflow-publish.yml`
- Verify: `Dockerfile`, `build_release.py`, `AGENTS.md`

**Interfaces:**
- Consumes: готовую `gitflic-dispatch` command.
- Produces: подписанный image ref формата `ghcr.io/kyrales/ai-review@sha256:` плюс 64 lowercase hex и зафиксированный release evidence для интеграции.

- [ ] **Step 1: Добавить failing docs/workflow assertions**

Добавить в image test чтение workflow/docs и проверки: publish smoke вызывает `gitflic-dispatch --help`; документация описывает `--all`, repeated ID, environment-only tokens, exit codes и no host Python.

- [ ] **Step 2: Запустить assertion и подтвердить RED**

Run:

```bash
pytest -q ai_review/tests/suites/image/test_image_smoke.py
```

Expected: FAIL на отсутствующем publish/docs contract.

- [ ] **Step 3: Обновить документацию и publish smoke**

Добавить пример container command без значений secrets. В workflow до push запустить hardened local image с `gitflic-dispatch --help`. После push сохранить `published_ref` exact digest как job output и подписать именно его существующим keyless cosign identity.

- [ ] **Step 4: Запустить полный Python suite**

Run:

```bash
pytest -q
```

Expected: PASS.

- [ ] **Step 5: Собрать обязательный Windows release**

Run из `F:\1C\Projects\RT_VT\ai_review`:

```powershell
python build_release.py
.\artifacts\releases\win-x64\ai-review.exe --help
.\artifacts\releases\win-x64\ai-review.exe gitflic-dispatch --help
```

Expected: оба help exit 0; binaries остаются ignored и не добавляются в git.

- [ ] **Step 6: Commit `ai_review` docs/workflow**

```bash
git add docs/cli/README.md docs/ci/README.md README.md .github/workflows/workflow-publish.yml ai_review/tests/suites/image/test_image_smoke.py
git commit -m "docs: describe GitFlic dispatch rollout"
```

- [ ] **Step 7: Merge/publish и зафиксировать digest**

После review/merge `feature/gitflic-dispatch` дождаться green `workflow-publish.yml`. Скопировать полный `published_ref` из output в stdin следующего скрипта; он принимает только exact digest, проверяет cosign issuer/identity и запускает help на Linux:

```bash
IFS= read -r dispatch_image
[[ "$dispatch_image" =~ ^ghcr\.io/kyrales/ai-review@sha256:[0-9a-f]{64}$ ]] || exit 2
docker pull "$dispatch_image"
docker run --rm --pull=never gcr.io/projectsigstore/cosign@sha256:9e5c2f2edc34351160407ca3416c61855bdf9403c3c5936e0f0be7fc261611b8 verify \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity https://github.com/Kyrales/ai-review/.github/workflows/workflow-publish.yml@refs/heads/main \
  "$dispatch_image"
docker run --rm --read-only --cap-drop=ALL --security-opt=no-new-privileges \
  --user 65532:65532 --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  "$dispatch_image" gitflic-dispatch --help
```

Expected: pull по immutable digest и help exit 0. Сохранить полный `published_ref` в описании интеграционного изменения `sppr_gitflic`; не подставлять `latest`.

### Task 7: Добавить тонкий launcher в `sppr_gitflic`

**Files:**
- Create: `../sppr_gitflic/tools/ai-review/dispatch-ai-review.sh`
- Create: `../sppr_gitflic/tools/ai-review/tests/test-dispatch-ai-review.sh`
- Reference: `../sppr_gitflic/tools/ai-review/run-ai-review.sh:128-151`

**Interfaces:**
- Consumes: `AI_REVIEW_MR_IDS`, CI ref/tag, env-file secrets, exact published image digest.
- Produces: hardened invocation `ai-review gitflic-dispatch`; host dependencies только Bash/Docker/Cosign flow, без Python/PowerShell.

- [ ] **Step 1: Добавить fake docker/cosign failing tests**

Harness записывает argv и список переданных env names без значений. Cases:

- untrusted branch/tag и malformed selector завершаются до чтения canary env и до docker;
- unset selector завершается до docker (CI rule отдельно не создаёт job);
- empty/`all` дают ровно `--all`;
- `79,79,81` даёт `--merge-request-id 79 --merge-request-id 81`;
- invalid `ALL`, whitespace, zero, leading zero, empty CSV item, 11 unique IDs отвергаются;
- image ref exact `ghcr.io/kyrales/ai-review@sha256:[0-9a-f]{64}`;
- docker flags содержат read-only/cap-drop/no-new-privileges/user/tmpfs и не содержат Docker socket/project mount;
- token values отсутствуют в argv/stdout/stderr.

- [ ] **Step 2: Запустить launcher test и подтвердить RED**

Run из `../sppr_gitflic`:

```bash
bash tools/ai-review/tests/test-dispatch-ai-review.sh
```

Expected: FAIL, launcher отсутствует.

- [ ] **Step 3: Реализовать pre-secret gate**

Начало script:

```bash
#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
[[ -z "${CI_COMMIT_TAG:-}" && "${CI_COMMIT_REF_NAME:-}" == ai-review-control ]] || exit 2
[[ "${AI_REVIEW_MR_IDS+x}" == x ]] || exit 2
```

До source env построить Bash array `selection_args`: пусто/точное `all` → `(--all)`; CSV regex → dedupe 1–10 и repeated arguments. Только затем source readable `SPPR_CI_ENV_FILE` и require primary token.

- [ ] **Step 4: Реализовать pinned/cosign verified hardened launch**

Вставить exact digest, полученный в Task 6, константой `dispatch_image`. Pull по digest; проверить cosign теми же pinned verifier image, issuer и workflow identity, что `run-ai-review.sh`. Запустить container без mounts/socket, передавая только имена environment variables:

```bash
docker run --rm --pull=never --read-only --cap-drop=ALL \
  --security-opt=no-new-privileges --user 65532:65532 \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --env AI_REVIEW_GITFLIC_TOKEN \
  --env AI_REVIEW_GITFLIC_TOKEN2 \
  --env AI_REVIEW_GITFLIC_USER_ID \
  "$dispatch_image" gitflic-dispatch \
  --owner rt-vt --project sppr --control-ref ai-review-control \
  "${selection_args[@]}"
```

Обернуть container command в `timeout --signal=TERM --kill-after=30s 840s`; job получает `timeout: 15m`. Не включать token values в command line. Не публиковать artifacts. Fake docker test имитирует timeout/exit 124 и подтверждает ненулевой итог без печати env values.

- [ ] **Step 5: Запустить launcher tests/syntax**

Run:

```bash
bash -n tools/ai-review/dispatch-ai-review.sh
bash tools/ai-review/tests/test-dispatch-ai-review.sh
```

Expected: PASS; fake log не содержит secrets и host Python invocation.

- [ ] **Step 6: Commit launcher**

```bash
git add tools/ai-review/dispatch-ai-review.sh tools/ai-review/tests/test-dispatch-ai-review.sh
git commit -m "feat: launch GitFlic AI dispatch container"
```

### Task 8: Подтвердить empty-variable contract, подключить job и исключить рекурсию

**Files:**
- Modify: `../sppr_gitflic/gitflic-ci_linux.yaml`
- Modify: `../sppr_gitflic/tools/ai-review/tests/test-ci-contract.ps1`
- Modify: `../sppr_gitflic/tools/ci/tests/test-gitflic-ci.sh`
- Modify: `../sppr_gitflic/tools/ci/tests/README.md`

**Interfaces:**
- Consumes: ручной pipeline на `ai-review-control` с явно добавленной `AI_REVIEW_MR_IDS`.
- Produces: только job `ai_review_dispatch`; child содержит существующий `ai_review`, но не dispatcher.

- [ ] **Step 1: До mutation-capable job проверить GitFlic empty/unset semantics**

В отдельном временном commit ветки `ai-review-control` добавить no-secret/no-POST probe job с тем же rule `$AI_REVIEW_MR_IDS != null`, script только печатает фиксированное `dispatch-contract-probe`. Через UI создать два pipeline: без variable и с явно добавленной пустой variable. Требование: в первом probe отсутствует, во втором присутствует. Затем удалить probe тем же revert/следующим commit до добавления launcher job. Если поведение иное, остановить Task 8 и пересмотреть activation contract/spec; не подключать настоящий dispatcher и не ослаблять требование пустого alias.

- [ ] **Step 2: Добавить failing YAML contract assertions**

Проверить block:

```text
tags: [linux-docker-145]
timeout: 15m
GIT_STRATEGY: "clone"
ARTIFACT_DOWNLOAD_STRATEGY: "none"
AI_REVIEW_DISPATCH_JOB: "true"
AI_REVIEW_MR_IDS != null
AI_REVIEW_REQUESTED != "true"
CI_COMMIT_TAG == null
CI_COMMIT_REF_NAME == "ai-review-control"
bash tools/ai-review/dispatch-ai-review.sh
```

Запретить `allow_failure`, artifacts, inline implementation, Python и PowerShell в job. Проверить, что worker POST contract не содержит `AI_REVIEW_MR_IDS`.

- [ ] **Step 3: Запустить contracts и подтвердить RED**

Run:

```bash
bash tools/ci/tests/test-gitflic-ci.sh
pwsh -NoProfile -File tools/ai-review/tests/test-ci-contract.ps1
```

Expected: FAIL на отсутствующем job.

- [ ] **Step 4: Добавить `ai_review_dispatch` и before-script guard**

Job rule:

```yaml
rules:
  - if: '$AI_REVIEW_MR_IDS != null && $AI_REVIEW_REQUESTED != "true" && $CI_COMMIT_TAG == null && $CI_COMMIT_REF_NAME == "ai-review-control"'
```

Job-local `AI_REVIEW_DISPATCH_JOB=true`, `timeout: 15m`; global env source пропускается одновременно для worker и dispatcher, чтобы launcher сам прочитал secrets только после runtime gate. Обычные jobs на `ai-review-control` и child worker правила не расширять.

- [ ] **Step 5: Зарегистрировать launcher test и прогнать contracts**

Run:

```bash
bash tools/ai-review/tests/test-dispatch-ai-review.sh
bash tools/ci/tests/test-gitflic-ci.sh
pwsh -NoProfile -File tools/ai-review/tests/test-ci-contract.ps1
```

Expected: PASS; отсутствующая variable не создаёт job, explicit empty остаётся допустимой по expression contract.

- [ ] **Step 6: Commit CI adapter**

```bash
git add gitflic-ci_linux.yaml tools/ai-review/tests/test-ci-contract.ps1 tools/ci/tests/test-gitflic-ci.sh tools/ci/tests/README.md
git commit -m "ci: dispatch selected AI reviews from GitFlic"
```

### Task 9: Документировать операторский flow и совместимость

**Files:**
- Modify: `../sppr_gitflic/tools/ci/README.md`
- Modify: `../sppr_gitflic/doc/specs/24_ai_review_gitflic_design.md`
- Test: `../sppr_gitflic/tools/ci/tests/test-gitflic-ci.sh`

**Interfaces:**
- Consumes: готовый двухрепозиторный runtime contract.
- Produces: инструкция GitFlic UI и явное сохранение локальных PowerShell flows.

- [ ] **Step 1: Добавить failing documentation assertions**

Потребовать строки `AI_REVIEW_MR_IDS=79,81,84`, `AI_REVIEW_MR_IDS=all`, `AI_REVIEW_MR_IDS=` и формулировки: no host Python; `Start-AiReview.ps1` и `Sync-AiReviewKnowledge.ps1` остаются независимыми локальными инструментами.

- [ ] **Step 2: Обновить design и operator README**

Описать UI steps, selector semantics, `none`, partial failure, pinned signed container, отсутствие PowerShell/Python в Linux job, риск большой очереди при `all` и необходимость нового dispatch вместо Restart после изменения head.

- [ ] **Step 3: Запустить docs contract**

Run:

```bash
bash tools/ci/tests/test-gitflic-ci.sh
```

Expected: PASS.

- [ ] **Step 4: Commit docs**

```bash
git add tools/ci/README.md doc/specs/24_ai_review_gitflic_design.md tools/ci/tests/test-gitflic-ci.sh
git commit -m "docs: describe containerized GitFlic dispatch"
```

### Task 10: Полная проверка и контролируемый rollout

**Files:**
- Verify: все файлы Tasks 1–9.
- Preserve: `../sppr_gitflic/tools/ai-review/Start-AiReview.ps1`, `Sync-AiReviewKnowledge.ps1`, `AiReview.psm1`.

**Interfaces:**
- Consumes: опубликованный signed digest и изменения обоих repositories.
- Produces: branch-ready интеграция и live evidence без секретов.

- [ ] **Step 1: Проверить `ai_review`**

Run:

```bash
ruff check ai_review
pytest -q
```

Expected: PASS.

- [ ] **Step 2: Проверить `sppr_gitflic` static/contract suites**

Run:

```bash
bash -n tools/ai-review/dispatch-ai-review.sh
bash tools/ai-review/tests/test-dispatch-ai-review.sh
bash tools/ai-review/tests/test-run-ai-review.sh
bash tools/ci/tests/test-gitflic-ci.sh
pwsh -NoProfile -File tools/ai-review/tests/test-ai-review.ps1
pwsh -NoProfile -File tools/ai-review/tests/test-sync-ai-review-knowledge.ps1
pwsh -NoProfile -File tools/ai-review/tests/test-ci-contract.ps1
git diff --exit-code "$(git merge-base origin/ai-review-control HEAD)..HEAD" -- tools/ai-review/Start-AiReview.ps1 tools/ai-review/Sync-AiReviewKnowledge.ps1 tools/ai-review/AiReview.psm1
```

Expected: PASS и zero diff для старого PowerShell runtime.

- [ ] **Step 3: Проверить EDT state**

Вызвать доступный `edt-mcp.get_problem_summary(projectName: "sppr")` либо ближайший опубликованный summary tool. Expected: CI/docs изменения не добавили EDT errors; исходные unrelated problems перечислены отдельно.

- [ ] **Step 4: Проверить image digest на Linux runner read-only**

Pull/verify exact digest и выполнить `gitflic-dispatch --help` hardened-командой из Task 6. Expected: exit 0; `python3` на host не вызывается и не добавляется в runner requirements.

- [ ] **Step 5: Получить отдельное подтверждение на live smoke одного MR**

До внешней мутации показать пользователю exact MR ID, ожидаемый mode и image digest. После подтверждения создать pipeline, указав в `AI_REVIEW_MR_IDS` ровно показанный десятичный ID. Expected: dispatcher SUCCESS, один worker только для actionable MR, current head, отсутствие secrets/artifacts.

- [ ] **Step 6: Проверить empty/all UI contract с отдельным подтверждением**

Read-only API preview показывает exact snapshot и число actionable MR. После отдельного подтверждения выполнить контролируемые pipelines с `AI_REVIEW_MR_IDS=all` и с явно добавленной пустой variable. Expected: один и тот же snapshot; `none` не создаёт workers. Любое расхождение блокирует завершение rollout и требует исправления activation contract.

- [ ] **Step 7: Зафиксировать безопасный evidence**

Записать pipeline/job IDs, image digest, selector mode, snapshot IDs, started/skipped/failed counts и статусы. Не записывать tokens, raw API bodies, prompts и response headers.

## Self-Review Checklist

- [ ] Каждое требование design spec связано с task/test.
- [ ] В `sppr_gitflic` не создаются `.py` и не добавляется host Python requirement.
- [ ] `FollowupReviewRunner` и dispatcher используют один `FollowupStateAnalyzer`.
- [ ] GET redirect forbidden, POST start no-retry и error redaction закреплены tests.
- [ ] Selector absent/empty/all/CSV имеет отдельные contract tests.
- [ ] Image publish предшествует pin digest и интеграции `sppr_gitflic`.
- [ ] Existing CLI/worker/PowerShell flows имеют regression tests.
- [ ] В плане отсутствуют плейсхолдеры, неназванные error cases и неразрешённые интерфейсы.
