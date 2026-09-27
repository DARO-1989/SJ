"""선택 가능한 매수/매도 지표 조건 목록.

각 조건은 캔들 df를 받아 캔들별 참/거짓 Series를 돌려준다.
- 실시간 매매: 마지막 값(또는 완성봉 기준이면 마지막-1)을 사용
- 백테스트: Series 전체를 사용
조건은 매수·매도 양쪽에서 모두 선택할 수 있다.
"""
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from . import indicators as ind


@dataclass
class Param:
    name: str
    label: str
    default: float
    min: float = None
    max: float = None
    step: float = None
    kind: str = "float"  # float | int | choice
    options: list = None


@dataclass
class Condition:
    key: str
    label: str
    group: str
    description: str
    params: list
    fn: Callable = field(repr=False)

    def defaults(self):
        return {p.name: p.default for p in self.params}

    def evaluate(self, df: pd.DataFrame, params: dict) -> pd.Series:
        merged = self.defaults()
        merged.update(params or {})
        for p in self.params:
            if p.kind == "int":
                merged[p.name] = int(merged[p.name])
            elif p.kind == "float":
                merged[p.name] = float(merged[p.name])
        out = self.fn(df, **merged)
        return out.fillna(False).astype(bool)


REGISTRY: dict = {}


def _register(key, label, group, description, params):
    def deco(fn):
        REGISTRY[key] = Condition(key, label, group, description, params, fn)
        return fn
    return deco


def P(name, label, default, lo=None, hi=None, step=None):
    kind = "int" if isinstance(default, int) and not isinstance(default, bool) else "float"
    if step is None:
        step = 1 if kind == "int" else 0.1
    return Param(name, label, default, lo, hi, step, kind)


def Choice(name, label, options, default=None):
    return Param(name, label, default or options[0], kind="choice", options=options)


def cross_up(a, b):
    b_prev = b.shift(1) if isinstance(b, pd.Series) else b
    return (a > b) & (a.shift(1) <= b_prev)


def cross_down(a, b):
    b_prev = b.shift(1) if isinstance(b, pd.Series) else b
    return (a < b) & (a.shift(1) >= b_prev)


def recent(sig: pd.Series, n: int) -> pd.Series:
    """최근 n봉 이내에 한 번이라도 발생."""
    if n <= 1:
        return sig
    return sig.astype(float).rolling(n, min_periods=1).max() > 0


def _ma(close, n, kind):
    return ind.ema(close, n) if kind == "EMA" else ind.sma(close, n)


MA_KIND = lambda: Choice("ma_type", "이평 종류", ["SMA", "EMA"])
LOOKBACK = lambda: P("lookback", "최근 N봉 이내 발생", 1, 1, 20)

# ============================================================== RSI
@_register("rsi_below", "RSI 이하 (과매도)", "RSI",
           "RSI가 기준값 이하일 때", [P("period", "기간", 14, 2, 100), P("level", "기준값", 30.0, 0, 100, 1.0)])
def _(df, period, level):
    return ind.rsi(df["close"], period) <= level


@_register("rsi_above", "RSI 이상 (과매수)", "RSI",
           "RSI가 기준값 이상일 때", [P("period", "기간", 14, 2, 100), P("level", "기준값", 70.0, 0, 100, 1.0)])
def _(df, period, level):
    return ind.rsi(df["close"], period) >= level


@_register("rsi_cross_up", "RSI 상향 돌파", "RSI",
           "RSI가 기준값을 아래에서 위로 돌파 (과매도 탈출)",
           [P("period", "기간", 14, 2, 100), P("level", "기준값", 30.0, 0, 100, 1.0), LOOKBACK()])
def _(df, period, level, lookback):
    return recent(cross_up(ind.rsi(df["close"], period), level), lookback)


@_register("rsi_cross_down", "RSI 하향 돌파", "RSI",
           "RSI가 기준값을 위에서 아래로 이탈 (과매수 이탈)",
           [P("period", "기간", 14, 2, 100), P("level", "기준값", 70.0, 0, 100, 1.0), LOOKBACK()])
def _(df, period, level, lookback):
    return recent(cross_down(ind.rsi(df["close"], period), level), lookback)


# ======================================================== 볼린저밴드
BB = lambda: [P("period", "기간", 20, 2, 200), P("k", "표준편차 배수", 2.0, 0.5, 5.0, 0.1)]


