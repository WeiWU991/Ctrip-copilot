import streamlit as st
import pandas as pd
import glob
import os
import re
from datetime import datetime
import googlemaps
import io

# ==========================================
# 1. 配置与初始化
# ==========================================
GOOGLE_MAPS_API_KEY = st.secrets.get("GOOGLE_MAPS_API_KEY", "")

st.set_page_config(page_title="Ctrip Travel Copilot", page_icon="✈️", layout="wide")

# ==========================================
# 2. 数据加载层 (全自动化适配版)
# ==========================================
class DataLoader:
    def __init__(self):
        self.day_tour_prices = {}
        self.itineraries = {}
        self.charter_prices = {}
        self.airport_prices = {}
        self.hotel_rates = {}        # 大阪酒店原始表
        self.hotel_calendar = {}     # 大阪酒店日历 {date: code}
        self.tokyo_hotel_rates = {}  # 东京酒店日历 {date: price}

    def load_data(self):
        try:
            # -------------------------------------------------
            # 1. 加载一日游价格
            # -------------------------------------------------
            # 查找文件名包含 "商品价格" 的文件
            price_files = glob.glob('*商品价格*.csv')
            if price_files:
                # 您的文件中表头在第1行 (header=0)
                df_price = pd.read_csv(price_files[0], header=0)
                df_price.columns = [str(c).strip() for c in df_price.columns]
                self.day_tour_prices = df_price.set_index('产品名称').to_dict('index')

            # -------------------------------------------------
            # 2. 加载具体行程 (排除商品价格表)
            # -------------------------------------------------
            tour_files = glob.glob('日游价格*.csv')
            for f in tour_files:
                if '商品价格' in f: continue
                # 提取纯净的线路名
                tour_name = f.split(' - ')[-1].replace('.csv', '').strip()
                self.itineraries[tour_name] = pd.read_csv(f, header=None)

            # -------------------------------------------------
            # 3. 加载包车价格 (重点修正: header=1)
            # -------------------------------------------------
            charter_files = glob.glob('*包车*.csv')
            # 优先找不含"接送机"的，如果找不到就找包含的(兜底)
            charter_file = next((f for f in charter_files if '接送机' not in f.split(' - ')[-1]), None)
            if not charter_file and charter_files:
                 charter_file = charter_files[0]
            
            if charter_file:
                # 您的包车文件表头在第2行 (Index 1)
                df_charter = pd.read_csv(charter_file, header=1)
                df_charter.columns = [str(c).strip() for c in df_charter.columns]
                if '包车线路' in df_charter.columns and '包车价格（人民币）' in df_charter.columns:
                     self.charter_prices = df_charter.set_index('包车线路')['包车价格（人民币）'].to_dict()

            # -------------------------------------------------
            # 4. 加载接送机价格
            # -------------------------------------------------
            # 接送机文件比较特殊，尝试自动寻找 "编号" 所在的行作为表头
            airport_files = glob.glob('*接送机*.csv')
            # 排除掉包车文件
            airport_file = next((f for f in airport_files if '包车' not in f.split(' - ')[-1]), None)
            
            if airport_file:
                df_raw = pd.read_csv(airport_file, header=None)
                # 寻找包含 "编号" 的行索引
                header_rows = df_raw[df_raw.eq('编号').any(axis=1)].index
                if not header_rows.empty:
                    header_idx = header_rows[0]
                    df_airport = pd.read_csv(airport_file, header=header_idx)
                    self.airport_prices = df_airport.to_dict('records')

            # -------------------------------------------------
            # 5. 加载大阪酒店 (PLAZA OSAKA)
            # -------------------------------------------------
            osaka_files = glob.glob('*OSAKA*.csv')
            # 区分价格表和日历表
            # 通常 Sheet1 是日历，OSAKA 是价格表
            osaka_rate_file = next((f for f in osaka_files if 'Sheet1' not in f), None)
            osaka_cal_file = next((f for f in osaka_files if 'Sheet1' in f), None)

            if osaka_rate_file:
                self.hotel_rates = pd.read_csv(osaka_rate_file)
            
            if osaka_cal_file:
                self._parse_osaka_vertical_calendar(osaka_cal_file)
            
            # -------------------------------------------------
            # 6. 加载东京酒店 (SHINJUKU WASHINGTON) - 新增自动解析
            # -------------------------------------------------
            shinjuku_files = glob.glob('*SHINJUKU*.csv')
            if shinjuku_files:
                self._parse_shinjuku_hotel(shinjuku_files[0])

            return True
        except Exception as e:
            st.error(f"数据加载模块报错: {str(e)}")
            return False

    def _parse_osaka_vertical_calendar(self, filepath):
        """解析大阪酒店: 日期|代码|日期|代码 格式"""
        try:
            df = pd.read_csv(filepath, header=None)
            for col_idx in range(df.shape[1] - 1):
                col_data = df.iloc[:, col_idx].astype(str)
                # 匹配 YYYY-MM-DD
                date_mask = col_data.str.match(r'202\d-\d{1,2}-\d{1,2}')
                
                if date_mask.any():
                    valid_rows = df[date_mask].index
                    dates = df.iloc[valid_rows, col_idx]
                    codes = df.iloc[valid_rows, col_idx + 1]
                    for d, c in zip(dates, codes):
                        if pd.notna(c):
                            self.hotel_calendar[str(d).strip()] = str(c).strip()
        except:
            pass

    def _parse_shinjuku_hotel(self, filepath):
        """解析新宿华盛顿: 上部价格表，下部日历"""
        try:
            df = pd.read_csv(filepath, header=None)
            
            # 1. 提取价格映射 (Code -> Price)
            # Row 3 (Index 3): Codes (A, B, C...)
            # Row 4 (Index 4): Prices (20,200￥...)
            price_map = {}
            codes = df.iloc[3, :].values
            prices = df.iloc[4, :].values
            
            for c, p in zip(codes, prices):
                if pd.notna(c) and pd.notna(p) and str(c).strip() in ['A', 'B', 'C', 'D', 'S']:
                    # 清理价格: 去掉 ￥ , 等符号
                    price_clean = re.sub(r'[^\d]', '', str(p))
                    if price_clean:
                        price_map[str(c).strip()] = float(price_clean)

            # 2. 解析日历
            # 日历从第 12 行 (Index 11) 开始
            # Col 0: 月份 (1月), Col 1: 日期 (1), Col 3: 等级 (D)
            # 需要推断年份: 文件头说是 2025.10 - 2026.03
            calendar_start_row = 11
            
            for idx, row in df.iloc[calendar_start_row:].iterrows():
                month_raw = str(row[0])
                day_raw = str(row[1])
                code = str(row[3]).strip()
                
                if '月' in month_raw and code in price_map:
                    month = int(re.search(r'(\d+)', month_raw).group(1))
                    day = int(float(day_raw)) # handle potential float parsing
                    
                    # 简单年份推断逻辑
                    year = 2025 if month >= 10 else 2026
                    
                    date_str = f"{year}-{month:02d}-{day:02d}"
                    self.tokyo_hotel_rates[date_str] = price_map[code]
                    
        except Exception as e:
            print(f"Shinjuku parse error: {e}")

