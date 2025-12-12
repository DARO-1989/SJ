import streamlit as st
import requests
import pandas as pd
import time
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# --- 페이지 설정 ---
st.set_page_config(
    page_title="체이스의 코인 분석기",
    page_icon="📈",
    layout="centered"
)

# --- 세션 상태 초기화 ---
if 'market_code' not in st.session_state:
    st.session_state['market_code'] = "KRW-BTC"

# --- 데이터 조회 함수 ---
def get_market_data(market, interval="minutes/15", count=200):
    url = f"https://api.upbit.com/v1/candles/{interval}"
    params = {"market": market, "count": count}
    headers = {"accept": "application/json"}
    
    try:
        response = requests.get(url, params=params, headers=headers)
        data = response.json()
        if not isinstance(data, list):
            return pd.DataFrame()
        df = pd.DataFrame(data)
        df = df.sort_values(by="candle_date_time_kst").reset_index(drop=True)
        return df
    except Exception as e:
        return pd.DataFrame()

# --- 지표 계산 함수 ---
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

# --- 점수 계산 함수 ---
def get_signal_score(rsi, price, lower, upper):
    score = 0
    action = "보류"
    color = "gray"
    emoji = "😐"
    desc = "매수/매도 보류 추천드립니다."

    if rsi < 30:
        base_score = 50
        rsi_bonus = (30 - rsi) * 2.5 
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
        score = 0
    
    return action, score, emoji, desc, color

# --- Plotly 차트 생성 함수 (업그레이드) ---
def plot_candle_chart(df, market_code):
    fig = make_subplots(
        rows=2, cols=1, 
        shared_xaxes=True, 
        vertical_spacing=0.05, # 간격 좁힘
        subplot_titles=(f'{market_code}', 'RSI (14)'),
        row_width=[0.3, 0.7]
    )

    # 1. 캔들스틱 (툴팁 한글화 적용)
    # hovertemplate을 쓰면 마우스 올렸을 때 나오는 글자를 커스텀할 수 있어.
    fig.add_trace(go.Candlestick(
        x=df['candle_date_time_kst'],
        open=df['opening_price'],
        high=df['high_price'],
        low=df['low_price'],
        close=df['trade_price'],
        name='Price',
        increasing_line_color='red', # 한국식: 상승은 빨강
        decreasing_line_color='blue' # 한국식: 하락은 파랑
    ), row=1, col=1)

    # 2. 볼린저 밴드
    fig.add_trace(go.Scatter(
        x=df['candle_date_time_kst'], y=df['Upper'],
        line=dict(color='rgba(255, 0, 0, 0.3)', width=1, dash='dot'), # 반투명 빨강
        name='상단 밴드',
        hoverinfo='skip' # 밴드는 툴팁 안 뜨게 (깔끔하게)
    ), row=1, col=1)
    
    fig.add_trace(go.Scatter(
        x=df['candle_date_time_kst'], y=df['Lower'],
        line=dict(color='rgba(0, 0, 255, 0.3)', width=1, dash='dot'), # 반투명 파랑
        name='하단 밴드',
        hoverinfo='skip'
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=df['candle_date_time_kst'], y=df['MA20'],
        line=dict(color='rgba(128, 128, 128, 0.5)', width=1), 
        name='중심선',
        hoverinfo='skip'
    ), row=1, col=1)

    # 3. RSI 차트
    fig.add_trace(go.Scatter(
        x=df['candle_date_time_kst'], y=df['RSI'],
        line=dict(color='#9370DB', width=2), # 보라색
        name='RSI'
    ), row=2, col=1)

    # RSI 기준선
    fig.add_shape(type="line", x0=df['candle_date_time_kst'].iloc[0], x1=df['candle_date_time_kst'].iloc[-1],
                  y0=70, y1=70, line=dict(color="red", width=1, dash="dash"), row=2, col=1)
    fig.add_shape(type="line", x0=df['candle_date_time_kst'].iloc[0], x1=df['candle_date_time_kst'].iloc[-1],
                  y0=30, y1=30, line=dict(color="blue", width=1, dash="dash"), row=2, col=1)

    # ★ 핵심 수정: 배경 투명화 + 글자색 자동 조절
    fig.update_layout(
        height=600,
        xaxis_rangeslider_visible=False, 
        showlegend=False, # 범례가 화면 가려서 끔
        margin=dict(l=10, r=10, t=30, b=10),
        # 아래 두 줄이 다크모드 호환의 핵심! (배경 투명하게)
        paper_bgcolor='rgba(0,0,0,0)', 
        plot_bgcolor='rgba(0,0,0,0)',
        font=dict(color="gray") # 글자색은 회색으로 무난하게
    )
    
    # 그리드 색상을 아주 연하게 설정
    grid_color = 'rgba(128, 128, 128, 0.2)'
    fig.update_xaxes(showgrid=True, gridwidth=1, gridcolor=grid_color)
    fig.update_yaxes(showgrid=True, gridwidth=1, gridcolor=grid_color)

    return fig

