"""업비트 자동매매 - 설정/실행 화면.

실행: streamlit run trader_app.py
"""
import copy
import os
import threading

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from autotrader.api import INTERVALS, UpbitAPI
from autotrader.backtest import run_backtest
from autotrader.conditions import GROUP_ORDER, REGISTRY, evaluate_rules, explain_rules
from autotrader.config import DATA_DIR, MIN_ORDER_KRW, load_config, load_keys, save_config
from autotrader.engine import TradingEngine, filter_universe

st.set_page_config(page_title="업비트 자동매매", page_icon="🤖", layout="wide")


# ------------------------------------------------------------ singletons
class BotRunner:
    def __init__(self):
        self.thread = None
        self.stop_event = None
        self.engine = None

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, cfg, api):
        if self.running:
            return
        self.engine = TradingEngine(copy.deepcopy(cfg), api)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.engine.run, args=(self.stop_event,), daemon=True)
        self.thread.start()

    def stop(self):
        if self.stop_event:
            self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=15)


@st.cache_resource
def get_runner():
    return BotRunner()


@st.cache_resource
def get_api(ak, sk):
    return UpbitAPI(ak, sk)


access_key, secret_key = load_keys()
api = get_api(access_key, secret_key)
runner = get_runner()

if "cfg" not in st.session_state:
    st.session_state.cfg = load_config()
base = st.session_state.cfg
cfg = copy.deepcopy(base)


@st.cache_data(ttl=5, show_spinner=False)
def cached_tickers(markets: tuple):
    return {t["market"]: t for t in api.get_tickers(list(markets))}


@st.cache_data(ttl=600, show_spinner=False)
def cached_markets():
    return api.get_krw_markets()


# ------------------------------------------------------------ rule editor
def num_input(label, p, value, key):
    if p.kind == "int":
        return int(st.number_input(label, min_value=int(p.min) if p.min is not None else None,
                                   max_value=int(p.max) if p.max is not None else None,
                                   value=int(value), step=int(p.step or 1), key=key))
    return float(st.number_input(label, min_value=float(p.min) if p.min is not None else None,
                                 max_value=float(p.max) if p.max is not None else None,
                                 value=float(value), step=float(p.step or 0.1), key=key, format="%.2f"))


def rule_editor(side, rules):
    keys = sorted(REGISTRY, key=lambda k: (GROUP_ORDER.index(REGISTRY[k].group), list(REGISTRY).index(k)))
    current = {r["key"]: r.get("params", {}) for r in rules if r["key"] in REGISTRY}
    selected = st.multiselect(
        "사용할 지표 조건 (여러 개 선택 가능)",
        options=keys,
        default=[k for k in keys if k in current],
        format_func=lambda k: f"[{REGISTRY[k].group}] {REGISTRY[k].label}",
        key=f"{side}_rules_select",
    )
    new_rules = []
    for k in selected:
        cond = REGISTRY[k]
        vals = cond.defaults()
        vals.update(current.get(k, {}))
        with st.expander(f"⚙️ {cond.label}", expanded=False):
            st.caption(cond.description)
            cols = st.columns(min(len(cond.params), 4) or 1)
            params = {}
            for i, p in enumerate(cond.params):
                with cols[i % len(cols)]:
                    wkey = f"{side}_{k}_{p.name}"
                    if p.kind == "choice":
                        idx = p.options.index(vals[p.name]) if vals[p.name] in p.options else 0
                        params[p.name] = st.selectbox(p.label, p.options, index=idx, key=wkey)
                    else:
                        params[p.name] = num_input(p.label, p, vals[p.name], wkey)
        new_rules.append({"key": k, "params": params})
    return new_rules


def time_str(label, value, key):
    v = st.text_input(label, value=value, key=key, help="HH:MM 형식 (24시간)")
    try:
        h, m = v.split(":")
        assert 0 <= int(h) <= 23 and 0 <= int(m) <= 59
        return f"{int(h):02d}:{int(m):02d}"
    except Exception:
        st.error(f"'{v}' 형식이 올바르지 않습니다. 예: 09:00")
        return value


