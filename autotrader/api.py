"""업비트 REST API 클라이언트 (시세 조회 + 주문).

공식 문서: https://docs.upbit.com
- 인증: JWT(HS256). payload = access_key, nonce, (파라미터가 있으면) query_hash(SHA512), query_hash_alg
- 주문: POST /v1/orders
    시장가 매수 -> side=bid, ord_type=price, price=<주문 총액 KRW>
    시장가 매도 -> side=ask, ord_type=market, volume=<수량>
"""
import base64
import hashlib
import hmac
import json
import threading
import time
import uuid
from urllib.parse import unquote, urlencode

import pandas as pd
import requests

BASE_URL = "https://api.upbit.com/v1"

# UI 표시명 -> 업비트 캔들 엔드포인트
INTERVALS = {
    "1분": "minutes/1",
    "3분": "minutes/3",
    "5분": "minutes/5",
    "10분": "minutes/10",
    "15분": "minutes/15",
    "30분": "minutes/30",
    "60분": "minutes/60",
    "240분": "minutes/240",
    "일봉": "days",
    "주봉": "weeks",
    "월봉": "months",
}


class UpbitError(Exception):
    def __init__(self, status, message):
        super().__init__(f"[{status}] {message}")
        self.status = status
        self.message = message


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_jwt(payload: dict, secret: str) -> str:
    """PyJWT 없이 HS256 JWT 생성."""
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header}.{body}".encode()
    sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{header}.{body}.{_b64url(sig)}"


