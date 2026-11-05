"""سیاست دسترسی: همه‌چیز پیش‌فرض سطح ۰ (غیرفعال). فقط مالک با دستور صریح فعال می‌کند."""

LEVELS = {0: 'غیرفعال', 1: 'فقط خواندن/تحلیل', 2: 'پیش‌نویس و پیشنهاد (با تأیید)', 3: 'اجرا در محدودهٔ مصوب'}

# key: (نام فارسی، سقف سطح مجاز طبق سند، فقط مالک ببیند؟، پیاده‌سازی شده در این نسخه؟)
SYSTEMS = {
    'telegram':      ('Telegram Bot', 3, False, True),
    'finance_data':  ('دفتر مالی دستی (درآمد/هزینه/سود/بدهی)', 1, True, True),
    'bank_view':     ('اطلاعات بانکی (فقط مشاهده)', 1, True, False),
    'hr_data':       ('اطلاعات کارکنان و عملکرد', 2, True, True),
    'crm':           ('CRM داخلی: مشتریان/سرنخ/معاملات', 2, False, True),
    'project_files': ('پرونده‌های پروژه‌های معماری', 1, False, True),
    'contracts_docs':('قراردادها، اسناد، مکاتبات', 1, True, True),
    'calendar':      ('تقویم و یادآوری داخلی ایجنت', 3, False, True),
    'whatsapp':      ('WhatsApp (Business API)', 3, False, False),
    'wordpress':     ('WordPress / پنل شیراز ملک', 3, False, False),
    'accounting':    ('نرم‌افزار حسابداری (هلو یا جایگزین)', 1, True, False),
    'revit_pm':      ('Revit و ابزار مدیریت پروژه (از طریق خروجی‌ها)', 1, False, True),
    'instagram_ads': ('Instagram و سامانه‌های تبلیغاتی', 3, False, False),
    'sms':           ('ارسال پیامک', 3, False, False),
    'sub_agents':    ('ایجنت/ربات‌های پیگیری', 3, False, False),
}

# اقدام‌هایی که اصلاً قابل انجام نیستند (هیچ ابزاری برایشان وجود ندارد؛ درخواست هم رد می‌شود)
FORBIDDEN = {
    'sign_contract': 'امضای قرارداد یا سند رسمی به نام مالک/شرکت',
    'create_debt': 'ایجاد وام، بدهی، ضمانت یا تعهد مالی جدید',
    'trade_major_asset': 'خرید، فروش یا واگذاری دارایی‌های عمده',
    'change_ownership': 'تغییر مالکیت یا ساختار سهامداری',
    'disclose_confidential': 'افشای اطلاعات محرمانه به افراد غیرمجاز',
    'delete_records': 'حذف اسناد، سوابق مالی یا مکاتبات',
    'unauthorized_commitment': 'ایجاد تعهد حقوقی/مالی در مذاکرات بدون مجوز',
}

# اقدام‌هایی که فقط با تأیید صریح مالک ادامه می‌یابند (دستیار فقط آماده‌سازی/پیشنهاد می‌کند)
NEEDS_APPROVAL = {
    'hire_fire_salary': 'استخدام، اخراج یا تغییر حقوق کارکنان',
    'payment_over_cap': 'پرداخت خارج از سقف مجاز',
    'investment_transfer': 'سرمایه‌گذاری و انتقال سرمایه',
    'major_financial_commitment': 'تعهد مالی مهم',
    'finalize_contract': 'نهایی‌سازی قرارداد',
    'major_negotiation': 'مذاکرهٔ مهم تجاری',
    'budget_org_change': 'تغییر بودجه و ساختار سازمانی',
    'legal_financial_decision': 'تصمیم حقوقی/مالی با اثر جدی',
    'wip_cap_exception': 'شروع پروژهٔ تازه بیش از سقف پروژهٔ هم‌زمان',
    'other': 'سایر موارد نیازمند تأیید',
}

READ, DRAFT, EXECUTE = 1, 2, 3
OP_LEVEL = {'read': READ, 'draft': DRAFT, 'execute': EXECUTE}