@_register("bb_lower_touch", "볼린저 하단 터치/이탈", "볼린저밴드",
           "저가(또는 종가)가 하단밴드 이하", BB() + [Choice("price", "기준 가격", ["저가", "종가"])])
def _(df, period, k, price):
    _, _, lower = ind.bollinger(df["close"], period, k)
    p = df["low"] if price == "저가" else df["close"]
    return p <= lower


@_register("bb_lower_reenter", "볼린저 하단 재진입", "볼린저밴드",
           "종가가 하단밴드 아래에 있다가 다시 위로 올라옴", BB() + [LOOKBACK()])
def _(df, period, k, lookback):
    _, _, lower = ind.bollinger(df["close"], period, k)
    return recent(cross_up(df["close"], lower), lookback)


@_register("bb_upper_touch", "볼린저 상단 터치/돌파", "볼린저밴드",
           "고가(또는 종가)가 상단밴드 이상", BB() + [Choice("price", "기준 가격", ["고가", "종가"])])
def _(df, period, k, price):
    _, upper, _ = ind.bollinger(df["close"], period, k)
    p = df["high"] if price == "고가" else df["close"]
    return p >= upper


@_register("bb_upper_breakout", "볼린저 상단 돌파 (추세추종)", "볼린저밴드",
           "종가가 상단밴드를 아래에서 위로 돌파", BB() + [LOOKBACK()])
def _(df, period, k, lookback):
    _, upper, _ = ind.bollinger(df["close"], period, k)
    return recent(cross_up(df["close"], upper), lookback)


@_register("bb_mid_cross_up", "볼린저 중심선 상향 돌파", "볼린저밴드",
           "종가가 중심선(이평)을 상향 돌파", BB() + [LOOKBACK()])
def _(df, period, k, lookback):
    mid, _, _ = ind.bollinger(df["close"], period, k)
    return recent(cross_up(df["close"], mid), lookback)


@_register("bb_mid_cross_down", "볼린저 중심선 하향 이탈", "볼린저밴드",
           "종가가 중심선(이평)을 하향 이탈", BB() + [LOOKBACK()])
def _(df, period, k, lookback):
    mid, _, _ = ind.bollinger(df["close"], period, k)
    return recent(cross_down(df["close"], mid), lookback)


@_register("bb_percent_b", "볼린저 %B 범위", "볼린저밴드",
           "%B(0=하단, 1=상단)가 지정 범위 안", BB() + [P("low", "최소 %B", 0.0, -2, 3, 0.05), P("high", "최대 %B", 0.2, -2, 3, 0.05)])
def _(df, period, k, low, high):
    _, upper, lower = ind.bollinger(df["close"], period, k)
    pb = (df["close"] - lower) / (upper - lower)
    return (pb >= low) & (pb <= high)


@_register("bb_squeeze", "볼린저 밴드폭 수축", "볼린저밴드",
           "밴드폭((상단-하단)/중심선)이 기준 % 이하 (변동성 수축)", BB() + [P("width", "밴드폭 % 이하", 5.0, 0.1, 50, 0.1)])
def _(df, period, k, width):
    mid, upper, lower = ind.bollinger(df["close"], period, k)
    return (upper - lower) / mid * 100 <= width


# ========================================================== 이동평균
@_register("ma_golden_cross", "이평선 골든크로스", "이동평균",
           "단기 이평이 장기 이평을 상향 돌파",
           [P("short", "단기", 5, 1, 200), P("long", "장기", 20, 2, 200), MA_KIND(), LOOKBACK()])
def _(df, short, long, ma_type, lookback):
    return recent(cross_up(_ma(df["close"], short, ma_type), _ma(df["close"], long, ma_type)), lookback)


@_register("ma_dead_cross", "이평선 데드크로스", "이동평균",
           "단기 이평이 장기 이평을 하향 돌파",
           [P("short", "단기", 5, 1, 200), P("long", "장기", 20, 2, 200), MA_KIND(), LOOKBACK()])
def _(df, short, long, ma_type, lookback):
    return recent(cross_down(_ma(df["close"], short, ma_type), _ma(df["close"], long, ma_type)), lookback)


@_register("price_above_ma", "가격 > 이평선", "이동평균",
           "종가가 이평선 위", [P("period", "기간", 20, 1, 200), MA_KIND()])
def _(df, period, ma_type):
    return df["close"] > _ma(df["close"], period, ma_type)


