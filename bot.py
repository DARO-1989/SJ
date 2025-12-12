import streamlit as st
import requests
import pandas as pd
import time

# --- 페이지 설정 ---
st.set_page_config(
    page_title="체이스의 코인 분석기",
    page_icon="📈",
    layout="centered"
)

# --- 세션 상태 초기화 (검색 기록 유지용) ---
if 'market_code' not in st.session_state:
    st.session_state['market_code'] = "KRW-BTC"

# --- 함수 정의 ---
def get_market_data(market, interval="minutes/15", count=200):
    url = f"https://api.upbit.com/v1/candles/{interval}"
    params = {"market": market, "count": count}
    headers = {"accept": "application/json"}
    
    try:
        response = requests.get(url, params=params, headers=headers)
        data = response.json()
        if not isinstance(data, list): # 에러 처리
            return pd.DataFrame()
        df = pd.DataFrame(data)
        df = df.sort_values(by="candle_date_time_kst").reset_index(drop=True)
        return df
    except Exception as e:
        return pd.DataFrame()

def calculate_indicators(df):
    if df.empty: return df
    
    # RSI (14)
    period = 14
    delta = df['trade_price'].diff()
    gain = (delta.where(delta > 0, 0)).fillna(0)
    loss = (-delta.where(delta < 0, 0)).fillna(0)
    
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    
    rs = avg_gain / avg_loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
    # 볼린저 밴드 (20, 2)
    period_bb = 20
    df['MA20'] = df['trade_price'].rolling(window=period_bb).mean()
    df['StdDev'] = df['trade_price'].rolling(window=period_bb).std()
    df['Upper'] = df['MA20'] + (df['StdDev'] * 2)
    df['Lower'] = df['MA20'] - (df['StdDev'] * 2)
    
    return df

def get_signal_score(rsi, price, lower, upper):
    """
    매수/매도 강도를 0~100% 점수로 환산
    """
    score = 0
    action = "보류"
    color = "gray"
    emoji = "😐"
    desc = "매수/매도 보류 추천드립니다."

    # 로직: RSI가 30보다 낮을수록, 밴드 하단을 뚫을수록 매수 강도 증가
    if rsi < 30:
        base_score = 50
        # RSI가 20이면 +30점, 10이면 +50점 더 줌
        rsi_bonus = (30 - rsi) * 2.5 
        # 가격이 하단 밴드보다 낮으면 추가 점수
        band_bonus = 20 if price < lower else 0
        
        total_score = min(100, base_score + rsi_bonus + band_bonus)
        
        action = "매수"
        color = "green"
        if total_score >= 80:
            emoji = "🚀"
            desc = "강한 매수 추천 드립니다."
        else:
            emoji = "🛒"
            desc = "매수 추천 드립니다."
        score = total_score

    # 로직: RSI가 70보다 높을수록, 밴드 상단을 뚫을수록 매도 강도 증가
    elif rsi > 70:
        base_score = 50
        rsi_bonus = (rsi - 70) * 2.5
        band_bonus = 20 if price > upper else 0
        
        total_score = min(100, base_score + rsi_bonus + band_bonus)
        
        action = "매도"
        color = "red"
        if total_score >= 80:
            emoji = "🔥"
            desc = "강한 매도 추천 드립니다."
        else:
            emoji = "📉"
            desc = "매도 추천 드립니다."
        score = total_score
        
    else:
        # 중립 구간
        score = 0
    
    return action, score, emoji, desc, color

# --- UI 레이아웃 (중앙 배치) ---
st.title("📈 체이스의 코인 분석기")

# 1. 상단 컨트롤 패널 (검색창을 가운데로 이동)
with st.container():
    col_input, col_int, col_btn = st.columns([2, 1, 1])
    
    with col_input:
        # 입력값을 session_state와 연동
        market_input = st.text_input("종목 코드", value=st.session_state['market_code'])
        # 입력값이 바뀌면 session_state 업데이트
        st.session_state['market_code'] = market_input.upper()
        
    with col_int:
        interval_map = {"1분": "minutes/1", "15분": "minutes/15", "1시간": "minutes/60", "4시간": "minutes/240", "1일": "days"}
        interval_label = st.selectbox("분봉", list(interval_map.keys()), index=1)
        
    with col_btn:
        st.write("") # 줄맞춤용 공백
        st.write("") 
        refresh = st.button("새로고침 🔄")

