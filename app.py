import streamlit as st
import pandas as pd
import glob
import os
import re
from datetime import datetime
import googlemaps
import google.generativeai as genai
import io

# ==========================================
# 1. 配置与初始化
# ==========================================
# 读取密钥
MAPS_KEY = st.secrets.get("GOOGLE_MAPS_API_KEY", "")
AI_KEY = st.secrets.get("GOOGLE_API_KEY", "")

# 配置 Google Maps
gmaps = None
if MAPS_KEY:
    try:
        gmaps = googlemaps.Client(key=MAPS_KEY)
    except:
        pass

# 配置 Gemini AI
if AI_KEY:
    genai.configure(api_key=AI_KEY)

st.set_page_config(page_title="Burton Travel Copilot", page_icon="✈️", layout="wide")

# ==========================================
# 2. 数据加载层 (保持稳定)
# ==========================================
class DataLoader:
    def __init__(self):
        self.day_tour_prices = {}
        self.itineraries = {}
        self.charter_prices = {}
        self.airport_prices = {}
        self.hotel_rates = {}
        self.hotel_calendar = {}
        self.tokyo_hotel_rates = {}

    def load_data(self):
        try:
            # 1. 一日游价格
            price_files = glob.glob('*商品价格*.csv')
            if price_files:
                df_price = pd.read_csv(price_files[0], header=0)
                df_price.columns = [str(c).strip() for c in df_price.columns]
                self.day_tour_prices = df_price.set_index('产品名称').to_dict('index')

            # 2. 行程详情
            tour_files = glob.glob('日游价格*.csv')
            for f in tour_files:
                if '商品价格' in f: continue
                tour_name = f.split(' - ')[-1].replace('.csv', '').strip()
                self.itineraries[tour_name] = pd.read_csv(f, header=None)

            # 3. 包车价格
            charter_files = glob.glob('*包车*.csv')
            charter_file = next((f for f in charter_files if '接送机' not in f.split(' - ')[-1]), None)
            if not charter_file and charter_files: charter_file = charter_files[0]
            if charter_file:
                df_charter = pd.read_csv(charter_file, header=1)
                df_charter.columns = [str(c).strip() for c in df_charter.columns]
                if '包车线路' in df_charter.columns:
                     self.charter_prices = df_charter.set_index('包车线路')['包车价格（人民币）'].to_dict()

            # 4. 接送机
            airport_files = glob.glob('*接送机*.csv')
            airport_file = next((f for f in airport_files if '包车' not in f.split(' - ')[-1]), None)
            if airport_file:
                df_raw = pd.read_csv(airport_file, header=None)
                header_rows = df_raw[df_raw.eq('编号').any(axis=1)].index
                if not header_rows.empty:
                    self.airport_prices = pd.read_csv(airport_file, header=header_rows[0]).to_dict('records')

            # 5. 大阪酒店
            osaka_files = glob.glob('*OSAKA*.csv')
            osaka_rate_file = next((f for f in osaka_files if 'Sheet1' not in f), None)
            osaka_cal_file = next((f for f in osaka_files if 'Sheet1' in f), None)
            if osaka_rate_file: self.hotel_rates = pd.read_csv(osaka_rate_file)
            if osaka_cal_file: self._parse_osaka_calendar(osaka_cal_file)
            
            # 6. 东京酒店
            shinjuku_files = glob.glob('*SHINJUKU*.csv')
            if shinjuku_files: self._parse_shinjuku_hotel(shinjuku_files[0])

            return True
        except Exception as e:
            st.error(f"Data Load Error: {e}")
            return False

    def _parse_osaka_calendar(self, filepath):
        try:
            df = pd.read_csv(filepath, header=None)
            for col in range(df.shape[1]-1):
                mask = df.iloc[:, col].astype(str).str.match(r'202\d-\d{1,2}-\d{1,2}')
                if mask.any():
                    for d, c in zip(df.loc[mask, col], df.loc[mask, col+1]):
                        if pd.notna(c): self.hotel_calendar[str(d).strip()] = str(c).strip()
        except: pass

    def _parse_shinjuku_hotel(self, filepath):
        try:
            df = pd.read_csv(filepath, header=None)
            price_map = {}
            for c, p in zip(df.iloc[3, :], df.iloc[4, :]):
                if str(c).strip() in ['A','B','C','D','S']:
                    price_map[str(c).strip()] = float(re.sub(r'[^\d]', '', str(p)))
            for idx, row in df.iloc[11:].iterrows():
                if '月' in str(row[0]) and str(row[3]).strip() in price_map:
                    m = int(re.search(r'(\d+)', str(row[0])).group(1))
                    y = 2025 if m >= 10 else 2026
                    self.tokyo_hotel_rates[f"{y}-{m:02d}-{int(row[1]):02d}"] = price_map[str(row[3]).strip()]
        except: pass

