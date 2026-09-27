"""단일 종목 백테스트: 선택한 매수/매도 조건을 과거 캔들에 적용.

가정 (실전과 차이가 있으니 참고용):
- 지표 신호는 봉 마감(종가) 기준으로 판단하고 그 종가에 체결
- 손절/익절/트레일링은 봉 안의 고가·저가로 판단, 같은 봉에서 둘 다 닿으면 손절 우선(보수적)
- 전 자금을 한 종목에 넣고 복리로 계산, 수수료 0.05% + 슬리피지 반영
- 종목 필터(거래대금 순위, 전일대비 %)는 과거 시점 재현이 불가능해서 적용 안 함
"""
from datetime import timedelta

import pandas as pd

from .conditions import evaluate_rules
from .config import FEE_RATE
from .engine import in_time_window, parse_hhmm


def run_backtest(df: pd.DataFrame, cfg: dict, slippage_pct: float = 0.05):
    b, s, m = cfg["buy"], cfg["sell"], cfg["money"]
    buy_sig = evaluate_rules(df, b["rules"], b.get("mode", "AND")) if b.get("rules") else pd.Series(False, index=df.index)
    sell_sig = evaluate_rules(df, s["rules"], s.get("mode", "OR")) if s.get("rules") else pd.Series(False, index=df.index)
    slip = slippage_pct / 100
    fee = FEE_RATE

    tp = float(s.get("take_profit_pct") or 0)
    sl = float(s.get("stop_loss_pct") or 0)
    trail_on = bool(s.get("trailing_enabled"))
    t_act = float(s.get("trailing_activate_pct") or 0)
    t_drop = float(s.get("trailing_drop_pct") or 0)
    max_hold = float(s.get("max_hold_minutes") or 0)
    min_profit = s.get("indicator_min_profit_pct")
    exit_time = s.get("exit_time") or ""
    cooldown = timedelta(minutes=float(m.get("cooldown_minutes") or 0))

    equity = 1.0
    pos = None
    last_exit = None
    trades, curve = [], []

    def close_pos(i, price, reason):
        nonlocal equity, pos, last_exit
        exit_px = price * (1 - slip)
        ret = (exit_px * (1 - fee)) / (pos["entry"] * (1 + fee)) - 1
        equity *= 1 + ret
        trades.append({
            "매수시각": pos["time"], "매수가": pos["entry"], "매도시각": df["time"].iloc[i],
            "매도가": exit_px, "수익률%": round(ret * 100, 3), "사유": reason,
        })
        last_exit = df["time"].iloc[i]
        pos = None

    for i in range(len(df)):
        o, h, lo, c = (df[k].iloc[i] for k in ("open", "high", "low", "close"))
        t = df["time"].iloc[i]
        if pos is not None and i > pos["idx"]:
            avg = pos["entry"]
            done = False
            if sl and lo <= avg * (1 - sl / 100):
                close_pos(i, min(o, avg * (1 - sl / 100)), "손절")
                done = True
            elif trail_on and (pos["peak"] / avg - 1) * 100 >= t_act and lo <= pos["peak"] * (1 - t_drop / 100):
                close_pos(i, min(o, pos["peak"] * (1 - t_drop / 100)), "트레일링")
                done = True
            elif tp and h >= avg * (1 + tp / 100):
                close_pos(i, max(o, avg * (1 + tp / 100)), "익절")
                done = True
            if not done:
                pos["peak"] = max(pos["peak"], h)
                pnl = (c / avg - 1) * 100
                if max_hold and t - pos["time"] >= timedelta(minutes=max_hold):
                    close_pos(i, c, "보유시간 초과")
                elif exit_time:
                    hh, mm = parse_hhmm(exit_time)
                    cut = t.replace(hour=hh, minute=mm, second=0)
                    if pos["time"] < cut <= t:
                        close_pos(i, c, "시각 청산")
                if pos is not None and bool(sell_sig.iloc[i]) and (min_profit is None or pnl >= min_profit):
                    close_pos(i, c, "지표 매도")
        elif pos is None and bool(buy_sig.iloc[i]):
            if last_exit is not None and (t - last_exit) < cooldown:
                pass
            elif last_exit is not None and t == last_exit:
                pass
            elif in_time_window(t, m.get("buy_start", "00:00"), m.get("buy_end", "23:59")):
                pos = {"entry": c * (1 + slip), "time": t, "idx": i, "peak": c}

        mark = equity * ((c * (1 - fee)) / (pos["entry"] * (1 + fee))) if pos else equity
        curve.append(mark)

    trades_df = pd.DataFrame(trades)
    eq = pd.Series(curve, index=df["time"])
    stats = {
        "총 수익률%": round((eq.iloc[-1] - 1) * 100, 2) if len(eq) else 0.0,
        "단순 보유 수익률%": round((df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100, 2) if len(df) else 0.0,
        "거래 횟수": len(trades_df),
        "승률%": round((trades_df["수익률%"] > 0).mean() * 100, 1) if len(trades_df) else 0.0,
        "평균 수익률%": round(trades_df["수익률%"].mean(), 3) if len(trades_df) else 0.0,
        "최대 낙폭(MDD)%": round(((eq / eq.cummax()) - 1).min() * 100, 2) if len(eq) else 0.0,
        "보유 중": pos is not None,
    }
    stats = {k: (float(v) if not isinstance(v, (bool, int)) else v) for k, v in stats.items()}
    return trades_df, eq, stats, buy_sig, sell_sig
