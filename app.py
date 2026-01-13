import streamlit as st
import pandas as pd
import glob
import os
import re
from datetime import datetime
import googlemaps
import google.generativeai as genai
import io
import time

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
# 2. 知识库加载器 (深层 Sheet 扫描)
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
        self.multiday_text = ""  # 多日游产品
        self.load_status = [] 
        self.loaded_counts = {"DayTour": 0, "Charter": 0, "Hotel": 0, "Docs": 0}

    def load_all(self):
        self.load_status = []
        try:
            # --- 策略 A: 扫描所有 Excel 文件 (深层扫描 Sheet) ---
            xlsx_files = glob.glob('*.xlsx') + glob.glob('*.xls')
            
            for f in xlsx_files:
                try:
                    xls = pd.ExcelFile(f)
                    filename = os.path.basename(f)
                    
                    # 遍历这个文件里的所有 Sheet
                    for sheet in xls.sheet_names:
                        sheet_clean = sheet.strip()
                        
                        # 1. 发现 [商品价格] Sheet -> 载入价格表
                        if "商品价格" in sheet_clean or "Price" in sheet_clean:
                            df = pd.read_excel(xls, sheet_name=sheet, header=0)
                            df.columns = [str(c).strip() for c in df.columns]
                            if '产品名称' in df.columns:
                                self.prices.update(df.set_index('产品名称').to_dict('index'))
                                self.load_status.append(f"✅ 提取价格: {filename} -> {sheet}")
                                self.loaded_counts["DayTour"] = len(self.prices)

                        # 2. 发现 [包车] Sheet -> 载入包车
                        elif "包车" in sheet_clean:
                            df = pd.read_excel(xls, sheet_name=sheet, header=1) # 包车通常header=1
                            df.columns = [str(c).strip() for c in df.columns]
                            if '包车线路' in df.columns:
                                self.charter.update(df.set_index('包车线路')['包车价格（人民币）'].to_dict())
                                self.load_status.append(f"✅ 提取包车: {filename} -> {sheet}")
                                self.loaded_counts["Charter"] = len(self.charter)

                        # 3. 发现 [接送机] Sheet -> 载入接送机
                        elif "接送机" in sheet_clean:
                            df_raw = pd.read_excel(xls, sheet_name=sheet, header=None)
                            # 动态找 Header
                            h_idx = -1
                            for idx, row in df_raw.iterrows():
                                if row.astype(str).str.contains("编号").any():
                                    h_idx = idx
                                    break
                            if h_idx != -1:
                                self.airport = pd.read_excel(xls, sheet_name=sheet, header=h_idx).to_dict('records')
                                self.load_status.append(f"✅ 提取接送机: {filename} -> {sheet}")

                        # 4. 发现 [OSAKA] 价格表
                        elif "OSAKA" in sheet_clean and "Sheet1" not in sheet_clean:
                            self.hotel_rates = pd.read_excel(xls, sheet_name=sheet)
                            self.loaded_counts["Hotel"] += 1
                        
                        # 5. 发现 [Sheet1] 且在 大阪文件里 -> 日历
                        elif "Sheet1" in sheet_clean and ("OSAKA" in filename or "PLAZA" in filename.upper()):
                            df = pd.read_excel(xls, sheet_name=sheet, header=None)
                            self._parse_calendar(df)
                            self.load_status.append(f"✅ 提取大阪日历: {filename}")

                        # 6. 东京酒店 (SHINJUKU)
                        elif "SHINJUKU" in filename.upper():
                            df = pd.read_excel(xls, sheet_name=sheet, header=None)
                            self._parse_shinjuku(df)
                            self.load_status.append(f"✅ 提取东京酒店: {filename}")
                            self.loaded_counts["Hotel"] += 1

                        # 7. 剩下的通常是 [具体线路行程] (如果文件名包含"日游")
                        elif "日游" in filename and "商品价格" not in sheet_clean:
                            # 排除掉价格表本身，其他的Sheet名通常就是线路名
                            self.itineraries[sheet_clean] = pd.read_excel(xls, sheet_name=sheet, header=None)

                except Exception as e:
                    self.load_status.append(f"⚠️ 无法读取 {f}: {e}")

            # --- 策略 B: 扫描 Markdown 文档 ---
            md_files = glob.glob('*.md')
            full_text = []
            for f in md_files:
                try:
                    with open(f, 'r', encoding='utf-8') as file:
                        content = file.read()
                        if "多日游" in f:
                            self.multiday_text += f"\n=== 多日游原始数据 ({f}) ===\n{content}\n"
                            self.load_status.append(f"✅ 加载多日游: {f}")
                        else:
                            full_text.append(f"=== 通用文档 ({f}) ===\n{content}\n")
                except: pass
            self.docs_text = "\n".join(full_text)
            self.loaded_counts["Docs"] = len(md_files)
            
            return True
        except Exception as e:
            self.load_status.append(f"❌ 致命错误: {str(e)}")
            return False

    def _parse_calendar(self, df):
        for c in range(df.shape[1]-1):
            mask = df.iloc[:, c].astype(str).str.match(r'202\d-\d{1,2}-\d{1,2}')
            if mask.any():
                for d, code in zip(df.loc[mask, c], df.loc[mask, c+1]):
                    if pd.notna(code): self.hotel_cal[str(d).strip()] = str(code).strip()

    def _parse_shinjuku(self, df):
        try:
            p_map = {str(c).strip(): float(re.sub(r'[^\d]','',str(p))) for c,p in zip(df.iloc[3], df.iloc[4]) if str(c).strip() in ['A','B','C','D','S']}
            for _, row in df.iloc[11:].iterrows():
                if '月' in str(row[0]) and str(row[3]).strip() in p_map:
                    m = int(re.search(r'(\d+)', str(row[0])).group(1))
                    y = 2025 if m >= 10 else 2026
                    self.tokyo_rates[f"{y}-{m:02d}-{int(row[1]):02d}"] = p_map[str(row[3]).strip()]
        except: pass

