"""자동매매 엔진: 종목 필터 → 매도 점검 → 매수 점검을 주기적으로 반복."""
import csv
import json
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime, timedelta

from .api import UpbitAPI, UpbitError
from .conditions import evaluate_rules, explain_rules
from .config import DATA_DIR, FEE_RATE, MIN_ORDER_KRW


def now():
    return datetime.now()


def parse_hhmm(s):
    h, m = s.strip().split(":")
    return int(h), int(m)


def in_time_window(t: datetime, start: str, end: str) -> bool:
    s = parse_hhmm(start)
    e = parse_hhmm(end)
    cur = (t.hour, t.minute)
    if s <= e:
        return s <= cur <= e
    return cur >= s or cur <= e  # 자정을 넘기는 구간 (예: 22:00~02:00)


def filter_universe(u: dict, markets: list, tickers: dict) -> list:
    """현재가(ticker) 기준 종목 필터 + 순위 정렬."""
    include = {m.strip().upper() for m in u.get("include", []) if m.strip()}
    exclude = {m.strip().upper() for m in u.get("exclude", []) if m.strip()}
    rows = []
    for m in markets:
        code = m["market"]
        t = tickers.get(code)
        if not t:
            continue
        if include and code not in include:
            continue
        if code in exclude:
            continue
        if u.get("exclude_warning") and m["warning"]:
            continue
        if u.get("exclude_caution") and m["caution"]:
            continue
        price = float(t["trade_price"])
        chg = float(t["signed_change_rate"]) * 100
        value24 = float(t["acc_trade_price_24h"])
        if not (u["change_min"] <= chg <= u["change_max"]):
            continue
        if u.get("price_min") and price < u["price_min"]:
            continue
        if u.get("price_max") and price > u["price_max"]:
            continue
        if u.get("min_value_24h") and value24 < u["min_value_24h"] * 1e8:
            continue
        rows.append({"market": code, "name": m["korean_name"], "price": price,
                     "change": chg, "value24": value24})
    rank_by = u.get("rank_by", "거래대금")
    if rank_by == "거래대금":
        rows.sort(key=lambda r: r["value24"], reverse=True)
    elif rank_by == "상승률":
        rows.sort(key=lambda r: r["change"], reverse=True)
    elif rank_by == "하락률":
        rows.sort(key=lambda r: r["change"])
    top_n = int(u.get("top_n") or 0)
    return rows[:top_n] if top_n > 0 else rows


# ---------------------------------------------------------------- brokers
class PaperBroker:
    """모의투자: 현재가로 즉시 체결된 것으로 가정, 수수료 반영."""
    name = "paper"

    def __init__(self, state):
        self.state = state

    def krw(self):
        return self.state["paper_krw"]

    def buy(self, market, krw_amount, price):
        fee = krw_amount * FEE_RATE
        volume = (krw_amount - fee) / price
        self.state["paper_krw"] -= krw_amount
        return {"volume": volume, "avg_price": price, "funds": krw_amount - fee, "fee": fee}

    def sell(self, market, volume, price):
        gross = volume * price
        fee = gross * FEE_RATE
        self.state["paper_krw"] += gross - fee
        return {"volume": volume, "avg_price": price, "funds": gross, "fee": fee}

    def coin_balance(self, market):
        return None


class LiveBroker:
    """실전: 업비트 시장가 주문."""
    name = "live"

    def __init__(self, api: UpbitAPI):
        self.api = api
        self._accounts = None

    def refresh(self):
        self._accounts = {a["currency"]: a for a in self.api.get_accounts()}

    def krw(self):
        if self._accounts is None:
            self.refresh()
        a = self._accounts.get("KRW")
        return float(a["balance"]) if a else 0.0

    def coin_balance(self, market):
        if self._accounts is None:
            self.refresh()
        a = self._accounts.get(market.split("-")[1])
        return float(a["balance"]) if a else 0.0

    def buy(self, market, krw_amount, price):
        res = self.api.buy_market(market, krw_amount)
        fill = self.api.wait_order_filled(res["uuid"])
        self._accounts = None
        if fill["volume"] <= 0:
            raise UpbitError("FILL", f"{market} 매수 체결 확인 실패: {fill['raw']}")
        return fill

    def sell(self, market, volume, price):
        res = self.api.sell_market(market, volume)
        fill = self.api.wait_order_filled(res["uuid"])
        self._accounts = None
        if fill["volume"] <= 0:
            raise UpbitError("FILL", f"{market} 매도 체결 확인 실패: {fill['raw']}")
        return fill


