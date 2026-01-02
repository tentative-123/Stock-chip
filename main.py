import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import yfinance as yf
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from io import StringIO
import time
import re
from datetime import datetime, timedelta
import pytz
from urllib.parse import urlparse, parse_qs
import shutil
import twstock
import copy
import numpy as np

# ================= 1. 系統設定 =================

st.set_page_config(layout="wide", page_title="籌碼K線", initial_sidebar_state="auto")

# ✅ CSS 優化：修正選取器，針對 Plotly 內層元素設定 touch-action
st.markdown("""
    <style>
    /* --- 通用字體設定 --- */
    html, body, [class*="css"] { font-size: 18px !important; }
    .stDataFrame { font-size: 16px !important; }
    
    /* --- 數據卡片樣式 --- */
    .metric-container {
        display: flex;
        justify-content: space-between;
        background-color: #262730;
        padding: 10px;
        border-radius: 5px;
        margin-top: 5px;
        flex-wrap: wrap;
    }
    .metric-item {
        text-align: center;
        width: 48%;
        min-width: 100px;
    }
    .metric-label {
        font-size: 0.9rem;
        color: #aaa;
        white-space: nowrap;
    }
    .metric-value {
        font-size: 1.2rem;
        font-weight: bold;
    }

    /* --- 手機版 RWD (螢幕 < 768px) --- */
    @media (max-width: 768px) {
        html, body, [class*="css"] { font-size: 15px !important; }
        .stDataFrame { font-size: 14px !important; }
        h1 { font-size: 1.8rem !important; }
        h2 { font-size: 1.5rem !important; }
        h3 { font-size: 1.3rem !important; }
        .metric-container { padding: 8px; gap: 5px; }
        .metric-label { font-size: 0.8rem; }
        .metric-value { font-size: 1rem; }
        
        /* 手機時：隱藏包含 desktop-marker 的容器 */
        div[data-testid="stVerticalBlock"]:has(> .element-container .desktop-marker) {
            display: none !important;
        }

        /* ✅ 針對 Plotly 內層設定 touch-action，讓手機雙指縮放生效 */
        div[data-testid="stPlotlyChart"] .js-plotly-plot,
        div[data-testid="stPlotlyChart"] .plotly,
        div[data-testid="stPlotlyChart"] canvas {
            touch-action: none !important;
        }
        
        /* 避免容器被頁面捲動搶走 */
        div[data-testid="stPlotlyChart"] {
            overscroll-behavior: contain;
        }
    }

    /* --- 電腦版 RWD (螢幕 > 768px) --- */
    @media (min-width: 769px) {
        /* 電腦時：隱藏包含 mobile-marker 的容器 */
        div[data-testid="stVerticalBlock"]:has(> .element-container .mobile-marker) {
            display: none !important;
        }
    }
    
    /* 強制讓 Plotly 區塊優先吃 touch 事件 */
    div[data-testid="stPlotlyChart"] > div {
      touch-action: none;   
    }
    </style>
    """, unsafe_allow_html=True)

COLOR_UP = '#ef5350'
COLOR_DOWN = '#26a69a'

# ================= 2. 輔助函式 =================

def normalize_name(name):
    return str(name).strip().replace(" ", "").replace("　", "")

def get_stock_name(stock_id):
    try:
        if stock_id in twstock.codes:
            return twstock.codes[stock_id].name
        return ""
    except:
        return ""