@_register("price_below_ma", "가격 < 이평선", "이동평균",
           "종가가 이평선 아래", [P("period", "기간", 20, 1, 200), MA_KIND()])
def _(df, period, ma_type):
    return df["close"] < _ma(df["close"], period, ma_type)


@_register("price_cross_up_ma", "가격이 이평선 상향 돌파", "이동평균",
           "종가가 이평선을 아래에서 위로 돌파", [P("period", "기간", 20, 1, 200), MA_KIND(), LOOKBACK()])
def _(df, period, ma_type, lookback):
    return recent(cross_up(df["close"], _ma(df["close"], period, ma_type)), lookback)


@_register("price_cross_down_ma", "가격이 이평선 하향 이탈", "이동평균",
           "종가가 이평선을 위에서 아래로 이탈", [P("period", "기간", 20, 1, 200), MA_KIND(), LOOKBACK()])
def _(df, period, ma_type, lookback):
    return recent(cross_down(df["close"], _ma(df["close"], period, ma_type)), lookback)


@_register("ma_bull_alignment", "이평선 정배열", "이동평균",
           "단기 > 중기 > 장기 이평",
           [P("p1", "단기", 5, 1, 200), P("p2", "중기", 20, 1, 200), P("p3", "장기", 60, 1, 200), MA_KIND()])
def _(df, p1, p2, p3, ma_type):
    c = df["close"]
    a, b, d = _ma(c, p1, ma_type), _ma(c, p2, ma_type), _ma(c, p3, ma_type)
    return (a > b) & (b > d)


@_register("ma_bear_alignment", "이평선 역배열", "이동평균",
           "단기 < 중기 < 장기 이평",
           [P("p1", "단기", 5, 1, 200), P("p2", "중기", 20, 1, 200), P("p3", "장기", 60, 1, 200), MA_KIND()])
def _(df, p1, p2, p3, ma_type):
    c = df["close"]
    a, b, d = _ma(c, p1, ma_type), _ma(c, p2, ma_type), _ma(c, p3, ma_type)
    return (a < b) & (b < d)


@_register("ma_slope_up", "이평선 상승 중", "이동평균",
           "이평선이 N봉 전보다 높음", [P("period", "기간", 20, 1, 200), P("bars", "비교 봉 수", 3, 1, 50), MA_KIND()])
def _(df, period, bars, ma_type):
    m = _ma(df["close"], period, ma_type)
    return m > m.shift(bars)


@_register("disparity_below", "이격도 이하", "이동평균",
           "종가/이평×100 이 기준 이하 (이평 대비 많이 빠짐)", [P("period", "기간", 20, 1, 200), P("level", "이격도", 95.0, 50, 150, 0.5)])
def _(df, period, level):
    return df["close"] / ind.sma(df["close"], period) * 100 <= level


@_register("disparity_above", "이격도 이상", "이동평균",
           "종가/이평×100 이 기준 이상 (이평 대비 많이 오름)", [P("period", "기간", 20, 1, 200), P("level", "이격도", 105.0, 50, 150, 0.5)])
def _(df, period, level):
    return df["close"] / ind.sma(df["close"], period) * 100 >= level


# ============================================================== MACD
MACD_P = lambda: [P("fast", "단기", 12, 2, 100), P("slow", "장기", 26, 3, 200), P("signal", "시그널", 9, 2, 50)]


@_register("macd_golden_cross", "MACD 골든크로스", "MACD",
           "MACD선이 시그널선을 상향 돌파", MACD_P() + [Choice("zone", "위치 조건", ["상관없음", "0선 아래에서만", "0선 위에서만"]), LOOKBACK()])
def _(df, fast, slow, signal, zone, lookback):
    line, sig, _ = ind.macd(df["close"], fast, slow, signal)
    c = cross_up(line, sig)
    if zone == "0선 아래에서만":
        c &= line < 0
    elif zone == "0선 위에서만":
        c &= line > 0
    return recent(c, lookback)


@_register("macd_dead_cross", "MACD 데드크로스", "MACD",
           "MACD선이 시그널선을 하향 돌파", MACD_P() + [LOOKBACK()])
def _(df, fast, slow, signal, lookback):
    line, sig, _ = ind.macd(df["close"], fast, slow, signal)
    return recent(cross_down(line, sig), lookback)


