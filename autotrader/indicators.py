"""보조지표 계산. 입력 df 컬럼: open, high, low, close, volume."""
import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rma(s: pd.Series, n: int) -> pd.Series:
    """와일더 이동평균 (RSI/ATR/ADX 표준)."""
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    gain = rma(d.clip(lower=0), n)
    loss = rma((-d).clip(lower=0), n)
    out = 100 - 100 / (1 + gain / loss)
    return out.where(loss != 0, 100.0).where(gain.notna())


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0):
    mid = sma(close, n)
    std = close.rolling(n, min_periods=n).std(ddof=0)
    return mid, mid + k * std, mid - k * std


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return line, sig, line - sig


def stochastic(df: pd.DataFrame, k_period: int = 14, k_smooth: int = 3, d_period: int = 3):
    """슬로우 스토캐스틱 (%K, %D)."""
    ll = df["low"].rolling(k_period, min_periods=k_period).min()
    hh = df["high"].rolling(k_period, min_periods=k_period).max()
    fast_k = 100 * (df["close"] - ll) / (hh - ll).replace(0, np.nan)
    slow_k = sma(fast_k, k_smooth)
    slow_d = sma(slow_k, d_period)
    return slow_k, slow_d


def cci(df: pd.DataFrame, n: int = 20) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    ma = sma(tp, n)
    md = tp.rolling(n, min_periods=n).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    return (tp - ma) / (0.015 * md.replace(0, np.nan))


def williams_r(df: pd.DataFrame, n: int = 14) -> pd.Series:
    hh = df["high"].rolling(n, min_periods=n).max()
    ll = df["low"].rolling(n, min_periods=n).min()
    return -100 * (hh - df["close"]) / (hh - ll).replace(0, np.nan)


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return rma(true_range(df), n)


def adx(df: pd.DataFrame, n: int = 14):
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr = rma(true_range(df), n)
    plus_di = 100 * rma(plus_dm, n) / tr
    minus_di = 100 * rma(minus_dm, n) / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return rma(dx, n), plus_di, minus_di


def mfi(df: pd.DataFrame, n: int = 14) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    flow = tp * df["volume"]
    pos = flow.where(tp > tp.shift(1), 0.0).rolling(n, min_periods=n).sum()
    neg = flow.where(tp < tp.shift(1), 0.0).rolling(n, min_periods=n).sum()
    out = 100 - 100 / (1 + pos / neg)
    return out.where(neg != 0, 100.0).where(pos.notna())


def obv(df: pd.DataFrame) -> pd.Series:
    sign = np.sign(df["close"].diff()).fillna(0)
    return (sign * df["volume"]).cumsum()
