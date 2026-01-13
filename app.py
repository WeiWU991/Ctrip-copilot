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

gmaps = None
if MAPS_KEY:
    try: gmaps = googlemaps.Client(key=MAPS_KEY)
    except: pass

if AI_KEY:
    try: genai.configure(api_key=AI_KEY)
    except: pass

st.set_page_config(page_title="Ctrip CS Copilot", page_icon="👩‍💼", layout="wide")

# ==========================================
# 2. 知识库加载器 (全兼容)
# ==========================================
class KnowledgeBase:
    def __init__(self):
        self.prices = {}         # 一日游价格
        self.itineraries = {}    # 一日游行程
        self.charter = {}        # 包车
        self.airport = []        # 接送机
        self.hotel_rates = {}    # 酒店价格
        self.hotel_cal = {}      # 酒店日历
        self.tokyo_rates = {}    # 东京酒店
        self.docs_text = ""      # 通用文档
        self.multiday_text = ""  # [新增] 专门存储多日游产品信息
        self.load_status = [] 

    def _read_any(self, pattern_keywords, header=0):
        """智能读取函数"""
        patterns = []
        for kw in pattern_keywords:
            patterns.append(f'*{kw}*.xlsx')
            patterns.append(f'*{kw}*.xls')
            patterns.append(f'*{kw}*.csv')
        found_files = sorted(list(set([f for p in patterns for f in glob.glob(p)])))
        if not found_files: return None, None
        target_file = found_files[0]
        ext = os.path.splitext(target_file)[1].lower()
        try:
            if ext in ['.xlsx', '.xls']: return pd.ExcelFile(target_file), "excel"
            else: return pd.read_csv(target_file, header=header), "csv"
        except Exception as e:
            self.load_status.append(f"❌ 读取失败 {target_file}: {e}")
            return None, None

    def load_all(self):
        try:
            # 1. 价格表
            data, ftype = self._read_any(['商品价格', 'Price'])
            if data is not None:
                if ftype == 'excel': df = pd.read_excel(data, sheet_name=0, header=0)
                else: df = data
                df.columns = [str(c).strip() for c in df.columns]
                if '产品名称' in df.columns:
                    self.prices = df.set_index('产品名称').to_dict('index')
                    self.load_status.append(f"✅ 一日游价格表: {len(self.prices)}条")

            # 2. 一日游行程
            all_files = glob.glob('*日游*.xlsx') + glob.glob('*日游*.csv')
            for f in all_files:
                if '商品价格' in f: continue
                name = os.path.splitext(os.path.basename(f))[0].split(' - ')[-1].strip()
                try:
                    if f.endswith('.xlsx'): self.itineraries[name] = pd.read_excel(f, header=None)
                    else: self.itineraries[name] = pd.read_csv(f, header=None)
                except: pass

            # 3. 包车/接送机
            data, ftype = self._read_any(['包车', '接送机', 'Charter'])
            if data is not None and ftype == 'excel':
                for sheet in data.sheet_names:
                    if "包车" in sheet:
                        df = pd.read_excel(data, sheet_name=sheet, header=1)
                        df.columns = [str(c).strip() for c in df.columns]
                        if '包车线路' in df.columns:
                            self.charter = df.set_index('包车线路')['包车价格（人民币）'].to_dict()
                    if "接送机" in sheet:
                        df_raw = pd.read_excel(data, sheet_name=sheet, header=None)
                        h_idx = df_raw[df_raw.eq('编号').any(axis=1)].index
                        if not h_idx.empty:
                            self.airport = pd.read_excel(data, sheet_name=sheet, header=h_idx[0]).to_dict('records')

            # 4. 酒店 (大阪/东京)
            data, ftype = self._read_any(['OSAKA', 'Plaza'])
            if data is not None and ftype == 'excel':
                rate_sheet = next((s for s in data.sheet_names if "OSAKA" in s), None)
                cal_sheet = next((s for s in data.sheet_names if "Sheet1" in s), None)
                if rate_sheet: self.hotel_rates = pd.read_excel(data, sheet_name=rate_sheet)
                if cal_sheet:
                    df = pd.read_excel(data, sheet_name=cal_sheet, header=None)
                    for c in range(df.shape[1]-1):
                        mask = df.iloc[:, c].astype(str).str.match(r'202\d-\d{1,2}-\d{1,2}')
                        if mask.any():
                            for d, code in zip(df.loc[mask, c], df.loc[mask, c+1]):
                                if pd.notna(code): self.hotel_cal[str(d).strip()] = str(code).strip()
            data, ftype = self._read_any(['SHINJUKU', 'Washington'])
            if data is not None and ftype == 'excel':
                df = pd.read_excel(data, header=None)
                p_map = {str(c).strip(): float(re.sub(r'[^\d]','',str(p))) for c,p in zip(df.iloc[3], df.iloc[4]) if str(c).strip() in ['A','B','C','D','S']}
                for _, row in df.iloc[11:].iterrows():
                    if '月' in str(row[0]) and str(row[3]).strip() in p_map:
                        m = int(re.search(r'(\d+)', str(row[0])).group(1))
                        y = 2025 if m >= 10 else 2026
                        self.tokyo_rates[f"{y}-{m:02d}-{int(row[1]):02d}"] = p_map[str(row[3]).strip()]
            
            # 5. Markdown 文档 (区分多日游和通用)
            md_files = glob.glob('*.md')
            full_text = []
            for f in md_files:
                try:
                    with open(f, 'r', encoding='utf-8') as file:
                        content = file.read()
                        if "多日游" in f:
                            self.multiday_text += f"=== 多日游产品库 ({f}) ===\n{content}\n"
                            self.load_status.append(f"✅ 加载多日游产品库: {f}")
                        else:
                            full_text.append(f"=== 通用文档 ({f}) ===\n{content}\n")
                except: pass
            self.docs_text = "\n".join(full_text)
            
            return True
        except Exception as e:
            self.load_status.append(f"❌ 系统错误: {str(e)}")
            return False

