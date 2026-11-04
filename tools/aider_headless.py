"""Run Aider with browser requests logged instead of opening the user's desktop.

The guard is installed before importing Aider and lasts only for this process.
It does not hide token-limit warnings, change model budgets, or edit settings.
"""
from contextlib import contextmanager
import json
import runpy
import sys
import webbrowser


@contextmanager
def browser_guard():
    names = ("open", "open_new", "open_new_tab")
    originals = {name: getattr(webbrowser, name) for name in names}

    def suppressed(url, *args, **kwargs):
        print(json.dumps({"event": "browser_open_suppressed", "url": str(url)[:2048]}),
              file=sys.stderr, flush=True)
        return False

    try:
        for name in names:
            setattr(webbrowser, name, suppressed)
        yield
    finally:
        for name, original in originals.items():
            setattr(webbrowser, name, original)


def main():
    with browser_guard():
        runpy.run_module("aider", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