# 자동 새로고침 체크박스 (하단에 작게)
auto_refresh = st.checkbox("10초마다 자동 갱신", value=False)

# --- 분석 로직 실행 ---
if market_input:
    market_code = st.session_state['market_code']
    # 'KRW-' 접두사 자동 처리
    if not market_code.startswith("KRW-") and not market_code.startswith("BTC-"):
        market_code = f"KRW-{market_code}"

    df = get_market_data(market_code, interval_map[interval_label])
    df = calculate_indicators(df)

    if not df.empty:
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        
        # 신호 및 점수 계산
        action, score, emoji, desc, color_code = get_signal_score(
            curr['RSI'], curr['trade_price'], curr['Lower'], curr['Upper']
        )

        st.divider()

        # 2. 메인 정보 표시 (레이아웃 변경)
        # 왼쪽: 가격 정보 / 오른쪽: 추천 정보
        m_col1, m_col2 = st.columns([1, 1.2])

        with m_col1:
            st.markdown("#### 현재 가격")
            price_change = curr['trade_price'] - prev['trade_price']
            price_pct = (price_change / prev['trade_price']) * 100
            st.metric(label=market_code, value=f"{curr['trade_price']:,.0f} 원", delta=f"{price_pct:.2f}%")

        with m_col2:
            st.markdown(f"#### {emoji} 투자 의견")
            
            # RSI와 멘트를 한 줄에 표시
            st.markdown(f"""
            <div style='background-color:#f0f2f6; padding:10px; border-radius:10px;'>
                <span style='font-size:1.2em; font-weight:bold; color:{color_code}'>{desc}</span><br>
                <span style='color:gray; font-size:0.9em;'>RSI 지수: <b>{curr['RSI']:.1f}</b></span>
            </div>
            """, unsafe_allow_html=True)

        # 3. 매수/매도 강도 게이지바 (퍼센트 표시)
        if action != "보류":
            st.write("")
            st.markdown(f"**📊 {action} 추천 강도: {score:.1f}%**")
            # 스트림릿 프로그레스바 사용 (색상은 테마 따름)
            st.progress(int(score))
            if score > 80:
                st.caption(f"💡 현재 과{action} 구간이 심화되었습니다. 적극적인 대응이 유효해 보입니다.")
            else:
                st.caption(f"💡 {action} 시그널이 감지되었습니다.")
        else:
            st.info("💡 현재는 특이 신호가 없는 '관망' 구간입니다.")

        st.divider()

        # 4. 차트 영역
        tab1, tab2 = st.tabs(["가격 차트", "데이터 상세"])
        
        with tab1:
            # 차트 데이터 준비
            chart_df = df.tail(100).copy()
            chart_df = chart_df.set_index('candle_date_time_kst')
            
            st.subheader("Price & Bollinger Bands")
            st.line_chart(chart_df[['trade_price', 'Upper', 'Lower']], color=["#000000", "#FF0000", "#0000FF"])
            
            st.subheader("RSI Index")
            # RSI 기준선(30, 70)을 시각적으로 알기 쉽게 표시하긴 어려우니 제목에 명시
            st.caption("RSI가 70 위면 과매수(매도 고려), 30 아래면 과매도(매수 고려)")
            st.line_chart(chart_df[['RSI']], color=["#800080"])

        with tab2:
            st.dataframe(df.tail(20)[['candle_date_time_kst', 'trade_price', 'RSI', 'Upper', 'Lower']].sort_index(ascending=False))

    else:
        st.error("데이터를 불러올 수 없습니다. 종목 코드를 확인해주세요.")

# 자동 새로고침 로직
if auto_refresh:
    time.sleep(10)
    st.rerun()