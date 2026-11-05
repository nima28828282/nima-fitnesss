import datetime as dt
import json
import time
from zoneinfo import ZoneInfo

from . import policy
from .internal import InternalMixin
from .jalali import fmt_date, fmt_dt, fa

ALL_TOOLS = ['task_create', 'task_update', 'task_list', 'project_upsert', 'project_list', 'decision_log', 'note_add',
             'staff_list', 'request_approval', 'ledger_status', 'connector_call', 'send_message',
             'crm_upsert', 'crm_log', 'crm_list', 'event_add', 'event_list', 'event_cancel', 'file_list', 'file_read', 'file_tag',
             'fin_add', 'fin_summary', 'fin_settle', 'hr_note', 'hr_list', 'contract_upsert', 'contract_list']
_CRM = {'crm_upsert', 'crm_log', 'crm_list', 'event_add', 'event_list', 'event_cancel', 'file_list', 'file_read', 'file_tag'}
ROLE_TOOLS = {
    'owner': set(ALL_TOOLS),
    'manager': {'task_create', 'task_update', 'task_list', 'project_list', 'note_add', 'staff_list', 'request_approval', 'send_message'} | _CRM,
    'staff': {'task_update', 'task_list', 'note_add', 'event_list', 'file_list'},
}

SCHEMAS = {
    'task_create': ('ایجاد وظیفه با مسئول، مهلت و معیار تکمیل و ابلاغ آن به مسئول (در تلگرام).', {
        'title': {'type': 'string'}, 'assignee_name': {'type': 'string', 'description': 'نام مسئول ثبت‌شده در فهرست کارکنان'},
        'due': {'type': 'string', 'description': 'مهلت به شکل YYYY-MM-DD یا YYYY-MM-DD HH:MM (میلادی، به وقت محلی)'},
        'criteria': {'type': 'string', 'description': 'معیار تکمیل'}, 'project': {'type': 'string'}}, ['title']),
    'task_update': ('تغییر وضعیت/مهلت یک وظیفه یا ثبت یادداشت پیشرفت. وضعیت‌ها: open, doing, blocked, done, cancelled.', {
        'task_id': {'type': 'integer'}, 'status': {'type': 'string'}, 'note': {'type': 'string'}, 'due': {'type': 'string'}}, ['task_id']),
    'task_list': ('فهرست وظایف. scope: mine | all | overdue | unfinished', {
        'scope': {'type': 'string'}, 'assignee_name': {'type': 'string'}}, []),
    'project_upsert': ('ایجاد/به‌روزرسانی پروژه. اگر فعال‌کردن آن از سقف پروژهٔ هم‌زمان فراتر برود، به‌جای اجرا درخواست تأیید مالک ثبت می‌شود.', {
        'name': {'type': 'string'}, 'status': {'type': 'string', 'description': 'active|paused|waiting|done'},
        'priority': {'type': 'integer'}, 'next_step': {'type': 'string'}}, ['name']),
    'project_list': ('فهرست پروژه‌ها و وضعیت آن‌ها.', {}, []),
    'decision_log': ('ثبت تصمیم مهم با گزینه‌ها، انتخاب، دلیل و مسئول اجرا (دفتر تصمیم).', {
        'title': {'type': 'string'}, 'options': {'type': 'string'}, 'choice': {'type': 'string'}, 'reason': {'type': 'string'}, 'responsible': {'type': 'string'}}, ['title', 'choice']),
    'note_add': ('ثبت یادداشت/درس‌آموخته/اطلاعات مهم برای بازیابی بعدی.', {'text': {'type': 'string'}}, ['text']),
    'staff_list': ('فهرست کارکنان و نقش‌ها.', {}, []),
    'request_approval': ('درخواست تأیید مالک برای اقدامات نیازمند تأیید. kind باید از فهرست مجاز باشد؛ اقدام‌های ممنوع رد می‌شوند. هیچ‌چیز پیش از تأیید اجرا نمی‌شود.', {
        'kind': {'type': 'string', 'enum': list(policy.NEEDS_APPROVAL) + list(policy.FORBIDDEN)}, 'summary': {'type': 'string'}, 'details': {'type': 'string'}}, ['kind', 'summary']),
    'ledger_status': ('وضعیت دفتر فعال‌سازی: هر سامانه در چه سطحی است.', {}, []),
    'connector_call': ('فراخوانی یک سامانهٔ متصل. فقط وقتی کار می‌کند که سطح سامانه در دفتر فعال‌سازی کافی باشد (read=۱، draft=۲، execute=۳).', {
        'system': {'type': 'string', 'enum': list(policy.SYSTEMS)}, 'op': {'type': 'string', 'enum': ['read', 'draft', 'execute']},
        'action': {'type': 'string'}, 'params': {'type': 'object'}}, ['system', 'op', 'action']),
    'crm_upsert': ('ثبت/ویرایش مشتری، سرنخ یا معامله در CRM داخلی. kind: contact|lead|deal. برای ویرایش crm_id بده. next_followup مهلت پیگیری است و در زمانش یادآوری تلگرام می‌رود. value به تومان.', {
        'name': {'type': 'string'}, 'kind': {'type': 'string'}, 'crm_id': {'type': 'integer'}, 'phone': {'type': 'string'}, 'company': {'type': 'string'},
        'source': {'type': 'string'}, 'status': {'type': 'string', 'description': 'مثلاً new|contacted|meeting|proposal|won|lost'}, 'value': {'type': 'integer'},
        'project': {'type': 'string'}, 'note': {'type': 'string'}, 'next_followup': {'type': 'string', 'description': 'YYYY-MM-DD یا YYYY-MM-DD HH:MM'}}, ['name']),
    'crm_log': ('ثبت تماس/جلسه/نتیجهٔ پیگیری برای یک رکورد CRM و تعیین پیگیری بعدی یا تغییر وضعیت.', {
        'crm_id': {'type': 'integer'}, 'text': {'type': 'string'}, 'next_followup': {'type': 'string'}, 'status': {'type': 'string'}}, ['crm_id', 'text']),
    'crm_list': ('جستجو/فهرست CRM و خلاصهٔ قیف فروش. followup_due=true: پیگیری‌های سررسید تا ۲۴ ساعت آینده. with_log_id: سابقهٔ یک رکورد.', {
        'query': {'type': 'string'}, 'kind': {'type': 'string'}, 'status': {'type': 'string'}, 'followup_due': {'type': 'boolean'}, 'with_log_id': {'type': 'integer'}}, []),
    'event_add': ('افزودن رویداد/جلسه به تقویم داخلی با یادآوری تلگرام (remind_minutes دقیقه قبل). تقویم گوگل نیست.', {
        'title': {'type': 'string'}, 'start': {'type': 'string', 'description': 'YYYY-MM-DD HH:MM (میلادی، وقت محلی)'}, 'end': {'type': 'string'},
        'remind_minutes': {'type': 'integer'}, 'note': {'type': 'string'}}, ['title', 'start']),
    'event_list': ('رویدادهای پیش‌رو در تقویم داخلی.', {'days': {'type': 'integer'}}, []),
    'event_cancel': ('لغو یک رویداد (حذف نمی‌شود، لغو ثبت می‌شود).', {'event_id': {'type': 'integer'}}, ['event_id']),
    'file_list': ('فهرست فایل‌هایی که کارکنان در تلگرام فرستاده‌اند (نقشه، قرارداد، خروجی Revit…). kind=revit_export برای جداول کمیت.', {
        'query': {'type': 'string'}, 'project': {'type': 'string'}, 'kind': {'type': 'string'}}, []),
    'file_read': ('خواندن/خلاصهٔ یک فایل: CSV/Excel (سرستون، تعداد ردیف، جمع ستون‌های عددی)، متن، PDF متنی. مدل‌های rvt/dwg/ifc فقط نگهداری می‌شوند.', {'file_id': {'type': 'integer'}}, ['file_id']),
    'file_tag': ('نسبت‌دادن فایل به پروژه و افزودن توضیح/نوع.', {'file_id': {'type': 'integer'}, 'project': {'type': 'string'}, 'note': {'type': 'string'}, 'kind': {'type': 'string'}}, ['file_id']),
    'fin_add': ('ثبت دفتری یک درآمد/هزینه/مطالبه/بدهی (تومان) در دفتر دستی مالک. فقط ثبت است؛ پرداخت یا انتقال وجه انجام نمی‌شود.', {
        'kind': {'type': 'string', 'enum': ['income', 'expense', 'receivable', 'debt']}, 'amount': {'type': 'integer'}, 'party': {'type': 'string'},
        'project': {'type': 'string'}, 'note': {'type': 'string'}, 'date': {'type': 'string'}}, ['kind', 'amount']),
    'fin_summary': ('خلاصهٔ مالی دفتر دستی: درآمد، هزینه، سود خالص، مطالبات و بدهی‌های باز، به تفکیک پروژه.', {'days': {'type': 'integer'}, 'project': {'type': 'string'}}, []),
    'fin_settle': ('بستن یک مطالبه یا بدهی در دفتر (تسویه‌شده).', {'fin_id': {'type': 'integer'}}, ['fin_id']),
    'hr_note': ('ثبت یادداشت عملکرد/موضوع کارمند (فقط مالک). تصمیم استخدام/حقوق با دستیار انجام نمی‌شود.', {'staff_name': {'type': 'string'}, 'kind': {'type': 'string'}, 'text': {'type': 'string'}}, ['staff_name', 'kind', 'text']),
    'hr_list': ('یادداشت‌های کارکنان و آمار انجام وظایف هر نفر.', {'staff_name': {'type': 'string'}}, []),
    'contract_upsert': ('ثبت/ویرایش قرارداد در دفتر قراردادها (طرف، مبلغ، شروع/پایان، وضعیت). امضا یا نهایی‌سازی نمی‌شود.', {
        'title': {'type': 'string'}, 'contract_id': {'type': 'integer'}, 'party': {'type': 'string'}, 'value': {'type': 'integer'}, 'start': {'type': 'string'},
        'end': {'type': 'string'}, 'status': {'type': 'string'}, 'project': {'type': 'string'}, 'note': {'type': 'string'}}, ['title']),
    'contract_list': ('فهرست قراردادها؛ expiring_days برای قراردادهای رو به پایان.', {'expiring_days': {'type': 'integer'}, 'query': {'type': 'string'}}, []),
    'send_message': ('ارسال پیام داخلی عملیاتی به یکی از کارکنان ثبت‌شده در تلگرام.', {'to_name': {'type': 'string'}, 'text': {'type': 'string'}}, ['to_name', 'text']),
}


