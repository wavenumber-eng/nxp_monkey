"""Run a clean installed-package test for a built wheel."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    """Run a subprocess and raise with captured context on failure."""
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        cmd_text = " ".join(command)
        raise SystemExit(
            f"Command failed ({completed.returncode}): {cmd_text}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )


def _latest_wheel(dist_dir: Path) -> Path:
    """Return the newest wheel in a dist directory."""
    wheels = sorted(
        dist_dir.glob("nxp_monkey-*.whl"),
        key=lambda path: path.stat().st_mtime,
    )
    if not wheels:
        raise SystemExit(f"No nxp_monkey wheel found in {dist_dir}")
    return wheels[-1]


def _venv_python(venv_dir: Path) -> Path:
    """Return the Python executable path for a venv."""
    script_dir = "Scripts" if os.name == "nt" else "bin"
    executable = "python.exe" if os.name == "nt" else "python"
    return venv_dir / script_dir / executable


def _venv_script(venv_dir: Path, name: str) -> Path:
    """Return an installed console script path for a venv."""
    script_dir = "Scripts" if os.name == "nt" else "bin"
    executable = f"{name}.exe" if os.name == "nt" else name
    return venv_dir / script_dir / executable


def _clean_env(venv_dir: Path) -> dict[str, str]:
    """Build an environment that prefers the test venv and avoids source leakage."""
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    script_dir = venv_dir / ("Scripts" if os.name == "nt" else "bin")
    env["PATH"] = str(script_dir) + os.pathsep + env.get("PATH", "")
    return env


def run_install_test(wheel: Path) -> None:
    """Install a wheel into a temporary venv and verify import/version behavior."""
    wheel = wheel.resolve()
    if not wheel.exists():
        raise SystemExit(f"Wheel does not exist: {wheel}")

    with tempfile.TemporaryDirectory(prefix="nxp_monkey_install_test_") as temp:
        temp_dir = Path(temp)
        venv_dir = temp_dir / "venv"
        sys.stdout.write(f"Creating test venv: {venv_dir}\n")
        _run([sys.executable, "-m", "venv", str(venv_dir)], cwd=temp_dir)

        python = _venv_python(venv_dir)
        env = _clean_env(venv_dir)
        _run([str(python), "-m", "pip", "install", str(wheel)], cwd=temp_dir, env=env)
        _run(
            [
                str(python),
                "-c",
                "import nxp_monkey; print(nxp_monkey.__version__)",
            ],
            cwd=temp_dir,
            env=env,
        )
        _run([str(_venv_script(venv_dir, "nxp-monkey")), "--version"], cwd=temp_dir, env=env)
        _run([str(_venv_script(venv_dir, "nxp-monkey")), "version"], cwd=temp_dir, env=env)
        _run([str(_venv_script(venv_dir, "nxpm")), "version"], cwd=temp_dir, env=env)
        _run([str(python), "-m", "nxp_monkey", "--version"], cwd=temp_dir, env=env)
        sys.stdout.write("Installed-package test passed.\n")


def main() -> None:
    """Parse arguments and run the install test."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wheel",
        type=Path,
        default=None,
        help="Wheel to install. Defaults to the newest nxp_monkey wheel in dist/.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    wheel = args.wheel or _latest_wheel(repo_root / "dist")
    run_install_test(wheel)


if __name__ == "__main__":
    main()
