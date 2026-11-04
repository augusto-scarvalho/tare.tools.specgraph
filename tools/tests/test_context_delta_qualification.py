"""The bounded qualification rejects missed edits rather than reporting byte savings."""
import pytest

from tools import qualify_context_delta as experiment


def test_frozen_context_tasks_keep_full_recomputation_parity():
    result = experiment.qualify()
    assert [row['case'] for row in result['cases']] == list(experiment.EDITS)
    assert result['summary']['reconstruction_equal'] == 8
    assert result['summary']['context_task_answers_equal'] == 8
    assert result['summary']['model_calls_for_data'] == 0
    assert all(row['handle_bytes'] > 0 and row['response_bytes'] > row['handle_bytes'] for row in result['cases'])


def test_missing_changed_unit_stops_the_comparison(monkeypatch):
    original = experiment.ContextPackager.generate_spec_delta

    def loses_edit(*args, **kwargs):
        result = original(*args, **kwargs)
        assert result['kind'] == 'delta' and result['upserts']
        return {**result, 'upserts': []}

    monkeypatch.setattr(experiment.ContextPackager, 'generate_spec_delta', loses_edit)
    with pytest.raises(AssertionError, match='X3 correctness failure: implementation'):
        experiment.qualify()