def render_broker_table(df, sum_data, color_hex, title):
    st.markdown(f"#### {title}")
    
    if "買超" in title:
        label_total = "🔴 合計買超張數"
        label_avg = "🔴 平均買超成本"
    else:
        label_total = "🟢 合計賣超張數"
        label_avg = "🟢 平均賣超成本"

    full_config = {
        "broker": "券商分點",
        "buy": st.column_config.NumberColumn("買進", format="%d"),
        "sell": st.column_config.NumberColumn("賣出", format="%d"),
        "net": st.column_config.NumberColumn("買賣超", format="%d"),
        "pct": "佔比"
    }
    
    st.dataframe(
        df.style.map(lambda x: f'color: {color_hex}; font-weight: bold', subset=['net']),
        use_container_width=True, height=500, hide_index=True, column_config=full_config
    )
    
    st.markdown(f"""
    <div class="metric-container" style="border-left: 5px solid {color_hex};">
        <div class="metric-item">
            <div class="metric-label">{label_total}</div>
            <div class="metric-value" style="color: {color_hex};">{sum_data['total']}</div>
        </div>
        <div class="metric-item">
            <div class="metric-label">{label_avg}</div>
            <div class="metric-value">{sum_data['avg']}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

# ================= 3. 爬蟲核心 =================

@st.cache_resource
def get_driver_path():
    return ChromeDriverManager().install()

def get_driver():
    options = Options()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    if shutil.which("chromium"):
        options.binary_location = shutil.which("chromium")
    elif shutil.which("chromium-browser"):
        options.binary_location = shutil.which("chromium-browser")
        
    if shutil.which("chromedriver"):
        service = Service(shutil.which("chromedriver"))
    else:
        service = Service(get_driver_path())

    driver = webdriver.Chrome(service=service, options=options)
    return driver

def calculate_date_range(stock_id, days):
    try:
        adj_days = days
        if days >= 120:
            adj_days = days - 1
            
        ticker = f"{stock_id}.TW"
        df = yf.Ticker(ticker).history(period=f"{max(adj_days + 60, 200)}d")
        
        if df.empty:
            ticker = f"{stock_id}.TWO"
            df = yf.Ticker(ticker).history(period=f"{max(adj_days + 60, 200)}d")
            
        if df.empty:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=adj_days * 1.5)
            return start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')
            
        df_target = df.tail(adj_days)
        start_date = df_target.index[0].strftime('%Y-%m-%d')
        end_date = df_target.index[-1].strftime('%Y-%m-%d')
        return start_date, end_date
    except:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        return start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')

@st.cache_data(persist="disk", ttl=604800)
def get_real_data_matrix(stock_id, start_date, end_date, refresh_nonce=0):
    driver = get_driver()
    base_url = "https://fubon-ebrokerdj.fbs.com.tw/z/zc/zco/zco.djhtm"
    url = f"{base_url}?a={stock_id}&e={start_date}&f={end_date}"

    try:
        driver.get(url)
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//*[contains(text(), '買超券商')]"))
            )
        except:
            return None, None, None, None, None, url

        html = driver.page_source
        tables = pd.read_html(StringIO(html), match="買超券商")
        if not tables:
            return None, None, None, None, None, url
        df = tables[0]
        
        header_row = -1
        for i, row in df.iterrows():
            row_str = row.astype(str).values
            if "買超券商" in row_str and "賣超券商" in row_str:
                header_row = i
                break
        if header_row == -1:
            return None, None, None, None, None, url

        broker_info = {}
        try:
            links = driver.find_elements(By.XPATH, "//table//a[contains(@href, 'zco0/zco0.djhtm')]")
            for link in links:
                name = normalize_name(link.text)
                href = link.get_attribute('href')
                if name and href:
                    parsed = urlparse(href)
                    params = parse_qs(parsed.query)
                    if 'b' in params and 'BHID' in params:
                        broker_info[name] = {
                            'b': params['b'][0],
                            'BHID': params['BHID'][0]
                        }
        except:
            pass

        sum_buy = {"total": "0", "avg": "0"}
        sum_sell = {"total": "0", "avg": "0"}
        
        try:
            total_buy_elem = driver.find_element(By.XPATH, "/html/body/div[1]/table/tbody/tr[2]/td[2]/table/tbody/tr/td/form/table/tbody/tr/td/table/tbody/tr[22]/td[2]")
            sum_buy['total'] = total_buy_elem.text.strip()
            
            avg_buy_elem = driver.find_element(By.XPATH, "/html/body/div[1]/table/tbody/tr[2]/td[2]/table/tbody/tr/td/form/table/tbody/tr/td/table/tbody/tr[23]/td[2]")
            sum_buy['avg'] = avg_buy_elem.text.strip()

            total_sell_elem = driver.find_element(By.XPATH, "/html/body/div[1]/table/tbody/tr[2]/td[2]/table/tbody/tr/td/form/table/tbody/tr/td/table/tbody/tr[22]/td[4]")
            sum_sell['total'] = total_sell_elem.text.strip()
            
            avg_sell_elem = driver.find_element(By.XPATH, "/html/body/div[1]/table/tbody/tr[2]/td[2]/table/tbody/tr/td/form/table/tbody/tr/td/table/tbody/tr[23]/td[4]")
            sum_sell['avg'] = avg_sell_elem.text.strip()
        except Exception:
            pass

        df_clean = df.iloc[header_row+1:].copy()
        df_buy = df_clean.iloc[:, [0, 1, 2, 3, 4]].copy()
        df_buy.columns = ['broker', 'buy', 'sell', 'net', 'pct']
        df_sell = df_clean.iloc[:, [5, 6, 7, 8, 9]].copy()
        df_sell.columns = ['broker', 'buy', 'sell', 'net', 'pct']

        def clean_sub_df(d):
            d = d.dropna(subset=['broker'])
            mask = d['broker'].astype(str).str.contains("合計|平均|買超券商|賣超券商", na=False)
            d = d[~mask]
            for col in ['buy', 'sell', 'net']:
                d[col] = d[col].astype(str).str.replace(',', '', regex=False).str.replace('+', '', regex=False).str.replace('nan', '', regex=False)
                d[col] = pd.to_numeric(d[col], errors='coerce').fillna(0).astype(int)
            return d

        df_buy = clean_sub_df(df_buy)
        df_sell = clean_sub_df(df_sell)
        df_buy = df_buy[df_buy['net'] > 0].sort_values('net', ascending=False).head(15).reset_index(drop=True)
        df_sell['abs_net'] = df_sell['net'].abs()
        df_sell = df_sell.sort_values('abs_net', ascending=False).head(15).drop(columns=['abs_net']).reset_index(drop=True)

        return df_buy, df_sell, sum_buy, sum_sell, broker_info, url
    except:
        return None, None, None, None, None, url
    finally:
        driver.quit()

# ✅ 使用 tuple key 增加 cache 穩定性
@st.cache_data(persist="disk", ttl=604800)
def get_specific_broker_daily(stock_id, broker_key, start_date, end_date, refresh_nonce=0):
    BHID, b, c_val = broker_key
    driver = get_driver()
    base_url = "https://fubon-ebrokerdj.fbs.com.tw/z/zc/zco/zco0/zco0.djhtm"
    target_url = (f"{base_url}?A={stock_id}"
                  f"&BHID={BHID}"
                  f"&b={b}"
                  f"&C={c_val}"
                  f"&D={start_date}"
                  f"&E={end_date}"
                  f"&ver=V3")

    table_xpath = "/html/body/div[1]/table/tbody/tr[2]/td[2]/table/tbody/tr/td/form/table/tbody/tr/td/table/tbody/tr[6]/td/table"

    try:
        driver.get(target_url)
        all_dfs = []
        page_count = 0
        max_pages = 60
        
        while page_count < max_pages:
            try:
                WebDriverWait(driver, 3).until(
                    EC.presence_of_element_located((By.XPATH, table_xpath))
                )
            except:
                break

            try:
                target_table = driver.find_element(By.XPATH, table_xpath)
                table_html = target_table.get_attribute('outerHTML')
                tables = pd.read_html(StringIO(table_html))
                current_df = tables[0] if tables else None
            except:
                html = driver.page_source
                tables = pd.read_html(StringIO(html), match="日期")
                current_df = tables[0] if tables else None

            if current_df is not None:
                all_dfs.append(current_df)
            
            try:
                next_links = driver.find_elements(By.XPATH, "//a[contains(text(), '下一頁')]")
                if next_links and next_links[0].is_enabled():
                    next_links[0].click()
                    time.sleep(0.5) 
                    page_count += 1
                else:
                    break 
            except:
                break

        if not all_dfs:
            return None, target_url

        df = pd.concat(all_dfs, ignore_index=True)
        df.columns = [str(c).strip().replace(" ", "") for c in df.columns]
        
        if '買賣超' not in df.columns and len(df.columns) >= 4:
            df = df.iloc[:, :4]
            df.columns = ['日期', '買進', '賣出', '買賣超']
            
        required = ['日期', '買進', '賣出', '買賣超']
        if not all(c in df.columns for c in required):
            return None, target_url

        df = df[df['日期'] != '日期']
        
        for col in ['買進', '賣出', '買賣超']:
             df[col] = (df[col].astype(str)
                        .str.replace(',', '', regex=False)
                        .str.replace('+', '', regex=False)
                        .str.replace('nan', '', regex=False))
             df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        df['買賣超_Calc'] = df['買進'] - df['賣出']

        def parse_date(d_str):
            s = str(d_str).strip()
            parts = re.split(r'[/-]', s)
            if len(parts) == 3:
                y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
                if y < 1911: y += 1911
                return f"{y:04d}-{m:02d}-{d:02d}"
            elif len(parts) == 2:
                m, d = int(parts[0]), int(parts[1])
                now = datetime.now()
                y = now.year
                if m > now.month + 2: y -= 1
                return f"{y:04d}-{m:02d}-{d:02d}"
            return None

        df['DateStr'] = df['日期'].apply(parse_date)
        df = df.dropna(subset=['DateStr'])
        df = df.sort_values('DateStr', ascending=True)
        
        return df, target_url
        
    except Exception:
        return None, target_url
    finally:
        driver.quit()

@st.cache_data(ttl=21600)
def get_stock_price(stock_id):
    ticker = f"{stock_id}.TW" if not stock_id.endswith('.TW') else stock_id
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period="2y")
        if df.empty:
            ticker = f"{stock_id}.TWO"
            stock = yf.Ticker(ticker)
            df = stock.history(period="2y")
        if df.empty: return None
        df.index = df.index.tz_localize(None)
        df['DateStr'] = df.index.strftime('%Y-%m-%d')
        
        df['MA5'] = df['Close'].rolling(window=5).mean()
        df['MA10'] = df['Close'].rolling(window=10).mean()
        df['MA20'] = df['Close'].rolling(window=20).mean()
        df['MA60'] = df['Close'].rolling(window=60).mean()

        return df
    except Exception:
        return None


def build_strategy_notes(merged_df, df_price, rank_start_date, rank_end_date):
    """根據分點籌碼與股價位置，產生簡短的策略備註。"""

    def add_note(unique_notes, text):
        if text and text not in unique_notes:
            unique_notes.append(text)

    notes = []

    base_df = merged_df if merged_df is not None and not merged_df.empty else df_price
    if base_df is None or base_df.empty:
        return ["暫無足夠資料產生策略備註。"]

    df = base_df.copy()
    df["Date"] = pd.to_datetime(df.get("DateStr"), errors="coerce")
    df = df.dropna(subset=["Date"]).sort_values("Date")

    last_row = df.iloc[-1]
    last_close = pd.to_numeric(last_row.get("Close"), errors="coerce")
    ma20 = pd.to_numeric(last_row.get("MA20"), errors="coerce")
    ma60 = pd.to_numeric(last_row.get("MA60"), errors="coerce")

    # 1) 股價位置與均線排列
    if pd.notna(last_close) and pd.notna(ma20) and pd.notna(ma60):
        if last_close >= ma20 >= ma60:
            add_note(notes, "股價在月/季線之上，均線多頭排列，偏多續看。")
        elif last_close >= ma20 and last_close < ma60:
            add_note(notes, "股價介於月線與季線，短線撐在月線但中期仍需觀察。")
        elif last_close >= ma60 and last_close < ma20:
            add_note(notes, "股價站季線但壓在月線下，若能回到月線上方偏多。")
        else:
            add_note(notes, "股價跌破月/季線，下檔需嚴控風險或等待止穩。")
    elif pd.notna(last_close) and pd.notna(ma20):
        add_note(notes, "股價相對月線仍具參考價值，建議密切追蹤月線攻防。")

    # 均線角度輔助判斷（使用近 5 日斜率）
    if len(df) >= 6 and pd.notna(df["MA20"].iloc[-1]):
        ma20_slope = pd.to_numeric(df["MA20"].iloc[-1] - df["MA20"].iloc[-6], errors="coerce")
        if pd.notna(ma20_slope):
            if ma20_slope > 0:
                add_note(notes, "月線近一週走升，趨勢動能偏正向。")
            elif ma20_slope < 0:
                add_note(notes, "月線近一週下彎，攻擊需提防轉弱。")

    # 2) 分點買賣超趨勢
    if merged_df is not None and not merged_df.empty and "買賣超_Final" in merged_df.columns:
        df_broker = merged_df.copy()
        df_broker["Date"] = pd.to_datetime(df_broker.get("DateStr"), errors="coerce")
        df_broker = df_broker.dropna(subset=["Date"]).sort_values("Date")
        df_broker["買賣超_Final"] = pd.to_numeric(df_broker.get("買賣超_Final", 0), errors="coerce").fillna(0)
        df_broker["cumulative_net"] = pd.to_numeric(df_broker.get("cumulative_net", 0), errors="coerce")

        # 近五日資金
        recent_5 = df_broker.tail(5)["買賣超_Final"].sum()
        if recent_5 > 0:
            add_note(notes, f"近五日累計買超 {recent_5:,.0f} 張，短線資金偏多。")
        elif recent_5 < 0:
            add_note(notes, f"近五日累計賣超 {abs(recent_5):,.0f} 張，短線偏空或獲利了結。")

        # 連續買賣超判讀
        tail_net = df_broker["買賣超_Final"].tail(3)
        if len(tail_net) == 3:
            if (tail_net > 0).all():
                add_note(notes, "分點連續 3 日買超，觀察是否帶動股價同步走高。")
            elif (tail_net < 0).all():
                add_note(notes, "分點連續 3 日賣超，留意籌碼鬆動或短線修正。")

        # 排名區間籌碼
        if rank_start_date and rank_end_date:
            rank_mask = (
                df_broker["DateStr"] >= rank_start_date
            ) & (
                df_broker["DateStr"] <= rank_end_date
            )
            rank_net = df_broker.loc[rank_mask, "買賣超_Final"].sum()
            if rank_net > 0:
                add_note(notes, f"統計區間累計買超 {rank_net:,.0f} 張，主力有加碼跡象。")
            elif rank_net < 0:
                add_note(notes, f"統計區間累計賣超 {abs(rank_net):,.0f} 張，需留意調節壓力。")
        else:
            add_note(notes, "未提供統計區間，僅供日線籌碼參考。")

        # 庫存變化：兩週累計
        if len(df_broker) >= 10:
            net_diff = df_broker["cumulative_net"].iloc[-1] - df_broker["cumulative_net"].iloc[-10]
            if net_diff > 0:
                add_note(notes, "近兩週庫存累積向上，買盤逐步增加。")
            elif net_diff < 0:
                add_note(notes, "近兩週庫存下降，分點正在出貨或降風險。")

    if not notes:
        add_note(notes, "資料正常但尚無明確方向，請搭配其他指標。")

    return notes

# ================= 4. 介面邏輯 =================

st.title(f"📊 籌碼K線")

tz = pytz.timezone('Asia/Taipei')
current_time = datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')

with st.sidebar:
    st.header("參數設定")
    stock_input_raw = st.text_input("股票代號", value="2313")
    stock_input = re.sub(r'\D', '', str(stock_input_raw)) if stock_input_raw else ""
    
    days_map = {
        "1日": 1, 
        "5日": 5, 
        "10日": 10, 
        "20日": 20, 
        "40日": 40, 
        "60日": 60, 
        "120日": 120, 
        "240日": 240
    }
    days_label = st.selectbox("統計天數 (交易日)", list(days_map.keys()), index=6) 
    selected_days = days_map[days_label]
    
    st.markdown(f"🕒 資料抓取時間: {current_time}")
    
    if st.button("查詢", type="primary"):
        st.rerun()
    
    # 強制更新按鈕
    if "refresh_nonce" not in st.session_state:
        st.session_state.refresh_nonce = 0
    if st.button("🔄 強制更新籌碼資料（忽略快取）"):
        st.session_state.refresh_nonce = int(time.time())
        st.rerun()

if stock_input:
    stock_name = get_stock_name(stock_input)
    stock_display = f"{stock_input} {stock_name}" if stock_name else stock_input

    rank_start_date, rank_end_date = calculate_date_range(stock_input, selected_days)
    
    with st.spinner(f"正在分析 {stock_display} 近 {selected_days} 交易日 ({rank_start_date} ~ {rank_end_date})..."):
        df_buy, df_sell, sum_buy, sum_sell, broker_info, target_url = get_real_data_matrix(
            stock_input, rank_start_date, rank_end_date, st.session_state.refresh_nonce
        )
        
    df_price = get_stock_price(stock_input)

    if df_buy is not None and df_sell is not None:
        st.subheader(f"🏆 {stock_display} 區間累積 ({rank_start_date} ~ {rank_end_date}) - 主力買賣超排行")
        st.caption(f"排行總表網址：{target_url}")
        
        with st.container():
            st.markdown('<div class="desktop-marker"></div>', unsafe_allow_html=True)
            col1, col2 = st.columns(2)
            with col1:
                render_broker_table(df_buy, sum_buy, COLOR_UP, "🔴 買超前 15 大")
            with col2:
                render_broker_table(df_sell, sum_sell, COLOR_DOWN, "🟢 賣超前 15 大")

        with st.container():
            st.markdown('<div class="mobile-marker"></div>', unsafe_allow_html=True)
            tab1, tab2 = st.tabs(["🔴 買超排行", "🟢 賣超排行"])
            with tab1:
                render_broker_table(df_buy, sum_buy, COLOR_UP, "🔴 買超前 15 大")
            with tab2:
                render_broker_table(df_sell, sum_sell, COLOR_DOWN, "🟢 賣超前 15 大")

        st.markdown("---")

        if df_price is not None and not df_price.empty:
            st.subheader("🔍 分點進出 vs 股價走勢")
            
            ma_options = ['MA5', 'MA10', 'MA20', 'MA60']
            selected_mas = st.multiselect("選擇要顯示的均線", ma_options, default=['MA5', 'MA10', 'MA20'])
            
            brokers_list = df_buy['broker'].tolist() + df_sell['broker'].tolist()
            brokers_list = list(dict.fromkeys(brokers_list))
            
            target_broker = st.selectbox("選擇要查看每日明細的券商", brokers_list)
            
            merged_df = None
            target_key = normalize_name(target_broker)
            
            broker_params = None
            if broker_info:
                if target_key in broker_info:
                    broker_params = broker_info[target_key]
                else:
                    for k, v in broker_info.items():
                        if target_key in k or k in target_key:
                            broker_params = v
                            break

            if broker_params:
                long_start_date = df_price['DateStr'].iloc[0] 
                long_end_date = df_price['DateStr'].iloc[-1] 
                
                broker_key = (broker_params['BHID'], broker_params['b'], broker_params.get('C', '1'))
                merged_key = (stock_input, broker_key, st.session_state.refresh_nonce)

                if st.session_state.get('merged_key') != merged_key:
                    with st.spinner(f"正在爬取 {target_broker} 完整 2 年每日明細..."):
                        broker_daily_df, detail_url = get_specific_broker_daily(
                            stock_input, broker_key, long_start_date, long_end_date, st.session_state.refresh_nonce
                        )
                        
                        st.markdown(f"**🔗 正在爬取單一券商網址：** `{detail_url}`")
                        
                        if broker_daily_df is not None and not broker_daily_df.empty:
                            broker_daily_df = broker_daily_df.drop_duplicates(subset=["DateStr"], keep="last").sort_values('DateStr')
                            merged_df = pd.merge(df_price, broker_daily_df, on='DateStr', how='left')
                            merged_df['買賣超_Final'] = merged_df['買賣超_Calc'].fillna(0)
                            merged_df['cumulative_net'] = merged_df['買賣超_Final'].cumsum()
                            
                            st.success(f"✅ 已載入 {target_broker} 2 年籌碼明細")
                            st.session_state['merged_df'] = merged_df
                            st.session_state['merged_key'] = merged_key
                        else:
                            st.session_state.pop('merged_df', None)
                            st.session_state['merged_key'] = merged_key
                            st.warning("⚠️ 該券商明細抓取失敗，先顯示純股價")
                else:
                    merged_df = st.session_state.get('merged_df')

            strategy_notes = build_strategy_notes(merged_df, df_price, rank_start_date, rank_end_date)

            st.markdown("#### 🧭 策略備註")
            for note in strategy_notes:
                st.markdown(f"- {note}")

            # 安全更新函式
            def safe_update_yaxes(fig, row, col, **kwargs):
                try:
                    fig.update_yaxes(row=row, col=col, **kwargs)
                except ValueError:
                    kwargs.pop("showspikelabels", None)
                    kwargs.pop("spikesnap", None)
                    kwargs.pop("ticklabelposition", None)
                    fig.update_yaxes(row=row, col=col, **kwargs)

            def safe_update_xaxes(fig, row, col, **kwargs):
                try:
                    fig.update_xaxes(row=row, col=col, **kwargs)
                except ValueError:
                    kwargs.pop("showspikelabels", None)
                    kwargs.pop("spikesnap", None)
                    kwargs.pop("ticklabelposition", None)
                    fig.update_xaxes(row=row, col=col, **kwargs)

            # 使用 Streamlit 原生按鈕控制區間
            plot_df = merged_df if merged_df is not None else df_price
            plot_df = plot_df.copy()
            
            # ✅ 先保證 Date 欄位存在並排序，避免 KeyError
            plot_df["Date"] = pd.to_datetime(plot_df["DateStr"], errors="coerce")
            plot_df = plot_df.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)
            
            # --- 區間按鈕邏輯 ---
            last_dt = plot_df['Date'].iloc[-1] 

            def dt_nbars(n: int):
                idx = max(0, len(plot_df) - n)
                return plot_df['Date'].iloc[idx]

            ranges = {
                "20日": (dt_nbars(20), last_dt),
                "3月":  (dt_nbars(60), last_dt),
                "6月":  (dt_nbars(120), last_dt),
                "1年":  (dt_nbars(240), last_dt),
                "全部": (plot_df['Date'].iloc[0], last_dt),
            }

            if "range_key" not in st.session_state:
                st.session_state.range_key = "3月"

            # 放置按鈕 (自動適配寬度)
            cols = st.columns(5)
            keys = ["20日", "3月", "6月", "1年", "全部"]
            for i, k in enumerate(keys):
                if cols[i].button(k, use_container_width=True):
                    st.session_state.range_key = k
            
            start_dt, end_dt = ranges[st.session_state.range_key]
            x_range_end_val = end_dt + timedelta(days=3)

            # ---------------------

            fig = make_subplots(
                rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, 
                row_heights=[0.85, 0.15], specs=[[{"secondary_y": False}], [{"secondary_y": True}]]
            )
            
            if '買賣超_Final' not in plot_df.columns:
                plot_df['買賣超_Final'] = 0

            # 移除重複的 Date 轉換
            x_data = plot_df['Date']

            trading_days = pd.to_datetime(plot_df['Date']).dt.normalize().dropna().unique()
            trading_days = pd.DatetimeIndex(trading_days).sort_values()

            min_dt = trading_days[0]
            last_dt_calc = trading_days[-1]

            all_days = pd.date_range(min_dt, last_dt_calc, freq="D")
            missing_days_dt = pd.date_range(min_dt, last_dt_calc, freq="D").difference(trading_days)
            missing_dates = [d.strftime("%Y-%m-%d") for d in missing_days_dt]

            plot_df["買賣超_Final"] = pd.to_numeric(plot_df.get("買賣超_Final", 0), errors="coerce").fillna(0)

            custom = np.stack([
                plot_df["DateStr"].astype(str).to_numpy(),
                plot_df["買賣超_Final"].to_numpy(dtype=float),
            ], axis=-1)

            # K線圖
            fig.add_trace(go.Candlestick(
                x=x_data, open=plot_df['Open'], high=plot_df['High'],
                low=plot_df['Low'], close=plot_df['Close'], name='股價',
                increasing_line_color=COLOR_UP, decreasing_line_color=COLOR_DOWN,
                increasing_fillcolor=COLOR_UP, decreasing_fillcolor=COLOR_DOWN,
                hoverinfo="skip" 
            ), row=1, col=1)

            # 隱形 Close 點
            fig.add_trace(go.Scatter(
                x=x_data,
                y=plot_df["Close"],
                mode="markers",
                marker=dict(size=18, opacity=0), 
                customdata=custom,
                hovertemplate=(
                    "<b>日期：%{customdata[0]}</b><br>"
                    "<b>收盤：%{y:.1f}</b><br>"
                    "<b>買賣超：%{customdata[1]:,.0f} 張</b>"
                    "<extra></extra>"
                ),
                showlegend=False,
            ), row=1, col=1)

            ma_colors = {'MA5': 'orange', 'MA10': 'cyan', 'MA20': 'magenta', 'MA60': 'green'}
            for ma in selected_mas:
                if ma in plot_df.columns:
                    plot_df[ma] = pd.to_numeric(plot_df[ma], errors='coerce')
                    fig.add_trace(go.Scatter(
                        x=x_data, y=plot_df[ma], name=ma,
                        mode='lines',
                        connectgaps=True,
                        line=dict(color=ma_colors.get(ma, 'white'), width=1.5),
                        hoverinfo='skip' 
                    ), row=1, col=1)

            if merged_df is not None:
                extended_buy_sell = list(merged_df['買賣超_Final'])
                
                merged_df['cumulative_net'] = pd.to_numeric(merged_df['cumulative_net'], errors='coerce')
                
                bar_colors = [
                    COLOR_UP if (v is not None and v > 0) else 
                    COLOR_DOWN if (v is not None and v < 0) else 'gray' 
                    for v in extended_buy_sell
                ]
                
                fig.add_trace(go.Bar(
                    x=x_data, 
                    y=extended_buy_sell, 
                    name='每日買賣超', 
                    marker_color=bar_colors,
                    opacity=0.55,
                    hoverinfo='skip'
                ), row=2, col=1, secondary_y=False)
                
                fig.add_trace(go.Scatter(
                    x=x_data,
                    y=merged_df['cumulative_net'],
                    name='兩年累計買賣超',
                    mode='lines',
                    line=dict(color='yellow', width=2.5),
                    connectgaps=True,
                    hoverinfo='skip'
                ), row=2, col=1, secondary_y=True)
                
                start_dt_vrect = pd.to_datetime(rank_start_date)
                end_dt_vrect = pd.to_datetime(rank_end_date)

                fig.add_vrect(
                    x0=start_dt_vrect, 
                    x1=end_dt_vrect,
                    fillcolor="gray", 
                    opacity=0.15, 
                    layer="below", 
                    line_width=0,
                    annotation_text="統計區間", 
                    annotation_position="top left",
                    row='all', col=1
                )

            # 設定 Y 軸
            safe_update_yaxes(
                fig, row=1, col=1,
                autorange=True,
                fixedrange=True,
                showgrid=True, gridcolor='rgba(128,128,128,0.2)',
                ticklabelposition="inside", 
                tickfont=dict(size=10, color='rgba(255,255,255,0.7)'),
                showspikes=True, spikemode="across", spikesnap="data",
                showspikelabels=True, 
                spikedash="solid", spikecolor="rgba(255,255,255,0.6)", spikethickness=1
            )
            fig.update_yaxes(
                fixedrange=True, 
                showticklabels=True, 
                row=2, col=1, 
                secondary_y=False, 
                showgrid=True, gridcolor='rgba(128,128,128,0.2)',
                ticklabelposition="inside", 
                tickfont=dict(size=10, color='rgba(255,255,255,0.7)')
            )
            fig.update_yaxes(
                fixedrange=True, 
                showticklabels=True, 
                row=2, col=1, 
                secondary_y=True, 
                showgrid=False,
                ticklabelposition="inside", 
                tickfont=dict(size=10, color='yellow')
            )

            # ✅ 修正：使用 Streamlit 按鈕計算出的 range
            safe_update_xaxes(
                fig, row=1, col=1,
                type='date',
                rangebreaks=[dict(values=missing_dates)], 
                range=[start_dt, x_range_end_val], 
                fixedrange=False,
                showspikes=True, spikemode="across", spikesnap="data",
                showspikelabels=True,
                spikedash="solid", spikecolor="rgba(255,255,255,0.6)", spikethickness=1
            )
            
            safe_update_xaxes(
                fig, row=2, col=1,
                type='date',
                rangebreaks=[dict(values=missing_dates)], 
                range=[start_dt, x_range_end_val], 
                fixedrange=False,
                showspikes=True, spikemode="across", spikesnap="data",
                showspikelabels=True,
                spikedash="solid", spikecolor="rgba(255,255,255,0.6)", spikethickness=1
            )

            # ✅ 修正：移除 updatemenus，優化 hoverlabel 樣式 (字體 16 + 加粗)
            fig.update_layout(
                xaxis_rangeslider_visible=False, 
                plot_bgcolor='rgba(20,20,20,1)', 
                paper_bgcolor='rgba(20,20,20,1)',
                font=dict(color='white', size=12), 
                title=dict(
                    text=f"{stock_display} - {target_broker if target_broker else '股價'} 籌碼追蹤", 
                    font=dict(size=28, color='white'), 
                    x=0, xanchor="left",
                    y=0.985, yanchor="top",
                    pad=dict(t=8, b=0, l=0, r=0)
                ), 
                hovermode='x unified', 
                hoverlabel=dict(
                    bgcolor="rgba(0,0,0,0.78)", # 深色背景
                    bordercolor="rgba(255,255,255,0.25)",
                    font=dict(color="white", size=16), # 放大字體
                    align="left"
                ),
                spikedistance=-1, 
                hoverdistance=50,
                legend=dict(orientation="h", y=0.88, yanchor="top", x=0, xanchor="left", bgcolor='rgba(0,0,0,0.5)', font=dict(size=10)),
            )

            fig_desktop = copy.deepcopy(fig)
            fig_mobile = copy.deepcopy(fig)

            fig_desktop.update_layout(
                height=800,
                dragmode='pan',
                margin=dict(l=0, r=0, t=120, b=0) 
            )

            fig_mobile.update_layout(
                height=520, 
                dragmode='pan',  
                title={**fig.layout.title.to_plotly_json(), "y": 1.0, "yanchor": "top"},
                margin=dict(l=0, r=0, t=100, b=0) 
            )
            
            config = {
                "scrollZoom": True,
                "displayModeBar": False,
                "responsive": True,
                "doubleClick": "reset"
            }

            with st.container():
                st.markdown('<div class="desktop-marker"></div>', unsafe_allow_html=True)
                st.plotly_chart(fig_desktop, use_container_width=True, config=config)

            with st.container():
                st.markdown('<div class="mobile-marker"></div>', unsafe_allow_html=True)
                st.plotly_chart(fig_mobile, use_container_width=True, config=config)

    else:
        st.error(f"⚠️ 查無資料，請確認股票代號或稍後再試。")
