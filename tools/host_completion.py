"""Terminal host completion is independent of exit code and candidate correctness."""


def completion_status(host, events, stderr, exit_code, *, timed_out=False):
    if timed_out or exit_code != 0:
        return {'completed':False,'reason':'PROCESS_TIMEOUT' if timed_out else 'PROCESS_FAILED'}
    if isinstance(stderr,bytes):stderr=stderr.decode('utf-8',errors='replace')
    if host=='agy':
        if 'print timeout' in stderr.lower() or 'returning partial output' in stderr.lower():
            return {'completed':False,'reason':'HOST_PARTIAL_TIMEOUT'}
        tools = {}
        for event in events:
            step = event.get('step_update', {}) if event.get('event') == 'step_update' else {}
            index = step.get('step_index')
            if index is not None and (step.get('step_type') == 'tool' or index in tools) and step.get('state'):
                tools[index] = step['state']
        pending = sorted(index for index, state in tools.items() if state not in ('DONE', 'ERROR', 'CANCELLED'))
        if pending:
            return {'completed':False,'reason':'HOST_PENDING_TOOLS','pending_steps':pending}
        rows=[e['result'] for e in events if e.get('event')=='result']
        complete=len(rows)==1 and rows[0].get('status')=='SUCCESS' and not rows[0].get('error')
    elif host=='claude':
        rows=[e for e in events if e.get('type')=='result']
        complete=len(rows)==1 and rows[0].get('subtype')=='success' and not rows[0].get('is_error')
    elif host=='codex':
        rows=[e for e in events if e.get('type') in ('turn.completed','turn.failed')]
        complete=len(rows)==1 and rows[0]['type']=='turn.completed'
    else:
        raise ValueError('unknown host')
    return {'completed':bool(complete),'reason':'COMPLETED' if complete else 'NO_SUCCESSFUL_TERMINAL_EVENT'}