# ==========================================
# 3. Agent (销售漏斗逻辑)
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
        """漏斗匹配: 多日游 -> 一日游 -> 包车 -> 通用"""
        day_products = list(self.kb.prices.keys())
        prompt = f"""
        User Query: "{user_query}"
        Day Tour List: {day_products}
        
        Analyze Intent (Return ONE string):
        1. "MultiDay": User asks about 5-day, 7-day packages, or general package questions (visa/flight within package).
        2. "DayTour: [Exact Name]": User asks about specific one-day trips (Fuji, Nara, Kyoto). Match closest name.
        3. "Charter": User wants customization, car rental, or modifying a fixed route.
        4. "General": Visa (standalone), Weather, Policy.
        """
        try:
            res = self.model_flash.generate_content(prompt)
            matched = res.text.strip()
            if "MultiDay" in matched: return "MultiDay"
            if "DayTour" in matched: 
                for p in day_products:
                    if p in matched: return f"DayTour:{p}"
                return "DayTour:Unknown"
            if "Charter" in matched: return "Charter"
            return "General"
        except: return "General"

    def generate_response(self, query, intent, context, model="flash"):
        if not AI_KEY: return "❌ 错误: 未配置 API Key"
        engine = self.model_pro if model == "pro" else self.model_flash
        
        # 严格的业务规则
        business_rules = """
        [BUSINESS RULES - DO NOT VIOLATE]
        1. **Scope**: If answer is NOT in context/docs, output: "请联系对应产品经理了解".
        2. **Multi-day Tours**: 
           - Quote 'Starting Price' found in docs.
           - MUST say: "价格仅供参考，最终库存和售价请点击链接二次确认".
           - Visa/Flight: Assume NOT included unless explicitly stated.
        3. **Day Tours**: 
           - Calculate total price based on Pax.
           - MUST say: "价格已确认，但余位需后台二次确认".
        4. **Charter**: Give estimated range, refer to PM for final quote.
        5. **Format**: Use the separators strictly for frontend parsing.
        """

        base_prompt = f"""
        Role: Ctrip Senior Sales Consultant.
        Question: "{query}"
        Intent: {intent}
        
        Calculated Context: {context}
        
        [Raw Data - Multi-day]:
        {self.kb.multiday_text}
        
        [Raw Data - General]:
        {self.kb.docs_text[:10000]}
        
        {business_rules}
        
        OUTPUT FORMAT:
        <<<REPLY_A>>>
        (Quick Reply: < 60 words. Conclusion + Disclaimer)
        <<<END_A>>>

        <<<REPLY_B>>>
        (Pro Reply: Greeting -> Detail -> Upsell -> Disclaimer)
        <<<END_B>>>

        <<<THOUGHTS>>>
        (Logic: Source file? Calculation?)
        <<<END_THOUGHTS>>>
        """
        try:
            return engine.generate_content(base_prompt).text
        except Exception as e:
            return f"❌ AI Error: {e}"

