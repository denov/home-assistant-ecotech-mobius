"""tools/mobius_cli.py runs without Home Assistant installed."""

from pathlib import Path
import subprocess
import sys

import pytest

pytest.importorskip("bleak_esphome")

CLI = Path(__file__).resolve().parents[1] / "tools" / "mobius_cli.py"


def test_cli_starts_without_home_assistant():
    # a fresh interpreter where importing homeassistant fails, like a venv without it
    code = (
        "import sys, runpy; sys.modules['homeassistant'] = None; "
        f"sys.argv = ['mobius_cli', '--help']; runpy.run_path({str(CLI)!r}, run_name='__main__')"
    )
    run = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    assert "scan" in run.stdout
