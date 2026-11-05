import datetime as dt
import json
import time
from zoneinfo import ZoneInfo

from . import internal
from .jalali import fmt_date, fmt_dt, fa, to_jalali, MONTHS


def _tasks(db, where, args=()):
    return db.q(f'SELECT t.*, s.name AS who FROM tasks t LEFT JOIN staff s ON s.id=t.assignee_id WHERE {where} ORDER BY COALESCE(due_ts,9e12)', args)


def _line(t, tz):
    due = fmt_date(dt.datetime.fromtimestamp(t['due_ts'], tz)) if t['due_ts'] else 'بدون مهلت'
    return f'• #{fa(t["id"])} {t["title"]} — {t["who"] or "بدون مسئول"} — مهلت: {due}'


def _count(db, event, since, ok_only=True):
    rows = db.q('SELECT detail FROM audit WHERE event=? AND ts>=?', (event, since))
    if not ok_only:
        return len(rows)
    n = 0
    for r in rows:
        try:
            n += 1 if json.loads(r['detail']).get('ok') else 0
        except (ValueError, AttributeError):
            n += 1
    return n


def activity(db, since, tz, label):
    """«امروز دستیار چه کرد؟» و «با چه کسانی ارتباط داشت؟»: فقط از سوابق ثبت‌شده."""
    out = []
    new_t = db.q('SELECT t.title, s.name AS who FROM tasks t LEFT JOIN staff s ON s.id=t.assignee_id WHERE t.created_ts>=?', (since,))
    items = []
    if new_t:
        items.append(f'{fa(len(new_t))} وظیفه ایجاد شد: ' + '؛ '.join(f'{x["title"]} ({x["who"] or "بدون مسئول"})' for x in new_t[:6]))
    n = _count(db, 'tool.task_update', since)
    if n:
        items.append(f'{fa(n)} به‌روزرسانی وضعیت وظیفه')
    n = len(db.q("SELECT 1 FROM audit WHERE event='followup' AND ts>=?", (since,)))
    if n:
        items.append(f'{fa(n)} پیگیری/یادآوری برای وظایف دیرکرده')
    ap = db.q('SELECT summary FROM approvals WHERE ts>=?', (since,))
    if ap:
        items.append(f'{fa(len(ap))} درخواست تأیید برای شما ارسال شد: ' + '؛ '.join(a['summary'] for a in ap[:4]))
    d = db.q('SELECT title FROM decisions WHERE ts>=?', (since,))
    if d:
        items.append(f'{fa(len(d))} تصمیم در دفتر تصمیم ثبت شد: ' + '؛ '.join(x['title'] for x in d[:4]))
    for ev, txt in (('tool.project_upsert', 'به‌روزرسانی پروژه'), ('tool.note_add', 'یادداشت/درس‌آموخته ثبت شد'), ('tool.send_message', 'پیام داخلی به همکاران ارسال شد')):
        n = _count(db, ev, since)
        if n:
            items.append(f'{fa(n)} {txt}')
    blocked = db.q("SELECT detail FROM audit WHERE ts>=? AND event IN ('connector.blocked','forbidden.request','tool.denied','unauthorized')", (since,))
    if blocked:
        items.append(f'{fa(len(blocked))} تلاش خارج از اختیار مسدود شد (سامانهٔ غیرفعال، درخواست ممنوع یا کاربر ناشناس)')
    led = db.q("SELECT detail FROM audit WHERE ts>=? AND event IN ('ledger.set','killswitch','resume')", (since,))
    if led:
        items.append(f'{fa(len(led))} تغییر در دفتر فعال‌سازی توسط شما')
    out += [f'کارهای دستیار {label}:'] + (['• ' + i for i in items] or ['• اقدامی ثبت نشد.'])
    owner = db.one("SELECT telegram_id FROM staff WHERE role='owner' AND active=1")
    rows = db.q("SELECT m.chat_id, m.role, COALESCE(s.name,'ناشناس') AS name FROM messages m LEFT JOIN staff s ON s.telegram_id=m.chat_id WHERE m.ts>=? AND m.role='user'", (since,))
    per = {}
    for r in rows:
        per[r['name']] = per.get(r['name'], 0) + 1
    mine = per.pop('مالک', 0) if owner else 0
    out += ['', f'ارتباطات {label}:', f'• با شما: {fa(mine)} پیام دریافت شد و پاسخ داده شد.' if mine else '• با شما: گفتگویی ثبت نشد.']
    if per:
        out.append('• با همکاران: ' + '، '.join(f'{k} ({fa(v)} پیام)' for k, v in per.items()))
    return out


