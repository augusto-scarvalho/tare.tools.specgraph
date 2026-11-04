"""Register real editable files while projecting only the approved chat context.

Native Aider reads/applies edits to the original workspace file. Only its chat-file
message is replaced; disk reads, edit application and system rules are unchanged.
The separate /run output carries the source packet exactly once.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import runpy
import sys
from aider_headless import browser_guard


@contextmanager
def editable_manifest(target):
    from aider.coders.base_coder import Coder
    original = Coder.get_chat_files_messages
    expected = Path(target).resolve()

    def messages(self):
        actual = {Path(name).resolve() for name in self.abs_fnames}
        if actual != {expected}:
            raise ValueError('registered editable files differ from authorized manifest')
        content = ('This existing file has been added to the chat and is authorized for editing:\n'
                   + str(expected) + '\nIts source context is supplied in the /run output below. '
                   'That context may be an excerpt; omitted code remains on disk. '
                   'Use exact SEARCH/REPLACE edits against the supplied source.')
        return [{'role': 'user', 'content': content}, {'role': 'assistant', 'content': 'Understood. This file is added and editable.'}]

    Coder.get_chat_files_messages = messages
    try:
        yield
    finally:
        Coder.get_chat_files_messages = original


def main():
    if len(sys.argv) < 3 or sys.argv[1] != '--authorized-file':
        raise ValueError('explicit authorized-file required')
    target = Path(sys.argv[2]).resolve()
    if not target.is_file():
        raise ValueError('editable file must exist')
    sys.argv = [sys.argv[0]] + sys.argv[3:] + ['--file', str(target)]
    with browser_guard(), editable_manifest(target):
        runpy.run_module('aider', run_name='__main__', alter_sys=True)


if __name__ == '__main__':
    main()
