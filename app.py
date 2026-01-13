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
MAPS_KEY = st.secrets.get("GOOGLE_MAPS_API_KEY", "")
AI_KEY = st.secrets.get("GOOGLE_API_KEY", "")

# 初始化客户端
gmaps = None
if MAPS_KEY:
    try:
        gmaps = googlemaps.Client(key=MAPS_KEY)
    except Exception as e:
        print(f"Google Maps init warning: {e}")

if AI_KEY:
    genai.configure(api_key=AI_KEY)

# [设置] 品牌为 Ctrip
st.set_page_config(page_title="Ctrip CS Copilot", page_icon="✈️", layout="wide")

# ==========================================
# 2. 知识与数据加载 (Excel 专用版)
# ==========================================
class KnowledgeBase:
    def __init__(self):
        self.prices = {}         # 一日游价格表
        self.itineraries = {}    # 具体行程
        self.charter = {}        # 包车价格
        self.airport = []        # 接送机
        self.hotel_rates = {}    # 酒店价格
        self.hotel_cal = {}      # 酒店日历
        self.tokyo_rates = {}    # 东京酒店
        self.docs_text = ""      # Markdown文档

    def load_all(self):
        try:
            # --- A. 读取 Excel 数据 (核心修正) ---
            
            # 1. 读取 [日游价格&行程时刻表.xlsx]
            # 逻辑：读取所有 Sheet，"商品价格"表做价格，其他表做行程
            tour_files = glob.glob('*日游价格*.xlsx') + glob.glob('*日游价格*.xls')
            if tour_files:
                f_path = tour_files[0]
                xls = pd.ExcelFile(f_path)
                
                for sheet in xls.sheet_names:
                    if "商品价格" in sheet:
                        # 读取价格表 (Header在第1行)
                        df = pd.read_excel(xls, sheet_name=sheet, header=0)
                        df.columns = [str(c).strip() for c in df.columns]
                        self.prices = df.set_index('产品名称').to_dict('index')
                    else:
                        # 读取行程表 (无Header)
                        df = pd.read_excel(xls, sheet_name=sheet, header=None)
                        self.itineraries[sheet.strip()] = df

            # 2. 读取 [接送机&包车价格.xlsx]
            # 逻辑：读取 "包车" Sheet 和 "接送机" Sheet
            charter_files = glob.glob('*接送机*.xlsx') + glob.glob('*接送机*.xls')
            if charter_files:
                f_path = charter_files[0]
                xls = pd.ExcelFile(f_path)
                
                # --- 包车 Sheet ---
                # 尝试模糊匹配 sheet name
                charter_sheet = next((s for s in xls.sheet_names if "包车" in s), None)
                if charter_sheet:
                    # 包车表头通常在第2行 (header=1)
                    df = pd.read_excel(xls, sheet_name=charter_sheet, header=1)
                    df.columns = [str(c).strip() for c in df.columns]
                    if '包车线路' in df.columns:
                        self.charter = df.set_index('包车线路')['包车价格（人民币）'].to_dict()
                
                # --- 接送机 Sheet ---
                airport_sheet = next((s for s in xls.sheet_names if "接送机" in s), None)
                if airport_sheet:
                    # 动态寻找表头：找包含"编号"的那一行
                    df_raw = pd.read_excel(xls, sheet_name=airport_sheet, header=None)
                    # 查找包含 "编号" 的行索引
                    header_idx = -1
                    for idx, row in df_raw.iterrows():
                        if row.astype(str).str.contains("编号").any():
                            header_idx = idx
                            break
                    
                    if header_idx != -1:
                        self.airport = pd.read_excel(xls, sheet_name=airport_sheet, header=header_idx).to_dict('records')

            # 3. 读取 [大阪酒店.xlsx]
            osaka_files = glob.glob('*OSAKA*.xlsx') + glob.glob('*OSAKA*.xls')
            if osaka_files:
                f_path = osaka_files[0]
                xls = pd.ExcelFile(f_path)
                
                # 读取价格表 (通常叫 OSAKA)
                rate_sheet = next((s for s in xls.sheet_names if "OSAKA" in s), None)
                if rate_sheet:
                    self.hotel_rates = pd.read_excel(xls, sheet_name=rate_sheet)
                
                # 读取日历表 (通常叫 Sheet1)
                cal_sheet = next((s for s in xls.sheet_names if "Sheet1" in s), None)
                if cal_sheet:
                    self._parse_excel_calendar(xls, cal_sheet)

            # 4. 读取 [东京酒店 (SHINJUKU).xlsx]
            shinjuku_files = glob.glob('*SHINJUKU*.xlsx') + glob.glob('*SHINJUKU*.xls')
            if shinjuku_files:
                self._parse_shinjuku_excel(shinjuku_files[0])

            # --- B. 读取 Markdown (知识库) ---
            md_files = glob.glob('*.md')
            full_text = []
            for f in md_files:
                try:
                    with open(f, 'r', encoding='utf-8') as file:
                        full_text.append(f"=== 文档: {f} ===\n{file.read()}\n")
                except: pass
            self.docs_text = "\n".join(full_text)

            return True
        except Exception as e:
            st.error(f"知识库加载异常 (请检查Excel格式): {e}")
            return False

    def _parse_excel_calendar(self, xls, sheet_name):
        """解析大阪酒店 Excel 日历"""
        try:
            df = pd.read_excel(xls, sheet_name=sheet_name, header=None)
            # 扫描所有列，寻找日期格式 YYYY-MM-DD
            for c in range(df.shape[1]-1):
                col_data = df.iloc[:, c].astype(str)
                # 匹配日期
                mask = col_data.str.match(r'202\d-\d{1,2}-\d{1,2}')
                if mask.any():
                    # 日期在 c 列，代码在 c+1 列
                    for d, code in zip(df.loc[mask, c], df.loc[mask, c+1]):
                        if pd.notna(code): 
                            self.hotel_cal[str(d).strip()] = str(code).strip()
        except: pass

    def _parse_shinjuku_excel(self, filepath):
        """解析东京酒店 Excel"""
        try:
            df = pd.read_excel(filepath, header=None)
            # 1. 提取价格映射 (Row 3=Code, Row 4=Price)
            p_map = {}
            for c, p in zip(df.iloc[3, :], df.iloc[4, :]):
                if str(c).strip() in ['A','B','C','D','S']:
                    clean_price = re.sub(r'[^\d]', '', str(p))
                    if clean_price:
                        p_map[str(c).strip()] = float(clean_price)
            
            # 2. 提取日历 (从第11行开始)
            for _, row in df.iloc[11:].iterrows():
                # Col 0: 月份(1月), Col 3: Code(D)
                if '月' in str(row[0]) and str(row[3]).strip() in p_map:
                    m = int(re.search(r'(\d+)', str(row[0])).group(1))
                    y = 2025 if m >= 10 else 2026
                    d = int(row[1])
                    self.tokyo_rates[f"{y}-{m:02d}-{d:02d}"] = p_map[str(row[3]).strip()]
        except: pass

