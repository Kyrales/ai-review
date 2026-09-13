# AI Review Knowledge Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Автоматически извлекать из новых ответов доверенных ревьюверов явно сформулированные переиспользуемые знания, публиковать их в followup с тегом `#ai-review-knowledge` и по подтверждению пользователя переносить знания из выбранных открытых MR в управляемую секцию `project-rules.md`.

**Architecture:** Вся переносимая логика находится в Python-пакете `ai_review`: строгая конфигурация, provider-neutral marker и review-source protocol, knowledge extractor/compiler, проверка источников, выбор MR и безопасная запись правил. GitFlic реализуется adapter-ом; GitLab сможет реализовать тот же protocol. В конкретном 1С-проекте остаются `.ai-review-ones.yaml`, `ai-review/prompts/project-rules.md` и тонкий PowerShell launcher без GitFlic/LLM/JSON/YAML логики.

**Tech Stack:** Python 3.11+, Pydantic, PyYAML, Typer, httpx, pytest/pytest-asyncio; PowerShell/Pester только для интеграционного launcher и совместимости marker parser.

**Spec:** `docs/superpowers/specs/2026-09-13-ai-review-knowledge-sync-design.md`

## Global Constraints

- Репозиторий реализации: `F:\1C\Projects\RT_VT\ai_review`; интеграционный 1С-проект: `F:\1C\Projects\RT_VT\sppr_gitflic`.
- По умолчанию функциональность выключена: отсутствующая секция `knowledge` эквивалентна `knowledge.enabled: false`.
- Источники знаний — только новые ответы username из allowlist текущего provider; сравнение username регистронезависимое.
- Из одного ответа извлекается не более `max_rules_per_reply`, допустимый диапазон 1–3, default 3. Это не общий лимит MR или запуска.
- Knowledge extractor — отдельный LLM-вызов после verdict-вызова; compiler sync — ещё один отдельный LLM-вызов.
- Sync рассматривает только открытые MR, в которых уже опубликован валидный AI followup с `#ai-review-knowledge`.
- Запись выполняется только после полного diff и подтверждения; `--yes`/`-Yes` пропускает вопрос, но не diff.
- Команда не выполняет commit, push или изменение дискуссий. Автоматические commit-шаги ниже — отдельные контрольные точки для исполнителя, а не поведение функциональности.
- Generic Python-модули и prompts не содержат `rt-vt`, `sppr`, имён EDT source set или бизнес-терминов конкретной конфигурации 1С.
- Перед каждым task сначала убедиться, что worktree не содержит чужих пересекающихся изменений; не перезаписывать их.

---

## Task 1: Строгая переносимая конфигурация knowledge

**Files:**

- Create: `ai_review/libs/config/knowledge.py`
- Create: `ai_review/libs/config/settings.py`
- Create: `ai_review/libs/config/yaml.py`
- Modify: `ai_review/config.py`
- Modify: `ai_review/cli/main.py`
- Test: `ai_review/tests/suites/libs/config/test_knowledge.py`
- Test: `ai_review/tests/suites/libs/config/test_settings.py`
- Test: `ai_review/tests/suites/cli/test_bootstrap.py`

- [x] **Step 1: Написать падающие тесты модели и fail-closed YAML parsing**

Проверить: отсутствующая секция и `enabled: false`; provider-specific списки username; case-insensitive unique usernames; default/границы `max_rules_per_reply`; безопасный относительный `sync.rules_file`; запрет absolute, `..`, symlink escape; duplicate/case-variant keys, unsafe custom tags и неизвестные поля внутри `knowledge`. Безопасные штатные YAML-конструкции (`anchors`, aliases, merge keys, inline collections и стандартные scalar tags) поддерживаются. Subprocess-тесты запускают `ai-review sync-knowledge --help` и `ai-review sync-knowledge --config <path> --all` без `AI_REVIEW_CONFIG_FILE_YAML` и без обязательных legacy `llm/vcs` settings.

- [x] **Step 2: Запустить тесты и подтвердить ожидаемое падение**

Run: `python -m pytest ai_review/tests/suites/libs/config/test_knowledge.py ai_review/tests/suites/libs/config/test_settings.py ai_review/tests/suites/cli/test_bootstrap.py -q`

Expected: import/model assertions fail because knowledge config and strict source do not exist.

- [x] **Step 3: Реализовать минимальный config contract**

Добавить immutable Pydantic-модели:

```python
class KnowledgeSyncConfig(BaseModel):
    rules_file: str = "ai-review/prompts/project-rules.md"

class TrustedReviewersConfig(BaseModel):
    gitflic: tuple[str, ...] = ()
    gitlab: tuple[str, ...] = ()

class KnowledgeConfig(BaseModel):
    enabled: bool = False
    trusted_reviewers: TrustedReviewersConfig = TrustedReviewersConfig()
    max_rules_per_reply: int = Field(default=3, ge=1, le=3)
    sync: KnowledgeSyncConfig = KnowledgeSyncConfig()
```

