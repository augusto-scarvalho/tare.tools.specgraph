"""Paired host/model diagnostics after editable-file and scope corrections."""
from __future__ import annotations
import argparse
import ast
import contextlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import qualify_native_host as native
import qualify_economy_pilot as economy
from economy_oracles import TASKS, SELECTIONS
from scoped_edit import target_node, span, validate_candidate
from host_completion import completion_status

SCHEMA = 'tare.tools/host-model-matrix/1'
HOSTS = {'aider-qwen': {'model':'qwen38', 'host':'aider', 'effort':'disabled'},
         'aider-opus': {'model':'opus-4.6-bedrock', 'host':'aider', 'effort':'disabled', 'inference_backend':'bedrock'},
         'claude-sonnet': {'model':'claude-sonnet-5', 'host':'claude', 'effort':'high'},
         'codex-astra': {'model':'gpt-6-astra', 'host':'codex', 'effort':'low'},
         'agy-gemini': {'model':'gemini-3.8-flash-high', 'host':'agy', 'effort':'high'}}
ORDER = [(task, arm) for i, task in enumerate(TASKS) for arm in (native.ARMS if i%2==0 else tuple(reversed(native.ARMS)))]
CONTRACT = (' Change only the body of the named target function. Local helper definitions inside that body are allowed. '
            'You may include unchanged decorators in the edit context. Preserve its signature, decorators, and all text outside its declaration.')
save, read, wire, sha = native.save, native.read, native.wire, native.sha
_original_frozen, _provider = native.frozen, native.base.provider


def frozen(directory):
    p = _original_frozen(directory)
    if p['schema'] != SCHEMA:
        raise ValueError('wrong host matrix protocol')
    return p


def context_packet(p, task_id, arm, dest):
    sys.path.insert(0, p['engine'])
    row = next(t for t in p['tasks'] if t['id'] == task_id)
    task = read(row['task']); start = time.perf_counter(); index_ms = 0.0
    if arm == 'full':
        body = economy.direct_packet(task, row['root'], dest/'workspace')
    else:
        from specgraph.indexer import index_repository
        from specgraph.storage import RepositoryIndex
        state = dest/'context-state'
        if state.exists():
            raise ValueError('cold index must not exist')
        metadata = index_repository(row['root'], state_dir=state)
        index_ms = (time.perf_counter()-start)*1000
        task = task | {'snapshot':metadata['content_hash']}
        header, _ = native.packet(task,row['root'],state,arm,dest/'workspace').split(b'\n',1)
        header = json.loads(header)
        with RepositoryIndex(row['root'], state_dir=state) as index:
            source = index.connection.execute('SELECT body FROM sources WHERE file_path=?',(task['path'],)).fetchone()[0]
        if sha(source.encode()) != task['source_sha256']:
            raise ValueError('source hash mismatch')
        lo,hi = span(target_node(ast.parse(source),task['symbol']))
        header['lines']=[lo,hi]; header['scope']='exact published target declaration including decorators; dependencies omitted'
        code=''.join(source.splitlines(keepends=True)[lo-1:hi])
        body=wire(header)+b'```python\n'+code.encode()+b'\n```\n'
    if len(body)>32768:
        raise ValueError('packet byte budget')
    (dest/'packet.txt').write_bytes(body)
    save(dest/'emission.json',{'bytes':len(body),'sha256':sha(body),'index_builds':int(arm=='focused'),
        'index_wall_ms':index_ms,'query_wall_ms':(time.perf_counter()-start)*1000,
        'selection_queries':int(arm=='focused'),'source_reads':1})
    return body


def prepare(args):
    economy.prepare(args)
    out=args.output.resolve();p=read(out/'protocol.json');engine=Path(__file__).resolve().parents[1]
    for row in p['tasks']:
        task=read(row['task']) | {'edit_scope':'function-body-v2'}
        task['prompt'] += CONTRACT
        save(Path(row['task']),task);p['files'][row['task']]=sha(wire(task))
    for name in ('qualify_host_matrix.py','scoped_edit.py','aider_projected.py','bedrock_observed.py','host_completion.py'):
        path=engine/'tools'/name;p['files'][str(path)]=sha(path.read_bytes())
    p.update(schema=SCHEMA,runner=str(Path(__file__).resolve()),headless=str(engine/'tools/aider_projected.py'),
        projected_editable=True,order=ORDER,hosts=HOSTS,scope='diagnostic reuse of three tasks; paired within each host; not fresh-task utility',
        decision_rule='all three tasks correct in each arm before any quality-preserving savings claim',
        budgets={'paired_cells':30,'aider_calls_per_cell':1,'aider_output_tokens':2048,'vendor_wall_seconds':180,
                 'claude_max_turns':8,'claude_max_budget_usd':2,'codex_agy_token_cap':None,'retries_by_investigator':0},
        cost_limits=['vendor tokenizers and tool policies differ: compare arms within host',
                     'CLI internal retries and hidden context may be unobservable',
                     'investigator and setup costs separate; no monetary savings inferred from tokens'])
    save(out/'protocol.json',p);save(out/'protocol-lock.json',{'sha256':sha(wire(p))})
    for name,host in HOSTS.items():
        folder=out/name;folder.mkdir()
        provider=_provider() if name=='aider-qwen' else __import__('bedrock_observed').identity() if name=='aider-opus' else host
        hp=p|host|{'provider':provider,'host_id':name}
        save(folder/'protocol.json',hp);save(folder/'protocol-lock.json',{'sha256':sha(wire(hp))})
    return {'status':'PREPARED','hosts':list(HOSTS),'paired_cells':30,'preparation_wall_ms':p['preparation_wall_ms']}