# ==========================================
# 3. 核心业务逻辑
# ==========================================
class TravelPlanner:
    def __init__(self, data_loader):
        self.data = data_loader
        self.gmaps = None
        if GOOGLE_MAPS_API_KEY:
            self.gmaps = googlemaps.Client(key=GOOGLE_MAPS_API_KEY)

    def search_tours(self, query):
        matches = []
        for name in self.data.day_tour_prices.keys():
            if str(query) in str(name):
                matches.append(name)
        return matches

    def get_hotel_price(self, city, date_str):
        # 1. 东京 (新逻辑: 直接从解析结果读)
        if city == "Tokyo":
            # 默认 fallback 到 A 级价格 (20200) 如果查不到
            return self.data.tokyo_hotel_rates.get(date_str, 20200)
        
        # 2. 大阪 (查日历 -> 查表)
        if city == "Osaka":
            code = self.data.hotel_calendar.get(date_str, "A") # 默认A
            try:
                # 模糊匹配 (A) 格式
                mask = self.data.hotel_rates.iloc[:, 0].str.contains(f"\\({code}\\)", na=False, regex=True)
                row = self.data.hotel_rates[mask]
                if not row.empty:
                    return float(row['Twin'].values[0])
            except:
                pass
            return 13500
        return 0

    def validate_route(self, start, waypoints, end):
        if not self.gmaps:
            return {"valid": False, "msg": "API未连接", "dist": 0, "dur": 0, "img": None}
        
        try:
            now = datetime.now()
            # Directions API
            res = self.gmaps.directions(start, end, waypoints=waypoints, mode="driving", departure_time=now, optimize_waypoints=True)
            if not res: return {"valid": False, "msg": "无法计算路线", "dist": 0, "dur": 0, "img": None}
            
            route = res[0]
            dist_km = round(sum(leg['distance']['value'] for leg in route['legs']) / 1000, 1)
            dur_h = round(sum(leg['duration']['value'] for leg in route['legs']) / 3600, 1)
            is_valid = (dist_km <= 300) and (dur_h <= 10)
            
            # Static Map
            poly = route['overview_polyline']['points']
            markers = [{'color':'green','label':'A','locations':[start]}, {'color':'red','label':'B','locations':[end]}, {'color':'blue','label':'P','locations':waypoints}]
            img_iter = self.gmaps.static_map(size=(600,300), path=f"enc:{poly}", markers=markers, maptype="roadmap", format="png")
            img_bytes = io.BytesIO()
            for chunk in img_iter: img_bytes.write(chunk)
            img_bytes.seek(0)
            
            return {"valid": is_valid, "msg": "✅ 合规" if is_valid else "⚠️ 超限", "dist": dist_km, "dur": dur_h, "img": img_bytes}
        except Exception as e:
            return {"valid": False, "msg": f"API Error: {e}", "dist": 0, "dur": 0, "img": None}

    def calculate_price(self, mode, tour_name, date, pax):
        city = "Osaka" if ("京都" in tour_name or "大阪" in tour_name) else "Tokyo"
        hotel = self.get_hotel_price(city, str(date))
        tour_fee = 0
        
        if mode == "跟团游 (标准线路)":
            info = self.data.day_tour_prices.get(tour_name, {})
            # 您的列名非常长，使用包含匹配来获取价格列
            price_col = next((c for c in info.keys() if '结算价格' in c), None)
            price = float(info.get(price_col, 350)) if price_col else 350
            tour_fee = price * pax
            
        elif mode == "包车定制 (自由行程)":
            base_price = 2500
            for k, v in self.data.charter_prices.items():
                if tour_name in str(k):
                    base_price = float(v)
                    break
            tour_fee = base_price + 1000 # 导游费
            
        return hotel + tour_fee, hotel, tour_fee