`StrictYamlConfigSettingsSource` должен отклонять duplicate keys до создания dict и применять запреты YAML trust-boundary только к node `knowledge`. Перенести класс `Settings` в модуль без side effects и добавить `load_settings(config_path: Path | None) -> Settings`; только legacy `ai_review/config.py` сохраняет совместимый `settings = load_settings()`. Для локальной команды добавить отдельный `SyncBootstrapSettings` и `load_sync_settings(config_path)`, которые читают `knowledge` без валидации обязательных CI-секций `llm/vcs`; runtime connection и LLM gateway собираются позднее только при `enabled: true`. `ai_review/cli/main.py` не импортирует legacy settings или command implementations на уровне модуля: handlers делают lazy import после разбора Typer options, а sync импортирует side-effect-free loader напрямую. В полном `Settings` также добавить `knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)`. Добавить `resolve_rules_path(config_path: Path, repository_root: Path) -> Path`: путь считается относительно каталога config, а реальный parent и symlink target обязаны оставаться внутри repository root.

- [x] **Step 4: Запустить тесты конфигурации**

Run: `python -m pytest ai_review/tests/suites/libs/config/test_knowledge.py ai_review/tests/suites/libs/config/test_settings.py ai_review/tests/suites/cli/test_bootstrap.py -q`

Expected: PASS.

- [x] **Step 5: Зафиксировать контрольную точку** *(изменения оставлены без commit по просьбе владельца)*

```bash
git add ai_review/libs/config/knowledge.py ai_review/libs/config/settings.py ai_review/libs/config/yaml.py ai_review/config.py ai_review/cli/main.py ai_review/tests/suites/libs/config/test_knowledge.py ai_review/tests/suites/libs/config/test_settings.py ai_review/tests/suites/cli/test_bootstrap.py
git commit -m "feat: add strict knowledge configuration"
```

## Task 2: Provider-neutral marker v2 и golden vectors

**Files:**

- Create: `ai_review/services/vcs/markers.py`
- Create: `ai_review/tests/fixtures/services/vcs/marker_vectors.json`
- Create: `ai_review/tests/suites/services/vcs/test_markers.py`
- Modify: `ai_review/services/vcs/gitflic/markers.py`
- Modify: `ai_review/tests/suites/services/vcs/gitflic/test_markers.py`

- [x] **Step 1: Добавить падающие golden-vector тесты**

Покрыть v1 read compatibility, v2 round trip с opaque string IDs, canonical base64url, `withdrawn`, duplicate/unknown/out-of-order fields, control characters, provider/project/review/origin-thread fields и точный SHA-256 publication framing из спецификации. Негативные vectors фиксируют: v2 допускает только `kind=followup`; `covered` содержит 1–50 уникальных canonical tokens, отсортированных по normalized raw ID; `origin`/`publication` обязательны; альтернативное base64url/field order отклоняется.

- [x] **Step 2: Подтвердить падение**

Run: `python -m pytest ai_review/tests/suites/services/vcs/test_markers.py ai_review/tests/suites/services/vcs/gitflic/test_markers.py -q`

Expected: v2 API and `withdrawn` assertions fail.

- [x] **Step 3: Вынести codec в общий модуль**

Определить `ReviewMarker`, `MarkerKind`, `render_marker()`, `parse_marker()`, `decorate_ai_message()` и `publication_key()`. Внутренне хранить `covered: tuple[str, ...]`; v1 принимает только UUID wire values, v2 — canonical base64url UTF-8 opaque IDs. GitFlic module временно re-export-ит общий API, чтобы не ломать импорты.

- [x] **Step 4: Запустить marker tests**

Run: `python -m pytest ai_review/tests/suites/services/vcs/test_markers.py ai_review/tests/suites/services/vcs/gitflic/test_markers.py -q`

Expected: PASS, каждый vector проверяет и wire text, и parsed model.

- [x] **Step 5: Зафиксировать контрольную точку** *(изменения оставлены без commit по просьбе владельца)*

```bash
git add ai_review/services/vcs/markers.py ai_review/services/vcs/gitflic/markers.py ai_review/tests/fixtures/services/vcs/marker_vectors.json ai_review/tests/suites/services/vcs/test_markers.py ai_review/tests/suites/services/vcs/gitflic/test_markers.py
git commit -m "feat: add provider-neutral review marker v2"
```

## Task 3: Состояние дискуссий и provider-neutral source protocol

**Files:**

- Modify: `ai_review/services/vcs/types.py`
- Modify: `ai_review/clients/gitflic/schema.py`
- Modify: `ai_review/clients/gitflic/client.py`
- Modify: `ai_review/services/vcs/gitflic/adapter.py`
- Modify: `ai_review/services/vcs/gitflic/client.py`
- Test: `ai_review/tests/suites/clients/gitflic/test_client.py`
- Test: `ai_review/tests/suites/services/vcs/gitflic/test_adapter.py`
- Test: `ai_review/tests/suites/services/vcs/gitflic/test_client.py`

- [x] **Step 1: Написать падающие tests для capabilities и repository review listing**

