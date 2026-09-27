"""화면 없이 봇만 실행 (서버/VPS에서 24시간 돌릴 때).

사용법:
    python run_bot.py                  # bot_config.json 설정으로 실행
    python run_bot.py --config my.json
    python run_bot.py --once           # 한 사이클만 실행하고 종료 (설정 점검용)

설정은 `streamlit run trader_app.py` 화면에서 만들고 [설정 저장]을 누르면 bot_config.json 으로 저장된다.
API 키는 환경변수 UPBIT_ACCESS_KEY / UPBIT_SECRET_KEY 로 넘긴다.
"""
import argparse
import signal
import threading

from autotrader.api import UpbitAPI
from autotrader.config import CONFIG_PATH, load_config, load_keys
from autotrader.engine import TradingEngine


def main():
    parser = argparse.ArgumentParser(description="업비트 자동매매 봇")
    parser.add_argument("--config", default=CONFIG_PATH)
    parser.add_argument("--once", action="store_true", help="한 사이클만 실행")
    args = parser.parse_args()

    cfg = load_config(args.config)
    ak, sk = load_keys()
    engine = TradingEngine(cfg, UpbitAPI(ak, sk))

    if args.once:
        engine.run_cycle()
        print(f"보유: {list(engine.state['positions'])}")
        return

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    engine.run(stop)


if __name__ == "__main__":
    main()
