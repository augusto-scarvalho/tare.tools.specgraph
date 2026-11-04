"""Browser suppression must survive unconditional yes and preserve failures."""
import json
import subprocess
import sys
import webbrowser
from pathlib import Path

import pytest

from tools.aider_headless import browser_guard


def test_browser_guard_never_dispatches_and_restores_after_failure(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("desktop browser was invoked")

    for name in ("open", "open_new", "open_new_tab"):
        monkeypatch.setattr(webbrowser, name, forbidden)
    with pytest.raises(RuntimeError), browser_guard():
        for name in ("open", "open_new", "open_new_tab"):
            assert getattr(webbrowser, name)("https://example.invalid/limit") is False
        raise RuntimeError("host failed")
    assert webbrowser.open is forbidden
    events = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert len(events) == 3
    assert all(e["event"] == "browser_open_suppressed" for e in events)


def test_guard_precedes_host_import_and_preserves_arguments_and_exit(tmp_path):
    # A stub exercises the executable entry point without model or Aider installs.
    wrapper = tmp_path / "entry.py"
    wrapper.write_bytes((Path(__file__).parents[1] / "tools/aider_headless.py").read_bytes())
    package = tmp_path / "aider"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "__main__.py").write_text(
        "from webbrowser import open_new_tab\n"
        "import sys\n"
        "assert sys.argv[1:] == ['--yes-always', '--message', 'bounded task']\n"
        "assert open_new_tab('https://example.invalid/limit') is False\n"
        "print('Model has hit a token limit!', file=sys.stderr)\n"
        "raise SystemExit(7)\n"
    )
    result = subprocess.run([sys.executable, str(wrapper), "--yes-always", "--message", "bounded task"],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 7
    assert '"event": "browser_open_suppressed"' in result.stderr
    assert "Model has hit a token limit!" in result.stderr