Тесты фиксируют передачу `resolved`, список только `OPENED` MR с pagination, чтение inline/general дискуссий по произвольному MR ID и `resolve`. GitFlic не получает непроверенный reopen endpoint: adapter объявляет `can_reply_resolved` только если это подтверждено contract fixture, иначе закрытая дискуссия направляется в continuation capability/fallback.

- [x] **Step 2: Подтвердить падение**

Run: `python -m pytest ai_review/tests/suites/clients/gitflic/test_client.py ai_review/tests/suites/services/vcs/gitflic/test_adapter.py ai_review/tests/suites/services/vcs/gitflic/test_client.py -q`

Expected: missing fields/protocol methods fail.

- [x] **Step 3: Реализовать contracts и GitFlic adapter**

Добавить:

```python
class ReviewThreadSchema(BaseModel):
    # existing fields
    resolved: bool | None = None

class ReviewSummarySchema(BaseModel):
    id: str | int
    source_branch: str
    state: Literal["open", "closed"]

class KnowledgeReviewSourceProtocol(Protocol):
    provider: VCSProvider
    project_key: str
    async def list_open_reviews(self) -> list[ReviewSummarySchema]: ...
    async def get_review(self, review_id: str | int) -> ReviewSummarySchema: ...
    async def get_review_threads(self, review_id: str | int) -> list[ReviewThreadSchema]: ...
```

`get_review()` возвращает фактическое состояние `open|closed`, чтобы sync мог повторно подтвердить открытость после выбора. Capability methods для state machine оставить отдельными runtime-checkable protocols (`resolve_thread`, `reopen_thread`, continuation posting), а значения `can_reply_resolved`/`can_reopen` получать от adapter, не из provider-name checks. GitFlic HTTP client использует существующий `/project/{owner}/{project}/merge-request/list`, фильтрует `status.id == OPENED` и не меняет current-MR `VCSClientProtocol` semantics.

- [x] **Step 4: Запустить GitFlic tests**

Run: `python -m pytest ai_review/tests/suites/clients/gitflic/test_client.py ai_review/tests/suites/services/vcs/gitflic -q`

Expected: PASS.

- [x] **Step 5: Зафиксировать контрольную точку** *(изменения оставлены без commit по просьбе владельца)*

```bash
git add ai_review/services/vcs/types.py ai_review/clients/gitflic ai_review/services/vcs/gitflic ai_review/tests/suites/clients/gitflic ai_review/tests/suites/services/vcs/gitflic
git commit -m "feat: expose portable review source capabilities"
```

## Task 4: Knowledge schema, extractor и tagged block

**Files:**

- Create: `ai_review/services/knowledge/__init__.py`
- Create: `ai_review/services/knowledge/schema.py`
- Create: `ai_review/services/knowledge/json.py`
- Create: `ai_review/services/knowledge/block.py`
- Create: `ai_review/services/knowledge/extractor.py`
- Create: `ai_review/prompts/default_knowledge_extractor.md`
- Create: `ai_review/tests/suites/services/knowledge/test_schema.py`
- Create: `ai_review/tests/suites/services/knowledge/test_block.py`
- Create: `ai_review/tests/suites/services/knowledge/test_extractor.py`
- Modify: `pyproject.toml`

- [x] **Step 1: Написать failing tests knowledge contract**

Проверить `false_positive|correction|new_check`, source reply ID/quote, rationale, одно-три правила на каждый source reply и не более 150 на followup, exact quote после NFC/EOL normalization, отсутствие знания без явного правила, явную команду «AI-ревьювер, зафиксируй в правилах» и детерминированный tagged block. RED-case `limit + 1` для одного `source_reply_id` отбрасывает весь набор только этого ответа без молчаливого обрезания, пишет причину в diagnostic log и сохраняет валидные знания других ответов. Общий strict JSON parser отклоняет exact/case-variant duplicate keys, unknown fields, trailing data, `NaN`/`Infinity`, depth >16 и превышение каждого лимита длины. В опубликованном block любая ошибка одного элемента делает невалидным весь block.

- [x] **Step 2: Подтвердить падение**

Run: `python -m pytest ai_review/tests/suites/services/knowledge/test_schema.py ai_review/tests/suites/services/knowledge/test_block.py ai_review/tests/suites/services/knowledge/test_extractor.py -q`

Expected: knowledge package imports fail.

- [x] **Step 3: Реализовать domain и отдельный extractor call**

Публичные interfaces:

```python
class KnowledgeCandidate(BaseModel):
    source_reply_id: str
    source_quote: str
    knowledge_type: Literal["false_positive", "correction", "new_check"] = Field(alias="type")
    rule: str
    rationale: str

class KnowledgeExtractor:
    async def extract(
        self,
        sources: Sequence[EligibleKnowledgeSource],
        max_rules_per_reply: int,
    ) -> tuple[KnowledgeCandidate, ...]: ...

def render_knowledge_block(items: Sequence[KnowledgeCandidate]) -> str: ...
def parse_knowledge_block(body: str) -> tuple[KnowledgeCandidate, ...] | None: ...
```