# ================================================================ sidebar
with st.sidebar:
    st.header("🤖 업비트 자동매매")
    mode_label = st.radio("매매 모드", ["모의투자", "실전투자"], index=0 if base["mode"] == "paper" else 1,
                          horizontal=True, disabled=runner.running)
    cfg["mode"] = "paper" if mode_label == "모의투자" else "live"
    if cfg["mode"] == "paper":
        cfg["paper_krw"] = st.number_input("모의투자 시작 원화", min_value=10000, value=int(base["paper_krw"]),
                                           step=100000, disabled=runner.running,
                                           help="모의 잔고가 없을 때(처음/초기화 후)만 적용")
    cfg["loop_seconds"] = st.number_input("점검 주기(초)", min_value=3, max_value=3600,
                                          value=int(base["loop_seconds"]), step=1)

    st.divider()
    if api.has_keys:
        st.success("API 키 설정됨")
    else:
        st.warning("API 키 없음 (모의투자만 가능)")
        st.caption("환경변수 UPBIT_ACCESS_KEY / UPBIT_SECRET_KEY 또는 .streamlit/secrets.toml 에 설정")

    live_ok = True
    if cfg["mode"] == "live":
        st.error("⚠️ 실전 모드: 실제 돈으로 주문됩니다.")
        live_ok = api.has_keys and st.checkbox("손실 위험을 이해했고 실전 매매를 시작합니다", key="live_confirm")

    c1, c2 = st.columns(2)
    # 저장/시작은 아래 탭들의 입력값까지 모두 읽은 뒤 스크립트 끝에서 처리한다
    save_clicked = c1.button("💾 설정 저장", width="stretch")
    if c2.button("↩️ 기본값", width="stretch", disabled=runner.running):
        from autotrader.config import DEFAULT_CONFIG
        st.session_state.cfg = copy.deepcopy(DEFAULT_CONFIG)
        for k in list(st.session_state.keys()):
            if k not in ("cfg",):
                del st.session_state[k]
        st.rerun()

    st.divider()
    start_clicked = False
    if runner.running:
        st.success(f"● 실행 중 ({'실전' if runner.engine.mode == 'live' else '모의'})")
        if runner.engine.last_cycle:
            st.caption(f"마지막 점검: {runner.engine.last_cycle:%H:%M:%S}")
        if st.button("■ 봇 정지", type="primary", width="stretch"):
            runner.stop()
            st.rerun()
        if st.button("🧯 보유 종목 전량 매도", width="stretch"):
            runner.engine.sell_all()
            st.toast("전량 매도 요청 완료")
    else:
        st.info("○ 정지됨")
        start_clicked = st.button("▶ 봇 시작", type="primary", width="stretch", disabled=not live_ok)
    if st.session_state.pop("flash", None):
        st.success(st.session_state.pop("flash_msg", "완료"))
    if st.session_state.get("start_error"):
        st.error(st.session_state.pop("start_error"))


# ================================================================== tabs
st.title("🤖 업비트 자동매매")
tab_u, tab_b, tab_s, tab_m, tab_run, tab_bt = st.tabs(
    ["🔎 종목 필터", "🟥 매수 조건", "🟦 매도 조건", "💰 자금 관리", "📊 실행 현황", "🧪 백테스트"])

