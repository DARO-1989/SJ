import streamlit as st
import requests
import pandas as pd
import time

# --- 페이지 설정 (모바일 친화적) ---
st.set_page_config(
    page_title="코인 감시자",
    page_icon="📈",
    layout="centered"
)

# --- 함수 정의 (기존 로직 재사용) ---
def get_market_data(market="KRW-BTC", interval="minutes/15", count=200):
    url = f"https://api.upbit.com/v1/candles/{interval}"
    params = {"market": market, "count": count}
    headers = {"accept": "application/json"}
    
    try:
        response = requests.get(url, params=params, headers=headers)
        data = response.json()
        df = pd.DataFrame(data)
        df = df.sort_values(by="candle_date_time_kst").reset_index(drop=True)
        return df
    except Exception as e:
        st.error(f"데이터 조회 실패: {e}")
        return pd.DataFrame()

def calculate_indicators(df):
    if df.empty: return df
    
    # RSI 계산
    period = 14
    delta = df['trade_price'].diff()
    gain = (delta.where(delta > 0, 0)).fillna(0)
    loss = (-delta.where(delta < 0, 0)).fillna(0)
    
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    
    rs = avg_gain / avg_loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
    # 볼린저 밴드 계산
    period_bb = 20
    df['MA20'] = df['trade_price'].rolling(window=period_bb).mean()
    df['StdDev'] = df['trade_price'].rolling(window=period_bb).std()
    df['Upper'] = df['MA20'] + (df['StdDev'] * 2)
    df['Lower'] = df['MA20'] - (df['StdDev'] * 2)
    
    return df

# --- UI 구성 ---
st.title("📈 실시간 코인 분석기")

# 사이드바 설정
with st.sidebar:
    st.header("설정")
    market = st.text_input("종목 코드", "KRW-BTC")
    interval_map = {"1분": "minutes/1", "15분": "minutes/15", "1시간": "minutes/60", "1일": "days"}
    interval_label = st.selectbox("분봉 선택", list(interval_map.keys()), index=1)
    interval = interval_map[interval_label]
    auto_refresh = st.checkbox("자동 새로고침 (10초)", value=False)

# 데이터 로드 버튼
if st.button("분석 시작") or auto_refresh:
    with st.spinner('데이터 불러오는 중...'):
        df = get_market_data(market, interval)
        df = calculate_indicators(df)
        
        if not df.empty:
            curr = df.iloc[-1]
            prev = df.iloc[-2]
            
            # 1. 현재가 정보 표시 (Metrics)
            col1, col2, col3 = st.columns(3)
            price_change = curr['trade_price'] - prev['trade_price']
            col1.metric("현재가", f"{curr['trade_price']:,.0f}", f"{price_change:,.0f}")
            col2.metric("RSI (14)", f"{curr['RSI']:.1f}", delta_color="off")
            
            # 신호 상태 표시
            signal_emoji = "😐"
            signal_text = "중립"
            bg_color = "gray"
            
            if curr['RSI'] < 30 and curr['trade_price'] < curr['Lower']:
                signal_emoji = "🚀"
                signal_text = "강력 매수"
                bg_color = "green"
            elif curr['RSI'] > 70 and curr['trade_price'] > curr['Upper']:
                signal_emoji = "📉"
                signal_text = "강력 매도"
                bg_color = "red"
                
            col3.markdown(f"### {signal_emoji} {signal_text}")

            # 2. 차트 그리기 (Streamlit 내장 차트)
            st.subheader("가격 & 볼린저 밴드")
            chart_data = df[['candle_date_time_kst', 'trade_price', 'Upper', 'Lower']].tail(50)
            chart_data = chart_data.set_index('candle_date_time_kst')
            st.line_chart(chart_data, color=["#000000", "#FF0000", "#0000FF"])
            
            st.subheader("RSI 지표")
            rsi_data = df[['candle_date_time_kst', 'RSI']].tail(50).set_index('candle_date_time_kst')
            st.line_chart(rsi_data)
            
            # 데이터 테이블 (접기 가능)
            with st.expander("상세 데이터 보기"):
                st.dataframe(df.tail(10)[['candle_date_time_kst', 'trade_price', 'RSI', 'Upper', 'Lower']].sort_index(ascending=False))

        # 자동 새로고침 로직
        if auto_refresh:
            time.sleep(10)
            st.rerun()