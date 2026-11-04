"""Explicit request profiles, independent reasoning and final-answer budgets."""
from __future__ import annotations

import hashlib
import json


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def qwen_profile(name, *, thinking, effort=None, reasoning_tokens=0, answer_tokens=4096, seed=0):
    if type(thinking) is not bool or type(reasoning_tokens) is not int or type(answer_tokens) is not int:
        raise ValueError('explicit boolean thinking and integer budgets required')
    if answer_tokens < 256 or reasoning_tokens < 0 or (thinking and reasoning_tokens == 0):
        raise ValueError('reserve answer space and a positive thinking budget')
    if (not thinking and (effort is not None or reasoning_tokens != 0)) or (thinking and effort not in ('low', 'medium', 'xhigh')):
        raise ValueError('effort must match thinking mode')
    if type(seed) is not int or not 0 <= seed <= 0xffffffff:
        raise ValueError('explicit uint32 seed required')
    # The native budget's forced closing message can add tokens beyond its cap.
    # This reserve is an envelope, not a promise of exact answer-token allocation.
    closing_reserve = 128 if thinking else 0
    return {'id': name, 'model': 'qwen38', 'thinking': thinking, 'effort': effort,
        'reasoning_tokens': reasoning_tokens, 'answer_reserve_tokens': answer_tokens,
        'closing_reserve_tokens': closing_reserve, 'max_tokens': reasoning_tokens + answer_tokens + closing_reserve,
        'seed': seed, 'cache_policy': 'reuse-disabled-per-request',
        'sampling': {'temperature': 1.0 if thinking else .7, 'top_p': .95 if thinking else .8,
                     'top_k': 20, 'min_p': 0.0, 'presence_penalty': 0.0 if thinking else 1.5,
                     'repeat_penalty': 1.0}}


PROFILES = {
    p['id']: p for p in (
        qwen_profile('qwen-instruct', thinking=False),
        qwen_profile('qwen-low-2048', thinking=True, effort='low', reasoning_tokens=2048),
        qwen_profile('qwen-medium-2048', thinking=True, effort='medium', reasoning_tokens=2048),
    )
}


def request_parameters(profile):
    kwargs = {'enable_thinking': profile['thinking']}
    if profile['thinking']:
        kwargs['reasoning_effort'] = profile['effort']
    sampling = profile['sampling']
    return {'temperature': sampling['temperature'], 'top_p': sampling['top_p'],
        'presence_penalty': sampling['presence_penalty'], 'max_tokens': profile['max_tokens'],
        'seed': profile['seed'], 'stream': False,
        'extra_body': {'chat_template_kwargs': kwargs, 'reasoning_budget_tokens': profile['reasoning_tokens'],
            'reasoning_format': 'deepseek', 'cache_prompt': False, 'top_k': sampling['top_k'],
            'min_p': sampling['min_p'], 'repeat_penalty': sampling['repeat_penalty']}}


def validate_wire(body, profile):
    expected = request_parameters(profile)
    expected.update(expected.pop('extra_body'))
    if body.get('model') != profile['model'] or not body.get('messages'):
        raise ValueError('wrong model or missing messages')
    if 'reasoning_effort' in body:
        raise ValueError('Qwen effort belongs inside chat_template_kwargs')
    for key, value in expected.items():
        if body.get(key, False if key == 'stream' else None) != value:
            raise ValueError(f'wire profile mismatch: {key}')


def identity_fingerprint(identity):
    required = ('binary_sha256', 'model_sha256', 'template_sha256', 'argv', 'effective_parameters')
    for name in required:
        if not identity.get(name):
            raise ValueError(f'incomplete provider identity: {name}')
    for name in required[:3]:
        value = identity[name]
        if len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError(f'invalid provider digest: {name}')
    if identity.get('model_hash_verified') is not True:
        raise ValueError('model bytes must be hashed, not inferred from an alias or registry')
    return digest({name: identity.get(name) for name in (*required, 'pid', 'process_start_ticks', 'model_stat')})


def response_usage(body):
    usage = body.get('usage', {})
    required = ('prompt_tokens', 'completion_tokens', 'total_tokens')
    if not all(type(usage.get(k)) is int and usage[k] >= 0 for k in required):
        return None
    if usage['prompt_tokens'] + usage['completion_tokens'] != usage['total_tokens']:
        return None
    return {'input_tokens': usage['prompt_tokens'], 'output_tokens': usage['completion_tokens'],
            'total_tokens': usage['total_tokens'],
            'reasoning_tokens': (usage.get('completion_tokens_details') or {}).get('reasoning_tokens'),
            'timings': body.get('timings'), 'source': 'each actual upstream response, including retries',
            'reasoning_accounting': 'included in completion; never added twice'}


def validate_controls(controls, profile, provider):
    verified = controls.get('profiles', {}).get(profile['id'], {})
    if (controls.get('schema') != 'tare.tools/qwen-transport-controls/1'
        or not controls.get('qualified') or not verified.get('passed')
        or verified.get('profile_sha256') != digest(profile)
        or controls.get('identity_fingerprint') != provider['fingerprint']):
        raise ValueError('Qwen profile needs current template/native-budget transport controls')
