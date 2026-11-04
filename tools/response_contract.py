"""Pure response and transport checks for bounded native host qualification."""
import json

PROFILES = ('provider-default', 'direct-answer')


def validate_request(request, profile, model="qwen38"):
    if profile not in PROFILES or request.get('model') != model:
        raise ValueError('frozen profile/model required')
    if (type(request.get('max_tokens')) is not int or request['max_tokens'] != 2048
            or request.get('stream', False) is not False or request.get('temperature') != 0):
        raise ValueError('frozen output, temperature and non-streaming contract required')
    kwargs = request.get('chat_template_kwargs')
    if profile == 'provider-default':
        if 'chat_template_kwargs' in request:
            raise ValueError('control must preserve provider default')
    elif not isinstance(kwargs, dict) or set(kwargs) != {'enable_thinking'} or kwargs['enable_thinking'] is not False:
        raise ValueError('explicit direct-answer setting missing from transport')


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _constant(value):
    raise ValueError('non-JSON numeric constant')


def observe_response(value):
    result = dict(response_status='INVALID_RESPONSE', answer=None, content_bytes=None,
                  reasoning_bytes=None, reasoning_tokens=None, answer_tokens=None,
                  token_split_source='UNKNOWN', finish_reason=None)
    if not isinstance(value, dict):
        return result
    choices = value.get('choices')
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        return result
    choice = choices[0]
    message = choice.get('message')
    if not isinstance(message, dict):
        return result
    content, reasoning = message.get('content'), message.get('reasoning_content')
    if any(v is not None and not isinstance(v, str) for v in (content, reasoning)):
        return result
    result.update(content_bytes=len((content or '').encode()),
                  reasoning_bytes=len(reasoning.encode()) if reasoning is not None else None,
                  finish_reason=choice.get('finish_reason'))
    # Character/byte lengths are diagnostics, never inferred token accounting.
    usage = value.get('usage')
    details = usage.get('completion_tokens_details') if isinstance(usage, dict) else None
    count = details.get('reasoning_tokens') if isinstance(details, dict) else None
    total = usage.get('completion_tokens') if isinstance(usage, dict) else None
    if type(count) is int and type(total) is int and 0 <= count <= total:
        result.update(reasoning_tokens=count, token_split_source='PROVIDER_REASONING_SUBSET')
        # Other completion categories may exist; the remainder is not necessarily answer tokens.
    if choice.get('finish_reason') == 'length':
        result['response_status'] = 'OUTPUT_LIMIT'
    elif choice.get('finish_reason') != 'stop':
        result['response_status'] = 'INCOMPLETE_RESPONSE'
    elif not content or not content.strip():
        result['response_status'] = 'EMPTY_ANSWER'
    else:
        try:
            proposal = json.loads(content, object_pairs_hook=_unique, parse_constant=_constant)
            if not isinstance(proposal, dict):
                raise ValueError('JSON object required')
            result.update(response_status='JSON_PROPOSAL', answer=proposal)
        except (ValueError, RecursionError):
            result['response_status'] = 'INVALID_JSON'
    return result


def response_decision(records, task_ids):
    expected = {(n, task, profile) for n in (1, 2) for task in task_ids for profile in PROFILES}
    actual = {(r['round'], r['task'], r['response_profile']) for r in records}
    complete = len(task_ids) == 2 and len(records) == len(expected) == 8 and actual == expected
    rounds = []
    for number in (1, 2):
        row = {'round': number}
        for profile in PROFILES:
            xs = [r for r in records if r['round'] == number and r['response_profile'] == profile]
            measured = len(xs) == 2 and all(len(r['calls']) == 1 and r['calls'][0].get('usage') is not None for r in xs)
            row[profile] = dict(trials=len(xs), successes=sum(r['success'] for r in xs),
                tokens=sum(r['calls'][0]['usage']['total_tokens'] for r in xs) if measured else None,
                total_wall_ms=sum(r['total_wall_ms'] for r in xs))
        a, b = (row[x] for x in PROFILES)
        row['pass'] = (b['successes'] == 2 and b['successes'] >= a['successes']
                       and a['tokens'] is not None and b['tokens'] is not None
                       and b['tokens'] <= a['tokens'] and b['total_wall_ms'] <= a['total_wall_ms'])
        rounds.append(row)
    admitted = complete and all(r['pass'] for r in rounds) and not any(
        r.get('global_analytics_changed') or r.get('provider_changed') for r in records)
    return dict(status='ACCEPT_LOCAL_RESPONSE_PROFILE' if admitted else 'REJECT_LOCAL_RESPONSE_PROFILE' if complete else 'INCOMPLETE',
                rounds=rounds, automatic_roles=[], host_work_complete=False, x1_reopened=False,
                scope='Two reused historical repairs; response profile only, not selector or general agent efficacy')
