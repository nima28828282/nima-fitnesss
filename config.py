import os
from dataclasses import dataclass


@dataclass
class Config:
    telegram_token: str = ''
    owner_id: int = 0
    telegram_proxy: str = ''
    provider: str = 'anthropic'
    api_key: str = ''
    model: str = ''
    base_url: str = ''
    tz: str = 'Asia/Tehran'
    daily_at: str = '20:00'
    weekly_day: int = 4
    weekly_at: str = '18:00'
    wip_cap: int = 0
    db_path: str = 'data/agent.db'
    prompt_path: str = 'prompt/system_prompt_fa.txt'


def load() -> Config:
    e = os.environ.get
    return Config(
        telegram_token=e('TELEGRAM_TOKEN', ''), owner_id=int(e('OWNER_TELEGRAM_ID', '0') or 0),
        telegram_proxy=e('TELEGRAM_PROXY', ''), provider=e('LLM_PROVIDER', 'anthropic'),
        api_key=e('LLM_API_KEY', ''), model=e('LLM_MODEL', ''), base_url=e('LLM_BASE_URL', ''),
        tz=e('TZ_NAME', 'Asia/Tehran'), daily_at=e('DAILY_REPORT_AT', '20:00'),
        weekly_day=int(e('WEEKLY_REPORT_DAY', '4')), weekly_at=e('WEEKLY_REPORT_AT', '18:00'),
        wip_cap=int(e('WIP_CAP', '0') or 0), db_path=e('DB_PATH', 'data/agent.db'),
    )