Extractor принимает LLM-root `{ "knowledge": [...] }`, передаёт модели только machine-labelled `eligible_knowledge_sources`, валидирует все ссылки и поэлементно отбрасывает неподтверждённые candidates, не меняя verdict. После группировки по `source_reply_id` группа, превышающая configured limit, отбрасывается целиком с диагностикой; группы других ответов сохраняются. Renderer публикует отдельный strict root `{ "rules": [...] }` с contract-полями `source_reply_id`, `source_quote`, `type`, `rule`, `rationale`; parser считает весь опубликованный блок атомарно невалидным при любой ошибке. Prompt включить в package data.

- [x] **Step 4: Запустить knowledge tests**

Run: `python -m pytest ai_review/tests/suites/services/knowledge -q`

Expected: PASS.

- [x] **Step 5: Зафиксировать контрольную точку** *(изменения оставлены без commit по просьбе владельца)*

```bash
git add ai_review/services/knowledge ai_review/prompts/default_knowledge_extractor.md ai_review/tests/suites/services/knowledge pyproject.toml
git commit -m "feat: extract and render review knowledge"
```

## Task 5: Интеграция knowledge и withdrawn в followup

**Files:**

- Modify: `ai_review/services/review/internal/followup/schema.py`
- Modify: `ai_review/services/review/runner/followup.py`
- Create: `ai_review/services/review/runner/followup_publication.py`
- Modify: `ai_review/services/review/service.py`
- Modify: `ai_review/tests/fixtures/services/review/runner/followup.py`
- Modify: `ai_review/tests/suites/services/review/runner/test_followup.py`
- Create: `ai_review/tests/suites/services/review/runner/test_followup_publication.py`
- Modify: `ai_review/tests/suites/services/review/runner/test_initial_recovery.py`

- [x] **Step 1: Добавить failing regression tests**

Проверить: extractor не вызывается при disabled/empty allowlist; вызывается отдельно после verdict для каждого набора новых trusted replies; не более трёх правил из каждого ответа; mixed trusted/untrusted; fast path шаблона не пропускает extraction; invalid knowledge не ломает followup; `withdrawn` публикуется и закрывает; новый ответ во время LLM остаётся pending.

В отдельном state-machine suite проверить каждую capability-ветку: direct reply при `can_reply_resolved`; reopen → reread → reply → resolve; continuation при неизвестном `resolved` или отсутствии reopen. Для каждой ветки проверить discovery по publication key среди всех inline/general threads, ambiguous POST с найденной/ненайденной публикацией, восстановление исходного closed-state, уже опубликованную незакрытую continuation (повторяется только resolve), ошибку resolve после успешного POST и отсутствие duplicate LLM/POST на повторном запуске.

- [x] **Step 2: Подтвердить падение**

Run: `python -m pytest ai_review/tests/suites/services/review/runner/test_followup.py ai_review/tests/suites/services/review/runner/test_followup_publication.py ai_review/tests/suites/services/review/runner/test_initial_recovery.py -q`

Expected: assertions for extractor, withdrawn and recovery fail; existing tests remain green where unaffected.

- [x] **Step 3: Реализовать orchestration без изменения текущих verdict semantics**

Расширить `FollowupReply.verdict` значением `withdrawn`. Инъецировать `KnowledgeExtractor` в runner. `_pending()` возвращает opaque IDs и comment records, trusted sources выбираются по normalized username. Выделить `FollowupPublicationStateMachine`: он делает discovery по publication key во всех inline/general threads до LLM/POST и после неоднозначной ошибки, реализует три capability-ветки и восстановление закрытого состояния из спецификации. `fixed` и `withdrawn` терминальны; `open`/`clarify` остаются открыты. Существующий production MR lock сохранить.

- [x] **Step 4: Запустить review regression suite**

Run: `python -m pytest ai_review/tests/suites/services/review/runner/test_followup.py ai_review/tests/suites/services/review/runner/test_followup_publication.py ai_review/tests/suites/services/review/runner/test_initial_recovery.py ai_review/tests/suites/services/review/test_service.py -q`

Expected: PASS.

- [x] **Step 5: Зафиксировать контрольную точку** *(изменения оставлены без commit по просьбе владельца)*

```bash
git add ai_review/services/review/internal/followup/schema.py ai_review/services/review/runner/followup.py ai_review/services/review/runner/followup_publication.py ai_review/services/review/service.py ai_review/tests/fixtures/services/review/runner/followup.py ai_review/tests/suites/services/review/runner/test_followup.py ai_review/tests/suites/services/review/runner/test_followup_publication.py ai_review/tests/suites/services/review/runner/test_initial_recovery.py ai_review/tests/suites/services/review/test_service.py
git commit -m "feat: publish knowledge from trusted followup replies"
```

## Task 6: Managed section и безопасная транзакция файла правил

**Files:**

- Create: `ai_review/services/knowledge/rules_file.py`
- Create: `ai_review/tests/suites/services/knowledge/test_rules_file.py`

- [x] **Step 1: Написать failing byte-preservation tests**

