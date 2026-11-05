"""ماژول‌های داخلی دستیار: CRM، تقویم، پرونده‌های پروژه/خروجی Revit، دفترهای دستی مالی/کارکنان/قراردادها.
همه‌چیز داخل پایگاه دادهٔ خود ایجنت است و به هیچ حساب بیرونی نیاز ندارد."""
import csv
import datetime as dt
import io
import json
import os
import re
import time

from . import policy
from .jalali import fa, fmt_date, fmt_dt

SCHEMA = """
CREATE TABLE IF NOT EXISTS crm(id INTEGER PRIMARY KEY, kind TEXT NOT NULL DEFAULT 'lead' CHECK(kind IN ('contact','lead','deal')),
  name TEXT NOT NULL, phone TEXT DEFAULT '', company TEXT DEFAULT '', source TEXT DEFAULT '', status TEXT DEFAULT 'new',
  value INTEGER DEFAULT 0, project TEXT DEFAULT '', note TEXT DEFAULT '', owner_id INTEGER, created_ts REAL, updated_ts REAL,
  next_followup_ts REAL, followup_sent INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS crm_log(id INTEGER PRIMARY KEY, crm_id INTEGER, ts REAL, actor TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, title TEXT NOT NULL, start_ts REAL NOT NULL, end_ts REAL, remind_ts REAL,
  creator_id INTEGER, note TEXT DEFAULT '', status TEXT DEFAULT 'active' CHECK(status IN ('active','cancelled')), reminded INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY, ts REAL, uploader TEXT, name TEXT, path TEXT, mime TEXT, size INTEGER,
  project TEXT DEFAULT '', note TEXT DEFAULT '', kind TEXT DEFAULT 'file');
CREATE TABLE IF NOT EXISTS fin(id INTEGER PRIMARY KEY, ts REAL, day_ts REAL, kind TEXT NOT NULL CHECK(kind IN ('income','expense','receivable','debt')),
  amount INTEGER NOT NULL, party TEXT DEFAULT '', project TEXT DEFAULT '', note TEXT DEFAULT '', actor TEXT, status TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS hr(id INTEGER PRIMARY KEY, ts REAL, staff_name TEXT, kind TEXT, text TEXT, actor TEXT);
CREATE TABLE IF NOT EXISTS contracts(id INTEGER PRIMARY KEY, title TEXT NOT NULL, party TEXT DEFAULT '', value INTEGER DEFAULT 0,
  start_ts REAL, end_ts REAL, status TEXT DEFAULT 'active', project TEXT DEFAULT '', note TEXT DEFAULT '', ts REAL, updated_ts REAL);
CREATE TRIGGER IF NOT EXISTS crm_no_delete BEFORE DELETE ON crm BEGIN SELECT RAISE(ABORT,'records are never deleted'); END;
CREATE TRIGGER IF NOT EXISTS fin_no_delete BEFORE DELETE ON fin BEGIN SELECT RAISE(ABORT,'records are never deleted'); END;
CREATE TRIGGER IF NOT EXISTS hr_no_delete BEFORE DELETE ON hr BEGIN SELECT RAISE(ABORT,'records are never deleted'); END;
CREATE TRIGGER IF NOT EXISTS ct_no_delete BEFORE DELETE ON contracts BEGIN SELECT RAISE(ABORT,'records are never deleted'); END;
CREATE TRIGGER IF NOT EXISTS files_no_delete BEFORE DELETE ON files BEGIN SELECT RAISE(ABORT,'records are never deleted'); END;
"""

# سطح لازم برای نوشتن در هر ماژول داخلی (خواندن همیشه ≥۱)
WRITE_LEVEL = {'crm': 2, 'calendar': 2, 'project_files': 1, 'revit_pm': 1, 'finance_data': 1, 'hr_data': 1, 'contracts_docs': 1}
INTERNAL = {'crm': 2, 'calendar': 3, 'project_files': 1, 'revit_pm': 1, 'finance_data': 1, 'hr_data': 2, 'contracts_docs': 1}
MAX_FILE = 20 * 1024 * 1024
KIND_FA = {'income': 'درآمد', 'expense': 'هزینه', 'receivable': 'مطالبه (طلب از دیگران)', 'debt': 'بدهی (به دیگران)'}