# --- UI 레이아웃 ---
st.title("📈 체이스의 코인 분석기")

# 상단 컨트롤 패널
with st.container():
    col_input, col_int, col_btn = st.columns([2, 1, 1])
    
    with col_input:
        market_input = st.text_input("종목 코드", value=st.session_state['market_code'])
        st.session_state['market_code'] = market_input.upper()
        
    with col_int:
        interval_map = {"1분": "minutes/1", "15분": "minutes/15", "1시간": "minutes/60", "4시간": "minutes/240", "1일": "days"}
        interval_label = st.selectbox("분봉", list(interval_map.keys()), index=1)
        
    with col_btn:
        st.write("") 
        st.write("") 
        refresh = st.button("새로고침 🔄")

auto_refresh = st.checkbox("10초마다 자동 갱신", value=False)

# 분석 로직
if market_input:
    market_code = st.session_state['market_code']
    if not market_code.startswith("KRW-") and not market_code.startswith("BTC-"):
        market_code = f"KRW-{market_code}"

    df = get_market_data(market_code, interval_map[interval_label])
    df = calculate_indicators(df)

    if not df.empty:
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        
        action, score, emoji, desc, color_code = get_signal_score(
            curr['RSI'], curr['trade_price'], curr['Lower'], curr['Upper']
        )

        st.divider()

        # 메인 정보 표시
        m_col1, m_col2 = st.columns([1, 1.2])

        with m_col1:
            st.markdown("#### 현재 가격")
            price_change = curr['trade_price'] - prev['trade_price']
            price_pct = (price_change / prev['trade_price']) * 100
            st.metric(label=market_code, value=f"{curr['trade_price']:,.0f} 원", delta=f"{price_pct:.2f}%")

        with m_col2:
            st.markdown(f"#### {emoji} 투자 의견")
            st.markdown(f"""
            <div style='background-color:#f0f2f6; padding:10px; border-radius:10px;'>
                <span style='font-size:1.2em; font-weight:bold; color:{color_code}'>{desc}</span><br>
                <span style='color:gray; font-size:0.9em;'>RSI 지수: <b>{curr['RSI']:.1f}</b></span>
            </div>
            """, unsafe_allow_html=True)

        # 추천 강도
        if action != "보류":
            st.write("")
            st.markdown(f"**📊 {action} 추천 강도: {score:.1f}%**")
            st.progress(int(score))
            if score > 80:
                st.caption(f"💡 현재 과{action} 구간이 심화되었습니다. 적극적인 대응이 유효해 보입니다.")
            else:
                st.caption(f"💡 {action} 시그널이 감지되었습니다.")
        else:
            st.info("💡 현재는 특이 신호가 없는 '관망' 구간입니다.")

        st.divider()

        # 차트 영역
        tab1, tab2 = st.tabs(["📊 프로 차트", "📋 데이터 상세"])
        
        with tab1:
            chart_df = df.tail(100)
            fig = plot_candle_chart(chart_df, market_code)
            st.plotly_chart(fig, use_container_width=True)

        with tab2:
            st.dataframe(df.tail(20)[['candle_date_time_kst', 'trade_price', 'RSI', 'Upper', 'Lower']].sort_index(ascending=False))

    else:
        st.error("데이터를 불러올 수 없습니다. 종목 코드를 확인해주세요.")

if auto_refresh:
    time.sleep(10)
    st.rerun()