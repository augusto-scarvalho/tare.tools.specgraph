import pytest
from tools.host_completion import completion_status


def test_agy_success_event_and_exit_zero_do_not_override_partial_timeout():
    events=[{'event':'result','result':{'status':'SUCCESS'}}]
    assert completion_status('agy',events,'',0)['completed']
    status=completion_status('agy',events,'[agy] print timeout after 2m50s with turn in progress; returning partial output',0)
    assert status=={'completed':False,'reason':'HOST_PARTIAL_TIMEOUT'}


@pytest.mark.parametrize('host,events',[
    ('claude',[{'type':'result','subtype':'error_max_turns','is_error':True}]),
    ('codex',[{'type':'turn.failed'}]),('agy',[]),
])
def test_exit_zero_is_insufficient_without_successful_terminal_event(host,events):
    assert not completion_status(host,events,'',0)['completed']


def test_recovered_transport_error_does_not_reject_completed_codex_turn():
    assert completion_status('codex',[{'type':'error','message':'reconnecting'},{'type':'turn.completed'}],'',0)['completed']


def test_agy_success_with_a_tool_still_active_is_incomplete_without_timeout_warning():
    events = [{'event':'step_update','step_update':{'step_index':44,'step_type':'tool','state':'ACTIVE'}},
              {'event':'result','result':{'status':'SUCCESS','response':'Waiting for execution to finish.'}}]
    assert completion_status('agy',events,'',0) == {
        'completed':False,'reason':'HOST_PENDING_TOOLS','pending_steps':[44]}
    events.insert(1, {'event':'step_update','step_update':{'step_index':44,'state':'DONE'}})
    assert completion_status('agy',events,'',0)['completed']