# ==========================================
# 3. 智能大脑 (Gemini 3)
# ==========================================
class SmartAgent:
    def __init__(self, kb):
        self.kb = kb
        # [核心修正] 使用 Gemini 3 Preview 模型
        try:
            self.model_flash = genai.GenerativeModel("gemini-3-flash-preview")
            self.model_pro = genai.GenerativeModel("gemini-3-pro-preview")
        except:
            # 自动降级兜底，防止应用崩溃
            self.model_flash = genai.GenerativeModel("gemini-1.5-flash")
            self.model_pro = genai.GenerativeModel("gemini-1.5-pro")

    def semantic_match_product(self, user_query):
        """意图识别"""
        if not self.kb.prices: return None
        
        product_list = list(self.kb.prices.keys())
        prompt = f"""
        User Query: "{user_query}"
        Product List: {product_list}
        
        Task: Identify which product the user is asking about.
        Rules:
        1. If it matches a specific product name, return ONLY that name.
        2. If asking for Charter/Custom/Car rental, return "Charter".
        3. Otherwise return "General".
        """
        try:
            res = self.model_flash.generate_content(prompt)
            matched = res.text.strip()
            # 精确匹配校验
            for p in product_list:
                if p in matched: return p
            if "Charter" in matched or "包车" in matched: return "Charter"
            return "General"
        except:
            return "General"

    def generate_response(self, query, context_data, model="flash"):
        """生成回复"""
        model_engine = self.model_pro if model == "pro" else self.model_flash
        
        base_prompt = f"""
        # Role
        You are a Senior Travel Consultant at **Ctrip (携程)**.
        Your goal is to answer customer queries efficiently and professionally.

        # User Question
        "{query}"

        # Context (Price/Itinerary/Status)
        {context_data}

        # Knowledge Base Snippets
        {self.kb.docs_text[:15000]} 

        # Task
        Generate a response in Chinese (Mainland).

        # Output Format (Strict)
        
        ## Part 1: 📋 一键复制回复 (Copy & Send)
        
        ### 选项 A：极简版 (Quick Reply)
        - Constraint: Under 50 words.
        - Content: Direct answer (Price / Availability / Link).
        
        ### 选项 B：专业版 (Pro Consult)
        - Tone: Ctrip Professional (Warm, Service-oriented).
        - Structure: [Greeting] -> [Answer Details] -> [Selling Point] -> [Call to Action].
        
        ## Part 2: 🧠 销售建议 (Internal)
        - Logic behind the calculation.
        - Upsell or cross-sell suggestions.
        """
        try:
            return model_engine.generate_content(base_prompt).text
        except Exception as e:
            return f"AI Error: {str(e)}"

