import logging
import os

from . import config, db as dbm, llm as llmm, core, bot


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    logging.getLogger('httpx').setLevel(logging.WARNING)      # توکن در URL لاگ نشود
    cfg = config.load()
    if not cfg.telegram_token or not cfg.owner_id:
        raise SystemExit('TELEGRAM_TOKEN و OWNER_TELEGRAM_ID را تنظیم کنید.')
    db = dbm.DB(cfg.db_path)
    if not db.one("SELECT 1 FROM staff WHERE role='owner'"):
        db.x("INSERT INTO staff(name,telegram_id,role) VALUES('مالک',?, 'owner')", (cfg.owner_id,))
        db.activate('telegram', 3, 'install', note='فعال با راه‌اندازی توسط مالک')
    prompt = open(cfg.prompt_path, encoding='utf-8').read()
    agent = core.Agent(db, cfg, llmm.make(cfg), None, prompt)
    app = bot.build_app(cfg, agent, db)
    db.audit('system', 'start', 'agent started')
    app.run_polling(allowed_updates=['message', 'callback_query'])


if __name__ == '__main__':
    main()