# ==========================================
# 3. Agent (核心业务逻辑升级)
# ==========================================
class SmartAgent:
    def __init__(self, kb):
        self.kb = kb
        try:
            self.model_flash = genai.GenerativeModel("gemini-3-flash-preview")
            self.model_pro = genai.GenerativeModel("gemini-3-pro-preview")
        except:
            self.model_flash = genai.GenerativeModel("gemini-1.5-flash")
            self.model_pro = genai.GenerativeModel("gemini-1.5-pro")

    def semantic_match_product(self, user_query):
        """三层漏斗匹配: 多日游 -> 一日游 -> 包车 -> 通用"""
        day_products = list(self.kb.prices.keys())
        
        prompt = f"""
        User Query: "{user_query}"
        Day Tour Products: {day_products}
        
        Task: Classify intent into ONE of these categories:
        1. "MultiDay": User is asking about a package tour (e.g., 5 days, 7 days, Osaka+Tokyo package) OR asking about flight/visa inclusion for a package.
        2. "DayTour": User is asking about a specific single-day trip (e.g., Fuji, Nara, Kyoto day trip). Return format: "DayTour: [Exact Name]".
        3. "Charter": User wants to customize itinerary, change route, or rent a car.
        4. "General": General questions (weather, visa policy alone).
        
        Note: If user asks about flight/visa in context of a tour, prioritize "MultiDay" check first.
        """
        try:
            res = self.model_flash.generate_content(prompt)
            matched = res.text.strip()
            if "MultiDay" in matched: return "MultiDay"
            if "DayTour" in matched: 
                # Extract specific product name
                for p in day_products:
                    if p in matched: return f"DayTour:{p}"
                return "DayTour:Unknown"
            if "Charter" in matched: return "Charter"
            return "General"
        except: return "General"

    def generate_response(self, query, intent_type, context_data, model="flash"):
        if not AI_KEY: return "❌ 错误: 未配置 API Key"
        engine = self.model_pro if model == "pro" else self.model_flash
        
        # 业务规则提示词 (Business Logic)
        business_rules = """
        [CRITICAL BUSINESS RULES]
        1. **Scope Check**: If the user asks about a product NOT in the provided context/docs, output: "请联系对应产品经理了解". Do not hallucinate.
        2. **Multi-day Tours**:
           - ALWAYS recommend the "Starting Price" (起价).
           - MANDATORY Disclaimer: "价格仅供参考，最终库存和售价请点击链接二次确认" (Stock/Price needs manual check).
           - Flights/Visa: Unless docs say "Included", assume NOT included.
        3. **Day Tours**:
           - Calculate total price based on provided data.
           - MANDATORY Disclaimer: "价格已确认，但余位需后台二次确认" (Price confirmed, Seat check required).
        4. **Hotel**:
           - MANDATORY Disclaimer: "房态实时变动，请以此价格为准，但需二次确认是否有房".
        5. **Charter**:
           - Provide estimated price range.
           - Recommendation: If Day Tour route is modified, suggest Charter.
        """

        base_prompt = f"""
        Role: Ctrip Senior Sales Consultant.
        Question: "{query}"
        Intent: {intent_type}
        
        Context Data (Price/Route):
        {context_data}
        
        Knowledge Base (Multi-day Products):
        {self.kb.multiday_text}
        
        Knowledge Base (General Docs):
        {self.kb.docs_text[:10000]}
        
        {business_rules}
        
        OUTPUT FORMAT (Strictly use these separators):
        
        <<<REPLY_A>>>
        (Quick Reply: < 60 words. Direct Answer + Link/Price + Mandatory Disclaimer)
        <<<END_A>>>

        <<<REPLY_B>>>
        (Pro Reply: Warm greeting -> Answer -> Product Recommendation (ID) -> Upsell -> Mandatory Disclaimer)
        <<<END_B>>>

        <<<THOUGHTS>>>
        (Logic: Which file did you read? Why did you recommend this? Calculation steps?)
        <<<END_THOUGHTS>>>
        """
        try:
            return engine.generate_content(base_prompt).text
        except Exception as e:
            return f"❌ AI Error: {str(e)}"

