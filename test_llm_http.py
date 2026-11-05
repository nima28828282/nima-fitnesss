import asyncio, json, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from app import llm, config, db as dbm, bot, core

CALLS = []

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['content-length'])))
        CALLS.append((self.path, self.headers, body))
        if self.path == '/v1/messages':
            tool_done = any(isinstance(m['content'], list) and m['content'] and m['content'][0].get('type') == 'tool_result' for m in body['messages'])
            out = {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': 'پایان'}]} if tool_done else \
                  {'stop_reason': 'tool_use', 'content': [{'type': 'text', 'text': '...'}, {'type': 'tool_use', 'id': 't1', 'name': 'note_add', 'input': {'text': 'سلام'}}]}
        else:
            tool_done = any(m['role'] == 'tool' for m in body['messages'])
            out = {'choices': [{'message': {'role': 'assistant', 'content': 'پایان'}}]} if tool_done else \
                  {'choices': [{'message': {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'c1', 'type': 'function', 'function': {'name': 'note_add', 'arguments': '{"text":"سلام"}'}}]}}]}
        b = json.dumps(out).encode(); self.send_response(200); self.send_header('content-type', 'application/json'); self.send_header('content-length', str(len(b))); self.end_headers(); self.wfile.write(b)

def serve():
    s = HTTPServer(('127.0.0.1', 0), H); threading.Thread(target=s.serve_forever, daemon=True).start(); return s, f'http://127.0.0.1:{s.server_port}'

async def _go(L):
    seen = []
    async def ex(n, a): seen.append((n, a)); return {'ok': True}
    out = await L.run('SYS', [{'role': 'user', 'content': 'hi'}], [{'name': 'note_add', 'description': 'd', 'input_schema': {'type': 'object', 'properties': {}}}], ex)
    return out, seen

def test_anthropic_loop():
    s, base = serve(); CALLS.clear()
    out, seen = asyncio.run(_go(llm.AnthropicLLM('K', 'm', base)))
    assert out == 'پایان' and seen == [('note_add', {'text': 'سلام'})]
    assert CALLS[0][1]['x-api-key'] == 'K' and CALLS[0][2]['system'] == 'SYS' and CALLS[0][2]['tools'][0]['name'] == 'note_add'

def test_openai_loop():
    s, base = serve(); CALLS.clear()
    out, seen = asyncio.run(_go(llm.OpenAILLM('K', 'm', base + '/v1')))
    assert out == 'پایان' and seen == [('note_add', {'text': 'سلام'})]
    assert CALLS[0][2]['messages'][0] == {'role': 'system', 'content': 'SYS'} and CALLS[0][2]['tools'][0]['function']['name'] == 'note_add'

def test_build_bot_offline():
    cfg = config.Config(telegram_token='123456:ABC-DEF', owner_id=1, db_path=':memory:')
    db = dbm.DB(':memory:'); a = core.Agent(db, cfg, None, None, 'x')
    app = bot.build_app(cfg, a, db)
    names = {j.name for j in app.job_queue.jobs()}
    assert {'job_daily', 'job_weekly', 'job_followup'} <= names
