import asyncio
import time
import pytest
from app import config, db as dbm, internal, reports, tools
from test_core import Notif, run


@pytest.fixture
def env2(tmp_path):
    cfg = config.Config(tz='Asia/Tehran', wip_cap=2, db_path=':memory:')
    db = dbm.DB(':memory:')
    db.x("INSERT INTO staff(name,telegram_id,role) VALUES('مالک',1,'owner')")
    db.x("INSERT INTO staff(name,telegram_id,role) VALUES('علی',2,'manager')")
    db.x("INSERT INTO staff(name,telegram_id,role) VALUES('سارا',3,'staff')")
    db.activate('telegram', 3, 'x')
    return cfg, db, Notif(), tmp_path


def box(env, tg):
    cfg, db, n, _ = env
    return tools.ToolBox(db, cfg, n, db.staff_by_tg(tg))


def on(db):
    for k, v in internal.INTERNAL.items():
        db.activate(k, v, 'owner')


def test_blocked_until_activated(env2):
    r = run(box(env2, 1).call('crm_upsert', {'name': 'آقای رضایی'}))
    assert r['ok'] is False and 'سطح' in r['error']
    on(env2[1])
    assert run(box(env2, 1).call('crm_upsert', {'name': 'آقای رضایی'}))['ok']


def test_crm_flow_dedupe_followup_and_reminder(env2):
    cfg, db, n, _ = env2; on(db)
    b = box(env2, 2)
    r = run(b.call('crm_upsert', {'name': 'رضایی', 'phone': '0912', 'kind': 'lead', 'value': 500000000, 'next_followup': '2020-01-01 10:00'}))
    assert r['ok']
    assert run(b.call('crm_upsert', {'name': 'رضایی', 'phone': '0912', 'kind': 'lead'}))['ok'] is False      # تکراری
    assert run(b.call('crm_log', {'crm_id': r['crm_id'], 'text': 'تماس گرفتم', 'status': 'meeting'}))['ok']
    lst = run(b.call('crm_list', {'followup_due': True}))
    assert lst['records'][0]['status'] == 'meeting'
    out = internal.due_notifications(db, b.tz)
    assert out and out[0][0] == 2 and 'رضایی' in out[0][1]
    assert internal.due_notifications(db, b.tz) == []                                                  # فقط یک بار
    assert run(b.call('crm_list', {'with_log_id': r['crm_id']}))['history'][0]['text'] == 'تماس گرفتم'
    with pytest.raises(Exception):
        db.x('DELETE FROM crm')


def test_staff_has_no_crm_or_finance(env2):
    on(env2[1])
    assert run(box(env2, 3).call('crm_list', {}))['ok'] is False
    assert run(box(env2, 2).call('fin_summary', {}))['ok'] is False       # مدیر به مالی دسترسی ندارد


def test_calendar_reminder(env2):
    cfg, db, n, _ = env2; on(db)
    b = box(env2, 1)
    soon = time.strftime('%Y-%m-%d %H:%M')
    r = run(b.call('event_add', {'title': 'جلسهٔ کارفرما', 'start': '2099-01-01 10:00', 'remind_minutes': 30}))
    assert r['ok']
    db.x('UPDATE events SET remind_ts=?, start_ts=? WHERE id=?', (time.time() - 5, time.time() + 600, r['event_id']))
    out = internal.due_notifications(db, b.tz)
    assert out and 'جلسهٔ کارفرما' in out[0][1]
    assert run(b.call('event_list', {'days': 3}))['events']
    assert run(b.call('event_cancel', {'event_id': r['event_id']}))['ok']