# ----------------------------------------------------------------- engine
class TradingEngine:
    def __init__(self, config: dict, api: UpbitAPI, log_size=500):
        self.cfg = config
        self.api = api
        self.mode = config.get("mode", "paper")
        os.makedirs(DATA_DIR, exist_ok=True)
        self.state_path = os.path.join(DATA_DIR, f"state_{self.mode}.json")
        self.trades_path = os.path.join(DATA_DIR, f"trades_{self.mode}.csv")
        self.log_path = os.path.join(DATA_DIR, f"bot_{self.mode}.log")
        self.logs = deque(maxlen=log_size)
        self.lock = threading.Lock()
        self.trade_lock = threading.RLock()  # 봇 스레드와 화면(전량매도)이 동시에 주문하지 않도록
        self.last_cycle = None
        self.last_candidates = []
        self._markets_cache = (0, [])
        self._candle_cache = {}

        self.logger = logging.getLogger(f"autotrader.{self.mode}")
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            fh = logging.FileHandler(self.log_path, encoding="utf-8")
            fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            self.logger.addHandler(fh)

        self.state = self._load_state()
        if self.mode == "live":
            if not api.has_keys:
                raise UpbitError("NO_KEY", "실전 모드는 API 키가 필요합니다.")
            self.broker = LiveBroker(api)
        else:
            self.broker = PaperBroker(self.state)

    # ------------------------------------------------------------ helpers
    def log(self, msg):
        line = f"{now():%m-%d %H:%M:%S} {msg}"
        self.logs.append(line)
        self.logger.info(msg)
        print(line, flush=True)

    def _load_state(self):
        if os.path.exists(self.state_path):
            with open(self.state_path, encoding="utf-8") as f:
                st = json.load(f)
        else:
            st = {}
        st.setdefault("positions", {})
        st.setdefault("cooldowns", {})
        st.setdefault("daily", {"date": "", "buys": 0, "realized": 0.0})
        st.setdefault("paper_krw", float(self.cfg.get("paper_krw", 1_000_000)))
        return st

    def save_state(self):
        with self.lock:
            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.state_path)

    def _record_trade(self, row: dict):
        new = not os.path.exists(self.trades_path)
        with open(self.trades_path, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(row.keys()))
            if new:
                w.writeheader()
            w.writerow(row)

    def _daily(self):
        today = now().strftime("%Y-%m-%d")
        d = self.state["daily"]
        if d.get("date") != today:
            d.update({"date": today, "buys": 0, "realized": 0.0})
        return d

    def _candles(self, market, interval):
        key = (market, interval)
        if key not in self._candle_cache:
            self._candle_cache[key] = self.api.get_candles(market, interval, 200)
        return self._candle_cache[key]

    def _markets(self):
        ts, data = self._markets_cache
        if time.time() - ts > 600 or not data:
            data = self.api.get_krw_markets()
            self._markets_cache = (time.time(), data)
        return data

    # ------------------------------------------------------- universe
    def select_universe(self, tickers: dict):
        return filter_universe(self.cfg["universe"], self._markets(), tickers)

    # ------------------------------------------------------------ cycle
    def run_cycle(self):
        with self.trade_lock:
            self._run_cycle()

    def _run_cycle(self):
        self._candle_cache = {}
        markets = [m["market"] for m in self._markets()]
        tickers = {t["market"]: t for t in self.api.get_tickers(markets)}
        if isinstance(self.broker, LiveBroker):
            self.broker.refresh()
            self._sync_live_positions()
        self._check_sells(tickers)
        self._check_buys(tickers)
        self.last_cycle = now()
        self.save_state()

    def _sync_live_positions(self):
        """사용자가 직접 매도한 경우 등 실제 잔고가 봇 기록보다 적으면 맞춘다."""
        for market, pos in list(self.state["positions"].items()):
            bal = self.broker.coin_balance(market)
            if bal is None:
                continue
            if bal * pos["avg_price"] < MIN_ORDER_KRW * 0.5:
                self.log(f"[동기화] {market} 실제 잔고 없음 → 봇 포지션 삭제")
                del self.state["positions"][market]
            elif bal < pos["volume"]:
                pos["volume"] = bal

    # ------------------------------------------------------------- sell
    def _sell_reason(self, market, pos, price):
        s = self.cfg["sell"]
        pnl = (price / pos["avg_price"] - 1) * 100
        pos["peak_price"] = max(pos.get("peak_price", pos["avg_price"]), price)
        if s.get("stop_loss_pct") and pnl <= -abs(s["stop_loss_pct"]):
            return f"손절 {pnl:.2f}%"
        if s.get("take_profit_pct") and pnl >= s["take_profit_pct"]:
            return f"익절 {pnl:.2f}%"
        if s.get("trailing_enabled"):
            peak_pnl = (pos["peak_price"] / pos["avg_price"] - 1) * 100
            drop = (1 - price / pos["peak_price"]) * 100
            if peak_pnl >= s["trailing_activate_pct"] and drop >= s["trailing_drop_pct"]:
                return f"트레일링 (최고 {peak_pnl:.2f}% → 현재 {pnl:.2f}%)"
        entry = datetime.fromisoformat(pos["entry_time"])
        if s.get("max_hold_minutes") and now() - entry >= timedelta(minutes=s["max_hold_minutes"]):
            return f"보유시간 초과 ({s['max_hold_minutes']}분)"
        if s.get("exit_time"):
            h, m = parse_hhmm(s["exit_time"])
            cut = now().replace(hour=h, minute=m, second=0, microsecond=0)
            if entry < cut <= now():
                return f"정해진 시각 청산 ({s['exit_time']})"
        if s.get("rules"):
            min_p = s.get("indicator_min_profit_pct")
            if min_p is None or pnl >= min_p:
                df = self._candles(market, s["interval"])
                if len(df) >= 3:
                    if s.get("use_closed_candle"):
                        df = df.iloc[:-1]
                    sig = evaluate_rules(df, s["rules"], s.get("mode", "OR"))
                    if bool(sig.iloc[-1]):
                        hit = [lbl for lbl, ok in explain_rules(df, s["rules"]) if ok]
                        return f"지표 매도 ({', '.join(hit)}) {pnl:.2f}%"
        return None

    def _check_sells(self, tickers):
        for market, pos in list(self.state["positions"].items()):
            t = tickers.get(market)
            if not t:
                continue
            price = float(t["trade_price"])
            try:
                reason = self._sell_reason(market, pos, price)
            except Exception as e:
                self.log(f"[오류] {market} 매도 판단 실패: {e}")
                continue
            if reason:
                self.sell(market, price, reason)

    def sell(self, market, price, reason):
        pos = self.state["positions"].get(market)
        if not pos:
            return
        volume = pos["volume"]
        bal = self.broker.coin_balance(market)
        if bal is not None:
            volume = min(volume, bal)
        if volume * price < MIN_ORDER_KRW:
            self.log(f"[매도 불가] {market} 평가금액이 최소주문금액({MIN_ORDER_KRW}원) 미만 → 포지션 정리")
            del self.state["positions"][market]
            return
        try:
            fill = self.broker.sell(market, volume, price)
        except Exception as e:
            self.log(f"[오류] {market} 매도 실패: {e}")
            return
        proceeds = fill["funds"] - fill["fee"]
        cost = pos["invested"] * (fill["volume"] / pos["volume"])
        pnl = proceeds - cost
        pnl_pct = pnl / cost * 100 if cost else 0.0
        self._daily()["realized"] += pnl
        remaining = pos["volume"] - fill["volume"]
        if remaining * price < MIN_ORDER_KRW:
            del self.state["positions"][market]
        else:
            pos["volume"] = remaining
            pos["invested"] -= cost
        self.state["cooldowns"][market] = now().isoformat()
        self.log(f"🔵 매도 {market} {fill['volume']:.8g}개 @ {fill['avg_price']:,.2f} | {reason} | 손익 {pnl:+,.0f}원 ({pnl_pct:+.2f}%)")
        self._record_trade({
            "time": now().isoformat(timespec="seconds"), "side": "매도", "market": market,
            "price": round(fill["avg_price"], 8), "volume": round(fill["volume"], 8),
            "amount": round(proceeds), "fee": round(fill["fee"], 2),
            "pnl": round(pnl), "pnl_pct": round(pnl_pct, 2), "reason": reason,
        })
        self.save_state()

    # -------------------------------------------------------------- buy
    def _order_amount(self, krw_available):
        m = self.cfg["money"]
        usable = krw_available - float(m.get("krw_reserve") or 0)
        if m.get("order_type") == "percent":
            amt = usable * float(m["order_pct"]) / 100
        else:
            amt = float(m["order_krw"])
        amt = min(amt, usable / (1 + FEE_RATE))  # 수수료 여유
        return int(amt)

    def _check_buys(self, tickers):
        m = self.cfg["money"]
        b = self.cfg["buy"]
        daily = self._daily()
        if not in_time_window(now(), m.get("buy_start", "00:00"), m.get("buy_end", "23:59")):
            return
        if m.get("max_buys_per_day") and daily["buys"] >= m["max_buys_per_day"]:
            return
        if m.get("daily_loss_limit_krw") and daily["realized"] <= -abs(m["daily_loss_limit_krw"]):
            return
        slots = int(m["max_positions"]) - len(self.state["positions"])
        if slots <= 0:
            return

        candidates = self.select_universe(tickers)
        self.last_candidates = candidates
        cooldown = timedelta(minutes=float(m.get("cooldown_minutes") or 0))
        for c in candidates:
            if slots <= 0:
                break
            market = c["market"]
            if market in self.state["positions"]:
                continue
            last = self.state["cooldowns"].get(market)
            if last and now() - datetime.fromisoformat(last) < cooldown:
                continue
            reason = "필터 조건만 사용"
            if b.get("rules"):
                try:
                    df = self._candles(market, b["interval"])
                except Exception as e:
                    self.log(f"[오류] {market} 캔들 조회 실패: {e}")
                    continue
                if len(df) < 3:
                    continue
                if b.get("use_closed_candle"):
                    df = df.iloc[:-1]
                sig = evaluate_rules(df, b["rules"], b.get("mode", "AND"))
                if not bool(sig.iloc[-1]):
                    continue
                reason = ", ".join(lbl for lbl, ok in explain_rules(df, b["rules"]) if ok)

            krw = self.broker.krw()
            amount = self._order_amount(krw)
            if amount < MIN_ORDER_KRW:
                self.log(f"[매수 보류] 주문 가능 금액 부족 (가용 {krw:,.0f}원, 주문액 {amount:,}원)")
                return
            if self.buy(market, amount, c["price"], reason):
                slots -= 1
                daily["buys"] += 1
                if m.get("max_buys_per_day") and daily["buys"] >= m["max_buys_per_day"]:
                    return

    def buy(self, market, amount, price, reason):
        try:
            fill = self.broker.buy(market, amount, price)
        except Exception as e:
            self.log(f"[오류] {market} 매수 실패: {e}")
            return False
        invested = fill["funds"] + fill["fee"]
        self.state["positions"][market] = {
            "volume": fill["volume"],
            "avg_price": fill["avg_price"],
            "invested": invested,
            "peak_price": fill["avg_price"],
            "entry_time": now().isoformat(timespec="seconds"),
            "reason": reason,
        }
        self.log(f"🔴 매수 {market} {fill['volume']:.8g}개 @ {fill['avg_price']:,.2f} ({invested:,.0f}원) | {reason}")
        self._record_trade({
            "time": now().isoformat(timespec="seconds"), "side": "매수", "market": market,
            "price": round(fill["avg_price"], 8), "volume": round(fill["volume"], 8),
            "amount": round(invested), "fee": round(fill["fee"], 2),
            "pnl": "", "pnl_pct": "", "reason": reason,
        })
        self.save_state()
        return True

    def sell_all(self, reason="수동 전량 매도"):
        with self.trade_lock:
            self._sell_all(reason)

    def _sell_all(self, reason):
        markets = list(self.state["positions"].keys())
        if not markets:
            return
        tickers = {t["market"]: t for t in self.api.get_tickers(markets)}
        if isinstance(self.broker, LiveBroker):
            self.broker.refresh()
        for market in markets:
            t = tickers.get(market)
            if t:
                self.sell(market, float(t["trade_price"]), reason)

    # -------------------------------------------------------------- loop
    def run(self, stop_event: threading.Event):
        self.log(f"▶ 봇 시작 ({'실전' if self.mode == 'live' else '모의'} 모드, {self.cfg['loop_seconds']}초 주기)")
        while not stop_event.is_set():
            started = time.time()
            try:
                self.run_cycle()
            except Exception as e:
                self.log(f"[오류] 사이클 실패: {e}")
            wait = max(1.0, float(self.cfg["loop_seconds"]) - (time.time() - started))
            stop_event.wait(wait)
        self.save_state()
        self.log("■ 봇 정지")