class UpbitAPI:
    def __init__(self, access_key: str = "", secret_key: str = "", timeout: float = 10.0):
        self.access_key = access_key or ""
        self.secret_key = secret_key or ""
        self.timeout = timeout
        self.session = requests.Session()
        self._lock = threading.Lock()
        self._last_call = 0.0
        # 시세 API는 초당 10회 제한 -> 호출 간 최소 간격
        self._min_gap = 0.11

    @property
    def has_keys(self) -> bool:
        return bool(self.access_key and self.secret_key)

    # ------------------------------------------------------------------ core
    def _auth_header(self, params):
        payload = {"access_key": self.access_key, "nonce": str(uuid.uuid4())}
        if params:
            query = unquote(urlencode(params, doseq=True)).encode()
            payload["query_hash"] = hashlib.sha512(query).hexdigest()
            payload["query_hash_alg"] = "SHA512"
        return "Bearer " + make_jwt(payload, self.secret_key)

    def _throttle(self):
        with self._lock:
            wait = self._min_gap - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()

    def _request(self, method, path, params=None, auth=False, retries=4):
        if auth and not self.has_keys:
            raise UpbitError("NO_KEY", "API 키가 설정되지 않았습니다.")
        url = BASE_URL + path
        last_err = None
        for attempt in range(retries):
            self._throttle()
            headers = {"accept": "application/json"}
            if auth:
                headers["Authorization"] = self._auth_header(params)
            try:
                if method == "GET":
                    r = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
                elif method == "POST":
                    r = self.session.post(url, json=params, headers=headers, timeout=self.timeout)
                elif method == "DELETE":
                    r = self.session.delete(url, params=params, headers=headers, timeout=self.timeout)
                else:
                    raise ValueError(method)
            except requests.RequestException as e:
                last_err = UpbitError("NETWORK", str(e))
                time.sleep(0.5 * (attempt + 1))
                continue

            if r.status_code == 429:  # 요청 수 초과
                last_err = UpbitError(429, "Too Many Requests")
                time.sleep(0.5 * (attempt + 1))
                continue
            if r.status_code >= 400:
                try:
                    err = r.json().get("error", {})
                    msg = f"{err.get('name', '')}: {err.get('message', r.text)}"
                except ValueError:
                    msg = r.text
                raise UpbitError(r.status_code, msg)
            return r.json()
        raise last_err or UpbitError("UNKNOWN", "요청 실패")

    # ------------------------------------------------------------ quotation
    def get_krw_markets(self):
        """KRW 마켓 목록 + 유의/주의 여부."""
        data = self._request("GET", "/market/all", {"isDetails": "true"})
        out = []
        for m in data:
            if not m["market"].startswith("KRW-"):
                continue
            event = m.get("market_event") or {}
            warning = m.get("market_warning", "NONE") == "CAUTION" or bool(event.get("warning"))
            caution = any(bool(v) for v in (event.get("caution") or {}).values())
            out.append({
                "market": m["market"],
                "korean_name": m.get("korean_name", ""),
                "warning": warning,   # 투자유의
                "caution": caution,   # 투자주의(가격 급등락, 거래량 급등 등)
            })
        return out

    def get_tickers(self, markets):
        result = []
        for i in range(0, len(markets), 100):
            chunk = markets[i:i + 100]
            result.extend(self._request("GET", "/ticker", {"markets": ",".join(chunk)}))
        return result

    def get_candles(self, market, interval="15분", count=200, to=None):
        endpoint = INTERVALS.get(interval, interval)
        params = {"market": market, "count": min(int(count), 200)}
        if to:
            params["to"] = to
        data = self._request("GET", f"/candles/{endpoint}", params)
        return candles_to_df(data)

    def get_candles_long(self, market, interval="15분", total=600):
        """200개 제한을 넘어 여러 번 나눠서 조회 (백테스트용)."""
        frames = []
        to = None
        remaining = total
        while remaining > 0:
            df = self.get_candles(market, interval, min(200, remaining), to=to)
            if df.empty:
                break
            frames.append(df)
            remaining -= len(df)
            to = df["time_utc"].iloc[0].strftime("%Y-%m-%dT%H:%M:%SZ")
            if len(df) < 200:
                break
        if not frames:
            return pd.DataFrame()
        out = pd.concat(frames).drop_duplicates("time").sort_values("time").reset_index(drop=True)
        return out

    # ------------------------------------------------------------- exchange
    def get_accounts(self):
        return self._request("GET", "/accounts", auth=True)

    def get_balance(self, currency):
        for a in self.get_accounts():
            if a["currency"] == currency:
                return float(a["balance"]), float(a.get("avg_buy_price") or 0)
        return 0.0, 0.0

    def buy_market(self, market, krw_amount):
        params = {"market": market, "side": "bid", "price": str(int(krw_amount)), "ord_type": "price"}
        return self._request("POST", "/orders", params, auth=True)

    def sell_market(self, market, volume):
        params = {"market": market, "side": "ask", "volume": format_volume(volume), "ord_type": "market"}
        return self._request("POST", "/orders", params, auth=True)

    def get_order(self, order_uuid):
        return self._request("GET", "/order", {"uuid": order_uuid}, auth=True)

    def wait_order_filled(self, order_uuid, tries=10, gap=0.5):
        """체결 결과 조회. 시장가 매수는 잔량 취소로 state='cancel'이 되는 경우가 있어 trades로 판단."""
        order = None
        for _ in range(tries):
            order = self.get_order(order_uuid)
            if order.get("state") in ("done", "cancel") and order.get("trades") is not None:
                break
            time.sleep(gap)
        trades = (order or {}).get("trades") or []
        volume = sum(float(t["volume"]) for t in trades)
        funds = sum(float(t["funds"]) for t in trades)
        fee = float((order or {}).get("paid_fee") or 0)
        avg_price = funds / volume if volume else 0.0
        return {"volume": volume, "funds": funds, "fee": fee, "avg_price": avg_price, "raw": order}


def format_volume(volume: float) -> str:
    """소수점 8자리에서 내림 (보유량보다 많이 주문되는 것 방지)."""
    v = int(float(volume) * 1e8) / 1e8
    return f"{v:.8f}"


def candles_to_df(data) -> pd.DataFrame:
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    out = pd.DataFrame({
        "time": pd.to_datetime(df["candle_date_time_kst"]),
        "time_utc": pd.to_datetime(df["candle_date_time_utc"]),
        "open": df["opening_price"].astype(float),
        "high": df["high_price"].astype(float),
        "low": df["low_price"].astype(float),
        "close": df["trade_price"].astype(float),
        "volume": df["candle_acc_trade_volume"].astype(float),
        "value": df["candle_acc_trade_price"].astype(float),
    })
    return out.sort_values("time").reset_index(drop=True)