def build(db, kind, tz_name, now=None):
    """گزارش متنی از دادهٔ ثبت‌شده. چیزی که در دیتابیس نیست گزارش نمی‌شود و صراحتاً «داده ندارد» نوشته می‌شود."""
    tz = ZoneInfo(tz_name)
    now = now or dt.datetime.now(tz)
    t = now.timestamp()
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    span = {'daily': 86400, 'weekly': 7 * 86400, 'monthly': 31 * 86400}[kind]
    since = t - span if kind != 'daily' else day0
    open_ = _tasks(db, "t.status IN ('open','doing','blocked')")
    late = [x for x in open_ if x['due_ts'] and x['due_ts'] < t]
    done = _tasks(db, "t.status='done' AND t.updated_ts>=?", (since,))
    blocked = [x for x in open_ if x['status'] == 'blocked']
    pend = db.q("SELECT * FROM approvals WHERE status='pending'")
    head = {'daily': 'گزارش روزانه', 'weekly': 'گزارش هفتگی', 'monthly': 'گزارش ماهانه'}[kind]
    out = [f'{head} — {fmt_date(now)}', '']
    out.append(f'خلاصه: {fa(len(done))} وظیفه انجام شد، {fa(len(open_))} باز است ({fa(len(late))} عقب‌افتاده)' + (f'، {fa(len(pend))} درخواست تأیید منتظر شماست.' if pend else '.'))
    out += [''] + activity(db, since, tz, {'daily': 'امروز', 'weekly': 'این هفته', 'monthly': 'این ماه'}[kind])
    if pend:
        out += ['', 'نیازمند تصمیم شما:'] + [f'• #{fa(p["id"])} {p["summary"]}' for p in pend]
    if late:
        out += ['', 'عقب‌افتاده‌ها:'] + [_line(x, tz) for x in late[:15]]
    if blocked:
        out += ['', 'متوقف‌شده:'] + [_line(x, tz) for x in blocked[:10]]
    if done:
        out += ['', 'انجام‌شده:'] + [_line(x, tz) for x in done[:15]]
    tomorrow = [x for x in open_ if x['due_ts'] and t <= x['due_ts'] < t + 2 * 86400]
    if kind == 'daily':
        out += ['', 'مهلت‌های ۴۸ ساعت آینده:'] + ([_line(x, tz) for x in tomorrow] or ['• موردی ثبت نشده'])
    if kind in ('weekly', 'monthly'):
        stale = [x for x in open_ if t - x['created_ts'] > 14 * 86400]
        out += ['', 'نیمه‌تمام‌های قدیمی‌تر از ۲ هفته:'] + ([_line(x, tz) for x in stale[:15]] or ['• ندارد'])
        out += ['', 'پروژه‌های فعال:'] + ([f'• {p["name"]} — گام بعد: {p["next_step"] or "ثبت نشده"}' for p in db.active_projects()] or ['• ثبت نشده'])
    if kind == 'monthly':
        fs = internal.fin_summary(db, 30)
        if fs['entries']:
            out += ['', f'مالی (دفتر دستی مالک، ۳۰ روز اخیر): درآمد {internal.toman(fs["income"])}، هزینه {internal.toman(fs["expense"])}، خالص {internal.toman(fs["net"])}؛ مطالبات باز {internal.toman(fs["open_receivable"])}، بدهی باز {internal.toman(fs["open_debt"])}. (حسابداری رسمی/بانک به ایجنت وصل نیست.)']
        else:
            out += ['', 'مالی: دفتر دستی خالی است؛ تحلیل درآمد، هزینه و سود خالص پس از ثبت داده در دفتر (یا اتصال حسابداری) ممکن می‌شود.']
    t_now = time.time()
    ev = db.q("SELECT title,start_ts FROM events WHERE status='active' AND start_ts BETWEEN ? AND ? ORDER BY start_ts", (t_now, t_now + (2 if kind == 'daily' else 7) * 86400))
    if ev:
        out += ['', 'رویدادهای پیش‌رو:'] + [f'• {e["title"]} — {fmt_dt(dt.datetime.fromtimestamp(e["start_ts"], tz))}' for e in ev[:10]]
    fu = db.q('SELECT name,kind,status,next_followup_ts FROM crm WHERE next_followup_ts IS NOT NULL AND next_followup_ts<? ORDER BY next_followup_ts LIMIT 10', (t_now + 2 * 86400,))
    if fu:
        out += ['', 'پیگیری‌های CRM:'] + [f'• {c["name"]} ({c["status"]}) — {fmt_dt(dt.datetime.fromtimestamp(c["next_followup_ts"], tz))}' for c in fu]
    if kind != 'daily':
        pipe = db.q("SELECT status,COUNT(*) n,SUM(value) v FROM crm WHERE kind IN ('lead','deal') GROUP BY status")
        if pipe:
            out += ['', 'قیف فروش:'] + [f'• {p["status"]}: {fa(p["n"])} مورد، ارزش {internal.toman(p["v"] or 0)}' for p in pipe]
        ct = db.q("SELECT title,end_ts FROM contracts WHERE status='active' AND end_ts IS NOT NULL AND end_ts<? ORDER BY end_ts LIMIT 8", (t_now + 30 * 86400,))
        if ct:
            out += ['', 'قراردادهای رو به پایان (۳۰ روز):'] + [f'• {c["title"]} — {fmt_date(dt.datetime.fromtimestamp(c["end_ts"], tz))}' for c in ct]
    out += ['', 'منابع: وظایف، پروژه‌ها و تأییدیه‌های ثبت‌شده در سامانهٔ دستیار.']
    return '\n'.join(out)


def jalali_day(now):
    return to_jalali(now.date())[2]
