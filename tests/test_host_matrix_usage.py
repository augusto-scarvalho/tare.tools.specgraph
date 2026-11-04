from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parents[1]/'tools'))
from qualify_host_matrix import cli_usage


def test_claude_counts_auxiliary_models_and_each_cache_bucket_once():
    models={name:{'inputTokens':1,'outputTokens':2,'cacheReadInputTokens':3,'cacheCreationInputTokens':4} for name in ('sonnet','haiku')}
    r=cli_usage([{'type':'result','modelUsage':models,'usage':{'input_tokens':999},'total_cost_usd':0.1}],'claude')
    assert r['total_tokens']==20 and r['input_tokens']==16 and r['output_tokens']==4
    assert r['cost_usd']==0.1


def test_reasoning_and_cache_subsets_are_not_added_to_codex_or_agy_total():
    x=cli_usage([{'type':'turn.completed','usage':{'input_tokens':100,'output_tokens':20,'cached_input_tokens':80}}],'codex')
    assert x['total_tokens']==120
    x=cli_usage([{'event':'result','result':{'usage':{'input_tokens':100,'output_tokens':20,'thinking_tokens':15,'total_tokens':120}}}],'agy')
    assert x['total_tokens'] is None and x['reported_total_tokens']==120 and x['thinking_tokens']==15
    assert cli_usage([],'agy') is None
    assert cli_usage([{'event':'result','result':{'usage':{'input_tokens':100,'output_tokens':20,'total_tokens':135}}}],'agy') is None
