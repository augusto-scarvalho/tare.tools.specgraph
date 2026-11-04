"""One source-exact Aider-style edit block; no JSON/code repair heuristics."""
try:
    from .response_contract import observe_response
except ImportError:
    from response_contract import observe_response


def parse_edit_block(content):
    if not isinstance(content, str) or len(content.encode()) > 16384:
        raise ValueError('bounded edit text required')
    lines = content.strip().splitlines()

    def unfence(xs):
        if xs and xs[0].startswith('```'):
            if len(xs) < 2 or xs[-1] != '```':
                raise ValueError('unfinished fence')
            return xs[1:-1]
        return xs

    lines = unfence(lines)
    if len(lines) < 5:
        raise ValueError('complete path and edit block required')
    path, lines = lines[0], unfence(lines[1:])
    markers = ('<<<<<<< SEARCH', '=======', '>>>>>>> REPLACE')
    if any(lines.count(marker) != 1 for marker in markers):
        raise ValueError('exactly one unambiguous edit block required')
    if lines[0] != markers[0] or lines[-1] != markers[2]:
        raise ValueError('prose outside edit block')
    middle = lines.index(markers[1])
    old, new = '\n'.join(lines[1:middle])+'\n', '\n'.join(lines[middle+1:-1])+'\n'
    if not old.strip() or not new.strip():
        raise ValueError('nonempty replacement required')
    return {'path':path, 'old':old, 'new':new}


def observe_edit_response(value):
    result = observe_response(value)
    if result['finish_reason'] != 'stop' or result['response_status'] == 'EMPTY_ANSWER':
        result['answer'] = None
        return result
    try:
        proposal = parse_edit_block(value['choices'][0]['message']['content'])
        result.update(response_status='EDIT_PROPOSAL', answer=proposal)
    except (ValueError, KeyError, TypeError, IndexError):
        result.update(response_status='INVALID_EDIT', answer=None)
    return result


def edit_instruction(task):
    return task['prompt'] + '\nReturn exactly one SEARCH/REPLACE edit, not JSON or an explanation. Copy actual source lines, not JSON-escaped strings. Use this exact shape:\n' + task['path'] + '\n```python\n<<<<<<< SEARCH\nexact old source lines\n=======\nreplacement source lines\n>>>>>>> REPLACE\n```\nThe executor accepts only one unique replacement within the supplied target function and runs hidden regression checks.'


def native_decision(records, tasks):
    expected = {(n, task) for n in (1,2) for task in tasks}
    complete = len(tasks) == 2 and len(records) == len(expected) == 4 and {(r['round'],r['task']) for r in records} == expected
    qualified = complete and all(r['response_profile'] == 'native-edit' and r['success'] and
        len(r['calls']) == 1 and r['calls'][0].get('usage') is not None and
        not r.get('global_analytics_changed') and not r.get('provider_changed') for r in records)
    return dict(status='QUALIFIED_LOCAL_EDIT_FORMAT' if qualified else 'REJECT_LOCAL_EDIT_FORMAT' if complete else 'INCOMPLETE',
                successes=sum(r['success'] for r in records), trials=len(records), automatic_roles=[],
                paired_savings_claim=False, host_work_complete=False, x1_reopened=False,
                scope='Four native edit proposals on reused historical repairs; not autonomous editing or general host qualification')
