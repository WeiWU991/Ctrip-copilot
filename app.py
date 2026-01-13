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

st.set_page_config(page_title="Trip Travel Copilot", page_icon="✈️", layout="wide")

# ==========================================
# 2. 数据加载层 (针对新上传数据的终极适配版)
# ==========================================
class DataLoader:
    def __init__(self):
        self.day_tour_prices = {}
        self.itineraries = {}
        self.charter_prices = {}
        self.airport_prices = {} # 新增接送机
        self.hotel_rates = {}
        self.hotel_calendar = {} 
        self.tokyo_hotel_rates = {}

    def load_data(self):
        try:
            # 1. 加载一日游价格
            # 假设文件名包含 "商品价格"
            price_files = glob.glob('*商品价格*.csv')
            if price_files:
                df_price = pd.read_csv(price_files[0])
                # 清理列名空格
                df_price.columns = [c.strip() for c in df_price.columns]
                self.day_tour_prices = df_price.set_index('产品名称').to_dict('index')

            # 2. 加载具体行程 (排除商品价格表)
            tour_files = glob.glob('日游价格*.csv')
            for f in tour_files:
                if '商品价格' in f: continue
                # 提取纯净的线路名，去掉文件名后缀和前缀
                tour_name = f.split(' - ')[-1].replace('.csv', '').strip()
                self.itineraries[tour_name] = pd.read_csv(f, header=None)

            # 3. 加载包车价格
            charter_files = glob.glob('*包车*.csv')
            # 排除接送机文件，只找包车文件
            charter_file = next((f for f in charter_files if '接送机' not in f.split(' - ')[-1]), None)
            # 如果上面没找到，尝试找 "接送机&包车价格.xlsx - 包车.csv"
            if not charter_file:
                 charter_file = '接送机&包车价格.xlsx - 包车.csv'
            
            if os.path.exists(charter_file):
                df_charter = pd.read_csv(charter_file)
                df_charter.columns = [c.strip() for c in df_charter.columns]
                if '包车线路' in df_charter.columns:
                     self.charter_prices = df_charter.set_index('包车线路')['包车价格（人民币）'].to_dict()

            # 4. 加载接送机价格 (注意 header 在第6行, index=5)
            airport_file = '接送机&包车价格.xlsx - 接送机.csv'
            if os.path.exists(airport_file):
                # 自动寻找含有 "编号" 的那一行作为表头
                df_raw = pd.read_csv(airport_file, header=None)
                header_idx = df_raw[df_raw.eq('编号').any(axis=1)].index[0]
                df_airport = pd.read_csv(airport_file, header=header_idx)
                self.airport_prices = df_airport.to_dict('records')

            # 5. 加载大阪酒店
            self.hotel_rates = pd.read_csv('HOTEL PLAZA OSAKA 20260101-20260630 AGT団体料金表Asia only.xlsx - OSAKA.csv')
            self._parse_vertical_calendar('HOTEL PLAZA OSAKA 20260101-20260630 AGT団体料金表Asia only.xlsx - Sheet1.csv')
            
            # 6. 加载东京酒店 (如果没有文件，使用默认兜底)
            if os.path.exists('tokyo_hotel_rates.csv'):
                df_tokyo = pd.read_csv('tokyo_hotel_rates.csv')
                self.tokyo_hotel_rates = pd.Series(df_tokyo.Price_Twin_A.values, index=df_tokyo.Date).to_dict()

            return True
        except Exception as e:
            st.error(f"数据加载模块报错: {str(e)}")
            return False

    def _parse_vertical_calendar(self, filepath):
        """
        解析大阪酒店那种 "日期|代码|日期|代码" 的垂直分块结构
        """
        try:
            df = pd.read_csv(filepath, header=None)
            # 遍历所有单元格，寻找日期格式 (YYYY-MM-DD)
            # 找到日期后，取它右边一格作为 Code
            for col_idx in range(df.shape[1] - 1): # 最后一列不可能是日期（因为右边没格子了）
                col_data = df.iloc[:, col_idx].astype(str)
                # 筛选出符合日期格式的行
                date_mask = col_data.str.match(r'202\d-\d{2}-\d{2}')
                
                if date_mask.any():
                    # 提取日期和对应的代码（右边一列）
                    valid_rows = df[date_mask].index
                    dates = df.iloc[valid_rows, col_idx]
                    codes = df.iloc[valid_rows, col_idx + 1]
                    
                    for d, c in zip(dates, codes):
                        if pd.notna(c):
                            self.hotel_calendar[str(d).strip()] = str(c).strip()
        except Exception as e:
            print(f"Calendar parse warning: {e}")

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
        # 1. 东京 (查表)
        if city == "Tokyo":
            return self.data.tokyo_hotel_rates.get(date_str, self.data.tokyo_hotel_rates.get('default', 25200))
        
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
            # 确保列名匹配
            col_name = '结算价格单位：人民币/人成人儿童婴儿同价'
            price = float(info.get(col_name, 350)) # 默认350
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
                    
                    # 价格等级展示
                    city = "Osaka" if ("京都" in tour or "大阪" in tour) else "Tokyo"
                    code = st.session_state.loader.hotel_calendar.get(str(date), "默认") if city == "Osaka" else "-"
                    
                    st.markdown(f"## 总价: <span style='color:#FF4B4B'>¥ {total:,.0f}</span>", unsafe_allow_html=True)
                    c1, c2, c3 = st.columns(3)
                    c1.metric("酒店 (基础)", f"¥ {hotel:,.0f}", f"等级: {code}")
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

