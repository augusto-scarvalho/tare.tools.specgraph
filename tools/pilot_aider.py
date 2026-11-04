"""Optional focused-context projection; full baseline invokes aider_headless directly."""
from contextlib import contextmanager
import json
from pathlib import Path
import runpy
import sys

from aider_headless import browser_guard


@contextmanager
def projected_files(expected):
    from aider.coders.base_coder import Coder
    original = Coder.get_chat_files_messages
    expected = {Path(p).resolve() for p in expected}

    def messages(self):
        if {Path(p).resolve() for p in self.abs_fnames} != expected:
            raise ValueError('editable file registration changed')
        text = ('These existing files are added to the chat and editable:\n' +
                '\n'.join(sorted(str(p) for p in expected)) +
                '\nSelected source context is included in the task message. Omitted code remains on disk.')
        return [{'role': 'user', 'content': text}, {'role': 'assistant', 'content': 'Understood.'}]

    Coder.get_chat_files_messages = messages
    try:
        yield
    finally:
        Coder.get_chat_files_messages = original


if __name__ == '__main__':
    manifest = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    sys.argv = [sys.argv[0], *sys.argv[2:]]
    with browser_guard(), projected_files(manifest):
        runpy.run_module('aider', run_name='__main__', alter_sys=True)