def cli_argv(p, dest):
    home=Path(os.environ['USERPROFILE']);host=p['host'];workspace=dest/'workspace'
    if host=='claude':
        exe=home/'AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe'
        return [str(exe),'--add-dir',str(workspace),'--model',p['model'],'--effort',p['effort'],'--output-format','stream-json','--verbose',
                '--safe-mode','--restricted','--tools','Read,Edit,Write,Glob,Grep','--allowedTools','Read,Edit,Write,Glob,Grep',
                '--permission-mode','acceptEdits','--permission-prompts','none','--strict-mcp-config','--mcp-config','{"mcpServers":{}}',
                '--no-session-persistence','--max-turns','8','--max-budget-usd','2','--no-chrome']
    if host=='codex':
        exe=home/'AppData/Local/Programs/OpenAI/Codex/bin/codex.exe'
        return [str(exe),'exec','--ignore-user-config','--ignore-rules','--ephemeral','--skip-git-repo-check','--json',
                '--model',p['model'],'-c','model_reasoning_effort="low"','-c','features.multi_agent=false',
                '--dangerously-bypass-approvals-and-sandbox','-C',str(workspace),'-']
    exe=home/'AppData/Local/agy/bin/agy.exe'
    return [str(exe),'--add-dir',str(workspace),'--model',p['model'],'--effort',p['effort'],'--output-format','stream-json',
            '--mode','accept-edits','--sandbox','--dangerously-skip-permissions','--disable-slash-commands','--print-timeout','170s','--log-file',str(dest/'agy.log'),'--input-format','stream-json']


def cli_usage(events, host):
    # Normalize only documented whole-session counters; never count repeated streaming snapshots twice.
    if host=='codex':
        completed=[e for e in events if e.get('type')=='turn.completed']
        if len(completed)!=1 or not completed[0].get('usage'):
            return None
        u=completed[0]['usage']
        return {'input_tokens':u['input_tokens'],'output_tokens':u['output_tokens'],
                'cached_input_tokens':u.get('cached_input_tokens'),'cache_write_tokens':None,
                'total_tokens':u['input_tokens']+u['output_tokens'],'source':'codex turn.completed','cost_usd':None}
    results=[e for e in events if e.get('type')=='result']
    if host == 'agy':
        results=[e['result'] for e in events if e.get('event')=='result']
    if len(results)!=1:return None
    e=results[0];u=e.get('usage')
    if not isinstance(u,dict):return None
    if host=='claude':
        models=e.get('modelUsage')
        required=('inputTokens','outputTokens','cacheReadInputTokens','cacheCreationInputTokens')
        if not isinstance(models,dict) or not models or not all(type(v.get(k)) is int for v in models.values() for k in required):return None
        sums={k:sum(v[k] for v in models.values()) for k in required}
        return {'input_tokens':sums['inputTokens']+sums['cacheReadInputTokens']+sums['cacheCreationInputTokens'],
                'output_tokens':sums['outputTokens'],'cached_input_tokens':sums['cacheReadInputTokens'],
                'cache_write_tokens':sums['cacheCreationInputTokens'],'total_tokens':sum(sums.values()),
                'source':'claude result.modelUsage including auxiliary models','cost_usd':e.get('total_cost_usd'),
                'models':models}
    if not all(type(u.get(k)) is int for k in ('input_tokens','output_tokens','total_tokens')) or u['total_tokens'] != u['input_tokens']+u['output_tokens']:
        return None
    return {'input_tokens':u['input_tokens'],'output_tokens':u['output_tokens'],'total_tokens':None,
            'reported_total_tokens':u['total_tokens'],'cache_accounting':'UNRESOLVED; cache can exceed reported input',
            'cached_input_tokens':u.get('cache_read_tokens'),'cache_write_tokens':None,
            'thinking_tokens':u.get('thinking_tokens'),'source':'agy result.usage; thinking subset not added', 'cost_usd':None}