# ------------------------------------------------------------- universe
with tab_u:
    u = cfg["universe"]
    ub = base["universe"]
    st.caption("매수 후보를 먼저 이 필터로 추린 뒤, 매수 조건(지표)을 검사합니다. 전일대비는 업비트 기준(매일 09:00 KST 초기화).")
    c1, c2 = st.columns(2)
    rank_opts = ["거래대금", "상승률", "하락률", "없음"]
    u["rank_by"] = c1.selectbox("순위 기준 (24시간 거래대금 / 전일대비 등락률)", rank_opts, index=rank_opts.index(ub["rank_by"]))
    u["top_n"] = c2.number_input("순위 N위 이내 (0=전체)", min_value=0, max_value=300, value=int(ub["top_n"]), step=1)
    c1, c2 = st.columns(2)
    u["change_min"] = c1.number_input("전일대비 최소 %", value=float(ub["change_min"]), step=0.5, format="%.2f")
    u["change_max"] = c2.number_input("전일대비 최대 %", value=float(ub["change_max"]), step=0.5, format="%.2f")
    c1, c2, c3 = st.columns(3)
    u["price_min"] = c1.number_input("현재가 최소 (0=제한없음)", min_value=0.0, value=float(ub["price_min"]), step=10.0)
    u["price_max"] = c2.number_input("현재가 최대 (0=제한없음)", min_value=0.0, value=float(ub["price_max"]), step=10.0)
    u["min_value_24h"] = c3.number_input("24h 거래대금 최소 (억원)", min_value=0.0, value=float(ub["min_value_24h"]), step=10.0)
    c1, c2 = st.columns(2)
    u["exclude_warning"] = c1.checkbox("투자유의 종목 제외", value=ub["exclude_warning"])
    u["exclude_caution"] = c2.checkbox("투자주의 종목 제외 (가격 급등락·거래량 급등 등)", value=ub["exclude_caution"])
    u["include"] = [x.strip().upper() for x in st.text_input(
        "이 종목만 대상 (쉼표로 구분, 비우면 전체) 예: KRW-BTC,KRW-ETH", ",".join(ub["include"])).split(",") if x.strip()]
    u["exclude"] = [x.strip().upper() for x in st.text_input(
        "제외 종목 (쉼표로 구분) 예: KRW-USDT", ",".join(ub["exclude"])).split(",") if x.strip()]
    st.caption("※ 거래'량'(코인 개수)은 코인마다 단가가 달라 순위 비교가 의미 없어서, 업비트 앱처럼 거래'대금'(원화) 기준으로 순위를 매깁니다.")

    if st.button("🔍 지금 필터 결과 보기"):
        try:
            mk = cached_markets()
            rows = filter_universe(u, mk, cached_tickers(tuple(m["market"] for m in mk)))
            df = pd.DataFrame(rows)
            if df.empty:
                st.warning("조건에 맞는 종목이 없습니다.")
            else:
                df["value24"] = (df["value24"] / 1e8).round(1)
                df["change"] = df["change"].round(2)
                st.dataframe(df.rename(columns={"market": "마켓", "name": "이름", "price": "현재가",
                                                "change": "전일대비%", "value24": "24h 거래대금(억)"}),
                             width="stretch", hide_index=True)
        except Exception as e:
            st.error(f"조회 실패: {e}")

# ------------------------------------------------------------------ buy
with tab_b:
    b = cfg["buy"]
    bb = base["buy"]
    c1, c2, c3 = st.columns(3)
    ivs = list(INTERVALS)
    b["interval"] = c1.selectbox("캔들 단위 (매수 판단)", ivs, index=ivs.index(bb["interval"]), key="buy_iv")
    b["mode"] = c2.radio("조건 결합", ["AND", "OR"], index=0 if bb["mode"] == "AND" else 1, horizontal=True,
                         key="buy_mode", help="AND: 선택한 조건을 모두 만족 / OR: 하나라도 만족")
    b["use_closed_candle"] = c3.checkbox("완성된 봉 기준으로 판단", value=bb["use_closed_candle"], key="buy_closed",
                                         help="체크 시 진행 중인 봉 대신 직전 완성봉으로 판단 (신호가 깜빡이는 것 방지, 대신 반응이 늦음)")
    b["rules"] = rule_editor("buy", bb["rules"])
    if not b["rules"]:
        st.warning("지표 조건이 없으면 '종목 필터'를 통과한 종목을 순위대로 바로 매수합니다.")

    if st.button("🔍 지금 매수 조건 충족 종목 스캔"):
        with st.spinner("스캔 중..."):
            try:
                mk = cached_markets()
                rows = filter_universe(cfg["universe"], mk, cached_tickers(tuple(m["market"] for m in mk)))
                out = []
                for r in rows:
                    df = api.get_candles(r["market"], b["interval"], 200)
                    if b["use_closed_candle"]:
                        df = df.iloc[:-1]
                    detail = explain_rules(df, b["rules"])
                    hit = bool(evaluate_rules(df, b["rules"], b["mode"]).iloc[-1]) if b["rules"] else True
                    row = {"마켓": r["market"], "이름": r["name"], "현재가": r["price"], "매수신호": "✅" if hit else ""}
                    row.update({lbl: "✅" if ok else "❌" for lbl, ok in detail})
                    out.append(row)
                st.dataframe(pd.DataFrame(out), width="stretch", hide_index=True)
            except Exception as e:
                st.error(f"스캔 실패: {e}")

