import pytest

from tools.response_contract import observe_response, validate_request, response_decision


def response(content='{"path":"a.py","old":"x","new":"y"}', finish='stop', reasoning=''):
    return {'model':'qwen38','choices':[{'message':{'content':content,'reasoning_content':reasoning},'finish_reason':finish}],
            'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}


@pytest.mark.parametrize('content', ['', None])
def test_reasoning_without_final_answer_is_not_a_patch(content):
    r=observe_response(response(content, 'length', 'pensando…'))
    assert r['response_status']=='OUTPUT_LIMIT' and r['answer'] is None
    assert r['reasoning_bytes']==len('pensando…'.encode()) and r['content_bytes']==0
    assert r['reasoning_tokens'] is None


def test_even_parseable_truncated_json_cannot_pass():
    assert observe_response(response(finish='length'))['answer'] is None


@pytest.mark.parametrize('content', ['```json\n{}\n```','{"path":"a","path":"b"}', 'NaN', '{} trailing'])
def test_ambiguous_or_non_strict_json_is_refused(content):
    assert observe_response(response(content))['response_status']=='INVALID_JSON'


def test_complete_json_is_only_a_proposal_not_a_verified_patch():
    r=observe_response(response())
    assert r['response_status']=='JSON_PROPOSAL' and r['answer']['path']=='a.py'
    assert 'success' not in r and r['reasoning_bytes']==0


def test_missing_and_multiple_choices_cannot_be_promoted():
    r=response();r['choices']*=2
    assert observe_response(r)['response_status']=='INVALID_RESPONSE'
    assert observe_response({})['response_status']=='INVALID_RESPONSE'


def request():
    return {'model':'qwen38','max_tokens':2048,'temperature':0,'stream':False}


def test_transport_refuses_ignored_profile_and_oversized_output():
    validate_request(request(),'provider-default')
    omitted=request();del omitted['stream'];validate_request(omitted,'provider-default')
    direct=request()|{'chat_template_kwargs':{'enable_thinking':False}}
    validate_request(direct,'direct-answer')
    for bad,profile in [(request(),'direct-answer'),(direct,'provider-default'),
                        (direct|{'max_tokens':4096},'direct-answer'),
                        (direct|{'chat_template_kwargs':{'enable_thinking':0}},'direct-answer'),
                        (direct|{'model':'different'},'direct-answer')]:
        with pytest.raises(ValueError):validate_request(bad,profile)


def trials():
    return [{'task':task,'round':n,'response_profile':profile,'success':True,
             'total_wall_ms':20 if profile=='provider-default' else 10,
             'calls':[{'usage':{'total_tokens':20 if profile=='provider-default' else 10}}]}
            for n in (1,2) for task in ('a','b') for profile in ('provider-default','direct-answer')]


@pytest.mark.parametrize('fault',['quality','tokens','time','missing','duplicate','side_effect','identity'])
def test_response_admission_needs_complete_correct_paired_trials(fault):
    rows=trials();assert response_decision(rows,['a','b'])['status']=='ACCEPT_LOCAL_RESPONSE_PROFILE'
    r=next(r for r in rows if r['response_profile']=='direct-answer' and r['round']==2)
    if fault=='quality':r['success']=False
    elif fault=='tokens':r['calls'][0]['usage']['total_tokens']=100
    elif fault=='time':r['total_wall_ms']=100
    elif fault=='missing':r['calls'][0]['usage']=None
    elif fault=='duplicate':rows[-1]=rows[0]
    elif fault=='side_effect':r['global_analytics_changed']=True
    else:r['provider_changed']=True
    assert response_decision(rows,['a','b'])['status']!='ACCEPT_LOCAL_RESPONSE_PROFILE'