def toman(n):
    return fa(f'{int(n):,}').replace(',', '٬') + ' تومان'


def parse_when(s, tz):
    for f in ('%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            d = dt.datetime.strptime(s.strip(), f).replace(tzinfo=tz)
            return (d.replace(hour=9) if f == '%Y-%m-%d' else d).timestamp()
        except ValueError:
            pass
    raise ValueError('قالب تاریخ نادرست؛ YYYY-MM-DD یا YYYY-MM-DD HH:MM (میلادی، وقت محلی)')


def _safe(name):
    return re.sub(r'[^\w.\-]+', '_', name, flags=re.U)[:80] or 'file'


def register_file(db, files_dir, uploader, name, mime, data, caption=''):
    """ذخیرهٔ فایل دریافتی (خروجی Revit، نقشه، قرارداد…) و ثبت در فهرست؛ بدون نیاز به هیچ حساب بیرونی."""
    if len(data) > MAX_FILE:
        raise ValueError('حجم فایل بیش از ۲۰ مگابایت است (سقف تلگرام برای ربات).')
    os.makedirs(files_dir, exist_ok=True)
    path = os.path.join(files_dir, f'{int(time.time())}_{_safe(name)}')
    with open(path, 'wb') as f:
        f.write(data)
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    kind = 'revit_export' if ext in ('csv', 'xlsx', 'xls', 'ifc', 'rvt', 'txt') and re.search(r'revit|schedule|quantity|متره|برآورد', name + caption, re.I) else 'file'
    fid = db.x('INSERT INTO files(ts,uploader,name,path,mime,size,project,note,kind) VALUES(?,?,?,?,?,?,?,?,?)',
               (time.time(), uploader, name, path, mime or '', len(data), '', caption, kind))
    db.audit(uploader, 'file.add', f'{fid} {name} {len(data)}B')
    return fid


def _decode(raw):
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return raw.decode('utf-16', 'replace')
    for enc in ('utf-8-sig', 'cp1256'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode('utf-8', 'replace')


def _num(s):
    s = str(s).strip().replace('٬', '').replace(',', '').translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٫', '0123456789.'))
    s = re.sub(r'[^\d.\-]', '', s) if re.fullmatch(r'-?[\d.,]+\s*[^\d\s]{0,6}', s) else s
    try:
        return float(s)
    except ValueError:
        return None


def table_summary(rows, max_rows=15):
    """خلاصهٔ جدول (مثلاً جدول متره/کمیت Revit): سرستون‌ها، تعداد ردیف، جمع ستون‌های عددی، نمونه ردیف‌ها."""
    rows = [r for r in rows if any(str(c).strip() for c in r)]
    if not rows:
        return {'rows': 0}
    head, body = [str(c).strip() for c in rows[0]], rows[1:]
    sums = {}
    for i, h in enumerate(head):
        vals = [_num(r[i]) for r in body if i < len(r) and str(r[i]).strip()]
        if vals and all(v is not None for v in vals) and len(vals) >= max(1, len(body) // 2):
            sums[h or f'col{i + 1}'] = round(sum(vals), 3)
    return {'columns': head, 'row_count': len(body), 'numeric_column_sums': sums, 'sample_rows': [list(map(str, r)) for r in body[:max_rows]]}


def read_file(path, name):
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    with open(path, 'rb') as f:
        raw = f.read()
    if ext in ('csv', 'tsv') or (ext == 'txt' and raw.count(b'\t') > 5):
        text = _decode(raw)
        try:
            dialect = csv.Sniffer().sniff(text[:4000], delimiters=',;\t|')
        except csv.Error:
            dialect = csv.excel
        return {'type': 'table', **table_summary(list(csv.reader(io.StringIO(text), dialect)))}
    if ext in ('txt', 'md', 'json', 'xml', 'log'):
        return {'type': 'text', 'text': _decode(raw)[:6000], 'truncated': len(raw) > 6000}
    if ext in ('xlsx', 'xlsm'):
        try:
            import openpyxl
        except ImportError:
            return {'type': 'unsupported', 'note': 'کتابخانهٔ openpyxl نصب نیست (pip install openpyxl). یا فایل را CSV بفرستید.'}
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        return {'type': 'workbook', 'sheets': {ws.title: table_summary([list(r) for r in ws.iter_rows(values_only=True)][:2000], 10) for ws in wb.worksheets[:5]}}
    if ext == 'pdf':
        try:
            from pypdf import PdfReader
            rd = PdfReader(io.BytesIO(raw))
            txt = '\n'.join((p.extract_text() or '') for p in rd.pages[:8])
            return {'type': 'pdf', 'pages': len(rd.pages), 'text': txt[:6000]}
        except Exception:
            return {'type': 'unsupported', 'note': 'متن PDF خوانده نشد (اسکن‌شده یا pypdf نصب نیست).'}
    return {'type': 'unsupported', 'note': f'قالب «{ext or "؟"}» را نمی‌توان خواند (نقشه/مدل سه‌بعدی مثل rvt، dwg، ifc فقط نگهداری می‌شود). از Revit جدول کمیت/متره را به CSV یا Excel خروجی بگیرید و بفرستید.'}


class InternalMixin:
    """ابزارهای داخلی؛ با کلاس ToolBox ترکیب می‌شود."""

    def _gate(self, system, write=False):
        name, _, owner_only, _ = policy.SYSTEMS[system]
        if owner_only and self.who['role'] != 'owner':
            return {'ok': False, 'error': 'این بخش فقط برای مالک است.'}
        need = WRITE_LEVEL[system] if write else 1
        lvl = self.db.level(system)
        if system == 'project_files':
            lvl = max(lvl, self.db.level('revit_pm'))
        if lvl < need:
            return {'ok': False, 'error': f'«{name}» در دفتر فعال‌سازی روی سطح {lvl} است و برای این کار سطح {need} لازم است. مالک باید با /activate_internal یا /activate {system} {need} فعالش کند.'}
        return None

    # ---------- CRM
    async def t_crm_upsert(self, name, kind='lead', crm_id=0, phone='', company='', source='', status='', value=0, project='', note='', next_followup=''):
        if (e := self._gate('crm', True)):
            return e
        if kind not in ('contact', 'lead', 'deal'):
            return {'ok': False, 'error': 'kind باید contact|lead|deal باشد.'}
        fu = self._due(next_followup) if next_followup else None
        t = time.time()
        if crm_id:
            cur = self.db.one('SELECT * FROM crm WHERE id=?', (crm_id,))
            if not cur:
                return {'ok': False, 'error': 'رکورد پیدا نشد.'}
            self.db.x('UPDATE crm SET kind=?,name=?,phone=?,company=?,source=?,status=?,value=?,project=?,note=?,updated_ts=?,next_followup_ts=COALESCE(?,next_followup_ts),followup_sent=CASE WHEN ? IS NULL THEN followup_sent ELSE 0 END WHERE id=?',
                      (kind or cur['kind'], name or cur['name'], phone or cur['phone'], company or cur['company'], source or cur['source'], status or cur['status'],
                       value or cur['value'], project or cur['project'], note or cur['note'], t, fu, fu, crm_id))
            self.db.x('INSERT INTO crm_log(crm_id,ts,actor,text) VALUES(?,?,?,?)', (crm_id, t, self.who['name'], f'ویرایش: {status or ""} {note or ""}'.strip()))
            return {'ok': True, 'crm_id': crm_id, 'updated': True}
        dup = self.db.one('SELECT id FROM crm WHERE name=? AND phone=? AND kind=?', (name, phone, kind))
        if dup:
            return {'ok': False, 'error': f'رکورد مشابه با شناسهٔ {dup["id"]} از قبل هست؛ برای ویرایش crm_id بدهید.'}
        i = self.db.x('INSERT INTO crm(kind,name,phone,company,source,status,value,project,note,owner_id,created_ts,updated_ts,next_followup_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                      (kind, name, phone, company, source, status or 'new', int(value or 0), project, note, self.who['id'], t, t, fu))
        self.db.x('INSERT INTO crm_log(crm_id,ts,actor,text) VALUES(?,?,?,?)', (i, t, self.who['name'], 'ایجاد'))
        return {'ok': True, 'crm_id': i}

    async def t_crm_log(self, crm_id, text, next_followup='', status=''):
        if (e := self._gate('crm', True)):
            return e
        if not self.db.one('SELECT id FROM crm WHERE id=?', (crm_id,)):
            return {'ok': False, 'error': 'رکورد پیدا نشد.'}
        t = time.time()
        self.db.x('INSERT INTO crm_log(crm_id,ts,actor,text) VALUES(?,?,?,?)', (crm_id, t, self.who['name'], text))
        sets, a = ['updated_ts=?'], [t]
        if status:
            sets.append('status=?'); a.append(status)
        if next_followup:
            sets += ['next_followup_ts=?', 'followup_sent=0']; a.append(self._due(next_followup))
        self.db.x(f'UPDATE crm SET {",".join(sets)} WHERE id=?', (*a, crm_id))
        return {'ok': True}

    def _fmt_crm(self, r):
        fu = fmt_dt(dt.datetime.fromtimestamp(r['next_followup_ts'], self.tz)) if r['next_followup_ts'] else '—'
        return {'id': r['id'], 'kind': r['kind'], 'name': r['name'], 'phone': r['phone'], 'company': r['company'], 'status': r['status'],
                'value': r['value'], 'project': r['project'], 'next_followup': fu, 'note': r['note']}

    async def t_crm_list(self, query='', kind='', status='', followup_due=False, with_log_id=0):
        if (e := self._gate('crm')):
            return e
        if with_log_id:
            r = self.db.one('SELECT * FROM crm WHERE id=?', (with_log_id,))
            if not r:
                return {'ok': False, 'error': 'رکورد پیدا نشد.'}
            log = [{'when': fmt_dt(dt.datetime.fromtimestamp(x['ts'], self.tz)), 'by': x['actor'], 'text': x['text']} for x in self.db.q('SELECT * FROM crm_log WHERE crm_id=? ORDER BY id DESC LIMIT 30', (with_log_id,))]
            return {'ok': True, 'record': self._fmt_crm(r), 'history': log}
        w, a = ['1=1'], []
        if query:
            w.append('(name LIKE ? OR company LIKE ? OR phone LIKE ? OR note LIKE ?)'); a += [f'%{query}%'] * 4
        if kind:
            w.append('kind=?'); a.append(kind)
        if status:
            w.append('status=?'); a.append(status)
        if followup_due:
            w.append('next_followup_ts IS NOT NULL AND next_followup_ts<?'); a.append(time.time() + 86400)
        rows = self.db.q(f'SELECT * FROM crm WHERE {" AND ".join(w)} ORDER BY COALESCE(next_followup_ts,9e12), id DESC LIMIT 50', a)
        pipe = self.db.q("SELECT kind,status,COUNT(*) n,SUM(value) v FROM crm GROUP BY kind,status")
        return {'ok': True, 'records': [self._fmt_crm(r) for r in rows], 'pipeline': pipe}

    # ---------- تقویم
    async def t_event_add(self, title, start, end='', remind_minutes=30, note=''):
        if (e := self._gate('calendar', True)):
            return e
        s = parse_when(start, self.tz)
        en = parse_when(end, self.tz) if end else None
        rem = s - int(remind_minutes) * 60 if remind_minutes else None
        i = self.db.x('INSERT INTO events(title,start_ts,end_ts,remind_ts,creator_id,note) VALUES(?,?,?,?,?,?)', (title, s, en, rem, self.who['id'], note))
        clash = [x['title'] for x in self.db.q("SELECT title FROM events WHERE status='active' AND id!=? AND start_ts BETWEEN ? AND ?", (i, s - 3600, s + 3600))]
        return {'ok': True, 'event_id': i, 'overlaps_with': clash}

    async def t_event_list(self, days=7):
        if (e := self._gate('calendar')):
            return e
        t = time.time()
        rows = self.db.q("SELECT * FROM events WHERE status='active' AND start_ts BETWEEN ? AND ? ORDER BY start_ts", (t - 3600, t + int(days) * 86400))
        return {'ok': True, 'events': [{'id': r['id'], 'title': r['title'], 'start': fmt_dt(dt.datetime.fromtimestamp(r['start_ts'], self.tz)), 'note': r['note']} for r in rows]}

    async def t_event_cancel(self, event_id):
        if (e := self._gate('calendar', True)):
            return e
        self.db.x("UPDATE events SET status='cancelled' WHERE id=?", (event_id,))
        return {'ok': True}

    # ---------- پرونده‌ها و خروجی Revit
    async def t_file_list(self, query='', project='', kind=''):
        if (e := self._gate('project_files')):
            return e
        w, a = ['1=1'], []
        if query:
            w.append('(name LIKE ? OR note LIKE ? OR project LIKE ?)'); a += [f'%{query}%'] * 3
        if project:
            w.append('project LIKE ?'); a.append(f'%{project}%')
        if kind:
            w.append('kind=?'); a.append(kind)
        rows = self.db.q(f'SELECT id,ts,uploader,name,size,project,note,kind FROM files WHERE {" AND ".join(w)} ORDER BY id DESC LIMIT 40', a)
        for r in rows:
            r['uploaded'] = fmt_date(dt.datetime.fromtimestamp(r.pop('ts'), self.tz))
        return {'ok': True, 'files': rows}

    async def t_file_read(self, file_id):
        if (e := self._gate('project_files')):
            return e
        r = self.db.one('SELECT * FROM files WHERE id=?', (file_id,))
        if not r or not os.path.exists(r['path']):
            return {'ok': False, 'error': 'فایل پیدا نشد.'}
        try:
            return {'ok': True, 'name': r['name'], 'project': r['project'], **read_file(r['path'], r['name'])}
        except Exception as ex:
            return {'ok': False, 'error': f'خواندن فایل ناموفق: {str(ex)[:120]}'}

    async def t_file_tag(self, file_id, project='', note='', kind=''):
        if (e := self._gate('project_files', True)):
            return e
        r = self.db.one('SELECT * FROM files WHERE id=?', (file_id,))
        if not r:
            return {'ok': False, 'error': 'فایل پیدا نشد.'}
        self.db.x('UPDATE files SET project=?,note=?,kind=? WHERE id=?', (project or r['project'], note or r['note'], kind or r['kind'], file_id))
        return {'ok': True}

    # ---------- دفتر مالی دستی (فقط مالک)
    async def t_fin_add(self, kind, amount, party='', project='', note='', date=''):
        if (e := self._gate('finance_data', True)):
            return e
        if kind not in KIND_FA:
            return {'ok': False, 'error': 'kind: income|expense|receivable|debt'}
        amount = int(amount)
        if amount <= 0:
            return {'ok': False, 'error': 'مبلغ باید مثبت و به تومان باشد.'}
        day = parse_when(date, self.tz) if date else self.now().timestamp()
        i = self.db.x('INSERT INTO fin(ts,day_ts,kind,amount,party,project,note,actor) VALUES(?,?,?,?,?,?,?,?)', (time.time(), day, kind, amount, party, project, note, self.who['name']))
        return {'ok': True, 'fin_id': i, 'note': 'این رکورد فقط ثبت دفتری است؛ هیچ پرداخت یا انتقال وجهی انجام نمی‌شود.'}

    async def t_fin_summary(self, days=30, project=''):
        if (e := self._gate('finance_data')):
            return e
        return {'ok': True, **fin_summary(self.db, int(days), project)}

    async def t_fin_settle(self, fin_id):
        if (e := self._gate('finance_data', True)):
            return e
        self.db.x("UPDATE fin SET status='settled' WHERE id=? AND kind IN ('receivable','debt')", (fin_id,))
        return {'ok': True}

    # ---------- کارکنان (فقط مالک)
    async def t_hr_note(self, staff_name, kind, text):
        if (e := self._gate('hr_data', True)):
            return e
        self.db.x('INSERT INTO hr(ts,staff_name,kind,text,actor) VALUES(?,?,?,?,?)', (time.time(), staff_name, kind, text, self.who['name']))
        return {'ok': True, 'note': 'ثبت دفتری است؛ تصمیم استخدام/اخراج/حقوق فقط با تأیید مالک و خارج از دستیار.'}

    async def t_hr_list(self, staff_name=''):
        if (e := self._gate('hr_data')):
            return e
        rows = self.db.q('SELECT staff_name,kind,text,ts FROM hr WHERE (?=\'\' OR staff_name LIKE ?) ORDER BY id DESC LIMIT 40', (staff_name, f'%{staff_name}%'))
        done = {r['assignee_id']: r for r in self.db.q("SELECT assignee_id, SUM(status='done') d, COUNT(*) n FROM tasks WHERE assignee_id IS NOT NULL GROUP BY assignee_id")}
        perf = [{'name': s['name'], 'tasks_total': done.get(s['id'], {}).get('n', 0), 'tasks_done': done.get(s['id'], {}).get('d', 0)} for s in self.db.q('SELECT * FROM staff WHERE active=1')]
        for r in rows:
            r['when'] = fmt_date(dt.datetime.fromtimestamp(r.pop('ts'), self.tz))
        return {'ok': True, 'notes': rows, 'task_stats': perf}

    # ---------- قراردادها (فقط مالک)
    async def t_contract_upsert(self, title, contract_id=0, party='', value=0, start='', end='', status='', project='', note=''):
        if (e := self._gate('contracts_docs', True)):
            return e
        s = parse_when(start, self.tz) if start else None
        en = parse_when(end, self.tz) if end else None
        t = time.time()
        if contract_id:
            c = self.db.one('SELECT * FROM contracts WHERE id=?', (contract_id,))
            if not c:
                return {'ok': False, 'error': 'قرارداد پیدا نشد.'}
            self.db.x('UPDATE contracts SET title=?,party=?,value=?,start_ts=?,end_ts=?,status=?,project=?,note=?,updated_ts=? WHERE id=?',
                      (title or c['title'], party or c['party'], value or c['value'], s or c['start_ts'], en or c['end_ts'], status or c['status'], project or c['project'], note or c['note'], t, contract_id))
            return {'ok': True, 'contract_id': contract_id}
        i = self.db.x('INSERT INTO contracts(title,party,value,start_ts,end_ts,status,project,note,ts,updated_ts) VALUES(?,?,?,?,?,?,?,?,?,?)', (title, party, int(value or 0), s, en, status or 'active', project, note, t, t))
        return {'ok': True, 'contract_id': i, 'note': 'ثبت دفتری است؛ دستیار قرارداد را امضا یا نهایی نمی‌کند.'}

    async def t_contract_list(self, expiring_days=0, query=''):
        if (e := self._gate('contracts_docs')):
            return e
        w, a = ['1=1'], []
        if expiring_days:
            w.append("status='active' AND end_ts IS NOT NULL AND end_ts<?"); a.append(time.time() + int(expiring_days) * 86400)
        if query:
            w.append('(title LIKE ? OR party LIKE ? OR project LIKE ?)'); a += [f'%{query}%'] * 3
        rows = self.db.q(f'SELECT * FROM contracts WHERE {" AND ".join(w)} ORDER BY COALESCE(end_ts,9e12) LIMIT 40', a)
        f = lambda x: fmt_date(dt.datetime.fromtimestamp(x, self.tz)) if x else '—'
        return {'ok': True, 'contracts': [{'id': r['id'], 'title': r['title'], 'party': r['party'], 'value': r['value'], 'start': f(r['start_ts']), 'end': f(r['end_ts']), 'status': r['status'], 'project': r['project']} for r in rows]}


def fin_summary(db, days=30, project=''):
    t0 = time.time() - days * 86400
    w, a = ['day_ts>=?'], [t0]
    if project:
        w.append('project LIKE ?'); a.append(f'%{project}%')
    rows = db.q(f'SELECT kind,SUM(amount) s,COUNT(*) n FROM fin WHERE {" AND ".join(w)} GROUP BY kind', a)
    s = {r['kind']: r['s'] for r in rows}
    inc, exp = s.get('income', 0), s.get('expense', 0)
    open_ = db.q("SELECT kind,SUM(amount) s FROM fin WHERE kind IN ('receivable','debt') AND status='open' GROUP BY kind")
    o = {r['kind']: r['s'] for r in open_}
    by_proj = db.q(f"SELECT project, SUM(CASE WHEN kind='income' THEN amount WHEN kind='expense' THEN -amount ELSE 0 END) net FROM fin WHERE {' AND '.join(w)} AND project!='' GROUP BY project ORDER BY net DESC", a)
    return {'period_days': days, 'income': inc, 'expense': exp, 'net': inc - exp, 'open_receivable': o.get('receivable', 0), 'open_debt': o.get('debt', 0),
            'by_project': by_proj, 'entries': sum(r['n'] for r in rows), 'source': 'دفتر دستی ثبت‌شده توسط مالک (نه حسابداری رسمی)'}


def due_notifications(db, tz, now_ts=None):
    """یادآوری‌های سررسیده → فهرست (telegram_id, متن). هر مورد فقط یک بار ارسال می‌شود."""
    t = now_ts or time.time()
    out = []
    for e in db.q("SELECT * FROM events WHERE status='active' AND reminded=0 AND remind_ts IS NOT NULL AND remind_ts<=? AND start_ts>?", (t, t - 3600)):
        s = db.one('SELECT telegram_id FROM staff WHERE id=?', (e['creator_id'],))
        if s and s['telegram_id']:
            out.append((s['telegram_id'], f'یادآوری رویداد: «{e["title"]}» — {fmt_dt(dt.datetime.fromtimestamp(e["start_ts"], tz))}' + (f'\n{e["note"]}' if e['note'] else '')))
        db.x('UPDATE events SET reminded=1 WHERE id=?', (e['id'],))
    for c in db.q('SELECT * FROM crm WHERE next_followup_ts IS NOT NULL AND followup_sent=0 AND next_followup_ts<=?', (t,)):
        s = db.one('SELECT telegram_id FROM staff WHERE id=?', (c['owner_id'],)) or db.one("SELECT telegram_id FROM staff WHERE role='owner'")
        if s and s['telegram_id']:
            out.append((s['telegram_id'], f'زمان پیگیری: {c["name"]} ({c["kind"]}، وضعیت {c["status"]}) — شناسه {fa(c["id"])}' + (f'\nیادداشت: {c["note"]}' if c['note'] else '')))
        db.x('UPDATE crm SET followup_sent=1 WHERE id=?', (c['id'],))
    return out