# ==========================================
# 4. 前端界面
# ==========================================
def main():
    if 'loaded' not in st.session_state:
        loader = DataLoader()
        if loader.load_data():
            st.session_state.loader = loader
            st.session_state.planner = TravelPlanner(loader)
            st.session_state.loaded = True
            
    planner = st.session_state.planner if 'planner' in st.session_state else None

    # Sidebar
    with st.sidebar:
        st.title("⚙️ 设置")
        date = st.date_input("出行日期", datetime(2026, 2, 1))
        pax = st.number_input("人数", 1, 15, 2)
        st.divider()
        if planner and planner.gmaps:
            st.success("Google Maps: 在线")
        else:
            st.error("Google Maps: 离线")

    st.title("🤖 Burton 智能行程报价器")
    
    tab1, tab2 = st.tabs(["🗺️ 行程规划", "✈️ 接送机查询"])
    
    # --- Tab 1: 行程 ---
    with tab1:
        query = st.text_input("客户想去哪？(例: 富士山, 奈良)", key="q1")
        if query and planner:
            matches = planner.search_tours(query)
            if not matches:
                st.warning("无相关线路")
            else:
                tour = st.selectbox("选择线路模板", matches)
                mode = st.radio("模式", ["跟团游 (标准线路)", "包车定制 (自由行程)"], horizontal=True)
                
                # 动态内容
                if mode == "跟团游 (标准线路)":
                    if tour in st.session_state.loader.itineraries:
                        with st.expander("查看行程详情", expanded=True):
                            st.dataframe(st.session_state.loader.itineraries[tour], use_container_width=True)
                else:
                    st.info("💡 包车模式支持修改行程 (含Google距离校验)")
                    c1, c2 = st.columns(2)
                    start = c1.text_input("起点", "大阪市区酒店")
                    end = c2.text_input("终点", "大阪市区酒店")
                    stops_txt = st.text_area("途经点", f"{query}\n景点B\n景点C")
                    stops = [s.strip() for s in stops_txt.split('\n') if s.strip()]
                    
                    if st.button("🚀 校验路线"):
                        with st.spinner("测算中..."):
                            res = planner.validate_route(start, stops, end)
                        if res['img']: st.image(res['img'], caption="Google路线图")
                        if res['valid']: st.success(f"{res['msg']} | {res['dist']}km | {res['dur']}h")
                        else: st.error(f"{res['msg']} | {res['dist']}km | {res['dur']}h")

                # 报价栏
                st.divider()
                if st.button("💰 计算总报价", type="primary"):
                    total, hotel, tour_fee = planner.calculate_price(mode, tour, date, pax)
                    
                    st.markdown(f"## 总价: <span style='color:#FF4B4B'>¥ {total:,.0f}</span>", unsafe_allow_html=True)
                    c1, c2, c3 = st.columns(3)
                    c1.metric("酒店 (基础)", f"¥ {hotel:,.0f}")
                    c2.metric("玩法费用", f"¥ {tour_fee:,.0f}", "包车+导游" if "包车" in mode else "人头费")
                    c3.metric("人数", f"{pax} 人")

    # --- Tab 2: 接送机 ---
    with tab2:
        st.write("### 接送机价格查询")
        if st.session_state.loader.airport_prices:
            df_ap = pd.DataFrame(st.session_state.loader.airport_prices)
            st.dataframe(df_ap, use_container_width=True)
        else:
            st.info("暂无接送机数据")

if __name__ == "__main__":
    main()