# ----------------------------------------------------------------- sell
with tab_s:
    s = cfg["sell"]
    sb = base["sell"]
    st.subheader("① 수익률/시간 기준 (하나라도 해당되면 매도)")
    c1, c2 = st.columns(2)
    s["take_profit_pct"] = c1.number_input("익절 % (0=사용 안 함)", min_value=0.0, value=float(sb["take_profit_pct"]), step=0.5)
    s["stop_loss_pct"] = c2.number_input("손절 % (0=사용 안 함)", min_value=0.0, value=float(sb["stop_loss_pct"]), step=0.5)
    s["trailing_enabled"] = st.checkbox("트레일링 스탑 사용", value=sb["trailing_enabled"],
                                        help="수익이 일정 % 이상 났다가, 최고가 대비 일정 % 하락하면 매도")
    if s["trailing_enabled"]:
        c1, c2 = st.columns(2)
        s["trailing_activate_pct"] = c1.number_input("작동 시작 수익률 %", min_value=0.0, value=float(sb["trailing_activate_pct"]), step=0.5)
        s["trailing_drop_pct"] = c2.number_input("최고가 대비 하락 %", min_value=0.1, value=float(sb["trailing_drop_pct"]), step=0.1)
    c1, c2 = st.columns(2)
    s["max_hold_minutes"] = c1.number_input("최대 보유 시간(분, 0=제한없음)", min_value=0, value=int(sb["max_hold_minutes"]), step=10)
    use_exit = c2.checkbox("매일 정해진 시각에 청산", value=bool(sb["exit_time"]),
                           help="변동성 돌파 전략처럼 다음날 아침에 파는 방식 (예: 08:50)")
    s["exit_time"] = time_str("청산 시각", sb["exit_time"] or "08:50", "exit_time") if use_exit else ""

    st.subheader("② 지표 기준")
    c1, c2, c3 = st.columns(3)
    s["interval"] = c1.selectbox("캔들 단위 (매도 판단)", ivs, index=ivs.index(sb["interval"]), key="sell_iv")
    s["mode"] = c2.radio("조건 결합", ["AND", "OR"], index=0 if sb["mode"] == "AND" else 1, horizontal=True, key="sell_mode")
    s["use_closed_candle"] = c3.checkbox("완성된 봉 기준으로 판단", value=sb["use_closed_candle"], key="sell_closed")
    s["rules"] = rule_editor("sell", sb["rules"])
    use_min = st.checkbox("지표 매도는 수익률이 일정 % 이상일 때만", value=sb["indicator_min_profit_pct"] is not None)
    s["indicator_min_profit_pct"] = st.number_input(
        "최소 수익률 %", value=float(sb["indicator_min_profit_pct"] or 0.5), step=0.1) if use_min else None
    if not (s["take_profit_pct"] or s["stop_loss_pct"] or s["trailing_enabled"] or s["max_hold_minutes"] or s["exit_time"] or s["rules"]):
        st.error("매도 조건이 하나도 없습니다. 매수 후 팔지 않게 됩니다.")

# ---------------------------------------------------------------- money
with tab_m:
    m = cfg["money"]
    mb = base["money"]
    c1, c2 = st.columns(2)
    ot = c1.radio("1회 매수 금액 방식", ["고정 금액", "가용 원화 %"], index=0 if mb["order_type"] == "fixed" else 1, horizontal=True)
    m["order_type"] = "fixed" if ot == "고정 금액" else "percent"
    if m["order_type"] == "fixed":
        m["order_krw"] = c2.number_input("1회 매수 금액(원)", min_value=MIN_ORDER_KRW, value=int(mb["order_krw"]), step=5000)
    else:
        m["order_pct"] = c2.number_input("가용 원화 대비 %", min_value=1.0, max_value=100.0, value=float(mb["order_pct"]), step=1.0)
    c1, c2, c3 = st.columns(3)
    m["max_positions"] = c1.number_input("동시 보유 최대 종목 수", min_value=1, max_value=50, value=int(mb["max_positions"]))
    m["cooldown_minutes"] = c2.number_input("같은 종목 재매수 대기(분)", min_value=0, value=int(mb["cooldown_minutes"]), step=5)
    m["max_buys_per_day"] = c3.number_input("하루 최대 매수 횟수 (0=무제한)", min_value=0, value=int(mb["max_buys_per_day"]))
    c1, c2 = st.columns(2)
    m["daily_loss_limit_krw"] = c1.number_input("하루 손실 한도(원, 넘으면 당일 매수 중단, 0=없음)", min_value=0,
                                                value=int(mb["daily_loss_limit_krw"]), step=10000)
    m["krw_reserve"] = c2.number_input("항상 남겨둘 원화", min_value=0, value=int(mb["krw_reserve"]), step=10000)
    c1, c2 = st.columns(2)
    with c1:
        m["buy_start"] = time_str("매수 허용 시작 시각", mb["buy_start"], "buy_start")
    with c2:
        m["buy_end"] = time_str("매수 허용 종료 시각", mb["buy_end"], "buy_end")
    st.caption(f"업비트 최소 주문금액 {MIN_ORDER_KRW:,}원, 수수료 0.05% 가 반영됩니다. 봇은 자신이 매수한 수량만 매도합니다(기존 보유 코인은 건드리지 않음).")