@_register("macd_above_zero", "MACD 0선 위", "MACD", "MACD선 > 0 (상승 추세)", MACD_P())
def _(df, fast, slow, signal):
    line, _, _ = ind.macd(df["close"], fast, slow, signal)
    return line > 0


@_register("macd_hist_rising", "MACD 히스토그램 증가", "MACD",
           "히스토그램이 N봉 연속 증가 (모멘텀 강화)", MACD_P() + [P("bars", "연속 봉 수", 2, 1, 10)])
def _(df, fast, slow, signal, bars):
    _, _, hist = ind.macd(df["close"], fast, slow, signal)
    up = hist > hist.shift(1)
    return up.astype(float).rolling(bars, min_periods=bars).min() > 0


# ========================================================= 스토캐스틱
STO = lambda: [P("k_period", "%K 기간", 14, 2, 100), P("k_smooth", "%K 평활", 3, 1, 20), P("d_period", "%D 기간", 3, 1, 20)]


@_register("stoch_golden_cross", "스토캐스틱 골든크로스", "스토캐스틱",
           "%K가 %D를 상향 돌파 (선택: 과매도 구간에서만)", STO() + [P("zone", "이 값 이하에서만 (100=무관)", 20.0, 0, 100, 1.0), LOOKBACK()])
def _(df, k_period, k_smooth, d_period, zone, lookback):
    k, d = ind.stochastic(df, k_period, k_smooth, d_period)
    return recent(cross_up(k, d) & (k <= zone), lookback)


@_register("stoch_dead_cross", "스토캐스틱 데드크로스", "스토캐스틱",
           "%K가 %D를 하향 돌파 (선택: 과매수 구간에서만)", STO() + [P("zone", "이 값 이상에서만 (0=무관)", 80.0, 0, 100, 1.0), LOOKBACK()])
def _(df, k_period, k_smooth, d_period, zone, lookback):
    k, d = ind.stochastic(df, k_period, k_smooth, d_period)
    return recent(cross_down(k, d) & (k >= zone), lookback)


@_register("stoch_below", "스토캐스틱 %K 이하", "스토캐스틱", "%K가 기준 이하", STO() + [P("level", "기준값", 20.0, 0, 100, 1.0)])
def _(df, k_period, k_smooth, d_period, level):
    k, _ = ind.stochastic(df, k_period, k_smooth, d_period)
    return k <= level


@_register("stoch_above", "스토캐스틱 %K 이상", "스토캐스틱", "%K가 기준 이상", STO() + [P("level", "기준값", 80.0, 0, 100, 1.0)])
def _(df, k_period, k_smooth, d_period, level):
    k, _ = ind.stochastic(df, k_period, k_smooth, d_period)
    return k >= level


# ======================================================= 기타 오실레이터
@_register("cci_below", "CCI 이하", "기타 지표", "CCI가 기준 이하 (과매도)", [P("period", "기간", 20, 2, 100), P("level", "기준값", -100.0, -500, 500, 5.0)])
def _(df, period, level):
    return ind.cci(df, period) <= level


@_register("cci_above", "CCI 이상", "기타 지표", "CCI가 기준 이상 (과매수)", [P("period", "기간", 20, 2, 100), P("level", "기준값", 100.0, -500, 500, 5.0)])
def _(df, period, level):
    return ind.cci(df, period) >= level


@_register("williams_below", "Williams %R 이하", "기타 지표", "%R이 기준 이하 (과매도, -100~0)", [P("period", "기간", 14, 2, 100), P("level", "기준값", -80.0, -100, 0, 1.0)])
def _(df, period, level):
    return ind.williams_r(df, period) <= level


@_register("williams_above", "Williams %R 이상", "기타 지표", "%R이 기준 이상 (과매수, -100~0)", [P("period", "기간", 14, 2, 100), P("level", "기준값", -20.0, -100, 0, 1.0)])
def _(df, period, level):
    return ind.williams_r(df, period) >= level


@_register("mfi_below", "MFI 이하", "기타 지표", "자금흐름지수(MFI)가 기준 이하", [P("period", "기간", 14, 2, 100), P("level", "기준값", 20.0, 0, 100, 1.0)])
def _(df, period, level):
    return ind.mfi(df, period) <= level


@_register("mfi_above", "MFI 이상", "기타 지표", "자금흐름지수(MFI)가 기준 이상", [P("period", "기간", 14, 2, 100), P("level", "기준값", 80.0, 0, 100, 1.0)])
def _(df, period, level):
    return ind.mfi(df, period) >= level