Покрыть ноль/одну/несколько marker pairs, UTF-8 с/без BOM, LF/CRLF и mixed EOL в manual prefix/suffix, invalid UTF-8, недопустимые rule lines, безопасное read-only извлечение `manual_context`, сохранение manual byte slices, atomic replace и pre-write SHA mismatch.

- [x] **Step 2: Подтвердить падение**

Run: `python -m pytest ai_review/tests/suites/services/knowledge/test_rules_file.py -q`

Expected: module import fails.

- [x] **Step 3: Реализовать managed-section transaction**

Определить `RulesDocument.read(path)`, `manual_context_text() -> str`, `with_rules(lines) -> bytes`, `unified_diff(new_bytes) -> str` и `RulesFileTransaction.commit(expected_sha256, new_bytes)`. `manual_context_text()` декодирует только сохранённые manual prefix/suffix и никогда не включает managed section. Marker pair и допустимая однострочная grammar берутся дословно из спецификации. Перед записью повторно проверяется SHA-256 исходного файла. Запись — temp file в том же directory + `os.replace`; при любом исключении исходный файл остаётся неизменным. Дополнительные mutex и `fsync` намеренно не используются: команда запускается локально, а внешние редакторы не соблюдают её блокировки.

- [x] **Step 4: Запустить rules-file tests**

Run: `python -m pytest ai_review/tests/suites/services/knowledge/test_rules_file.py -q`

Expected: PASS.

- [x] **Step 5: Зафиксировать контрольную точку** *(изменения оставлены без commit по просьбе владельца)*

```bash
git add ai_review/services/knowledge/rules_file.py ai_review/tests/suites/services/knowledge/test_rules_file.py
git commit -m "feat: update managed knowledge rules atomically"
```

## Task 7: LLM compiler с единым decisions contract

**Files:**

- Create: `ai_review/services/knowledge/compiler.py`
- Create: `ai_review/prompts/default_knowledge_compiler.md`
- Create: `ai_review/tests/suites/services/knowledge/test_compiler.py`
- Modify: `pyproject.toml`

- [x] **Step 1: Написать failing compiler contract tests**

Проверить действия `add|merge|duplicate|conflict|reject`, полное однократное покрытие входных индексов единым массивом decisions, запрет unknown indexes/actions, запрет правила без source, симметричное исключение new-vs-new conflicts и validation `result_rule` против newline/list marker/control/fence/managed marker. Compiler response проходит тот же strict JSON parser с duplicate/case-variant/unknown/trailing/non-finite/depth checks. Отдельные tests: кандидат конфликтует только с ручным правилом — decision `conflict`, локальная operation отсутствует; `replace` сохраняет позицию target; один managed target заменяется не более одного раза; `add` сортируются по минимальному source input index; конфликтующие inputs не блокируют независимые valid operations.

- [x] **Step 2: Подтвердить падение**

Run: `python -m pytest ai_review/tests/suites/services/knowledge/test_compiler.py -q`

Expected: compiler module import fails.

- [x] **Step 3: Реализовать compiler**

```python
class KnowledgeCompiler:
    async def compile(
        self,
        manual_context: str,
        existing_rules: Sequence[str],
        candidates: Sequence[VerifiedKnowledgeCandidate],
    ) -> CompilationResult: ...
```

LLM получает ручную часть как read-only context, а существующие rules и candidates — как недоверенные индексированные data blocks. LLM возвращает только единый массив decisions с `source_input_indexes`, action, target, result rule, related indexes и reason. После strict JSON parsing локальный validator детерминированно выводит итоговый список operations; renderer применяет только проверенные операции: `replace` остаётся на позиции target, каждый target заменяется максимум один раз, `add` упорядочиваются по минимальному `source_input_index`, конфликтующие inputs исключаются без блокировки независимых operations. Любая contract error отменяет весь compile. Prompt включить в package data.

- [x] **Step 4: Запустить compiler tests**

Run: `python -m pytest ai_review/tests/suites/services/knowledge/test_compiler.py -q`

Expected: PASS.

- [x] **Step 5: Зафиксировать контрольную точку** *(изменения оставлены без commit по просьбе владельца)*

```bash
git add ai_review/services/knowledge/compiler.py ai_review/prompts/default_knowledge_compiler.md ai_review/tests/suites/services/knowledge/test_compiler.py pyproject.toml
git commit -m "feat: compile verified knowledge into rules"
```

## Task 8: Отдельный sync LLM gateway на OpenAI Responses v2

**Files:**

- Create: `ai_review/services/knowledge/llm_gateway.py`
- Modify: `ai_review/clients/openai/v2/client.py`
- Modify: `ai_review/clients/openai/v2/types.py`
- Create: `ai_review/tests/suites/services/knowledge/test_llm_gateway.py`
- Modify: `ai_review/tests/suites/clients/openai/v2/test_client.py`

- [x] **Step 1: Написать failing transport/config tests**

