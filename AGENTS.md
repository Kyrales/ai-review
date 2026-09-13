# Инструкции для Claude Code

## Сборка релизов после изменений

После любого изменения, которое затрагивает CLI, зависимости, prompts или resources, пересобирай релиз из актуальных исходников до передачи результата пользователю.

### Windows

Из корня репозитория выполни:

```powershell
cd F:\1C\Projects\RT_VT\ai_review
python build_release.py
.\artifacts\releases\win-x64\ai-review.exe --help
```

Результат должен находиться по пути:

```text
artifacts/releases/win-x64/ai-review.exe
```

### Linux

Сборку выполняй на Linux или в Linux CI runner:

```bash
cd /path/to/ai_review
python build_release.py
./artifacts/releases/linux-x64/ai-review --help
```

Результат должен находиться по пути `artifacts/releases/linux-x64/ai-review`.

### Ограничения и правила

- PyInstaller не выполняет кросс-компиляцию: Windows-релиз собирай на Windows, Linux-релиз — на Linux, macOS-релиз — на macOS.
- Готовые файлы имеют фиксированную структуру: `artifacts/releases/win-x64/ai-review.exe` и `artifacts/releases/linux-x64/ai-review`.
- После сборки обязательно запускай соответствующий бинарник с `--help` и проверяй код завершения `0`.
- Если сборка или проверка завершилась ошибкой, релиз не считать готовым и сначала устранить причину.
- `.venv` не удалять: он изолирует зависимости разработки и сборки. Собранные бинарники автономны и не требуют `.venv` при запуске.
- Не коммить бинарники из `artifacts/releases/`; каталог предназначен для локальных и CI-артефактов и игнорируется Git.
- Для чистой пересборки допускается `python build_release.py --clean`, но перед этим проверь `git status`, чтобы не затронуть незакоммиченные файлы в `build/` или `dist/`.

### Использование релиза

Для PowerShell-launcher из соседнего репозитория `sppr_gitflic` добавляй каталог Windows-релиза в PATH:

```powershell
$env:PATH = "F:\1C\Projects\RT_VT\ai_review\artifacts\releases\win-x64;" + $env:PATH
```

Либо передавай полный путь к `ai-review.exe` через параметр `-AiReviewExecutable` скрипта `Sync-AiReviewKnowledge.ps1`.