# ==========================================
# 3. 业务与 AI 逻辑层
# ==========================================
class TravelPlanner:
    def __init__(self, data):
        self.data = data

    def get_hotel_price(self, city, date):
        if city == "Tokyo": return self.data.tokyo_hotel_rates.get(date, 20200)
        if city == "Osaka":
            code = self.data.hotel_calendar.get(date, "A")
            try:
                mask = self.data.hotel_rates.iloc[:, 0].str.contains(f"\\({code}\\)", na=False)
                return float(self.data.hotel_rates[mask]['Twin'].values[0])
            except: return 13500
        return 0

    def validate_route(self, start, waypoints, end):
        if not gmaps: return {"valid": False, "msg": "地图API未连接", "dist":0, "dur":0, "img":None}
        try:
            now = datetime.now()
            res = gmaps.directions(start, end, waypoints=waypoints, mode="driving", departure_time=now, optimize_waypoints=True)
            if not res: return {"valid": False, "msg": "无法规划路线", "dist":0, "dur":0, "img":None}
            
            route = res[0]
            dist = round(sum(l['distance']['value'] for l in route['legs'])/1000, 1)
            dur = round(sum(l['duration']['value'] for l in route['legs'])/3600, 1)
            valid = (dist <= 300) and (dur <= 10)
            
            poly = route['overview_polyline']['points']
            markers = [{'color':'green','label':'A','locations':[start]}, {'color':'red','label':'B','locations':[end]}, {'color':'blue','label':'P','locations':waypoints}]
            img_iter = gmaps.static_map(size=(600,300), path=f"enc:{poly}", markers=markers, maptype="roadmap", format="png")
            img_bytes = io.BytesIO()
            for chunk in img_iter: img_bytes.write(chunk)
            img_bytes.seek(0)
            
            return {"valid": valid, "msg": "✅ 合规" if valid else "⚠️ 超限", "dist": dist, "dur": dur, "img": img_bytes}
        except Exception as e: return {"valid": False, "msg": f"API Error: {e}", "dist":0, "dur":0, "img":None}

    def generate_sales_copy(self, model_name, context):
        """调用 Gemini 生成销售话术"""
        if not AI_KEY: return "⚠️ 未配置 GOOGLE_API_KEY，无法生成 AI 话术。"
        
        # 映射模型名称
        model_map = {
            "Gemini 3 Fast (Flash)": "gemini-3-flash-preview",
            "Gemini 3 Pro (Professional)": "gemini-3-pro-preview"
        }
        model_id = model_map.get(model_name, "gemini-3-flash-preview")
        
        # 系统提示词 (v3.0)
        system_prompt = f"""
        # Role
        你是由 Trip.com (携程) 研发的高级旅游销售顾问助手。
        
        # Context (当前订单详情)
        {context}
        
        # Output Format (严格遵守)
        请严格按照以下两部分输出：

        ## Part 1: 📋 一键复制回复 (Copy & Send)
        请提供两个版本的回复，放入独立的 Markdown 代码块中：
        
        ### 选项 A：极简版 (Quick Reply)
        * **约束**：50字以内。直接回答核心结论(价格/合规性) + 预订链接(如有)。
        
        ### 选项 B：专业版 (Pro Consult)
        * **语气**：热情、专业、有销售诱惑力。
        * **结构**：[热情开场] -> [详细解答/报价] -> [卖点植入(环球影城/神户牛等)] -> [行动呼吁] -> [免责声明]。
        
        ## Part 2: 🧠 思考与核查
        简述你的计算逻辑或信息来源。
        """
        
        try:
            model = genai.GenerativeModel(model_id)
            response = model.generate_content(system_prompt)
            return response.text
        except Exception as e:
            return f"AI 生成失败: {str(e)}"

