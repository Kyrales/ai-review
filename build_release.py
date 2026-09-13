#!/usr/bin/env python3
"""Build standalone executables for ai-review using PyInstaller."""

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def get_release_layout() -> tuple[str, str]:
    """Get the release directory and executable name for the current platform."""
    system = platform.system().lower()

    if system == "windows":
        return "win-x64", "ai-review.exe"
    if system == "linux":
        return "linux-x64", "ai-review"
    if system == "darwin":
        return "macos-x64", "ai-review"
    raise RuntimeError(f"Unsupported platform: {platform.system()}")


def build_executable(output_dir: Path, clean: bool = False) -> None:
    """Build standalone executable using PyInstaller."""
    project_root = Path(__file__).parent.resolve()
    dist_dir = project_root / "dist"
    build_dir = project_root / "build"

    if clean and dist_dir.exists():
        print(f"Cleaning {dist_dir}")
        shutil.rmtree(dist_dir)
    if clean and build_dir.exists():
        print(f"Cleaning {build_dir}")
        shutil.rmtree(build_dir)

    # Ensure PyInstaller is installed
    try:
        import PyInstaller
    except ImportError:
        print("PyInstaller not found. Installing...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    # Build command
    entry_point = project_root / "ai_review" / "cli" / "main.py"
    if not entry_point.exists():
        raise FileNotFoundError(f"Entry point not found: {entry_point}")

    release_dir_name, executable_name = get_release_layout()
    exe_name = f"ai-review-{release_dir_name}"

    cmd = [
        sys.executable,
        "-m", "PyInstaller",
        "--name", exe_name,
        "--onefile",
        "--console",
        "--clean",
        "--noconfirm",
        str(entry_point),
    ]

    # Add data files (prompts and resources)
    prompts_dir = project_root / "ai_review" / "prompts"
    resources_dir = project_root / "ai_review" / "resources"

    if prompts_dir.exists():
        cmd.extend(["--add-data", f"{prompts_dir}{':' if platform.system() != 'Windows' else ';'}ai_review/prompts"])
    if resources_dir.exists():
        cmd.extend(["--add-data", f"{resources_dir}{':' if platform.system() != 'Windows' else ';'}ai_review/resources"])

    print(f"Building executable: {exe_name}")
    print(f"Command: {' '.join(cmd)}")

    subprocess.check_call(cmd, cwd=project_root)

    # Move to platform-specific artifacts directory
    platform_output_dir = output_dir / release_dir_name
    platform_output_dir.mkdir(parents=True, exist_ok=True)

    built_exe = dist_dir / f"{exe_name}{'.exe' if platform.system() == 'Windows' else ''}"
    target_exe = platform_output_dir / executable_name

    if not built_exe.exists():
        raise FileNotFoundError(f"Built executable not found: {built_exe}")

    print(f"Moving {built_exe} -> {target_exe}")
    shutil.move(str(built_exe), str(target_exe))

    # Make executable on Unix
    if platform.system() != "Windows":
        target_exe.chmod(0o755)

    print(f"\nSuccessfully built: {target_exe}")
    print(f"  Size: {target_exe.stat().st_size / 1024 / 1024:.2f} MB")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build ai-review standalone executables")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).parent / "artifacts" / "releases",
        help="Output directory for built executables",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Clean dist and build directories before building",
    )

    args = parser.parse_args()

    try:
        build_executable(args.output_dir, args.clean)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
