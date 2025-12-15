import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import time

# --- 페이지 설정 (모바일 최적화) ---
st.set_page_config(
    page_title="코인 프로 차트",
    page_icon="💎",
    layout="wide" # 화면 넓게 쓰기
)

# --- 데이터 가져오기 함수 ---
@st.cache_data(ttl=15) # 15초 동안은 데이터를 캐시해서 속도 향상
def get_market_data(market, interval, count=200):
    url = f"https://api.upbit.com/v1/candles/{interval}"
    params = {"market": market, "count": count}
    headers = {"accept": "application/json"}
    
    try:
        response = requests.get(url, params=params, headers=headers)
        data = response.json()
        df = pd.DataFrame(data)
        # 날짜 변환 (중요: 이게 없으면 X축이 안 나옴)
        df['candle_date_time_kst'] = pd.to_datetime(df['candle_date_time_kst'])
        df = df.sort_values(by="candle_date_time_kst").reset_index(drop=True)
        return df
    except Exception as e:
        return pd.DataFrame()

# --- 투자 의견 분석 함수 (핵심 로직 복구) ---
def analyze_signal(df):
    if df.empty or len(df) < 20:
        return "데이터 부족", "gray"
    
    # 신호 분석을 위해 강제로 지표 계산 (차트 표시 여부와 상관없이)
    close = df['trade_price']
    
    # 1. RSI (14)
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).fillna(0)
    loss = (-delta.where(delta < 0, 0)).fillna(0)
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()
    rs = avg_gain / avg_loss
    rsi_series = 100 - (100 / (1 + rs))
    
    # 2. 볼린저 밴드 (20, 2)
    ma20 = close.rolling(window=20).mean()
    std = close.rolling(window=20).std()
    upper = ma20 + (std * 2)
    lower = ma20 - (std * 2)
    
    # 현재 값 추출
    curr_rsi = rsi_series.iloc[-1]
    curr_price = close.iloc[-1]
    curr_upper = upper.iloc[-1]
    curr_lower = lower.iloc[-1]
    
    # 신호 판단 로직
    if curr_rsi < 30 and curr_price < curr_lower:
        return f"🚀 강력 매수 (과매도 + 하단 이탈)", "green"
    elif curr_rsi < 30:
        return f"📈 매수 권장 (RSI {curr_rsi:.1f} 과매도)", "blue"
    elif curr_price < curr_lower:
        return f"📈 매수 권장 (볼린저 밴드 하단 터치)", "blue"
    elif curr_rsi > 70 and curr_price > curr_upper:
        return f"📉 강력 매도 (과매수 + 상단 돌파)", "red"
    elif curr_rsi > 70:
        return f"📉 매도 권장 (RSI {curr_rsi:.1f} 과매수)", "orange"
    elif curr_price > curr_upper:
        return f"📉 매도 권장 (볼린저 밴드 상단 터치)", "orange"
    else:
        return f"😐 중립 / 관망 (특이 신호 없음)", "gray"

# --- 보조지표 계산 함수 (차트용) ---
def add_indicators(df, indicators):
    # 이동평균선 (MA)
    if "MA(이동평균)" in indicators:
        df['MA5'] = df['trade_price'].rolling(window=5).mean()
        df['MA20'] = df['trade_price'].rolling(window=20).mean()
        df['MA60'] = df['trade_price'].rolling(window=60).mean()

    # 볼린저 밴드 (Bollinger Bands)
    if "Bollinger Bands" in indicators:
        df['MA20_BB'] = df['trade_price'].rolling(window=20).mean()
        std = df['trade_price'].rolling(window=20).std()
        df['Upper'] = df['MA20_BB'] + (std * 2)
        df['Lower'] = df['MA20_BB'] - (std * 2)

    # RSI (상대강도지수)
    if "RSI" in indicators:
        delta = df['trade_price'].diff()
        gain = (delta.where(delta > 0, 0)).fillna(0)
        loss = (-delta.where(delta < 0, 0)).fillna(0)
        avg_gain = gain.rolling(window=14).mean()
        avg_loss = loss.rolling(window=14).mean()
        rs = avg_gain / avg_loss
        df['RSI'] = 100 - (100 / (1 + rs))

    return df

# --- 메인 UI ---
st.title("📈 업비트 프로 차트")

