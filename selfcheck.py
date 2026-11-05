"""بررسی سریع قبل از اجرا:  python selfcheck.py   (از روی همان سرور/مسیر .env)"""
import asyncio, os, sys
import httpx
from app import config
try:
    from dotenv import load_dotenv; load_dotenv()
except Exception:
    for l in open('.env', encoding='utf-8') if os.path.exists('.env') else []:
        l = l.split('#')[0].strip()
        if '=' in l: k, v = l.split('=', 1); os.environ.setdefault(k.strip(), v.strip())
cfg = config.load(); ok = True
def res(name, good, msg=''):
    global ok; ok &= good; print(('✅' if good else '❌'), name, msg)
res('توکن تلگرام تنظیم شده', bool(cfg.telegram_token) and ':' in cfg.telegram_token)
res('شناسهٔ عددی مالک تنظیم شده', cfg.owner_id > 0, '(از @userinfobot بگیرید)' if cfg.owner_id <= 0 else '')
res('کلید مدل زبانی تنظیم شده', bool(cfg.api_key) and 'PUT_' not in cfg.api_key)
async def go():
    kw = {'proxy': cfg.telegram_proxy} if cfg.telegram_proxy else {}
    async with httpx.AsyncClient(timeout=20, **kw) as c:
        try:
            r = (await c.get(f'https://api.telegram.org/bot{cfg.telegram_token}/getMe')).json()
            res('اتصال به تلگرام و اعتبار توکن', r.get('ok', False), '@' + r['result']['username'] if r.get('ok') else str(r.get('description')))
        except Exception as e: res('اتصال به تلگرام', False, type(e).__name__ + ' — سرور باید به api.telegram.org دسترسی داشته باشد (یا TELEGRAM_PROXY)')
    from app import llm
    try:
        out = await llm.make(cfg).run('Reply with the word OK.', [{'role': 'user', 'content': 'ping'}], [{'name': 'noop', 'description': 'unused', 'input_schema': {'type': 'object', 'properties': {}}}], None)
        res('اتصال به مدل زبانی', True, str(out)[:60])
    except Exception as e: res('اتصال به مدل زبانی', False, type(e).__name__ + ': ' + str(e)[:100])
asyncio.run(go()); sys.exit(0 if ok else 1)
