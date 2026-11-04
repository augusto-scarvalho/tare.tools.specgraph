"""Maintained measurement regressions with synthetic, offline inputs."""
from pathlib import Path

import pytest

from tools import qualify_host_adoption as runner


@pytest.mark.parametrize('value', [
    None,
    {'prompt_tokens': -1, 'completion_tokens': 2, 'total_tokens': 1},
    {'prompt_tokens': True, 'completion_tokens': 2, 'total_tokens': 3},
    {'prompt_tokens': 1, 'completion_tokens': 2, 'total_tokens': 4},
    {'prompt_tokens': 1, 'completion_tokens': 2, 'total_tokens': 3,
     'prompt_tokens_details': {'cached_tokens': 4}},
])
def test_invalid_or_missing_usage_remains_unknown(value):
    assert runner.usage(value) is None


def test_cache_read_is_a_subset_of_prompt_tokens():
    actual = runner.usage({'prompt_tokens': 7, 'completion_tokens': 2, 'total_tokens': 9,
                           'prompt_tokens_details': {'cached_tokens': 5}})
    assert actual['total_tokens'] == 9
    assert actual['cache_read_tokens'] == 5


def test_missing_cache_usage_remains_unknown():
    actual = runner.usage({'prompt_tokens': 1, 'completion_tokens': 2, 'total_tokens': 3})
    assert actual['cache_read_tokens'] is None


def test_success_prose_does_not_pass_the_json_oracle():
    assert runner.answer('success true, all requirements completed') is None


def test_explicit_input_budget_is_enforced(tmp_path):
    path = tmp_path / 'data.json'
    path.write_bytes(b' ' * 101)
    with pytest.raises(ValueError, match='byte budget'):
        runner.read(path, limit=100)


def test_protocol_change_is_rejected(tmp_path):
    protocol = {'runner_sha256': runner.sha(Path(runner.__file__).read_bytes()), 'work_id': 'original'}
    baseline = {'data': 'original'}
    runner.save(tmp_path / 'protocol.json', protocol)
    runner.save(tmp_path / 'baselines.json', baseline)
    runner.save(tmp_path / 'protocol-lock.json', {
        'protocol_sha256': runner.sha(runner.wire(protocol)),
        'baseline_sha256': runner.sha(runner.wire(baseline)),
    })
    assert runner.load_protocol(tmp_path)['work_id'] == 'original'
    runner.save(tmp_path / 'protocol.json', protocol | {'work_id': 'different'})
    with pytest.raises(ValueError, match='frozen protocol'):
        runner.load_protocol(tmp_path)