Проверить precedence environment над локальным `.env` для `AI_REVIEW_API_URL`, `AI_REVIEW_API_TOKEN`, `AI_REVIEW_MODEL`, `AI_REVIEW_REASONING_EFFORT`; URL `/responses`; Bearer auth; `model`, `input`, `instructions`, `stream: false`, `reasoning.effort`, `max_output_tokens`; timeout 45 минут; JSON и SSE success; failed/incomplete/multiple completed/mismatched delta-done/20 MiB/100000-event errors; secret redaction. Gateway не делает retry поверх существующей политики `OpenAIV2HTTPClient`.

- [x] **Step 2: Подтвердить падение**

Run: `python -m pytest ai_review/tests/suites/services/knowledge/test_llm_gateway.py ai_review/tests/suites/clients/openai/v2/test_client.py -q`

Expected: sync gateway/config injection assertions fail; existing v2 parser tests show the reusable behavior.

- [x] **Step 3: Реализовать explicit gateway factory без global settings**

`SyncLLMSettings.from_environment(repository_root)` читает только четыре разрешённые настройки, задаёт timeout `2700.0` seconds и не сериализует token в diagnostics. `get_openai_v2_http_client(config=..., timeout=...)` принимает явную config dependency, сохраняя legacy zero-argument factory. `KnowledgeLLMGateway.ask(instructions, input_text, max_output_tokens)` строит `OpenAIResponsesRequestSchema`, вызывает client один раз и возвращает `first_text`; транспортная retry policy остаётся внутри существующего client.

- [x] **Step 4: Запустить gateway и parser tests**

Run: `python -m pytest ai_review/tests/suites/services/knowledge/test_llm_gateway.py ai_review/tests/suites/clients/openai/v2/test_client.py ai_review/tests/suites/clients/openai/v2/test_schema.py -q`

Expected: PASS.

- [x] **Step 5: Зафиксировать контрольную точку**

Контрольная точка выполнена без commit по указанию пользователя; изменения
оставлены в рабочем дереве для последующей общей фиксации.

```bash
git add ai_review/services/knowledge/llm_gateway.py ai_review/clients/openai/v2/client.py ai_review/clients/openai/v2/types.py ai_review/tests/suites/services/knowledge/test_llm_gateway.py ai_review/tests/suites/clients/openai/v2/test_client.py
git commit -m "feat: add explicit knowledge sync LLM gateway"
```

## Task 9: Переносимый sync orchestrator и CLI

**Files:**

- Create: `ai_review/services/knowledge/source.py`
- Create: `ai_review/services/knowledge/repository_identity.py`
- Create: `ai_review/services/knowledge/gitflic_source.py`
- Create: `ai_review/services/knowledge/sync.py`
- Create: `ai_review/cli/commands/sync_knowledge.py`
- Modify: `ai_review/cli/main.py`
- Create: `ai_review/tests/suites/services/knowledge/test_source.py`
- Create: `ai_review/tests/suites/services/knowledge/test_repository_identity.py`
- Create: `ai_review/tests/suites/services/knowledge/test_sync.py`
- Create: `ai_review/tests/suites/cli/test_sync_knowledge.py`

- [x] **Step 1: Написать failing source/orchestrator/CLI tests**

Fixtures включают open/closed MR, MR, закрытый между selection и preview, valid/forged tag, wrong AI author, damaged marker, unknown/duplicate source ID, quote mismatch и MR без знаний. Ошибка чтения отдельного MR отображается отдельной строкой с ошибкой и не маскируется как «знаний нет»; остальные MR остаются выбираемыми. CLI tests проверяют таблицу, один ID, comma-list, `all`/`все`, Enter, invalid/not-listed ID, повторное чтение state и discussions перед LLM, полный decisions/conflicts/counts/diff preview, отказ, `--yes` и early exit при disabled.

Repository identity tests проверяют precedence `CLI override > VCS__... env > origin remote`, HTTPS/SSH GitFlic remotes, default API URL только для `gitflic.ru`, fail-closed unknown/self-hosted host без API URL, token из существующих env/`.env` имён и trusted AI identity из explicit setting либо authenticated-user endpoint. Значения секретов не появляются в diagnostic text.

- [x] **Step 2: Добавить переносимость как executable contract**

Один parametrized test запускает тот же `KnowledgeSyncService` для двух GitFlic fixtures с разными owner/project/repository root/rules path. Второй test запускает fake provider с `VCSProvider.GITLAB` через `KnowledgeReviewSourceProtocol`; knowledge domain и orchestrator не импортируют GitFlic/GitLab classes и не ветвятся по provider.

- [x] **Step 3: Подтвердить падение**

Run: `python -m pytest ai_review/tests/suites/services/knowledge/test_source.py ai_review/tests/suites/services/knowledge/test_repository_identity.py ai_review/tests/suites/services/knowledge/test_sync.py ai_review/tests/suites/cli/test_sync_knowledge.py -q`

Expected: source, service and command imports fail.

- [x] **Step 4: Реализовать validation pipeline и command**