# ==========================================
# 4. 前端界面 (复制功能增强版)
# ==========================================
def main():
    if 'kb' not in st.session_state:
        kb = KnowledgeBase()
        kb.load_all()
        st.session_state.kb = kb
        st.session_state.agent = SmartAgent(kb)
        st.session_state.loaded = True

    with st.sidebar:
        st.title("⚙️ 设置")
        if 'kb' in st.session_state:
            with st.expander("📊 知识库状态"):
                for log in st.session_state.kb.load_status:
                    if "❌" in log: st.error(log)
                    else: st.write(log)
        st.divider()
        date = st.date_input("出行日期", datetime(2026, 2, 1))
        pax = st.number_input("人数", 1, 15, 2)
        model = st.radio("AI 模型", ["Gemini 3 Fast", "Gemini 3 Pro"])

    st.title("👩‍💼 Ctrip 客服 Copilot")
    st.caption("✅ 业务规则已加载：多日游(起价+查库存) | 一日游(算价+查余位) | 兜底(找PM)")

    user_input = st.chat_input("输入问题... (如: 有没有关西5日游？包含机票吗？)")

    if user_input and 'kb' in st.session_state:
        agent = st.session_state.agent
        kb = st.session_state.kb
        
        with st.chat_message("user"): st.write(user_input)
        
        with st.status("🧠 正在分析意图与库存...", expanded=True) as status:
            # 1. 意图识别
            raw_intent = agent.semantic_match_product(user_input)
            st.write(f"🔍 意图分类: **{raw_intent}**")
            
            context = ""
            
            # --- 场景 A: 一日游 (DayTour) ---
            if "DayTour:" in raw_intent:
                product_name = raw_intent.split(":")[1]
                if product_name in kb.prices:
                    info = kb.prices[product_name]
                    # 价格计算
                    price_col = next((c for c in info if '结算' in c), None)
                    unit = float(info.get(price_col, 350))
                    tour_fee = unit * pax
                    # 酒店兜底
                    h_price = 13500 # 仅作参考
                    total = h_price + tour_fee
                    
                    context = f"""
                    [Day Tour Match]
                    - Product: {product_name}
                    - Unit Price: {unit}
                    - Pax: {pax}
                    - Calculated Tour Fee: {tour_fee}
                    - Rule: Price Confirmed, Seat Availability Needs Check.
                    """
                else:
                    context = "Product recognized but details missing. Check docs."

            # --- 场景 B: 多日游 (MultiDay) ---
            elif raw_intent == "MultiDay":
                context = """
                [Multi-day Intent]
                - Search 'multiday_text' for matching packages (5-day, 7-day, etc).
                - Rule: Quote 'Starting Price' found in text.
                - Rule: MUST link to online product.
                - Rule: Disclaimer 'Final price/stock needs manual check'.
                - Rule: Visa/Flight usually NOT included unless specified.
                """
                
            # --- 场景 C: 包车 (Charter) ---
            elif raw_intent == "Charter":
                context = """
                [Charter Intent]
                - User wants custom route.
                - Base Price: ~2500 RMB.
                - Suggest Charter if Day Tour doesn't fit needs.
                """
            
            # --- 场景 D: 通用/未知 ---
            else:
                context = "General Q&A. If answer not in docs, refer to PM."

            status.update(label="✅ 分析完成", state="complete", expanded=False)

        with st.chat_message("assistant"):
            sel_model = "flash" if "Fast" in model else "pro"
            raw_text = agent.generate_response(user_input, raw_intent, context, sel_model)
            
            # --- 强壮的解析与复制功能 ---
            part_a = re.search(r'<<<REPLY_A>>>(.*?)<<<END_A>>>', raw_text, re.DOTALL)
            part_b = re.search(r'<<<REPLY