# --------------------------------------------------------------- status
with tab_run:
    auto = st.toggle("자동 새로고침(5초)", value=runner.running)

    def render_status():
        mode = runner.engine.mode if runner.running else cfg["mode"]
        if runner.running:
            state = runner.engine.state
            logs = list(runner.engine.logs)[-200:]
        else:
            import json
            path = os.path.join(DATA_DIR, f"state_{mode}.json")
            state = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {"positions": {}}
            lp = os.path.join(DATA_DIR, f"bot_{mode}.log")
            logs = open(lp, encoding="utf-8").read().splitlines()[-200:] if os.path.exists(lp) else []

        positions = state.get("positions", {})
        prices = {}
        if positions:
            try:
                prices = {k: float(v["trade_price"]) for k, v in cached_tickers(tuple(sorted(positions))).items()}
            except Exception as e:
                st.caption(f"현재가 조회 실패: {e}")

        c1, c2, c3, c4 = st.columns(4)
        if mode == "paper":
            krw = state.get("paper_krw", cfg["paper_krw"])
        else:
            try:
                krw = runner.engine.broker.krw() if runner.running else sum(
                    float(a["balance"]) for a in api.get_accounts() if a["currency"] == "KRW")
            except Exception:
                krw = float("nan")
        eval_amt = sum(p["volume"] * prices.get(mk, p["avg_price"]) for mk, p in positions.items())
        invested = sum(p["invested"] for p in positions.values())
        c1.metric("보유 원화", f"{krw:,.0f}")
        c2.metric("코인 평가금액", f"{eval_amt:,.0f}", f"{eval_amt - invested:+,.0f}")
        daily = state.get("daily", {})
        c3.metric("오늘 실현손익", f"{daily.get('realized', 0):+,.0f}")
        c4.metric("오늘 매수 횟수", daily.get("buys", 0))

        st.subheader(f"보유 종목 ({'모의' if mode == 'paper' else '실전'})")
        if positions:
            rows = []
            for mk, p in positions.items():
                cur = prices.get(mk, p["avg_price"])
                rows.append({"마켓": mk, "수량": p["volume"], "평균단가": p["avg_price"], "현재가": cur,
                             "수익률%": round((cur / p["avg_price"] - 1) * 100, 2),
                             "매수금액": round(p["invested"]), "매수시각": p["entry_time"], "매수사유": p.get("reason", "")})
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        else:
            st.caption("보유 종목 없음")

        if runner.running and runner.engine.last_candidates:
            with st.expander(f"마지막 스캔 후보 ({len(runner.engine.last_candidates)}개)"):
                st.dataframe(pd.DataFrame(runner.engine.last_candidates), width="stretch", hide_index=True)

        st.subheader("로그")
        st.code("\n".join(reversed(logs)) or "(없음)", language=None)

        tp = os.path.join(DATA_DIR, f"trades_{mode}.csv")
        st.subheader("거래 내역")
        if os.path.exists(tp):
            tdf = pd.read_csv(tp, encoding="utf-8-sig")
            sells = tdf[tdf["side"] == "매도"]
            if len(sells):
                c1, c2, c3 = st.columns(3)
                c1.metric("누적 실현손익", f"{pd.to_numeric(sells['pnl']).sum():+,.0f}원")
                c2.metric("매도 횟수", len(sells))
                c3.metric("승률", f"{(pd.to_numeric(sells['pnl']) > 0).mean() * 100:.1f}%")
            st.dataframe(tdf.iloc[::-1], width="stretch", hide_index=True)
        else:
            st.caption("거래 내역 없음")

    if auto:
        st.fragment(run_every="5s")(render_status)()
    else:
        render_status()

    if not runner.running and cfg["mode"] == "paper":
        if st.button("🗑️ 모의투자 기록 초기화"):
            for f in ("state_paper.json", "trades_paper.csv", "bot_paper.log"):
                p = os.path.join(DATA_DIR, f)
                if os.path.exists(p):
                    os.remove(p)
            st.rerun()

