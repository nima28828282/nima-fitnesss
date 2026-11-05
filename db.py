import json
import os
import sqlite3
import time
from . import policy
from . import internal

SCHEMA = """
CREATE TABLE IF NOT EXISTS staff(id INTEGER PRIMARY KEY, name TEXT NOT NULL, telegram_id INTEGER UNIQUE,
  role TEXT NOT NULL CHECK(role IN ('owner','manager','staff')), lang TEXT DEFAULT 'fa', note TEXT DEFAULT '', active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS projects(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, status TEXT NOT NULL DEFAULT 'active'
  CHECK(status IN ('active','paused','waiting','done')), priority INTEGER DEFAULT 3, next_step TEXT DEFAULT '', updated_ts REAL);
CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY, title TEXT NOT NULL, assignee_id INTEGER, creator TEXT, project TEXT DEFAULT '',
  due_ts REAL, criteria TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','doing','blocked','done','cancelled')),
  created_ts REAL, updated_ts REAL, remind_count INTEGER DEFAULT 0, last_remind_ts REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS task_events(id INTEGER PRIMARY KEY, task_id INTEGER, ts REAL, actor TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY, ts REAL, title TEXT, options TEXT, choice TEXT, reason TEXT, responsible TEXT);
CREATE TABLE IF NOT EXISTS approvals(id INTEGER PRIMARY KEY, ts REAL, kind TEXT, summary TEXT, details TEXT, requester_id INTEGER,
  status TEXT DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')), decided_ts REAL);
CREATE TABLE IF NOT EXISTS ledger(system TEXT PRIMARY KEY, level INTEGER NOT NULL DEFAULT 0, limits TEXT DEFAULT '', approved_by TEXT DEFAULT '', approved_ts REAL, note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, ts REAL, actor TEXT, event TEXT, detail TEXT);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT,'audit is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT,'audit is append-only'); END;
CREATE TRIGGER IF NOT EXISTS dec_no_delete BEFORE DELETE ON decisions BEGIN SELECT RAISE(ABORT,'records are never deleted'); END;
CREATE TRIGGER IF NOT EXISTS task_no_delete BEFORE DELETE ON tasks BEGIN SELECT RAISE(ABORT,'records are never deleted'); END;
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, chat_id INTEGER, ts REAL, role TEXT, content TEXT);
CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY, ts REAL, author TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
"""


class DB:
    def __init__(self, path: str):
        if path != ':memory:':
            os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        self.c = sqlite3.connect(path)
        self.c.row_factory = sqlite3.Row
        self.c.executescript(SCHEMA)
        self.c.executescript(internal.SCHEMA)
        for k in policy.SYSTEMS:
            self.c.execute('INSERT OR IGNORE INTO ledger(system,level) VALUES(?,0)', (k,))
        self.c.commit()

    # --- عمومی
    def q(self, sql, args=()):
        return [dict(r) for r in self.c.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        r = self.c.execute(sql, args).fetchone()
        return dict(r) if r else None

    def x(self, sql, args=()):
        cur = self.c.execute(sql, args)
        self.c.commit()
        return cur.lastrowid

    def audit(self, actor, event, detail=''):
        self.x('INSERT INTO audit(ts,actor,event,detail) VALUES(?,?,?,?)', (time.time(), str(actor), event, str(detail)[:2000]))

    # --- تنظیمات
    def get(self, key, default=None):
        r = self.one('SELECT value FROM settings WHERE key=?', (key,))
        return r['value'] if r else default

    def set(self, key, value):
        self.x('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(value)))

    @property
    def paused(self) -> bool:
        return self.get('paused', '0') == '1'

    # --- کارکنان
    def staff_by_tg(self, tg):
        return self.one('SELECT * FROM staff WHERE telegram_id=? AND active=1', (tg,))

    def staff_by_name(self, name):
        return self.one('SELECT * FROM staff WHERE name=? AND active=1', (name,)) or self.one('SELECT * FROM staff WHERE name LIKE ? AND active=1', (f'%{name}%',))

    # --- دفتر فعال‌سازی
    def ledger(self):
        return self.q('SELECT * FROM ledger ORDER BY system')

    def level(self, system) -> int:
        r = self.one('SELECT level FROM ledger WHERE system=?', (system,))
        return 0 if self.paused or not r else int(r['level'])

    def activate(self, system, level, by, limits='', note=''):
        if system not in policy.SYSTEMS:
            raise ValueError('سامانهٔ نامعتبر')
        cap = policy.SYSTEMS[system][1]
        if level < 0 or level > cap:
            raise ValueError(f'سقف مجاز این سامانه طبق سند، سطح {cap} است')
        self.x('UPDATE ledger SET level=?,limits=?,approved_by=?,approved_ts=?,note=? WHERE system=?', (level, limits, by, time.time(), note, system))
        self.audit(by, 'ledger.set', f'{system}={level} limits={limits}')

    def kill_all(self, by):
        self.c.execute('UPDATE ledger SET level=0, approved_by=?, approved_ts=?', (str(by), time.time()))
        self.c.commit()
        self.set('paused', '1')
        self.audit(by, 'killswitch', 'all access revoked, agent paused')

    def resume(self, by):
        self.set('paused', '0')
        self.audit(by, 'resume', 'agent resumed (ledger remains at 0 until re-activated)')

    # --- پروژه‌ها
    def active_projects(self):
        return self.q("SELECT * FROM projects WHERE status='active' ORDER BY priority,name")

    # --- پیام‌ها
    def add_msg(self, chat, role, content):
        self.x('INSERT INTO messages(chat_id,ts,role,content) VALUES(?,?,?,?)', (chat, time.time(), role, content))

    def history(self, chat, n=16):
        rows = self.q('SELECT role,content FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT ?', (chat, n))
        return list(reversed(rows))