def cli_trial(directory, task_id, arm):
    start=time.perf_counter();p=frozen(directory);taskrow=next(t for t in p['tasks'] if t['id']==task_id);task=read(taskrow['task'])
    dest=directory/f'r1-{task_id}-{arm}';dest.mkdir();workspace=dest/'workspace';target=workspace/task['path']
    target.parent.mkdir(parents=True);source=Path(taskrow['source']).read_bytes();target.write_bytes(source)
    packet=context_packet(p,task_id,arm,dest)
    prompt=(task['prompt']+'\nTarget: '+task['path']+' ('+task['symbol']+').\n'
            'Implement the repair in the existing workspace file using your native editing tools. '
            'The supplied context is an excerpt or full file; you may read the actual target if needed. '
            'Do not delegate, browse, install packages, inspect other directories, or modify other files. '
            'External evaluation will run after you finish.\n\n').encode()+packet
    (dest/'prompt.txt').write_bytes(prompt);argv=cli_argv(p,dest);save(dest/'argv.json',argv)
    env=os.environ.copy();env.update(PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1')
    # Prevent inherited host settings from silently changing the requested model/effort.
    for key in ('CLAUDE_CODE_EFFORT_LEVEL','ANTHROPIC_MODEL','CODEX_THREAD_ID','CLAUDECODE'):
        env.pop(key,None)
    row={'host_id':p['host_id'],'model':p['model'],'effort':p['effort'],'task':task_id,'arm':arm,'round':1,
         'success':False,'interventions':0,'investigator_retries':0,'hidden_retries':None,'usage':None}
    proc=subprocess.Popen(argv,cwd=workspace,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        stdout,stderr=proc.communicate(wire({'event':'user','message':{'content':prompt.decode()}}) if p['host']=='agy' else prompt,timeout=180);row['exit_code']=proc.returncode
    except subprocess.TimeoutExpired:
        subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True,timeout=5)
        stdout,stderr=proc.communicate(timeout=5);row.update(exit_code=None,timeout=True)
    (dest/'stdout.jsonl').write_bytes(stdout);(dest/'stderr.txt').write_bytes(stderr)
    events=[]
    for line in stdout.decode('utf-8',errors='replace').splitlines():
        try:
            value=json.loads(line)
            if isinstance(value,dict):events.append(value)
        except ValueError:pass
    row['usage']=cli_usage(events,p['host']);row['event_count']=len(events)
    row['completion']=completion_status(p['host'],events,stderr,row.get('exit_code'),timed_out=row.get('timeout',False))
    row['candidate_sha256']=sha(target.read_bytes());row['changed']=target.read_bytes()!=source
    row['workspace_files']=sorted(f.relative_to(workspace).as_posix() for f in workspace.rglob('*') if f.is_file())
    try:
        validate_candidate(source,target.read_bytes(),task)
        row['scope_valid']=row['workspace_files']==[task['path']]
    except (ValueError,SyntaxError) as exc:
        row.update(scope_valid=False,scope_error=str(exc))
    if row['changed'] and row.get('scope_valid'):
        check=subprocess.run([sys.executable,'-B',taskrow['oracle'],str(target)],capture_output=True,timeout=10)
        (dest/'verification.stdout').write_bytes(check.stdout);(dest/'verification.stderr').write_bytes(check.stderr)
        row.update(verification_exit_code=check.returncode,success=check.returncode==0 and row['completion']['completed'])
    row['whole_wall_ms']=(time.perf_counter()-start)*1000
    save(dest/'receipt.json',row);return row


def run(directory):
    p=frozen(directory)
    if p['host']=='aider':
        probe=read(directory/'offline-preflight/receipt.json')
        if not probe['success'] or probe['calls'][0].get('conflicting_no_files') or not probe['calls'][0].get('editable_registered'):
            raise ValueError('corrected offline native transport must pass')
    with (directory/'run-reserved.json').open('x') as f:json.dump({'cells':ORDER,'no_retries':True},f)
    rows=[]
    for task,arm in ORDER:
        start=time.perf_counter()
        if p['host']=='aider':
            row=native.trial(directory,task,arm,1);row['whole_wall_ms']=(time.perf_counter()-start)*1000
            row.update(host_id=p['host_id'],model=p['model'],effort=p['effort'])
            save(directory/f'r1-{task}-{arm}/receipt.json',row)
        else:row=cli_trial(directory,task,arm)
        rows.append(row);save(directory/'results.json',{'records':rows})
        print(json.dumps({k:row[k] for k in ('host_id','task','arm','success','whole_wall_ms')}),flush=True)
        if row.get('provider_changed') or row.get('global_analytics_changed'):break
    return {'host':p['host_id'],'completed':len(rows),'successes':sum(r['success'] for r in rows)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','emit','probe','run'])
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--source-commit');parser.add_argument('--implementation-commit');parser.add_argument('--contract',type=Path)
    parser.add_argument('--task',choices=TASKS);parser.add_argument('--arm',choices=native.ARMS);parser.add_argument('--trial-dir',type=Path)
    args=parser.parse_args();out=args.output.resolve()
    native.frozen=frozen
    if args.action=='prepare': result=prepare(args)
    else:
        p=frozen(out)
        if p.get('inference_backend')=='bedrock':native.base.provider=__import__('bedrock_observed').identity
        if args.action=='emit':sys.stdout.buffer.write(context_packet(p,args.task,args.arm,args.trial_dir.resolve()));return
        if args.action=='probe':
            result=native.trial(out,TASKS[0],'focused',0,offline=True)
        else:result=run(out)
    sys.stdout.buffer.write(wire(result))


if __name__=='__main__':main()