# ==========================================
# 4. 前端界面
# ==========================================
def main():
    if 'loaded' not in st.session_state:
        loader = DataLoader()
        if loader.load_data():
            st.session_state.loader = loader
            st.session_state.planner = TravelPlanner(loader) # Pass loader not instance
            st.session_state.loaded = True
            
    planner = st.session_state.planner if 'planner' in st.session_state else None

    # Sidebar
    with st.sidebar:
        st.title("⚙️ 设置")
        st.subheader("🤖 AI 模型选择")
        ai_model = st.radio("选择大脑:", ["Gemini 3 Fast (Flash)", "Gemini 3 Pro (Professional)"], captions=["速度快，适合简单报价", "逻辑强，适合复杂咨询"])
        
        st.divider()
        date = st.date_input("出行日期", datetime(2026, 2, 1))
        pax = st.number_input("人数", 1, 15, 2)
        
        if AI_KEY: st.success("✅ Gemini AI: 已连接")
        else: st.error("❌ Gemini AI: 未连接")

    st.title("🤖 Burton 智能行程报价器 (AI版)")
    
    tab1, tab2 = st.tabs(["🗺️ 智能报价", "✈️ 资源查询"])
    
    with tab1:
        query = st.text_input("客户需求 (例如: 富士山一日游，或者 想要大阪去京都的包车)", key="q1")
        
        if query and planner:
            # 搜索逻辑
            matches = planner.data.day_tour_prices.keys()
            matched_tours = [m for m in matches if query in str(m)]
            
            if not matched_tours and "包车" not in query:
                st.warning("未找到匹配的标准一日游线路，建议使用包车模式。")
                matched_tours = ["自定义包车"]
            
            tour = st.selectbox("选择线路模板", matched_tours + ["自定义包车"])
            
            # 模式判定
            mode = "跟团游"
            if tour == "自定义包车" or "包车" in query:
                mode = "包车定制"
            
            st.info(f"当前模式: **{mode}**")
            
            # 动态表单
            route_info = {}
            if mode == "包车定制":
                c1, c2 = st.columns(2)
                start = c1.text_input("起点", "大阪市区")
                end = c2.text_input("终点", "京都市区")
                stops = st.text_area("途经点", query if query else "奈良公园")
                waypoints = [s.strip() for s in stops.split('\n') if s.strip()]
                
                if st.button("🚀 校验路线 & 生成报价"):
                    with st.spinner("AI 正在计算距离并生成话术..."):
                        # 1. 查地图
                        res = planner.validate_route(start, waypoints, end)
                        
                        # 2. 算价格
                        # 简单估算：包车底价 + 导游
                        base_charter = 2500
                        tour_fee = base_charter + 1000
                        city = "Osaka" if "大阪" in start else "Tokyo"
                        hotel = planner.get_hotel_price(city, str(date))
                        total = hotel + tour_fee
                        
                        # 3. 准备 AI 上下文
                        context_str = f"""
                        - 客户需求: {query}
                        - 出行日期: {date}
                        - 人数: {pax}
                        - 模式: 包车定制
                        - 路线校验: {res['msg']} (距离{res['dist']}km, 耗时{res['dur']}h)
                        - 费用明细: 总价 ¥{total} (含 酒店¥{hotel} + 包车¥{base_charter} + 导游¥1000)
                        - 推荐行程: 起点{start} -> {waypoints} -> 终点{end}
                        """
                        
                        # 4. 展示结果
                        if res['img']: st.image(res['img'], caption="Google 路线图")
                        if res['valid']: st.success(f"路线合规: {res['dist']}km")
                        else: st.error(f"路线预警: {res['msg']}")
                        
                        # 5. 调用 AI
                        ai_reply = planner.generate_sales_copy(ai_model, context_str)
                        st.markdown(ai_reply)

            else: # 跟团游
                if st.button("💰 生成报价 & 话术"):
                    with st.spinner("AI 正在检索知识库..."):
                        # 1. 算价格
                        info = planner.data.day_tour_prices.get(tour, {})
                        price_col = next((c for c in info.keys() if '结算价格' in c), None)
                        unit_price = float(info.get(price_col, 350))
                        tour_fee = unit_price * pax
                        city = "Osaka" if "大阪" in tour else "Tokyo"
                        hotel = planner.get_hotel_price(city, str(date))
                        total = hotel + tour_fee
                        
                        # 2. 准备 AI 上下文
                        itinerary_df = planner.data.itineraries.get(tour, pd.DataFrame())
                        itinerary_str = itinerary_df.to_string() if not itinerary_df.empty else "详见标准行程文档"
                        
                        context_str = f"""
                        - 客户需求: {query}
                        - 产品: {tour} (跟团游)
                        - 出行日期: {date}
                        - 人数: {pax}
                        - 费用明细: 总价 ¥{total} (含 酒店¥{hotel} + 团费¥{tour_fee})
                        - 详细行程: {itinerary_str}
                        """
                        
                        # 3. 调用 AI
                        ai_reply = planner.generate_sales_copy(ai_model, context_str)
                        st.markdown(ai_reply)

    with tab2:
        st.dataframe(pd.DataFrame(st.session_state.loader.airport_prices), use_container_width=True)

if __name__ == "__main__":
    main()
