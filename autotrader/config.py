"""봇 설정 기본값 / 저장 / 불러오기."""
import copy
import json
import os

DEFAULT_CONFIG = {
    "mode": "paper",            # paper(모의) | live(실전)
    "loop_seconds": 10,         # 한 사이클 주기(초)
    "paper_krw": 1_000_000,     # 모의투자 시작 원화
    "universe": {               # 매수 후보 종목 필터 (현재가 API 기준, 전일대비 = 09:00 KST 기준)
        "rank_by": "거래대금",   # 거래대금 | 상승률 | 하락률 | 없음
        "top_n": 20,            # 순위 N위 이내 (최대 스캔 종목 수 역할도 함)
        "change_min": -100.0,   # 전일대비 최소 %
        "change_max": 100.0,    # 전일대비 최대 %
        "price_min": 0,         # 현재가 최소 (0=제한 없음)
        "price_max": 0,         # 현재가 최대 (0=제한 없음)
        "min_value_24h": 0,     # 24시간 거래대금 최소 (억원)
        "exclude_warning": True,  # 투자유의 종목 제외
        "exclude_caution": False,  # 투자주의(급등락 등) 종목 제외
        "include": [],          # 이 종목만 대상 (비우면 전체)
        "exclude": [],          # 제외 종목
    },
    "buy": {
        "interval": "15분",
        "mode": "AND",          # AND: 모두 만족 / OR: 하나라도 만족
        "use_closed_candle": False,  # True면 직전 완성봉 기준으로 판단
        "rules": [
            {"key": "rsi_below", "params": {"period": 14, "level": 30}},
            {"key": "bb_lower_touch", "params": {"period": 20, "k": 2.0, "price": "저가"}},
        ],
    },
    "sell": {
        "interval": "15분",
        "mode": "OR",
        "use_closed_candle": False,
        "rules": [
            {"key": "rsi_above", "params": {"period": 14, "level": 70}},
        ],
        "indicator_min_profit_pct": None,  # 지표 매도는 수익률이 이 값 이상일 때만 (None=항상)
        "take_profit_pct": 3.0,     # 익절 % (0=사용 안 함)
        "stop_loss_pct": 2.0,       # 손절 % (0=사용 안 함)
        "trailing_enabled": False,  # 트레일링 스탑
        "trailing_activate_pct": 2.0,  # 이 수익률 도달 후 작동
        "trailing_drop_pct": 1.0,      # 최고가 대비 이만큼 하락하면 매도
        "max_hold_minutes": 0,      # 최대 보유 시간 (0=제한 없음)
        "exit_time": "",            # 매일 이 시각(HH:MM)에 이전 보유분 일괄 청산 ("": 사용 안 함)
    },
    "money": {
        "order_type": "fixed",      # fixed(고정 금액) | percent(가용 원화 %)
        "order_krw": 10000,         # 1회 매수 금액
        "order_pct": 10.0,          # 가용 원화 대비 %
        "max_positions": 3,         # 동시 보유 최대 종목 수
        "cooldown_minutes": 30,     # 매도 후 같은 종목 재매수 금지 시간
        "max_buys_per_day": 20,     # 하루 최대 매수 횟수 (0=무제한)
        "daily_loss_limit_krw": 0,  # 하루 실현손실이 이 금액 넘으면 당일 매수 중단 (0=무제한)
        "krw_reserve": 0,           # 항상 남겨둘 원화
        "buy_start": "00:00",       # 매수 허용 시작 시각
        "buy_end": "23:59",         # 매수 허용 종료 시각
    },
}

MIN_ORDER_KRW = 5000   # 업비트 원화마켓 최소 주문금액
FEE_RATE = 0.0005      # 업비트 원화마켓 수수료 0.05%

CONFIG_PATH = os.environ.get("BOT_CONFIG", "bot_config.json")
DATA_DIR = os.environ.get("BOT_DATA_DIR", "bot_data")


def _merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str = CONFIG_PATH) -> dict:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return _merge(DEFAULT_CONFIG, json.load(f))
    return copy.deepcopy(DEFAULT_CONFIG)


def save_config(cfg: dict, path: str = CONFIG_PATH):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def load_keys():
    """API 키: 환경변수 → .streamlit/secrets.toml 순서로 찾음. 설정 파일에는 저장하지 않는다."""
    ak = os.environ.get("UPBIT_ACCESS_KEY", "")
    sk = os.environ.get("UPBIT_SECRET_KEY", "")
    if ak and sk:
        return ak, sk
    try:
        import streamlit as st
        return st.secrets.get("UPBIT_ACCESS_KEY", ""), st.secrets.get("UPBIT_SECRET_KEY", "")
    except Exception:
        return "", ""