@_register("adx_above", "ADX 이상 (추세 강도)", "기타 지표",
           "ADX가 기준 이상 (선택: +DI > -DI 상승추세만)", [P("period", "기간", 14, 2, 100), P("level", "기준값", 25.0, 0, 100, 1.0), Choice("direction", "방향", ["상관없음", "상승추세만(+DI>-DI)", "하락추세만(-DI>+DI)"])])
def _(df, period, level, direction):
    a, pdi, mdi = ind.adx(df, period)
    c = a >= level
    if direction.startswith("상승"):
        c &= pdi > mdi
    elif direction.startswith("하락"):
        c &= mdi > pdi
    return c


@_register("obv_rising", "OBV 상승", "기타 지표", "OBV가 OBV 이평선 위 (매집)", [P("period", "OBV 이평 기간", 20, 2, 200)])
def _(df, period):
    o = ind.obv(df)
    return o > ind.sma(o, period)


# ============================================================ 거래량
@_register("volume_spike", "거래량 급증", "거래량",
           "현재 봉 거래량 ≥ 직전 N봉 평균 × 배수", [P("period", "평균 기간", 20, 2, 200), P("mult", "배수", 3.0, 1.0, 50, 0.5)])
def _(df, period, mult):
    avg = df["volume"].shift(1).rolling(period, min_periods=period).mean()
    return df["volume"] >= avg * mult


@_register("volume_increase", "거래량 연속 증가", "거래량", "거래량이 N봉 연속 증가", [P("bars", "연속 봉 수", 3, 1, 20)])
def _(df, bars):
    up = df["volume"] > df["volume"].shift(1)
    return up.astype(float).rolling(bars, min_periods=bars).min() > 0


@_register("trade_value_min", "봉 거래대금 이상", "거래량", "현재 봉 거래대금이 기준(백만원) 이상", [P("value", "거래대금(백만원)", 100.0, 0, 100000, 10.0)])
def _(df, value):
    return df["value"] >= value * 1_000_000


# ======================================================= 가격/캔들 패턴
@_register("volatility_breakout", "변동성 돌파 (래리 윌리엄스)", "가격/돌파",
           "현재가 ≥ 이번 봉 시가 + 직전 봉 (고가-저가) × K. 일봉과 함께 쓰는 게 일반적", [P("k", "K 값", 0.5, 0.1, 1.5, 0.05)])
def _(df, k):
    target = df["open"] + (df["high"].shift(1) - df["low"].shift(1)) * k
    return df["close"] >= target


@_register("n_high_breakout", "N봉 최고가 돌파", "가격/돌파", "종가가 직전 N봉 최고가를 돌파 (신고가)", [P("period", "N봉", 20, 2, 200)])
def _(df, period):
    return df["close"] > df["high"].shift(1).rolling(period, min_periods=period).max()


@_register("n_low_breakdown", "N봉 최저가 이탈", "가격/돌파", "종가가 직전 N봉 최저가를 이탈", [P("period", "N봉", 20, 2, 200)])
def _(df, period):
    return df["close"] < df["low"].shift(1).rolling(period, min_periods=period).min()


@_register("drop_from_high", "N봉 고점 대비 하락 (눌림목)", "가격/돌파",
           "직전 N봉 최고가 대비 X% 이상 하락", [P("period", "N봉", 20, 2, 200), P("pct", "하락률 %", 5.0, 0.1, 90, 0.1)])
def _(df, period, pct):
    hh = df["high"].rolling(period, min_periods=period).max()
    return df["close"] <= hh * (1 - pct / 100)


@_register("rise_from_low", "N봉 저점 대비 상승", "가격/돌파",
           "직전 N봉 최저가 대비 X% 이상 상승", [P("period", "N봉", 20, 2, 200), P("pct", "상승률 %", 5.0, 0.1, 500, 0.1)])
def _(df, period, pct):
    ll = df["low"].rolling(period, min_periods=period).min()
    return df["close"] >= ll * (1 + pct / 100)


@_register("change_range", "N봉 전 대비 변동률 범위", "가격/돌파",
           "N봉 전 종가 대비 변동률이 최소~최대 % 사이", [P("bars", "N봉 전", 1, 1, 200), P("min_pct", "최소 %", 1.0, -100, 1000, 0.1), P("max_pct", "최대 %", 5.0, -100, 1000, 0.1)])