`KnowledgeSourceService` валидирует AI identity, marker, covered/source relation, allowlist author и exact normalized quote, дедуплицируя `(provider, project, review_id, source_reply_id)` в пределах запуска. `KnowledgeSyncService.preview(selected_ids)` для каждого ID вызывает `get_review()` и отклоняет уже не открытый MR, затем повторно читает discussions и компилирует один общий deterministic proposal. Он возвращает `SyncPreview(decisions, conflicts, counts, diff, expected_sha256, new_bytes)`; `commit(preview)` использует rules transaction. Typer command `sync-knowledge` принимает `--repository-root`, `--config`, repeatable `--merge-request-id`, `--all`, `--yes` и optional `--provider/--host/--owner/--project/--api-url`; без selection options показывает только eligible open MR и читает `all|все`. Repository identity вычисляет общий resolver, не launcher.

Команда использует `KnowledgeSyncService` и `KnowledgeLLMGateway` напрямую,
без runtime-прокси, динамической проверки awaitable и адаптера только ради
перестановки аргументов.

- [x] **Step 5: Запустить focused и CLI regression tests**

Run: `python -m pytest ai_review/tests/suites/services/knowledge ai_review/tests/suites/cli/test_sync_knowledge.py ai_review/tests/suites/cli/test_main.py -q`

Expected: PASS.

- [x] **Step 6: Подготовить контрольную точку (без commit по запросу пользователя)**

```bash
git add ai_review/services/knowledge ai_review/cli/commands/sync_knowledge.py ai_review/cli/main.py ai_review/tests/suites/services/knowledge ai_review/tests/suites/cli
git commit -m "feat: add portable knowledge sync command"
```

## Task 10: Тонкая интеграция в 1С-проект

**Files (`F:\1C\Projects\RT_VT\sppr_gitflic`):**

- Modify: `.ai-review-ones.yaml`
- Modify: `ai-review/prompts/project-rules.md`
- Create: `tools/ai-review/Sync-AiReviewKnowledge.ps1`
- Modify: `tools/ai-review/AiReview.psm1`
- Modify: `tools/ai-review/tests/test-ai-review.ps1`
- Create: `tools/ai-review/tests/test-sync-ai-review-knowledge.ps1`
- Create: `tools/ai-review/tests/marker-vectors.json`

- [x] **Step 1: Написать failing PowerShell contract tests**

Launcher test подменяет executable и утверждает, что переданы repository root/config path, `-MergeRequestId` как repeatable `--merge-request-id`, `-All` как `--all`, `-Yes` как `--yes` и optional connection overrides без преобразования; секреты не попадают в argv/output. Marker tests читают те же значения golden vectors, поддерживают v1/v2/withdrawn и считают fixed/withdrawn terminal.

- [x] **Step 2: Подтвердить падение**

Run from `sppr_gitflic`: `pwsh -NoProfile -File tools/ai-review/tests/test-sync-ai-review-knowledge.ps1`

Expected: launcher/file not found or assertions fail.

- [x] **Step 3: Добавить project-owned configuration and rules boundary**

В `.ai-review-ones.yaml` добавить одобренную секцию `knowledge`; usernames заполнить фактическими проектными значениями, а не generic defaults. В `project-rules.md` добавить ровно одну пустую managed marker pair, не меняя ручные правила.

Prerequisite: до изменения YAML владелец проекта предоставляет точный список trusted reviewer usernames. Если список не зафиксирован, остановить только этот Step и не угадывать значения из display name, commit author или текста комментариев.

Для текущего проекта список берётся только из уже настроенного `GITFLIC_USERNAME` локального `.env`; секретные значения и display name не используются.

- [x] **Step 4: Реализовать thin launcher и marker compatibility**

`Sync-AiReviewKnowledge.ps1` определяет repository root от `$PSScriptRoot`, вызывает установленный `ai-review sync-knowledge`, передаёт optional connection overrides и не содержит identity inference, HTTP requests, pagination, YAML/JSON parsing, LLM prompt/schema или file-update algorithm. Для стандартного GitFlic remote launcher переносится без правок. `AiReview.psm1` меняется только там, где существующему `Start-AiReview.ps1` нужны marker v2/withdrawn.

- [x] **Step 5: Запустить PowerShell contract suite**

Run:

```powershell
pwsh -NoProfile -File tools/ai-review/tests/test-sync-ai-review-knowledge.ps1
pwsh -NoProfile -File tools/ai-review/tests/test-ai-review.ps1
pwsh -NoProfile -File tools/ai-review/tests/test-ci-contract.ps1
```

Expected: PASS; существующий Start-AiReview contract не изменился.

- [x] **Step 6: Зафиксировать отдельную контрольную точку интеграционного проекта**

```bash
git add .ai-review-ones.yaml ai-review/prompts/project-rules.md tools/ai-review/Sync-AiReviewKnowledge.ps1 tools/ai-review/AiReview.psm1 tools/ai-review/tests
git commit -m "feat: enable portable AI review knowledge sync"
```

## Task 11: Portability audit, документация и полная проверка

**Files:**

- Modify: `README.md`
- Create: `docs/knowledge-sync.md`
- Modify: `ai_review/tests/suites/ci/test_gitflic_runtime.py`
- Modify: `ai_review/tests/suites/libs/test_gitflic_config_template.py`

