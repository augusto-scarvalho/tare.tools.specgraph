"""Real host message construction must register editability without leaking full text."""
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'tools'))
from aider_projected import editable_manifest


def test_registered_file_projection_preserves_native_io_and_restores(tmp_path):
    pytest.importorskip('aider')
    from aider.coders.base_coder import Coder
    from types import SimpleNamespace
    target=tmp_path/'unit.py';target.write_text('def omitted_secret(): pass\n')
    original=Coder.get_chat_files_messages
    with editable_manifest(target):
        value=Coder.get_chat_files_messages(SimpleNamespace(abs_fnames={str(target)}))
        joined='\n'.join(x['content'] for x in value)
        assert 'has been added to the chat' in joined
        assert 'omitted_secret' not in joined and 'not sharing any files' not in joined
        assert target.read_text()=='def omitted_secret(): pass\n'
        with pytest.raises(ValueError):Coder.get_chat_files_messages(SimpleNamespace(abs_fnames=set()))
    assert Coder.get_chat_files_messages is original