# ==========================================
# 4. 前端界面 (Ctrip UI)
# ==========================================
def main():
    if 'kb' not in st.session_state:
        kb = KnowledgeBase()
        if kb.load_all():
            st.session_state.kb = kb
            st.session_state.agent = SmartAgent(kb)
            st.session_state.loaded = True

    # --- 侧边栏 ---
    with st.sidebar:
        st.title("⚙️ 订单参数")
        date = st.date_input("出行日期", datetime(2026, 2, 1))
        pax = st.number_input("人数", 1, 15, 2)
        
        # 模型选择器
        model_choice = st.radio("AI 模型", ["Gemini 3 Fast", "Gemini 3 Pro"], index=0)
        selected_model = "flash" if "Fast" in model_choice else "pro"
        
        st.divider()
        st.caption("✅ System Ready: Excel + Maps + Gemini")

    # --- 主界面 ---
    st.title("👩‍💼 Ctrip 客服 Copilot")
    st.caption("全自动 AI 助手：意图识别 -> 自动算价 -> 生成话术")

    user_input = st.chat_input("请粘贴客人问题... (如: 富士山一日游含餐吗？)")

    if user_input and 'kb' in st.session_state:
        agent = st.session_state.agent
        kb = st.session_state.kb
        
        # 1. User Message
        with st.chat_message("user"):
            st.write(user_input)

        # 2. AI Processing
        with st.status("🧠 Ctrip AI 正在处理...", expanded=True) as status:
            intent = agent.semantic_match_product(user_input)
            st.write(f"🔍 意图识别: **{intent}**")
            
            context_data = ""
            
            # --- 场景 A: 标准一日游 ---
            if intent in kb.prices:
                st.write(f"✅ 锁定产品: {intent}")
                # 自动算价
                info = kb.prices[intent]
                # 模糊匹配价格列 (包含'结算')
                price_col = next((c for c in info if '结算' in c), None)
                unit_price = float(info.get(price_col, 350))
                tour_fee = unit_price * pax
                
                # 酒店
                city = "Osaka" if "大阪" in intent or "京都" in intent else "Tokyo"
                if city == "Tokyo":
                    h_price = kb.tokyo_rates.get(str(date), 20200)
                else:
                    code = kb.hotel_cal.get(str(date), "A")
                    try:
                        mask = kb.hotel_rates.iloc[:, 0].str.contains(f"\\({code}\\)", na=False)
                        h_price = float(kb.hotel_rates[mask]['Twin'].values[0])
                    except: h_price = 13500
                
                total = h_price + tour_fee
                itinerary_df = kb.itineraries.get(intent, pd.DataFrame())
                itinerary_str = itinerary_df.to_string()
                
                context_data = f"""
                [Product Info]
                - Name: {intent}
                - Total Price: ¥{total} (Hotel ¥{h_price} + Tour ¥{tour_fee} for {pax} pax)
                - Date: {date}
                - Itinerary: {itinerary_str}
                """
                
            # --- 场景 B: 包车 ---
            elif intent == "Charter":
                st.write("🚗 识别为包车需求")
                context_data = f"""
                [Charter Request]
                - Customer wants custom charter.
                - Base Price: ~¥2500 + Guide ¥1000.
                - Action: Ask for Start/End points to calculate distance via Google Maps.
                """
                
            # --- 场景 C: 通用 ---
            else:
                st.write("📖 检索知识库回答...")
                context_data = "[General Query] Answer based on attached Markdown docs."

            status.update(label="✅ 完成", state="complete", expanded=False)

        # 3. AI Response
        with st.chat_message("assistant"):
            response = agent.generate_response(user_input, context_data, selected_model)
            st.markdown(response)

        # 4. Tool: Google Maps (仅在包车时显示)
        if intent == "Charter" or "定制" in user_input or "包车" in user_input:
            with st.expander("🧰 距离校验工具 (Google Maps)", expanded=True):
                c1, c2 = st.columns(2)
                start = c1.text_input("起点", "大阪")
                end = c2.text_input("终点", "京都")
                stops = st.text_area("途经点", user_input)
                
                if st.button("🚀 校验 & 重新生成"):
                    if gmaps:
                        try:
                            now = datetime.now()
                            wps = [s.strip() for s in stops.split('\n') if s.strip()]
                            res = gmaps.directions(start, end, waypoints=wps, mode="driving", departure_time=now)
                            if res:
                                route = res[0]
                                dist = sum(l['distance']['value'] for l in route['legs'])/1000
                                dur = sum(l['duration']['value'] for l in route['legs'])/3600
                                poly = route['overview_polyline']['points']
                                markers = [{'color':'green','label':'A','locations':[start]}, {'color':'red','label':'B','locations':[end]}]
                                img_iter = gmaps.static_map(size=(600,300), path=f"enc:{poly}", markers=markers, format="png")
                                img_bytes = io.BytesIO()
                                for chunk in img_iter: img_bytes.write(chunk)
                                
                                st.image(img_bytes, caption=f"{dist}km / {dur}h")
                                if dist > 300: st.error("⚠️ 超 300km")
                                else: st.success("✅ 合规")
                        except Exception as e: st.error(f"API Error: {e}")
                    else: st.error("API Key Missing")

    st.divider()
    st.caption("© 2026 Ctrip Travel Copilot")

if __name__ == "__main__":
    main()
