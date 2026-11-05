import datetime as dt
import logging
import os
import time
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from . import internal, policy, reports
from .tools import ToolBox
from .jalali import fa, fmt_dt

log = logging.getLogger('agent')
CHUNK = 3800


class TgNotifier:
    def __init__(self, app):
        self.app = app

    async def send(self, tg_id, text):
        try:
            for i in range(0, len(text), CHUNK):
                await self.app.bot.send_message(tg_id, text[i:i + CHUNK])
            return True
        except Exception as e:
            log.warning('send failed %s: %s', tg_id, e)
            return False

    async def ask_approval(self, tg_id, aid, text):
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('✅ تأیید', callback_data=f'ap:{aid}:1'), InlineKeyboardButton('❌ رد', callback_data=f'ap:{aid}:0')]])
        try:
            await self.app.bot.send_message(tg_id, text, reply_markup=kb)
            return True
        except Exception as e:
            log.warning('approval send failed: %s', e)
            return False


def build_app(cfg, agent, db):
    builder = Application.builder().token(cfg.telegram_token)
    if cfg.telegram_proxy:
        builder = builder.proxy(cfg.telegram_proxy).get_updates_proxy(cfg.telegram_proxy)
    app = builder.build()
    agent.n = TgNotifier(app)

    def role_of(update):
        return db.staff_by_tg(update.effective_user.id) if update.effective_user else None

    def owner_only(fn):
        async def w(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            who = role_of(update)
            if not who or who['role'] != 'owner':
                db.audit(update.effective_user.id if update.effective_user else '?', 'owner_cmd.denied', update.effective_message.text[:60] if update.effective_message else '')
                return
            await fn(update, ctx, who)
        return w

    async def on_text(update, ctx):
        if not update.message or not update.message.text:
            return
        ans = await agent.handle(update.effective_user.id, update.message.text)
        if ans:
            for i in range(0, len(ans), CHUNK):
                await update.message.reply_text(ans[i:i + CHUNK])

    async def on_doc(update, ctx):
        m = update.message
        who = role_of(update)
        if not m or not m.document or not who:
            return
        if db.paused:
            return await m.reply_text('دستیار متوقف است.')
        if max(db.level('project_files'), db.level('revit_pm')) < 1:
            return await m.reply_text('دریافت فایل هنوز فعال نیست؛ مالک باید /activate_internal بزند.')
        d = m.document
        if (d.file_size or 0) > internal.MAX_FILE:
            return await m.reply_text('حجم فایل بیش از ۲۰ مگابایت است.')
        try:
            data = bytes(await (await d.get_file()).download_as_bytearray())
            fid = internal.register_file(db, os.path.join(os.path.dirname(cfg.db_path) or '.', 'files'), who['name'], d.file_name or 'file', d.mime_type, data, m.caption or '')
        except Exception as e:
            db.audit('system', 'file.error', str(e)[:200])
            return await m.reply_text(f'ذخیرهٔ فایل ناموفق بود: {str(e)[:100]}')
        await m.reply_text(f'فایل ذخیره شد (شناسه {fa(fid)}). برای خواندنش بنویسید: «فایل {fa(fid)} را بخوان» و برای نسبت‌دادن به پروژه نام پروژه را بگویید.')
        if m.caption:
            ans = await agent.handle(update.effective_user.id, f'فایل {fid} ({d.file_name}) را فرستادم. توضیح: {m.caption}')
            if ans:
                await m.reply_text(ans[:CHUNK])

    @owner_only
    async def activate_internal(update, ctx, who):
        for k, lv in internal.INTERNAL.items():
            db.activate(k, lv, who['name'], note='activate_internal')
        await update.message.reply_text('ماژول‌های داخلی روشن شد: CRM (۲)، تقویم (۲/۳)، پرونده‌ها و Revit (۱)، دفتر مالی (۱)، کارکنان (۲)، قراردادها (۱).\nهر کدام را با /revoke <system> می‌توانید خاموش کنید؛ وضعیت: /ledger')

    async def agenda(update, ctx):
        who = role_of(update)
        if not who:
            return
        r = await ToolBox(db, cfg, agent.n, who).call('event_list', {'days': 7})
        await update.message.reply_text(r.get('error') or ('\n'.join(f'• {e["start"]} — {e["title"]}' for e in r['events']) or 'رویدادی در ۷ روز آینده نیست.'))

    async def files_cmd(update, ctx):
        who = role_of(update)
        if not who:
            return
        r = await ToolBox(db, cfg, agent.n, who).call('file_list', {})
        await update.message.reply_text(r.get('error') or ('\n'.join(f'• #{fa(f["id"])} {f["name"]} — {f["project"] or "بدون پروژه"} ({f["uploaded"]})' for f in r['files']) or 'فایلی ثبت نشده.'))

    async def crm_cmd(update, ctx):
        who = role_of(update)
        if not who or who['role'] == 'staff':
            return
        r = await ToolBox(db, cfg, agent.n, who).call('crm_list', {'followup_due': True})
        await update.message.reply_text(r.get('error') or ('\n'.join(f'• #{fa(c["id"])} {c["name"]} ({c["status"]}) — پیگیری: {c["next_followup"]}' for c in r['records']) or 'پیگیری سررسیدی نیست.'))

    async def job_reminders(ctx):
        if db.paused:
            return
        for tg, text in internal.due_notifications(db, ZoneInfo(cfg.tz)):
            await agent.n.send(tg, text)

    async def start(update, ctx):
        who = role_of(update)
        if who:
            await update.message.reply_text(f'سلام {who["name"]}. دستیار مدیریتی آماده است. هر چیزی بنویسید؛ دستورها: /tasks /agenda /files /crm /report')

    async def tasks(update, ctx):
        who = role_of(update)
        if not who:
            return
        rows = reports._tasks(db, "t.status IN ('open','doing','blocked')" + ('' if who['role'] != 'staff' else f' AND t.assignee_id={int(who["id"])}'))
        tz = ZoneInfo(cfg.tz)
        await update.message.reply_text('\n'.join(reports._line(r, tz) for r in rows[:30]) or 'وظیفهٔ بازی نیست.')

    @owner_only
    async def ledger(update, ctx, who):
        lines = ['دفتر فعال‌سازی:']
        for r in db.ledger():
            n, cap, _, impl = policy.SYSTEMS[r['system']]
            lines.append(f'• {r["system"]} — {n}: سطح {fa(r["level"])} ({policy.LEVELS[r["level"]]}) / سقف سند: {fa(cap)}' + ('' if impl else ' [اتصال پیاده‌سازی نشده]'))
        lines.append('\nفعال‌سازی: /activate <system> <0-3> [توضیح محدودیت]\nقطع: /revoke <system>   قطع کامل: /killswitch')
        await update.message.reply_text('\n'.join(lines))

    @owner_only
    async def activate(update, ctx, who):
        a = ctx.args
        if len(a) < 2 or not a[1].isdigit():
            return await update.message.reply_text('نمونه: /activate whatsapp 2 فقط پاسخ اولیهٔ قالب مصوب')
        try:
            db.activate(a[0], int(a[1]), who['name'], ' '.join(a[2:]))
        except ValueError as e:
            return await update.message.reply_text(f'رد شد: {e}')
        await update.message.reply_text(f'{a[0]} روی سطح {fa(a[1])} ثبت شد.')

    @owner_only
    async def revoke(update, ctx, who):
        if not ctx.args:
            return await update.message.reply_text('نمونه: /revoke whatsapp')
        try:
            db.activate(ctx.args[0], 0, who['name'], note='revoked')
        except ValueError as e:
            return await update.message.reply_text(str(e))
        await update.message.reply_text('لغو شد.')

    @owner_only
    async def kill(update, ctx, who):
        db.kill_all(who['name'])
        await update.message.reply_text('همهٔ دسترسی‌ها صفر شد و دستیار متوقف شد. ازسرگیری: /resume')

    @owner_only
    async def resume(update, ctx, who):
        db.resume(who['name'])
        await update.message.reply_text('دستیار فعال شد؛ دسترسی‌ها همچنان ۰ است. با /activate دوباره روشن کنید.')

    @owner_only
    async def staff_add(update, ctx, who):
        # /staff_add <telegram_id> <manager|staff> نام...
        a = ctx.args
        if len(a) < 3 or not a[0].isdigit() or a[1] not in ('manager', 'staff'):
            return await update.message.reply_text('نمونه: /staff_add 123456 manager علی احمدی')
        db.x('INSERT INTO staff(name,telegram_id,role) VALUES(?,?,?) ON CONFLICT(telegram_id) DO UPDATE SET name=excluded.name, role=excluded.role, active=1', (' '.join(a[2:]), int(a[0]), a[1]))
        db.audit(who['name'], 'staff.add', ' '.join(a))
        await update.message.reply_text('ثبت شد.')

    @owner_only
    async def staff_off(update, ctx, who):
        if not ctx.args or not ctx.args[0].isdigit():
            return await update.message.reply_text('نمونه: /staff_off 123456')
        db.x("UPDATE staff SET active=0 WHERE telegram_id=? AND role!='owner'", (int(ctx.args[0]),))
        db.audit(who['name'], 'staff.off', ctx.args[0])
        await update.message.reply_text('غیرفعال شد (سوابق حفظ می‌شود).')

    @owner_only
    async def report(update, ctx, who):
        kind = ctx.args[0] if ctx.args and ctx.args[0] in ('daily', 'weekly', 'monthly') else 'daily'
        await update.message.reply_text(reports.build(db, kind, cfg.tz))

    async def on_cb(update, ctx):
        q = update.callback_query
        who = db.staff_by_tg(q.from_user.id)
        await q.answer()
        if not who or who['role'] != 'owner':
            return
        _, aid, ok = q.data.split(':')
        ap = db.one("SELECT * FROM approvals WHERE id=? AND status='pending'", (int(aid),))
        if not ap:
            return await q.edit_message_text('این درخواست قبلاً تعیین تکلیف شده است.')
        db.x('UPDATE approvals SET status=?, decided_ts=? WHERE id=?', ('approved' if ok == '1' else 'rejected', time.time(), int(aid)))
        db.audit(who['name'], 'approval.decide', f'{aid}={ok}')
        if ap['kind'] == 'wip_cap_exception' and ok == '1':
            import json
            d = json.loads(ap['details'] or '{}')
            db.x('INSERT INTO projects(name,status,priority,next_step,updated_ts) VALUES(?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET status=excluded.status,updated_ts=excluded.updated_ts',
                 (d.get('name', '?'), 'active', d.get('priority', 3), d.get('next_step', ''), time.time()))
        await q.edit_message_text(f'{q.message.text}\n\n{"✅ تأیید شد" if ok == "1" else "❌ رد شد"}')
        req = db.one('SELECT telegram_id FROM staff WHERE id=?', (ap['requester_id'],))
        if req and req['telegram_id'] and req['telegram_id'] != q.from_user.id:
            await agent.n.send(req['telegram_id'], f'درخواست #{fa(aid)} {"تأیید شد" if ok == "1" else "رد شد"}.')
        if ap['kind'] != 'wip_cap_exception':
            await agent.n.send(q.from_user.id, 'یادآوری: تأیید فقط ثبت شد؛ دستیار این اقدام را خودش اجرا نمی‌کند.')

    # ---- کارهای زمان‌بندی‌شده
    def hm(s):
        h, m = s.split(':')
        return dt.time(int(h), int(m), tzinfo=ZoneInfo(cfg.tz))

    async def job_daily(ctx):
        if db.paused:
            return
        now = dt.datetime.now(ZoneInfo(cfg.tz))
        o = db.one("SELECT telegram_id FROM staff WHERE role='owner' AND active=1")
        if o:
            await agent.n.send(o['telegram_id'], reports.build(db, 'daily', cfg.tz))
            if reports.jalali_day(now) == 1:
                await agent.n.send(o['telegram_id'], reports.build(db, 'monthly', cfg.tz))

    async def job_weekly(ctx):
        if db.paused:
            return
        o = db.one("SELECT telegram_id FROM staff WHERE role='owner' AND active=1")
        if o:
            await agent.n.send(o['telegram_id'], reports.build(db, 'weekly', cfg.tz))

    async def job_followup(ctx):
        """پیگیری تدریجی: یادآوری → پیگیری دوم (۲۴ ساعت بعد) → گزارش تأخیر به مالک (۲۴ ساعت بعد)."""
        if db.paused or db.level('telegram') < 3:
            return
        t = time.time()
        for x in db.q("SELECT t.*, s.telegram_id AS tg, s.name AS who FROM tasks t LEFT JOIN staff s ON s.id=t.assignee_id WHERE t.status IN ('open','doing','blocked') AND t.due_ts IS NOT NULL AND t.due_ts<? AND t.remind_count<3", (t,)):
            if t - max(x['last_remind_ts'], x['due_ts']) < 86400 and x['remind_count'] > 0:
                continue
            n = x['remind_count']
            if n < 2 and x['tg']:
                await agent.n.send(x['tg'], f'{"یادآوری" if n == 0 else "پیگیری دوم"}: وظیفهٔ #{fa(x["id"])} «{x["title"]}» از مهلتش گذشته است. وضعیت را بفرمایید.')
            elif n == 2 or not x['tg']:
                o = db.one("SELECT telegram_id FROM staff WHERE role='owner' AND active=1")
                if o:
                    await agent.n.send(o['telegram_id'], f'تأخیر: وظیفهٔ #{fa(x["id"])} «{x["title"]}» ({x["who"] or "بدون مسئول"}) پس از دو یادآوری هنوز باز است. پیشنهاد: تماس مستقیم یا تغییر مسئول/مهلت.')
                n = 2
            db.x('UPDATE tasks SET remind_count=?, last_remind_ts=? WHERE id=?', (n + 1, t, x['id']))
            db.audit('system', 'followup', f'task {x["id"]} step {n + 1}')

    for c, f in [('start', start), ('tasks', tasks), ('ledger', ledger), ('activate', activate), ('revoke', revoke), ('killswitch', kill),
                 ('resume', resume), ('activate_internal', activate_internal), ('agenda', agenda), ('files', files_cmd), ('crm', crm_cmd), ('staff_add', staff_add), ('staff_off', staff_off), ('report', report)]:
        app.add_handler(CommandHandler(c, f))
    app.add_handler(CallbackQueryHandler(on_cb, pattern=r'^ap:'))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(filters.Document.ALL, on_doc))
    jq = app.job_queue
    jq.run_daily(job_daily, hm(cfg.daily_at))
    jq.run_daily(job_weekly, hm(cfg.weekly_at), days=(cfg.weekly_day,))
    jq.run_repeating(job_followup, interval=1800, first=120)
    jq.run_repeating(job_reminders, interval=60, first=30)
    return app
