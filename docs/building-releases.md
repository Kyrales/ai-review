# Building AI Review Releases

## Quick Start

```bash
# Install PyInstaller (if not already installed)
pip install pyinstaller

# Build for current platform
python build_release.py

# Build with clean rebuild
python build_release.py --clean

# Custom output directory
python build_release.py --output-dir ./custom/path
```

## Output

Executables are placed in `artifacts/releases/` by default:
- Windows: `ai-review-windows-amd64.exe`
- Linux: `ai-review-linux-x86_64`
- macOS: `ai-review-macos-arm64` or `ai-review-macos-x86_64`

## Environment Variable Setup

### Windows (PowerShell)

Add to your PowerShell profile or set permanently:

```powershell
# Temporary (current session)
$env:PATH = "F:\1C\Projects\RT_VT\ai_review\artifacts\releases;" + $env:PATH

# Permanent (user level)
[Environment]::SetEnvironmentVariable("PATH", 
    "F:\1C\Projects\RT_VT\ai_review\artifacts\releases;" + 
    [Environment]::GetEnvironmentVariable("PATH", "User"),
    "User")
```

### Linux/macOS (bash/zsh)

Add to `~/.bashrc` or `~/.zshrc`:

```bash
export PATH="/path/to/ai_review/artifacts/releases:$PATH"
```

## Using with Sync-AiReviewKnowledge.ps1

After building and setting PATH:

```powershell
# Will automatically find ai-review.exe in PATH
. 'F:\1C\Projects\RT_VT\sppr_gitflic\tools\ai-review\Sync-AiReviewKnowledge.ps1' -MergeRequestId 123

# Or specify explicit path
. 'F:\1C\Projects\RT_VT\sppr_gitflic\tools\ai-review\Sync-AiReviewKnowledge.ps1' `
    -AiReviewExecutable 'F:\1C\Projects\RT_VT\ai_review\artifacts\releases\ai-review-windows-amd64.exe' `
    -MergeRequestId 123
```

## About .venv

The `.venv` directory is a Python virtual environment used for development:
- **Keep it**: Isolates project dependencies from system Python
- **It's ignored by git**: Won't be committed (see `.gitignore`)
- **Safe to delete**: Can recreate with `python -m venv .venv`

The built executables in `artifacts/releases/` are **standalone** and don't need `.venv` or any Python installation to run.
