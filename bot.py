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

# --- 보조지표 계산 함수 (선택한 것만 계산) ---
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

# 1. 설정 메뉴 (사이드바 대신 상단 확장 메뉴 사용 - 모바일 공간 확보)
with st.expander("⚙️ 차트 설정 및 종목 선택", expanded=True):
    col1, col2 = st.columns(2)
    with col1:
        market = st.text_input("종목 코드", "KRW-BTC")
    with col2:
        interval_opts = {"1분": "minutes/1", "15분": "minutes/15", "1시간": "minutes/60", "4시간": "minutes/240", "1일": "days"}
        selected_interval = st.selectbox("시간 단위", list(interval_opts.keys()), index=1)
        interval = interval_opts[selected_interval]

    # 보조지표 선택 (멀티 셀렉트)
    indicators = st.multiselect(
        "보조지표 선택",
        ["MA(이동평균)", "Bollinger Bands", "RSI"],
        default=["Bollinger Bands", "RSI"] # 기본값
    )

    if st.button("새로고침"):
        st.rerun()

# 2. 데이터 로드
with st.spinner('차트 그리는 중...'):
    df = get_market_data(market, interval, count=300) # 데이터를 좀 더 많이 가져옴

    if not df.empty:
        df = add_indicators(df, indicators)
        
        # --- 차트 그리기 (Plotly) ---
        # RSI가 선택되었으면 그래프를 위아래 2개로 나눔, 아니면 1개
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
            increasing_line_color='#FF3333', # 한국 스타일 빨강(상승)
            decreasing_line_color='#3333FF'  # 한국 스타일 파랑(하락)
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
            # 기준선 30, 70 추가
            fig.add_hline(y=70, line_dash="dash", line_color="red", row=2, col=1)
            fig.add_hline(y=30, line_dash="dash", line_color="blue", row=2, col=1)

        # --- 레이아웃 디자인 (모바일 최적화 핵심) ---
        fig.update_layout(
            height=600, # 차트 전체 높이
            xaxis_rangeslider_visible=False, # 기본 레인지 슬라이더 끄고 (아래에서 커스텀 설정)
            dragmode='pan', # 기본 동작을 '드래그 이동'으로 설정
            margin=dict(l=10, r=10, t=30, b=20), # 여백 최소화
            paper_bgcolor="white",
            plot_bgcolor="white",
            showlegend=False, # 모바일 공간 위해 범례 숨김 (필요하면 True)
        )

        # X축 설정 (여기가 스크롤바 핵심)
        fig.update_xaxes(
            rangeslider_visible=True, # 하단 스크롤바 켜기!
            rangeslider_thickness=0.1, # 스크롤바 두께
            tickformat="%H:%M", # 시간 포맷 (예: 14:30)
            showgrid=True, gridcolor='#eee'
        )
        fig.update_yaxes(showgrid=True, gridcolor='#eee')

        # 차트 출력 (use_container_width=True로 화면 꽉 차게)
        st.plotly_chart(fig, use_container_width=True)

        # 현재 상태 텍스트로 요약
        curr_price = df['trade_price'].iloc[-1]
        st.success(f"현재가: {curr_price:,.0f} KRW")

    else:
        st.error("데이터를 불러오지 못했습니다. 종목 코드를 확인해주세요.")