# ------------------------------------------------------------- backtest
with tab_bt:
    st.caption("현재 탭들에서 설정한 매수/매도 조건을 한 종목의 과거 캔들에 적용해 봅니다. "
               "종목 필터(순위·전일대비)는 과거 재현이 안 돼서 제외되고, 매수 캔들 단위로 매도 지표도 계산합니다.")
    c1, c2, c3, c4 = st.columns(4)
    bt_market = c1.text_input("종목", "KRW-BTC").strip().upper()
    bt_iv = c2.selectbox("캔들 단위", ivs, index=ivs.index(cfg["buy"]["interval"]), key="bt_iv")
    bt_n = c3.number_input("캔들 개수", min_value=100, max_value=5000, value=1000, step=100)
    bt_slip = c4.number_input("슬리피지 %", min_value=0.0, value=0.05, step=0.01, format="%.2f")
    if cfg["sell"]["interval"] != bt_iv and cfg["sell"]["rules"]:
        st.info(f"매도 캔들 단위({cfg['sell']['interval']})가 다르지만 백테스트는 {bt_iv} 기준으로 계산합니다.")
    if st.button("▶ 백테스트 실행", type="primary"):
        if not cfg["buy"]["rules"]:
            st.error("백테스트는 매수 지표 조건이 1개 이상 필요합니다.")
        else:
            with st.spinner("캔들 불러오는 중..."):
                try:
                    df = api.get_candles_long(bt_market, bt_iv, int(bt_n))
                except Exception as e:
                    df = pd.DataFrame()
                    st.error(f"조회 실패: {e}")
            if not df.empty:
                bcfg = copy.deepcopy(cfg)
                trades, eq, stats, _, _ = run_backtest(df, bcfg, bt_slip)
                cols = st.columns(len(stats))
                for col, (k, v) in zip(cols, stats.items()):
                    col.metric(k, v if not isinstance(v, bool) else ("예" if v else "아니오"))
                st.caption(f"기간: {df['time'].iloc[0]} ~ {df['time'].iloc[-1]} ({len(df)}봉)")

                fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3], vertical_spacing=0.04)
                fig.add_trace(go.Candlestick(x=df["time"], open=df["open"], high=df["high"], low=df["low"],
                                             close=df["close"], name="가격", increasing_line_color="#e03131",
                                             decreasing_line_color="#1971c2"), row=1, col=1)
                if len(trades):
                    fig.add_trace(go.Scatter(x=trades["매수시각"], y=trades["매수가"], mode="markers", name="매수",
                                             marker=dict(symbol="triangle-up", size=11, color="#e8590c")), row=1, col=1)
                    fig.add_trace(go.Scatter(x=trades["매도시각"], y=trades["매도가"], mode="markers", name="매도",
                                             marker=dict(symbol="triangle-down", size=11, color="#5f3dc4"),
                                             text=trades["사유"]), row=1, col=1)
                fig.add_trace(go.Scatter(x=eq.index, y=(eq - 1) * 100, name="누적 수익률%",
                                         line=dict(color="#2f9e44")), row=2, col=1)
                fig.update_layout(height=650, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=30, b=10))
                st.plotly_chart(fig, width="stretch")
                if len(trades):
                    st.dataframe(trades, width="stretch", hide_index=True)
                else:
                    st.info("이 기간에는 매매가 발생하지 않았습니다.")


# ------------------------------------------------ 저장 / 시작 (모든 입력값 반영 후)
if save_clicked or start_clicked:
    save_config(cfg)
    st.session_state.cfg = copy.deepcopy(cfg)
    if runner.running and runner.engine.mode == cfg["mode"]:
        runner.engine.cfg = copy.deepcopy(cfg)  # 실행 중인 봇에 즉시 반영
    st.session_state.flash = True
    st.session_state.flash_msg = "설정을 저장했습니다." + (" 실행 중인 봇에도 반영됐습니다." if runner.running else "")
    if start_clicked:
        try:
            runner.start(cfg, api)
            st.session_state.flash_msg = "봇을 시작했습니다."
        except Exception as e:
            st.session_state.flash = False
            st.session_state.start_error = f"시작 실패: {e}"
    st.rerun()
