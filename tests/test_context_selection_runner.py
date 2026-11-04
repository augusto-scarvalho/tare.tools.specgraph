"""Measurement and mutation boundary falsifiers for the X1 native experiment."""
import pytest
from tools.qualify_context_selection import actual_usage, checked_replacement, decide

SOURCE=b'def target():\n    return 1\n\ndef other():\n    return 2\n'
TASK={'path':'src/unit.py','target_range':[1,2]}


def test_patch_can_only_replace_one_fragment_of_the_explicit_target():
    p={'path':'src/unit.py','old':'    return 1','new':'    return 3'}
    assert checked_replacement(SOURCE,TASK,p)==SOURCE.replace(b'return 1',b'return 3')
    with pytest.raises(ValueError,match='path'):
        checked_replacement(SOURCE,TASK,p|{'path':'../outside.py'})
    with pytest.raises(ValueError,match='escapes'):
        checked_replacement(SOURCE,TASK,p|{'old':'    return 2'})
    with pytest.raises(ValueError,match='declaration'):
        checked_replacement(SOURCE,TASK,p|{'new':'    return 3\n\ndef extra():\n    return 4'})


def test_nonunique_and_nonchanging_proposals_are_refused():
    with pytest.raises(ValueError,match='exactly once'):
        checked_replacement(SOURCE,TASK,{'path':'src/unit.py','old':'return','new':'yield'})
    with pytest.raises(ValueError):
        checked_replacement(SOURCE,TASK,{'path':'src/unit.py','old':'return 1','new':'return 1'})


@pytest.mark.parametrize('usage',[None,{}, {'prompt_tokens':10,'completion_tokens':5,'total_tokens':14}, {'prompt_tokens':True,'completion_tokens':5,'total_tokens':6}, {'prompt_tokens':10,'completion_tokens':5,'total_tokens':15,'prompt_tokens_details':{'cached_tokens':11}}])
def test_missing_inconsistent_or_invalid_usage_cannot_be_promoted(usage):
    assert actual_usage(usage) is None


def test_cache_is_a_subset_not_added_twice():
    result=actual_usage({'prompt_tokens':10,'completion_tokens':5,'total_tokens':15,'prompt_tokens_details':{'cached_tokens':8}})
    assert result['total_tokens']==15 and result['cache_read_tokens']==8


def measured():
    p={'tasks':[{'warm_selector':{x:{'p95_ms':1} for x in ('simple','utility-v1')}}],'scope':'test'}
    records=[{'round':n,'policy':policy,'success':True,'total_wall_ms':20 if policy=='simple' else 10,'calls':[{'usage':{'total_tokens':100 if policy=='simple' else 80}}]} for n in (1,2) for policy in ('simple','utility-v1') for _ in range(2)]
    return p,records


@pytest.mark.parametrize('fault',['quality','time','tokens','missing_usage','one_round','overhead','side_effect'])
def test_lower_bytes_or_one_good_round_do_not_establish_gain(fault):
    p,r=measured()
    assert decide(p,r)['status']=='ACCEPT_BOUNDED_IMPLEMENTER_EXPERIMENT'
    candidate=next(x for x in r if x['round']==2 and x['policy']=='utility-v1')
    if fault=='quality':candidate['success']=False
    elif fault=='time':candidate['total_wall_ms']=100
    elif fault=='tokens':candidate['calls'][0]['usage']['total_tokens']=500
    elif fault=='missing_usage':candidate['calls'][0]['usage']=None
    elif fault=='one_round':r=r[:4]
    elif fault=='overhead':p['tasks'][0]['warm_selector']['utility-v1']['p95_ms']=22
    else:candidate['global_analytics_changed']=True
    assert decide(p,r)['status']!='ACCEPT_BOUNDED_IMPLEMENTER_EXPERIMENT'