# ==========================================
# 4. 前端界面 (自动复制兜底)
# ==========================================
def main():
    if 'kb' not in st.session_state:
        kb = KnowledgeBase()
        kb.load_all()
        st.session_state.kb = kb
        st.session_state.agent = SmartAgent(kb)
        st.session_state.loaded = True

    with st.sidebar:
        st.title("⚙️ 知识库监控")
        if 'kb' in st.session_state:
            # 实时显示加载数量
            counts = st.session_state.kb.loaded_counts
            c1, c2 = st.columns(2)
            c1.metric("一日游线路", counts["DayTour"])
            c2.metric("包车线路", counts["Charter"])
            c3, c4 = st.columns(2)
            c3.metric("酒店数据", counts["Hotel"])
            c4.metric("文档", counts["Docs"])
            
            with st.expander("查看加载日志"):
                for log in st.session_state.kb.load_status:
                    if "❌" in log: st.error(log)
                    elif "✅" in log: st.success(log)
                    else: st.write(log)
        
        st.divider()
        date = st.date_input("出行日期", datetime(2026, 2, 1))
        pax = st.number_input("人数", 1, 15, 2)
        model = st.radio("AI 模型", ["Gemini 3 Fast", "Gemini 3 Pro"])

    st.title("👩‍💼 Ctrip 客服 Copilot")
    st.info("💡 销售提醒：多日游需查链接库存 | 一日游需查后台余位 | 未知问题找PM")

    user_input = st.chat_input("输入问题... (如: 有没有关西5日游？富士山一日游含餐吗？)")

    if user_input and 'kb' in st.session_state:
        agent = st.session_state.agent
        kb = st.session_state.kb
        
        with st.chat_message("user"): st.write(user_input)
        
        # 1. 分析阶段
        with st.status("🧠 全局数据检索中...", expanded=True) as status:
            raw_intent = agent.semantic_match_product(user_input)
            st.write(f"🔍 意图识别: **{raw_intent}**")
            
            context = ""
            
            # --- 分支逻辑 ---
            if "DayTour:" in raw_intent:
                p_name = raw_intent.split(":")[1]
                if p_name in kb.prices:
                    info = kb.prices[p_name]
                    # 查找包含'结算'的价格列
                    p_col = next((c for c in info if '结算' in c), None)
                    if p_col:
                        unit = float(info[p_col])
                        total = unit * pax
                        context = f"Product: {p_name}, Unit: {unit}, Total: {total} (Pax: {pax})."
                    else:
                        context = f"Product {p_name} found, but price column missing."
                else:
                    context = "Product recognized but not in price list."
            
            elif raw_intent == "MultiDay":
                context = "User asks about Multi-day. CHECK 'multiday_text' for details."
            
            elif raw_intent == "Charter":
                context = "User asks for Charter. Suggest approx price + PM check."
            
            else:
                context = "General Question. Check Docs."

            status.update(label="✅ 数据准备就绪", state="complete", expanded=False)

        # 2. 生成阶段
        with st.chat_message("assistant"):
            sel_model = "flash" if "Fast" in model else "pro"
            raw_text = agent.generate_response(user_input, raw_intent, context, sel_model)
            
            # 3. 强力解析 (防止断行/格式错误)
            try:
                part_a = re.search(r'<<<REPLY_A>>>([\s\S]*?)<<<END_A>>>', raw_text).group(1).strip()
            except: part_a = None
            
            try:
                part_b = re.search(r'<<<REPLY_B>>>([\s\S]*?)<<<END_B>>>', raw_text).group(1).strip()
            except: part_b = None
            
            try:
                thoughts = re.search(r'<<<THOUGHTS>>>([\s\S]*?)<<<END_THOUGHTS>>>', raw_text).group(1).strip()
            except: thoughts = None

            # 4. 展示 (强制显示复制框)
            st.subheader("📋 选项 A：极简版")
            if part_a:
                st.code(part_a, language=None)
            else:
                st.warning("⚠️ 格式自适应模式")
                st.code(raw_text, language=None) # 兜底

            if part_b:
                st.subheader("💼 选项 B：专业版")
                st.code(part_b, language=None)

            with st.expander("🧠 AI 思考与建议"):
                if thoughts: st.markdown(thoughts)
                else: st.write("无")

        # 工具箱
        if raw_intent == "Charter" or "包车" in user_input:
            with st.expander("🧰 距离校验工具", expanded=True):
                c1, c2 = st.columns(2)
                start = c1.text_input("起点", "大阪")
                end = c2.text_input("终点", "京都")
                if st.button("🚀 校验"):
                    if gmaps:
                        now = datetime.now()
                        res = gmaps.directions(start, end, mode="driving", departure_time=now)
                        if res:
                            d = res[0]['legs'][0]['distance']['text']
                            st.success(f"距离: {d}")
                        else: st.error("未找到路线")

if __name__ == "__main__":
    main()
