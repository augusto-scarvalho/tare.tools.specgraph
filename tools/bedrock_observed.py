"""Single Converse request using the Acer keyring; no retries or persisted secrets."""
import json
import urllib.request
from urllib.parse import quote

MODEL = 'us.anthropic.claude-opus-4-6-v1'
REGION = 'us-east-1'


def identity():
    return {'provider': 'amazon-bedrock', 'model': MODEL, 'region': REGION,
            'identity_scope': 'pinned route; backend instance not exposed'}


def converse(request, dest):
    import keyring
    token = keyring.get_password('universal-agent-harness', 'AWS_BEARER_TOKEN_BEDROCK')
    if not token:
        raise ValueError('Bedrock credential absent from Acer keyring')
    system, messages = [], []
    for row in request['messages']:
        role, content = row['role'], row['content']
        if not isinstance(content, str):
            raise ValueError('text-only Bedrock trial')
        if role == 'system':
            system.append({'text': content})
        elif role in ('user', 'assistant'):
            if messages and messages[-1]['role'] == role:
                messages[-1]['content'].append({'text': content})
            else:
                messages.append({'role': role, 'content': [{'text': content}]})
        else:
            raise ValueError('unexpected message role')
    payload = {'system': system, 'messages': messages,
               'inferenceConfig': {'maxTokens': request['max_tokens'], 'temperature': request['temperature']}}
    raw = json.dumps(payload).encode()
    (dest / 'bedrock-request.json').write_bytes(raw)
    req = urllib.request.Request(f'https://bedrock-runtime.{REGION}.amazonaws.com/model/{quote(MODEL, safe="")}/converse',
        data=raw, headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
    with urllib.request.urlopen(req, timeout=115) as response:
        body = response.read(1048577)
    if len(body) > 1048576:
        raise ValueError('Bedrock response byte limit')
    (dest / 'bedrock-response.json').write_bytes(body)
    decoded = json.loads(body)
    usage = decoded['usage']; content = ''.join(b.get('text', '') for b in decoded['output']['message']['content'])
    normalized = {'prompt_tokens': usage['inputTokens'], 'completion_tokens': usage['outputTokens'], 'total_tokens': usage['totalTokens']}
    if 'cacheReadInputTokens' in usage:
        normalized['prompt_tokens_details'] = {'cached_tokens': usage['cacheReadInputTokens']}
    return {'id': 'bedrock-observed', 'object': 'chat.completion', 'model': MODEL,
            'choices': [{'index':0, 'finish_reason': 'stop' if decoded['stopReason'] == 'end_turn' else 'length' if decoded['stopReason']=='max_tokens' else decoded['stopReason'],
                         'message': {'role':'assistant','content':content}}], 'usage':normalized}
