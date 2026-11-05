import asyncio
import json
import httpx


class LLMError(Exception):
    pass


async def _post(client, url, headers, body):
    for i in range(3):
        r = await client.post(url, headers=headers, json=body, timeout=120)
        if r.status_code in (429, 500, 502, 503, 529) and i < 2:
            await asyncio.sleep(2 * (i + 1))
            continue
        if r.status_code >= 400:
            raise LLMError(f'{r.status_code}: {r.text[:300]}')
        return r.json()


class AnthropicLLM:
    def __init__(self, key, model, base=''):
        self.key, self.model = key, model
        self.url = (base.rstrip('/') if base else 'https://api.anthropic.com') + '/v1/messages'

    async def run(self, system, history, tools, execute, max_rounds=6):
        msgs = [{'role': m['role'], 'content': m['content']} for m in history]
        hdr = {'x-api-key': self.key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'}
        async with httpx.AsyncClient() as c:
            for _ in range(max_rounds):
                d = await _post(c, self.url, hdr, {'model': self.model, 'max_tokens': 2000, 'system': system, 'messages': msgs, 'tools': tools})
                blocks = d.get('content', [])
                uses = [b for b in blocks if b.get('type') == 'tool_use']
                if d.get('stop_reason') != 'tool_use' or not uses:
                    return ''.join(b.get('text', '') for b in blocks if b.get('type') == 'text').strip()
                msgs.append({'role': 'assistant', 'content': blocks})
                res = []
                for u in uses:
                    r = await execute(u['name'], u.get('input') or {})
                    res.append({'type': 'tool_result', 'tool_use_id': u['id'], 'content': json.dumps(r, ensure_ascii=False)})
                msgs.append({'role': 'user', 'content': res})
        return 'پردازش بیش از حد طول کشید؛ لطفاً دوباره بپرسید.'


class OpenAILLM:
    def __init__(self, key, model, base=''):
        self.key, self.model = key, model
        self.url = (base.rstrip('/') if base else 'https://api.openai.com/v1') + '/chat/completions'

    async def run(self, system, history, tools, execute, max_rounds=6):
        msgs = [{'role': 'system', 'content': system}] + [{'role': m['role'], 'content': m['content']} for m in history]
        fn = [{'type': 'function', 'function': {'name': t['name'], 'description': t['description'], 'parameters': t['input_schema']}} for t in tools]
        hdr = {'Authorization': f'Bearer {self.key}', 'content-type': 'application/json'}
        async with httpx.AsyncClient() as c:
            for _ in range(max_rounds):
                d = await _post(c, self.url, hdr, {'model': self.model, 'messages': msgs, 'tools': fn})
                m = d['choices'][0]['message']
                calls = m.get('tool_calls') or []
                if not calls:
                    return (m.get('content') or '').strip()
                msgs.append(m)
                for tc in calls:
                    try:
                        args = json.loads(tc['function'].get('arguments') or '{}')
                    except json.JSONDecodeError:
                        args = {}
                    r = await execute(tc['function']['name'], args)
                    msgs.append({'role': 'tool', 'tool_call_id': tc['id'], 'content': json.dumps(r, ensure_ascii=False)})
        return 'پردازش بیش از حد طول کشید؛ لطفاً دوباره بپرسید.'


def make(cfg):
    if not cfg.api_key or not cfg.model:
        raise SystemExit('LLM_API_KEY و LLM_MODEL باید تنظیم شوند.')
    return (AnthropicLLM if cfg.provider == 'anthropic' else OpenAILLM)(cfg.api_key, cfg.model, cfg.base_url)