def schemas_for(role):
    out = []
    for n in ALL_TOOLS:
        if n in ROLE_TOOLS.get(role, ()):
            d, props, req = SCHEMAS[n]
            out.append({'name': n, 'description': d, 'input_schema': {'type': 'object', 'properties': props, 'required': req}})
    return out


class ToolBox(InternalMixin):
    def __init__(self, db, cfg, notifier, who, now=None):
        self.db, self.cfg, self.n, self.who = db, cfg, notifier, who   # who = ردیف staff
        self.tz = ZoneInfo(cfg.tz)
        self.now = now or (lambda: dt.datetime.now(self.tz))

    def _due(self, s):
        if not s:
            return None
        for f in ('%Y-%m-%d %H:%M', '%Y-%m-%d'):
            try:
                d = dt.datetime.strptime(s.strip(), f).replace(tzinfo=self.tz)
                if f == '%Y-%m-%d':
                    d = d.replace(hour=18)
                return d.timestamp()
            except ValueError:
                pass
        raise ValueError('قالب مهلت نادرست؛ YYYY-MM-DD یا YYYY-MM-DD HH:MM')

    def _fmt_task(self, t):
        a = self.db.one('SELECT name FROM staff WHERE id=?', (t['assignee_id'],)) if t['assignee_id'] else None
        due = fmt_dt(dt.datetime.fromtimestamp(t['due_ts'], self.tz)) if t['due_ts'] else '—'
        return {'id': t['id'], 'title': t['title'], 'assignee': a['name'] if a else '—', 'due': due, 'status': t['status'],
                'overdue': bool(t['due_ts'] and t['due_ts'] < time.time() and t['status'] in ('open', 'doing', 'blocked')),
                'criteria': t['criteria'], 'project': t['project']}

    async def call(self, name, args):
        role = self.who['role']
        if name not in ROLE_TOOLS.get(role, ()):
            self.db.audit(self.who['name'], 'tool.denied', f'{name} role={role}')
            return {'ok': False, 'error': 'این ابزار برای نقش فعلی مجاز نیست.'}
        try:
            res = await getattr(self, 't_' + name)(**(args or {}))
        except TypeError as e:
            res = {'ok': False, 'error': f'آرگومان نادرست: {e}'}
        except ValueError as e:
            res = {'ok': False, 'error': str(e)}
        self.db.audit(self.who['name'], 'tool.' + name, json.dumps({'args': args, 'ok': res.get('ok')}, ensure_ascii=False))
        return res

    # ---------- وظایف
    async def t_task_create(self, title, assignee_name='', due='', criteria='', project=''):
        a = None
        if assignee_name:
            a = self.db.staff_by_name(assignee_name)
            if not a:
                return {'ok': False, 'error': f'«{assignee_name}» در فهرست کارکنان نیست. ابتدا مالک باید او را ثبت کند.'}
        tid = self.db.x('INSERT INTO tasks(title,assignee_id,creator,project,due_ts,criteria,created_ts,updated_ts) VALUES(?,?,?,?,?,?,?,?)',
                        (title, a['id'] if a else None, self.who['name'], project, self._due(due), criteria, time.time(), time.time()))
        self.db.x('INSERT INTO task_events(task_id,ts,actor,text) VALUES(?,?,?,?)', (tid, time.time(), self.who['name'], 'ایجاد وظیفه'))
        notified = False
        if a and a.get('telegram_id') and self.db.level('telegram') >= 3:
            txt = f'وظیفهٔ جدید #{fa(tid)}: {title}' + (f'\nمهلت: {fmt_date(dt.datetime.fromtimestamp(self._due(due), self.tz))}' if due else '') + (f'\nمعیار تکمیل: {criteria}' if criteria else '')
            notified = await self.n.send(a['telegram_id'], txt)
        return {'ok': True, 'task_id': tid, 'assignee_notified': notified}

    async def t_task_update(self, task_id, status='', note='', due=''):
        t = self.db.one('SELECT * FROM tasks WHERE id=?', (task_id,))
        if not t:
            return {'ok': False, 'error': 'وظیفه پیدا نشد.'}
        if self.who['role'] == 'staff' and t['assignee_id'] != self.who['id']:
            return {'ok': False, 'error': 'فقط وظایف خودتان قابل تغییر است.'}
        if status and status not in ('open', 'doing', 'blocked', 'done', 'cancelled'):
            return {'ok': False, 'error': 'وضعیت نامعتبر.'}
        sets, args = ['updated_ts=?'], [time.time()]
        if status:
            sets.append('status=?'); args.append(status)
        if due:
            sets.append('due_ts=?'); args.append(self._due(due)); sets.append('remind_count=0')
        self.db.x(f'UPDATE tasks SET {",".join(sets)} WHERE id=?', (*args, task_id))
        self.db.x('INSERT INTO task_events(task_id,ts,actor,text) VALUES(?,?,?,?)', (task_id, time.time(), self.who['name'], f'{status} {note}'.strip() or 'به‌روزرسانی'))
        return {'ok': True}

    async def t_task_list(self, scope='mine', assignee_name=''):
        role, uid = self.who['role'], self.who['id']
        if role == 'staff':
            scope = 'mine'
        w, a = ["status IN ('open','doing','blocked')"], []
        if scope == 'mine':
            w.append('assignee_id=?'); a.append(uid)
        elif assignee_name:
            s = self.db.staff_by_name(assignee_name)
            if s:
                w.append('assignee_id=?'); a.append(s['id'])
        if scope == 'overdue':
            w.append('due_ts<?'); a.append(time.time())
        rows = self.db.q(f'SELECT * FROM tasks WHERE {" AND ".join(w)} ORDER BY COALESCE(due_ts,9e12) LIMIT 60', a)
        return {'ok': True, 'tasks': [self._fmt_task(r) for r in rows]}

    # ---------- پروژه‌ها
    async def t_project_upsert(self, name, status='active', priority=3, next_step=''):
        if status not in ('active', 'paused', 'waiting', 'done'):
            return {'ok': False, 'error': 'وضعیت نامعتبر.'}
        cur = self.db.one('SELECT * FROM projects WHERE name=?', (name,))
        activating = status == 'active' and (not cur or cur['status'] != 'active')
        n_active = len(self.db.active_projects())
        cap = self.cfg.wip_cap
        if activating and (cap == 0 or n_active >= cap):
            summary = f'فعال‌کردن پروژهٔ «{name}» در حالی که {fa(n_active)} پروژهٔ فعال داریم' + (f' (سقف {fa(cap)})' if cap else ' (سقف هنوز تعیین نشده)')
            r = await self._approval('wip_cap_exception', summary, json.dumps({'name': name, 'priority': priority, 'next_step': next_step}, ensure_ascii=False))
            return {'ok': False, 'blocked': True, 'reason': 'سقف پروژهٔ هم‌زمان؛ درخواست تأیید برای مالک ارسال شد. پروژه‌های فعال فعلی را به مالک نشان بده و بپرس کدام متوقف شود.', **r,
                    'active_projects': [p['name'] for p in self.db.active_projects()]}
        self.db.x('INSERT INTO projects(name,status,priority,next_step,updated_ts) VALUES(?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET status=excluded.status,priority=excluded.priority,next_step=excluded.next_step,updated_ts=excluded.updated_ts',
                  (name, status, priority, next_step, time.time()))
        return {'ok': True}

    async def t_project_list(self):
        return {'ok': True, 'projects': self.db.q('SELECT name,status,priority,next_step FROM projects ORDER BY status,priority'), 'wip_cap': self.cfg.wip_cap}

    # ---------- ثبت‌ها
    async def t_decision_log(self, title, choice, options='', reason='', responsible=''):
        i = self.db.x('INSERT INTO decisions(ts,title,options,choice,reason,responsible) VALUES(?,?,?,?,?,?)', (time.time(), title, options, choice, reason, responsible))
        return {'ok': True, 'decision_id': i}

    async def t_note_add(self, text):
        self.db.x('INSERT INTO notes(ts,author,text) VALUES(?,?,?)', (time.time(), self.who['name'], text))
        return {'ok': True}

    async def t_staff_list(self):
        return {'ok': True, 'staff': self.db.q('SELECT name,role,lang,note FROM staff WHERE active=1')}

    async def t_ledger_status(self):
        return {'ok': True, 'ledger': [{'system': r['system'], 'name': policy.SYSTEMS[r['system']][0], 'level': r['level'], 'level_name': policy.LEVELS[r['level']], 'limits': r['limits']} for r in self.db.ledger()], 'paused': self.db.paused}

    # ---------- تأیید
    async def _approval(self, kind, summary, details=''):
        aid = self.db.x('INSERT INTO approvals(ts,kind,summary,details,requester_id) VALUES(?,?,?,?,?)', (time.time(), kind, summary, details, self.who['id']))
        owner = self.db.one("SELECT telegram_id FROM staff WHERE role='owner' AND active=1")
        sent = False
        if owner and owner['telegram_id']:
            sent = await self.n.ask_approval(owner['telegram_id'], aid, f'درخواست تأیید #{fa(aid)}\nنوع: {policy.NEEDS_APPROVAL.get(kind, kind)}\nاز طرف: {self.who["name"]}\n{summary}')
        return {'approval_id': aid, 'owner_notified': sent}

    async def t_request_approval(self, kind, summary, details=''):
        if kind in policy.FORBIDDEN:
            self.db.audit(self.who['name'], 'forbidden.request', f'{kind}: {summary}')
            return {'ok': False, 'forbidden': True, 'error': f'«{policy.FORBIDDEN[kind]}» طبق سند در هیچ شرایطی قابل انجام یا تأیید از طریق دستیار نیست.'}
        if kind not in policy.NEEDS_APPROVAL:
            kind = 'other'
        r = await self._approval(kind, summary, details)
        return {'ok': True, 'status': 'pending', **r, 'note': 'تا تأیید صریح مالک هیچ اقدامی انجام نمی‌شود.'}

    # ---------- اتصال‌ها
    async def t_connector_call(self, system, op, action, params=None):
        if system not in policy.SYSTEMS:
            return {'ok': False, 'error': 'سامانهٔ نامعتبر.'}
        name, cap, owner_only, implemented = policy.SYSTEMS[system]
        need = policy.OP_LEVEL[op]
        lvl = self.db.level(system)
        if owner_only and self.who['role'] != 'owner':
            return {'ok': False, 'error': 'این منبع فقط برای مالک قابل دسترسی است.'}
        if lvl < need:
            self.db.audit(self.who['name'], 'connector.blocked', f'{system} {op} level={lvl}')
            return {'ok': False, 'error': f'«{name}» در دفتر فعال‌سازی روی سطح {lvl} ({policy.LEVELS[lvl]}) است و برای «{op}» سطح {need} لازم است. فعال‌سازی فقط با دستور مالک.'}
        if need > cap:
            return {'ok': False, 'error': 'این عملیات طبق سند برای این سامانه مجاز نیست.'}
        if not implemented:
            return {'ok': False, 'error': f'اتصال «{name}» در این نسخه هنوز پیاده‌سازی نشده است؛ سطح آن فعال است ولی هیچ عملیاتی اجرا نشد.'}
        return {'ok': False, 'error': 'این سامانه داخلی است؛ به‌جای connector_call از ابزارهای اختصاصی‌اش استفاده کن (crm_*، event_*، file_*، fin_*، hr_*، contract_*).'}

    async def t_send_message(self, to_name, text):
        s = self.db.staff_by_name(to_name)
        if not s or not s['telegram_id']:
            return {'ok': False, 'error': 'گیرنده در فهرست کارکنان نیست.'}
        if self.db.level('telegram') < 3:
            return {'ok': False, 'error': 'ارسال پیام در دفتر فعال‌سازی فعال نیست.'}
        ok = await self.n.send(s['telegram_id'], f'پیام از طرف {self.who["name"]} (دستیار مدیریتی):\n{text}')
        return {'ok': ok}