def _(df, bars, min_pct, max_pct):
    chg = (df["close"] / df["close"].shift(bars) - 1) * 100
    return (chg >= min_pct) & (chg <= max_pct)


@_register("bullish_candle", "양봉 (몸통 크기)", "캔들 패턴", "현재 봉이 양봉이고 몸통이 시가 대비 X% 이상", [P("pct", "최소 몸통 %", 0.5, 0, 50, 0.1)])
def _(df, pct):
    return (df["close"] > df["open"]) & ((df["close"] / df["open"] - 1) * 100 >= pct)


@_register("bearish_candle", "음봉 (몸통 크기)", "캔들 패턴", "현재 봉이 음봉이고 몸통이 시가 대비 X% 이상", [P("pct", "최소 몸통 %", 0.5, 0, 50, 0.1)])
def _(df, pct):
    return (df["close"] < df["open"]) & ((1 - df["close"] / df["open"]) * 100 >= pct)


@_register("consecutive_down_rebound", "연속 하락 후 반등", "캔들 패턴",
           "직전 N봉 연속 음봉 후 현재 봉 양봉", [P("bars", "연속 음봉 수", 3, 1, 20)])
def _(df, bars):
    down = (df["close"] < df["open"]).astype(float)
    prev_all_down = down.shift(1).rolling(bars, min_periods=bars).min() > 0
    return prev_all_down & (df["close"] > df["open"])


@_register("consecutive_up", "N봉 연속 양봉", "캔들 패턴", "최근 N봉 모두 양봉", [P("bars", "봉 수", 3, 1, 20)])
def _(df, bars):
    up = (df["close"] > df["open"]).astype(float)
    return up.rolling(bars, min_periods=bars).min() > 0


@_register("consecutive_down", "N봉 연속 음봉", "캔들 패턴", "최근 N봉 모두 음봉", [P("bars", "봉 수", 3, 1, 20)])
def _(df, bars):
    down = (df["close"] < df["open"]).astype(float)
    return down.rolling(bars, min_periods=bars).min() > 0


@_register("hammer", "망치형 (긴 아래꼬리)", "캔들 패턴",
           "아래꼬리가 몸통의 N배 이상 & 윗꼬리 짧음", [P("mult", "꼬리/몸통 배수", 2.0, 1, 10, 0.5)])
def _(df, mult):
    body = (df["close"] - df["open"]).abs()
    lower = df[["open", "close"]].min(axis=1) - df["low"]
    upper = df["high"] - df[["open", "close"]].max(axis=1)
    rng = (df["high"] - df["low"]).replace(0, float("nan"))
    return (lower >= body * mult) & (upper <= rng * 0.25)


@_register("atr_ratio_above", "변동성(ATR%) 이상", "기타 지표",
           "ATR/종가 × 100 이 기준 % 이상 (움직임 있는 종목)", [P("period", "기간", 14, 2, 100), P("pct", "ATR %", 1.0, 0, 50, 0.1)])
def _(df, period, pct):
    return ind.atr(df, period) / df["close"] * 100 >= pct


GROUP_ORDER = ["RSI", "볼린저밴드", "이동평균", "MACD", "스토캐스틱", "거래량", "가격/돌파", "캔들 패턴", "기타 지표"]


def evaluate_rules(df: pd.DataFrame, rules: list, mode: str = "AND") -> pd.Series:
    """rules = [{"key":..., "params": {...}}, ...] 을 AND/OR로 결합한 Series."""
    if not rules:
        return pd.Series(False, index=df.index)
    sigs = []
    for r in rules:
        cond = REGISTRY.get(r["key"])
        if cond is None:
            continue
        sigs.append(cond.evaluate(df, r.get("params", {})))
    if not sigs:
        return pd.Series(False, index=df.index)
    out = sigs[0]
    for s in sigs[1:]:
        out = (out & s) if mode == "AND" else (out | s)
    return out


def explain_rules(df: pd.DataFrame, rules: list, idx: int = -1) -> list:
    """각 조건의 해당 봉 충족 여부 [(label, bool), ...]."""
    out = []
    for r in rules:
        cond = REGISTRY.get(r["key"])
        if cond is None:
            continue
        s = cond.evaluate(df, r.get("params", {}))
        out.append((cond.label, bool(s.iloc[idx]) if len(s) else False))
    return out