# 1. 설정 메뉴
with st.expander("⚙️ 차트 설정 및 종목 선택", expanded=True):
    col1, col2 = st.columns(2)
    with col1:
        market = st.text_input("종목 코드", "KRW-BTC")
    with col2:
        interval_opts = {"1분": "minutes/1", "15분": "minutes/15", "1시간": "minutes/60", "4시간": "minutes/240", "1일": "days"}
        selected_interval = st.selectbox("시간 단위", list(interval_opts.keys()), index=1)
        interval = interval_opts[selected_interval]

    indicators = st.multiselect(
        "보조지표 선택",
        ["MA(이동평균)", "Bollinger Bands", "RSI"],
        default=["Bollinger Bands", "RSI"]
    )

    if st.button("새로고침"):
        st.rerun()

# 2. 데이터 로드 및 분석
with st.spinner('차트 그리는 중...'):
    df = get_market_data(market, interval, count=300)

    if not df.empty:
        # 투자 의견 분석 (차트 그리기 전에 먼저 계산해서 보여줌)
        signal_text, signal_color = analyze_signal(df)
        curr_price = df['trade_price'].iloc[-1]
        
        # 상단 정보 박스 (메트릭 + 신호)
        m_col1, m_col2 = st.columns([1, 2])
        with m_col1:
            prev_price = df['trade_price'].iloc[-2]
            change = curr_price - prev_price
            st.metric(label="현재가", value=f"{curr_price:,.0f} KRW", delta=f"{change:,.0f} KRW")
        with m_col2:
            st.markdown(f"""
            <div style='padding: 10px; border-radius: 5px; background-color: {signal_color}; color: white; text-align: center; font-weight: bold;'>
                {signal_text}
            </div>
            """, unsafe_allow_html=True)
            
        # 차트 데이터 준비
        df = add_indicators(df, indicators)
        
        # --- 차트 그리기 (Plotly) ---
        rows = 2 if "RSI" in indicators else 1
        row_heights = [0.7, 0.3] if "RSI" in indicators else [1.0]
        
        fig = make_subplots(
            rows=rows, cols=1, 
            shared_xaxes=True, 
            vertical_spacing=0.05,
            row_heights=row_heights
        )

        # [메인 차트] 캔들스틱
        fig.add_trace(go.Candlestick(
            x=df['candle_date_time_kst'],
            open=df['opening_price'], high=df['high_price'],
            low=df['low_price'], close=df['trade_price'],
            name='Price',
            increasing_line_color='#FF3333',
            decreasing_line_color='#3333FF'
        ), row=1, col=1)

        # [지표] 이동평균선
        if "MA(이동평균)" in indicators:
            fig.add_trace(go.Scatter(x=df['candle_date_time_kst'], y=df['MA5'], line=dict(color='orange', width=1), name='MA5'), row=1, col=1)
            fig.add_trace(go.Scatter(x=df['candle_date_time_kst'], y=df['MA20'], line=dict(color='violet', width=1), name='MA20'), row=1, col=1)
            fig.add_trace(go.Scatter(x=df['candle_date_time_kst'], y=df['MA60'], line=dict(color='green', width=1), name='MA60'), row=1, col=1)

        # [지표] 볼린저 밴드
        if "Bollinger Bands" in indicators:
            fig.add_trace(go.Scatter(x=df['candle_date_time_kst'], y=df['Upper'], line=dict(color='gray', width=1, dash='dot'), name='BB Upper'), row=1, col=1)
            fig.add_trace(go.Scatter(x=df['candle_date_time_kst'], y=df['Lower'], line=dict(color='gray', width=1, dash='dot'), fill='tonexty', fillcolor='rgba(200,200,200,0.1)', name='BB Lower'), row=1, col=1)

        # [서브 차트] RSI
        if "RSI" in indicators:
            fig.add_trace(go.Scatter(x=df['candle_date_time_kst'], y=df['RSI'], line=dict(color='purple', width=2), name='RSI'), row=2, col=1)
            fig.add_hline(y=70, line_dash="dash", line_color="red", row=2, col=1)
            fig.add_hline(y=30, line_dash="dash", line_color="blue", row=2, col=1)

        # --- 레이아웃 디자인 ---
        fig.update_layout(
            height=600,
            xaxis_rangeslider_visible=False,
            dragmode='pan',
            margin=dict(l=10, r=10, t=30, b=20),
            paper_bgcolor="white",
            plot_bgcolor="white",
            showlegend=False,
        )

        fig.update_xaxes(
            rangeslider_visible=True,
            rangeslider_thickness=0.1,
            tickformat="%H:%M",
            showgrid=True, gridcolor='#eee'
        )
        fig.update_yaxes(showgrid=True, gridcolor='#eee')

        st.plotly_chart(fig, use_container_width=True)

    else:
        st.error("데이터를 불러오지 못했습니다. 종목 코드를 확인해주세요.")