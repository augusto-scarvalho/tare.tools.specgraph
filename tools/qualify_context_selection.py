"""Frozen two-arm historical repairs through native Aider and a bounded executor.

No global hook activation, model switch or live checkout edit. The host proposes
one exact replacement; only the copied task file can be changed by the executor.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import threading
import time
import urllib.request

try:
 from .response_contract import observe_response, validate_request
 from .native_edit_contract import observe_edit_response, edit_instruction
except ImportError:
 from response_contract import observe_response, validate_request
 from native_edit_contract import observe_edit_response, edit_instruction

ENDPOINT = os.environ.get('TARE_LOCAL_MODELS', 'http://127.0.0.1:8080').rstrip('/').removesuffix('/v1') + '/v1'
MAX_REQUEST = 131072
MAX_RESPONSE = 1048576

def wire(x):return (json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def sha(x):return hashlib.sha256(x).hexdigest()
def save(p,x):Path(p).write_bytes(wire(x))
def read(p):
 raw=Path(p).read_bytes()
 if len(raw)>1048576:raise ValueError('bounded JSON input required')
 return json.loads(raw)
def load_module(path,name):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def provider():
 with urllib.request.urlopen(ENDPOINT.removesuffix('/v1')+'/health',timeout=5) as r:j=json.loads(r.read(16384))
 if j.get('current_model')!='qwen38' or j.get('backend_healthy') is not True:raise ValueError('already loaded healthy qwen38 required; never switch')
 return {k:j.get(k) for k in ('status','current_model','backend_healthy','role','backend_pid','backend_port')}

def actual_usage(value):
 if not isinstance(value,dict):return None
 keys=('prompt_tokens','completion_tokens','total_tokens')
 if any(type(value.get(k)) is not int or value[k]<0 for k in keys):return None
 if value['total_tokens']!=value['prompt_tokens']+value['completion_tokens']:return None
 cache=(value.get('prompt_tokens_details') or {}).get('cached_tokens')
 if cache is not None and (type(cache) is not int or not 0<=cache<=value['prompt_tokens']):return None
 return {k:value[k] for k in keys}|{'cache_read_tokens':cache,'cache_accounting':'SUBSET_OF_INPUT' if cache is not None else 'UNKNOWN'}

def parse_answer(text):
 if not isinstance(text,str):return None
 text=text.strip()
 if text.startswith('```') and text.endswith('```'):text='\n'.join(text.splitlines()[1:-1])
 try:return json.loads(text)
 except ValueError:return None

def checked_replacement(source, task, proposal):
 """Refuse path changes, ambiguous replacements and edits outside the target."""
 if not isinstance(proposal,dict) or set(proposal)!={'path','old','new'} or proposal['path']!=task['path']:
  raise ValueError('exact target path and replacement object required')
 old,new=proposal['old'],proposal['new']
 if not isinstance(old,str) or not isinstance(new,str) or not old or old==new or len(new.encode())>8192:
  raise ValueError('bounded nonempty replacement required')
 text=source.decode('utf-8')
 if text.count(old)!=1:raise ValueError('replacement must match exactly once')
 lines=text.splitlines(keepends=True);start,end=task['target_range'];lo=len(''.join(lines[:start-1]));hi=len(''.join(lines[:end]))
 pos=text.index(old)
 if pos<lo or pos+len(old)>hi:raise ValueError('replacement escapes target function')
 result=text.replace(old,new,1);tree=ast.parse(result)
 before=sorted((type(n).__name__,n.name) for n in ast.walk(ast.parse(text)) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)))
 after=sorted((type(n).__name__,n.name) for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)))
 if before!=after:raise ValueError('replacement changes declaration inventory')
 return result.encode('utf-8')

def analytics():
 p=Path.home()/'.aider/analytics.json'
 return {'path':str(p),'sha256':sha(p.read_bytes()) if p.is_file() else None}

def prepare(args):
 out=args.output.resolve()
 if out.exists():raise ValueError('fresh output required')
 verification=read(args.prepared/'oracle-verification.json')
 if verification['status']!='PASS':raise ValueError('before/after oracle verification required')
 prov=provider();selector=load_module(args.selector,'x1_selector');tasks=[];files={}
 def freeze(p):
  p=p.resolve();files[str(p)]=sha(p.read_bytes());return str(p)
 for directory in sorted(args.prepared.iterdir()):
  if not (directory/'task.json').is_file():continue
  task=read(directory/'task.json');input_path=directory/'input.json';data=read(input_path)
  if sha((directory/'corpus'/task['path']).read_bytes())!=task['source_sha256']:raise ValueError('task source mismatch')
  if sha((directory/'oracle.py').read_bytes())!=task['oracle_sha256']:raise ValueError('oracle mismatch')
  warm={};sizes={}
  for policy in ('simple','utility-v1'):
   samples=[]
   for _ in range(120):
    started=time.perf_counter_ns();result=selector.select_context(**data,role='implementer',policy=policy,max_bytes=16384);samples.append((time.perf_counter_ns()-started)/1e6)
   warm[policy]={'count':len(samples),'p50_ms':statistics.median(samples),'p95_ms':sorted(samples)[113],'samples_ms':samples};sizes[policy]=result['bytes']
  tasks.append({'id':task['id'],'task':freeze(directory/'task.json'),'input':freeze(input_path),'source':freeze(directory/'corpus'/task['path']),'oracle':freeze(directory/'oracle.py'),'warm_selector':warm,'packet_bytes':sizes})
 if len(tasks)!=2:raise ValueError('exactly two contracted repairs required')
 contract=freeze(args.contract);selector_path=freeze(args.selector);runner=freeze(Path(__file__));headless=freeze(Path(__file__).with_name('aider_headless.py'))
 freeze(args.prepared/'oracle-verification.json');freeze(Path(__file__).with_name('response_contract.py'));freeze(Path(__file__).with_name('native_edit_contract.py'))
 out.mkdir();protocol={'schema':'tare.tools/specgraph-x1-repair/2','authority':'NONE','work_id':'SPECGRAPH-COMPLETION-X1','host_work_id':'SPECGRAPH-FOLLOWUP-HOST-ADOPTION-20260908','tasks':tasks,'files':files,'contract':contract,'selector':selector_path,'runner':runner,'headless_entry':headless,'rounds':2,'model':'qwen38','provider':prov,'host':{'aider_version':importlib.metadata.version('aider-chat'),'python':sys.executable},'budgets':{'packet_bytes':16384,'model_calls_per_trial':1,'output_tokens':2048,'request_bytes':MAX_REQUEST,'response_bytes':MAX_RESPONSE,'trial_seconds':150,'retry_budget':0},'decision_rule':'All utility trials pass, no correctness loss, lower total tokens and no greater total task wall time in each round; warm selector additional p95 <=20ms. Otherwise reject this scorer for adoption. Missing counters prohibit promotion.','scope':'Historical implementation replay, Aider explicit /run, bounded patch executor and hidden unit falsifiers; not automatic hooks or complete multi-role lifecycle','automatic_roles':[],'analytics_before':analytics()}
 save(out/'protocol.json',protocol);save(out/'protocol-lock.json',{'sha256':sha(wire(protocol))})
 return {'status':'PREPARED_NO_INFERENCE','protocol_sha256':sha(wire(protocol)),'tasks':[{'id':x['id'],'packet_bytes':x['packet_bytes']} for x in tasks]}

def frozen(out):
 p=read(out/'protocol.json')
 if sha(wire(p))!=read(out/'protocol-lock.json')['sha256']:raise ValueError('protocol changed')
 for path,digest in p['files'].items():
  if sha(Path(path).read_bytes())!=digest:raise ValueError('frozen input changed: '+path)
 return p

def emit(out,task_id,policy,dest):
 p=frozen(out);task=next(x for x in p['tasks'] if x['id']==task_id);selector=load_module(p['selector'],'x1_selector')
 started=time.perf_counter();result=selector.select_context(**read(task['input']),role='implementer',policy=policy,max_bytes=p['budgets']['packet_bytes']);raw=wire(result['packet'])
 if len(raw)>p['budgets']['packet_bytes']:raise ValueError('packet budget exceeded')
 (dest/'packet.json').write_bytes(raw);save(dest/'selection.json',result|{'emission_wall_ms':(time.perf_counter()-started)*1000})
 sys.stdout.buffer.write(raw)

def trial(out,task_id,policy,round_number,*,response_profile=None):
 p=frozen(out);task=next(x for x in p['tasks'] if x['id']==task_id);detail=read(task['task']);dest=out/(f'r{round_number}-{task_id}-{policy}'+('--'+response_profile if response_profile else ''));dest.mkdir()
 started=time.perf_counter();health=provider();records=[];gate=threading.Lock()
 if response_profile and (health!=p['provider'] or response_profile not in p['response_profiles']):raise ValueError('frozen provider instance and profile required')
 class Observer(BaseHTTPRequestHandler):
  def log_message(self,*args):pass
  def do_POST(self):
   if self.path!='/v1/chat/completions':self.send_error(404);return
   try:length=int(self.headers.get('Content-Length','0'))
   except ValueError:self.send_error(400);return
   if not 0<length<=MAX_REQUEST:self.send_error(413);return
   raw=self.rfile.read(length)
   try:request=json.loads(raw)
   except ValueError:self.send_error(400);return
   if request.get('model')!=p['model'] or request.get('stream',False) is not False or type(request.get('max_tokens')) is not int or not 1<=request['max_tokens']<=2048:
    self.send_error(400,'frozen model and output cap required');return
   if response_profile:
    try:validate_request(request,'direct-answer' if response_profile=='native-edit' else response_profile)
    except ValueError as e:self.send_error(400,str(e));return
   with gate:
    if records:self.send_error(429,'single call budget');return
    packet=(dest/'packet.json').read_text(encoding='utf-8').strip()
    included=any(isinstance(m,dict) and isinstance(m.get('content'),str) and packet in m['content'] for m in request.get('messages',[]))
    record={'request_sha256':sha(raw),'request_bytes':len(raw),'packet_observed':included,'usage':None,'answer':None};records.append(record)
   if response_profile:
    (dest/'request.json').write_bytes(raw);save(dest/'attempt.json',record|{'state':'FORWARD_ATTEMPT_RESERVED'})
   provider_started=time.perf_counter()
   try:
    forward=urllib.request.Request(ENDPOINT+'/chat/completions',data=raw,headers={'Content-Type':'application/json','Authorization':'Bearer local-key'})
    with urllib.request.urlopen(forward,timeout=115) as response:body=response.read(MAX_RESPONSE+1)
    if len(body)>MAX_RESPONSE:raise ValueError('response budget exceeded')
    if response_profile:(dest/'response.json').write_bytes(body)
    decoded=json.loads(body)
    record.update(response_sha256=sha(body),usage=actual_usage(decoded.get('usage')),model=decoded.get('model'))
    if response_profile:record.update((observe_edit_response if response_profile=='native-edit' else observe_response)(decoded))
    else:
     choice=decoded['choices'][0];record.update(answer=parse_answer(choice['message'].get('content')),finish_reason=choice.get('finish_reason'))
    self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
   except Exception as e:
    record['error']=type(e).__name__;self.send_error(502,'bounded provider failure')
   finally:
    record['provider_wall_ms']=(time.perf_counter()-provider_started)*1000
    if response_profile:save(dest/'attempt.json',record|{'state':'FORWARD_FINISHED'})
 server=ThreadingHTTPServer(('127.0.0.1',0),Observer);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 helper=[sys.executable,'-B',str(Path(__file__).resolve()),'emit','--output',str(out),'--task',task_id,'--policy',policy,'--trial-dir',str(dest)]
 if os.name=='nt':
  if any(any(c in v for c in '%&|<>^\r\n') for v in helper):raise ValueError('shell-sensitive path unsupported')
  command=subprocess.list2cmdline(helper)
 else:
  import shlex
  command=shlex.join(helper)
 (dest/'commands.txt').write_text('/run '+command+'\n',encoding='utf-8')
 instruction=detail['prompt']+'\nReturn ONLY a JSON object with keys path, old, new. Path must be '+detail['path']+'. old must be one unique exact substring inside the supplied target function; new its replacement. Preserve exact indentation and line breaks. Do not return a full rewritten file or markdown. No tool commands. The external executor will apply this one substitution and run hidden regression checks.'
 if response_profile=='native-edit':instruction=edit_instruction(detail)
 (dest/'prompt.txt').write_text(instruction,encoding='utf-8');(dest/'empty.yml').write_text('{}\n');(dest/'empty.env').write_text('')
 params={'max_tokens':2048,'num_retries':0,'temperature':0}
 if response_profile in {'direct-answer','native-edit'}:params['extra_body']={'chat_template_kwargs':{'enable_thinking':False}}
 (dest/'model.yml').write_text(json.dumps([{'name':'openai/qwen38','edit_format':'ask','extra_params':params}]))
 env={k:v for k,v in os.environ.items() if k.upper() in {'PATH','PATHEXT','SYSTEMROOT','WINDIR','COMSPEC','TEMP','TMP','HOME','USERPROFILE','APPDATA','LOCALAPPDATA'}}
 env.update(PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1')
 argv=[sys.executable,'-B',p['headless_entry'],'--model','openai/qwen38','--openai-api-base',f'http://127.0.0.1:{server.server_port}/v1','--openai-api-key','local-key','--model-settings-file',str(dest/'model.yml'),'--config',str(dest/'empty.yml'),'--env-file',str(dest/'empty.env'),'--edit-format','ask','--map-tokens','0','--no-git','--no-auto-commits','--no-dirty-commits','--no-stream','--no-pretty','--no-check-update','--no-show-release-notes','--no-analytics','--no-detect-urls','--no-show-model-warnings','--yes-always','--timeout','120','--load',str(dest/'commands.txt'),'--message-file',str(dest/'prompt.txt'),'--chat-history-file',str(dest/'host-chat.md'),'--input-history-file',str(dest/'host-input.txt')]
 result={'task':task_id,'policy':policy,'round':round_number,'success':False,'interventions':0,'retries':0,'calls':records}
 if response_profile:result['response_profile']=response_profile
 stdout=stderr=b''
 host_start=time.perf_counter()
 try:
  process=subprocess.Popen(argv,cwd=dest,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  try:stdout,stderr=process.communicate(timeout=130);result['exit_code']=process.returncode
  except subprocess.TimeoutExpired:
   if os.name=='nt':subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,timeout=5)
   else:process.kill()
   stdout,stderr=process.communicate(timeout=5);result.update(exit_code=None,timeout=True)
  result['host_wall_ms']=(time.perf_counter()-host_start)*1000
  (dest/'stdout.txt').write_bytes(stdout[:65536]);(dest/'stderr.txt').write_bytes(stderr[:8192])
  if result.get('exit_code')==0 and len(records)==1 and records[0]['packet_observed'] and (not response_profile or records[0].get('response_status') in {'JSON_PROPOSAL','EDIT_PROPOSAL'}):
   try:
    source=Path(task['source']).read_bytes();new=checked_replacement(source,detail,records[0]['answer']);candidate=dest/'candidate.py';candidate.write_bytes(new)
    check_start=time.perf_counter();check=subprocess.run([sys.executable,'-B',task['oracle'],str(candidate)],cwd=dest,env=env,capture_output=True,timeout=10)
    result.update(verification_wall_ms=(time.perf_counter()-check_start)*1000,verification_exit_code=check.returncode,verification=parse_answer(check.stdout.decode('utf-8')),candidate_sha256=sha(new),success=check.returncode==0)
    (dest/'verification.stdout').write_bytes(check.stdout);(dest/'verification.stderr').write_bytes(check.stderr)
   except Exception as e:result['candidate_error']=type(e).__name__+':'+str(e)[:200]
 except Exception as e:result['host_error']=type(e).__name__+':'+str(e)[:200]
 finally:
  server.shutdown();server.server_close();result['total_wall_ms']=(time.perf_counter()-started)*1000
 if response_profile:
  try:
   result['provider_after']=provider();result['provider_changed']=result['provider_after']!=p['provider']
  except Exception as e:result.update(provider_changed=True,provider_error=type(e).__name__)
  result['browser_requests_suppressed']=stderr.count(b'browser_open_suppressed')
 result['analytics_after']=analytics()
 if result['analytics_after']!=p['analytics_before']:result['global_analytics_changed']=True
 save(dest/'receipt.json',result);return result

def decide(p,records):
 rounds=[]
 complete=len(records)==8
 for number in (1,2):
  row={'round':number}
  for policy in ('simple','utility-v1'):
   xs=[r for r in records if r['round']==number and r['policy']==policy]
   measured=len(xs)==2 and all(len(r['calls'])==1 and r['calls'][0].get('usage') is not None for r in xs)
   row[policy]={'trials':len(xs),'successes':sum(r['success'] for r in xs),'tokens':sum(r['calls'][0]['usage']['total_tokens'] for r in xs) if measured else None,'total_wall_ms':sum(r['total_wall_ms'] for r in xs)}
  a,b=row['simple'],row['utility-v1'];row['gain']=b['successes']==2 and b['successes']>=a['successes'] and a['tokens'] is not None and b['tokens'] is not None and b['tokens']<a['tokens'] and b['total_wall_ms']<=a['total_wall_ms'];rounds.append(row)
 warm=all(t['warm_selector']['utility-v1']['p95_ms']-t['warm_selector']['simple']['p95_ms']<=20 for t in p['tasks'])
 promote=complete and warm and all(r['gain'] for r in rounds) and not any(r.get('global_analytics_changed') for r in records)
 return {'status':'ACCEPT_BOUNDED_IMPLEMENTER_EXPERIMENT' if promote else 'REJECT_SCORER_FOR_ADOPTION' if complete else 'INCOMPLETE','rounds':rounds,'warm_budget_pass':warm,'automatic_roles':[],'host_work_complete':False,'scope':p['scope']}

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['prepare','emit','run']);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--prepared',type=Path);parser.add_argument('--selector',type=Path);parser.add_argument('--contract',type=Path);parser.add_argument('--task');parser.add_argument('--policy',choices=['simple','utility-v1']);parser.add_argument('--trial-dir',type=Path);parser.add_argument('--execute',action='store_true');args=parser.parse_args();args.output=args.output.resolve()
 if args.action=='prepare':result=prepare(args)
 elif args.action=='emit':emit(args.output,args.task,args.policy,args.trial_dir.resolve());return
 else:
  if not args.execute:parser.error('run requires --execute; prepare never infers')
  p=frozen(args.output);records=[]
  for number in (1,2):
   for t in p['tasks']:
    for policy in (('simple','utility-v1') if number==1 else ('utility-v1','simple')):
     r=trial(args.output,t['id'],policy,number);records.append(r);save(args.output/'results.json',{'records':records,'decision':decide(p,records)});print(json.dumps({k:r[k] for k in ('task','policy','round','success','total_wall_ms')}),flush=True)
     if r.get('global_analytics_changed'):raise ValueError('unexpected analytics change; trials stopped')
  result=decide(p,records);save(args.output/'decision.json',result)
 print(wire(result).decode(),end='')

if __name__=='__main__':main()