- [x] **Step 1: Добавить failing packaging/runtime tests**

Проверить наличие обоих packaged prompts, доступность `ai-review sync-knowledge --help`, отключённый default, GitFlic config template и отсутствие project-specific строк в `ai_review/services/knowledge`, `ai_review/prompts/default_knowledge_*` и `ai_review/cli/commands/sync_knowledge.py`. Integration contract одновременно проверяет четыре части текущего path mapping: source `$CI_PROJECT_DIR/ai-review/prompts`, staging config/prompts, read-only mount `/control/ai-review` и абсолютные `/control/ai-review/prompts/...` ссылки всех review modes.

- [x] **Step 2: Обновить документацию переноса**

`docs/knowledge-sync.md` описывает четыре project-owned элемента: секция YAML, rules file с managed markers, подключение rules prompt ко всем используемым review/followup режимам и thin launcher. Отдельная таблица сопоставляет repository path `<root>/ai-review/prompts`, CI-host trusted staging path и container runtime path `/control/ai-review/prompts`; текст явно указывает, что mapping задаёт `run-ai-review.sh`, а не YAML. Объяснить отличие абсолютных runtime paths секции `prompt` от относительного к YAML `knowledge.sync.rules_file`, стандартный layout для переноса и согласованное изменение launcher плюс contract tests при нестандартном layout. Portability fixture проверяет подключение rules prompt и mapping. Добавить примеры GitFlic env/config, disabled mode, interactive/all/non-interactive запуск и extension point `KnowledgeReviewSourceProtocol` для будущего GitLab adapter. Инструкция применима к любому 1С-проекту и не содержит `rt-vt`, `sppr`, EDT source-set или абсолютного Windows-пути.

- [x] **Step 3: Запустить focused portability audit**

Run:

```powershell
python -m pytest ai_review/tests/suites/ci/test_gitflic_runtime.py ai_review/tests/suites/libs/test_gitflic_config_template.py ai_review/tests/suites/cli/test_sync_knowledge.py -q
rg -n -i "rt-vt|sppr|src/cf|src/cfe" ai_review/services/knowledge ai_review/prompts/default_knowledge_extractor.md ai_review/prompts/default_knowledge_compiler.md ai_review/cli/commands/sync_knowledge.py
```

Expected: pytest PASS; `rg` returns no matches (exit 1 is expected for the second command).

- [x] **Step 4: Запустить полный Python suite**

Run: `python -m pytest -q`

Expected: PASS, no regressions in other providers/review modes.

- [x] **Step 5: Запустить полный integration contract suite**

Run from `F:\1C\Projects\RT_VT\sppr_gitflic`:

```powershell
pwsh -NoProfile -File tools/ai-review/tests/test-sync-ai-review-knowledge.ps1
pwsh -NoProfile -File tools/ai-review/tests/test-ai-review.ps1
pwsh -NoProfile -File tools/ai-review/tests/test-ci-contract.ps1
bash tools/ai-review/tests/test-run-ai-review.sh
```

Expected: PASS.

- [x] **Step 6: Проверить diff обоих репозиториев и зафиксировать docs/tests**

> Diff и status проверены; commit намеренно не создавался по указанию пользователя.

Run:

```powershell
git -C F:\1C\Projects\RT_VT\ai_review diff --check
git -C F:\1C\Projects\RT_VT\sppr_gitflic diff --check
git -C F:\1C\Projects\RT_VT\ai_review status --short
git -C F:\1C\Projects\RT_VT\sppr_gitflic status --short
```

Expected: no whitespace errors; only intended files are changed.

```bash
git add README.md docs/knowledge-sync.md ai_review/tests/suites/ci/test_gitflic_runtime.py ai_review/tests/suites/libs/test_gitflic_config_template.py
git commit -m "docs: describe portable knowledge sync"
```

## Final acceptance

- [x] В followup сохранены прежние `fixed/open/clarify` сценарии и добавлен `withdrawn`.
- [x] Knowledge block появляется только из новых ответов trusted reviewer и содержит максимум три правила на один ответ.
- [x] Закрытая человеком дискуссия обрабатывается через reopen/reply/resolve либо безопасный continuation fallback.
- [x] Sync показывает только eligible открытые MR, поддерживает выбор нескольких и всех показанных MR.
- [x] LLM не может обойти source validation, managed-section grammar, conflict policy или confirmation boundary.
- [x] `knowledge.enabled: false` не вызывает GitFlic/LLM и не читает/пишет rules file.
- [x] Для второго GitFlic 1С-проекта перенос требует только конфигурацию, rules file и launcher; Python-код не копируется и не меняется.
- [x] Инструкция любого 1С-проекта объясняет repository/staging/container paths, владельца mapping и отличие runtime prompt path от локального `knowledge.sync.rules_file`.
- [x] Будущий GitLab source подключается реализацией protocol без изменения knowledge domain/compiler/writer.
- [x] Полные Python и PowerShell suites зелёные; перед заявлением о готовности приложены фактические команды и результаты.