def test_finance_register_and_monthly_report(env2):
    cfg, db, n, _ = env2; on(db)
    b = box(env2, 1)
    run(b.call('fin_add', {'kind': 'income', 'amount': 800000000, 'project': 'ویلا', 'party': 'کارفرما'}))
    run(b.call('fin_add', {'kind': 'expense', 'amount': 300000000, 'project': 'ویلا'}))
    f = run(b.call('fin_add', {'kind': 'receivable', 'amount': 100000000}))
    s = run(b.call('fin_summary', {}))
    assert s['net'] == 500000000 and s['open_receivable'] == 100000000 and s['by_project'][0]['project'] == 'ویلا'
    assert run(b.call('fin_add', {'kind': 'income', 'amount': -5}))['ok'] is False
    rep = reports.build(db, 'monthly', cfg.tz)
    assert 'دفتر دستی مالک' in rep and 'حسابداری رسمی' in rep
    run(b.call('fin_settle', {'fin_id': f['fin_id']}))
    assert run(b.call('fin_summary', {}))['open_receivable'] == 0


def test_hr_and_contracts_owner_only(env2):
    cfg, db, n, _ = env2; on(db)
    o = box(env2, 1)
    assert run(o.call('hr_note', {'staff_name': 'سارا', 'kind': 'عملکرد', 'text': 'خوب'}))['ok']
    assert run(o.call('hr_list', {}))['task_stats']
    c = run(o.call('contract_upsert', {'title': 'قرارداد طراحی ویلا', 'party': 'آقای الف', 'value': 900000000, 'end': '2020-02-01'}))
    assert run(o.call('contract_list', {'expiring_days': 30}))['contracts'][0]['title'].startswith('قرارداد')
    assert run(box(env2, 2).call('contract_list', {}))['ok'] is False


def test_file_intake_and_read_revit_csv(env2):
    cfg, db, n, tmp = env2; on(db)
    csv_bytes = 'Family,Count,Area\nدیوار,12,340.5\nپنجره,8,16\n'.encode('utf-8-sig')
    fid = internal.register_file(db, str(tmp / 'files'), 'علی', 'revit_schedule.csv', 'text/csv', csv_bytes, 'جدول کمیت ویلا')
    assert db.one('SELECT kind FROM files WHERE id=?', (fid,))['kind'] == 'revit_export'
    r = run(box(env2, 2).call('file_read', {'file_id': fid}))
    assert r['ok'] and r['row_count'] == 2 and r['numeric_column_sums']['Count'] == 20 and r['numeric_column_sums']['Area'] == 356.5
    assert run(box(env2, 2).call('file_tag', {'file_id': fid, 'project': 'ویلا'}))['ok']
    assert run(box(env2, 3).call('file_list', {'project': 'ویلا'}))['files'][0]['name'] == 'revit_schedule.csv'
    u16 = internal.register_file(db, str(tmp / 'files'), 'علی', 'q.csv', '', 'A\tB\n1\t2\n'.encode('utf-16'), '')
    assert run(box(env2, 2).call('file_read', {'file_id': u16}))['numeric_column_sums']['B'] == 2
    rvt = internal.register_file(db, str(tmp / 'files'), 'علی', 'model.rvt', '', b'xx', '')
    assert run(box(env2, 2).call('file_read', {'file_id': rvt}))['type'] == 'unsupported'
    with pytest.raises(ValueError):
        internal.register_file(db, str(tmp / 'files'), 'x', 'big.bin', '', b'0' * (internal.MAX_FILE + 1))


def test_xlsx_read(env2):
    openpyxl = pytest.importorskip('openpyxl')
    cfg, db, n, tmp = env2; on(db)
    wb = openpyxl.Workbook(); ws = wb.active; ws.append(['Item', 'Qty']); ws.append(['a', 3]); ws.append(['b', 4])
    p = tmp / 'x.xlsx'; wb.save(p)
    fid = internal.register_file(db, str(tmp / 'files'), 'علی', 'x.xlsx', '', p.read_bytes(), '')
    r = run(box(env2, 1).call('file_read', {'file_id': fid}))
    assert r['ok'] and list(r['sheets'].values())[0]['numeric_column_sums']['Qty'] == 7
