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
# 2. 暴力数据加载器 (不再依赖文件名)
# ==========================================
class KnowledgeBase:
    def __init__(self):
        self.prices = {}         
        self.charter = {}        
        self.airport = []        
        self.hotel_rates = {}    
        self.hotel_cal = {}      
        self.tokyo_rates = {}    
        self.docs_text = ""      
        self.multiday_text = ""  
        self.load_status = [] # 诊断日志
        self.debug_info = []  # 详细调试信息

    def _clean_columns(self, df):
        """清洗列名：去空格、去换行"""
        df.columns = [str(c).strip().replace('\n', '') for c in df.columns]
        return df

    def _find_header(self, df, keywords):
        """在DF前10行里暴力寻找包含关键词的行"""
        # 1. 检查当前列名
        for kw in keywords:
            if any(kw in str(c) for c in df.columns):
                return self._clean_columns(df)
        
        # 2. 检查前10行数据
        for idx, row in df.head(10).iterrows():
            row_str = " ".join([str(x) for x in row.values])
            for kw in keywords:
                if kw in row_str:
                    # 找到表头，重置DataFrame
                    df.columns = df.iloc[idx]
                    df = df.iloc[idx+1:].reset_index(drop=True)
                    return self._clean_columns(df)
        return None

    def load_all(self):
        self.load_status = []
        self.debug_info = []
        
        # 1. 扫描所有Excel (无视文件名)
        all_xlsx = glob.glob('*.xlsx') + glob.glob('*.xls')
        
        for f in all_xlsx:
            try:
                xls = pd.ExcelFile(f)
                filename = os.path.basename(f)
                
                for sheet in xls.sheet_names:
                    try:
                        # 先读各种可能：无header
                        df_raw = pd.read_excel(xls, sheet_name=sheet, header=None)
                        
                        # --- 尝试匹配：一日游价格表 ---
                        # 特征：有"产品名称" 或 "线路名称"
                        df_price = self._find_header(df_raw.copy(), ["产品名称", "线路名称"])
                        if df_price is not None:
                            # 进一步确认：是不是有价格列？
                            price_col = next((c for c in df_price.columns if "结算" in c or "价格" in c or "成人" in c), None)
                            if price_col:
                                # 成功！
                                records = df_price.set_index(df_price.columns[0]).to_dict('index')
                                self.prices.update(records)
                                self.load_status.append(f"✅ 发现价格表: {filename} ({sheet}) - {len(records)}条")
                                self.debug_info.append(f"  -> 价格列名识别为: {price_col}")
                                continue # 匹配成功，跳过后续检查

                        # --- 尝试匹配：包车表 ---
                        # 特征：有"包车线路"
                        df_charter = self._find_header(df_raw.copy(), ["包车线路"])
                        if df_charter is not None:
                            price_col = next((c for c in df_charter.columns if "价格" in c or "结算" in c), None)
                            if price_col:
                                records = df_charter.set_index(df_charter.columns[0])[price_col].to_dict()
                                self.charter.update(records)
                                self.load_status.append(f"✅ 发现包车表: {filename} ({sheet})")
                                continue

                        # --- 尝试匹配：酒店 (OSAKA) ---
                        if "OSAKA" in sheet.upper() or "OSAKA" in filename.upper():
                            if "Sheet1" not in sheet: # 排除日历
                                self.hotel_rates = pd.read_excel(xls, sheet_name=sheet)
                                self.load_status.append(f"✅ 发现酒店表: {filename}")

                        # --- 尝试匹配：日历 (Sheet1) ---
                        if "Sheet1" in sheet and ("OSAKA" in filename.upper() or "PLAZA" in filename.upper()):
                             self._parse_calendar(df_raw)
                             self.load_status.append(f"✅ 发现日历: {filename}")

                    except Exception as sheet_e:
                         self.debug_info.append(f"⚠️ 跳过 {sheet}: {str(sheet_e)}")

            except Exception as e:
                self.load_status.append(f"❌ 读取文件失败 {f}: {e}")

        # 2. 扫描 Markdown
        md_files = glob.glob('*.md')
        full_text = []
        for f in md_files:
            try:
                with open(f, 'r', encoding='utf-8') as file:
                    c = file.read()
                    if "多日游" in f: self.multiday_text += f"\n=== {f} ===\n{c}\n"
                    else: full_text.append(f"=== {f} ===\n{c}\n")
            except: pass
        self.docs_text = "\n".join(full_text)
        self.load_status.append(f"✅ 文档加载: {len(md_files)}个")
        
        return True

    def _parse_calendar(self, df):
        for c in range(df.shape[1]-1):
            mask = df.iloc[:, c].astype(str).str.match(r'202\d-\d{1,2}-\d{1,2}')
            if mask.any():
                for d, code in zip(df.loc[mask, c], df.loc[mask, c+1]):
                    if pd.notna(code): self.hotel_cal[str(d).strip()] = str(code).strip()

# ==========================================
# 3. Agent
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
        # 即使没加载到价格，也要能回答多日游
        day_products = list(self.kb.prices.keys())
        prompt = f"""
        User Query: "{user_query}"
        Day Tours Available: {day_products}
        
        Classify Intent:
        1. "MultiDay": Package tours (5 days, 7 days), Visa, Flights.
        2. "DayTour:[Name]": Specific day trip in the list.
        3. "Charter": Custom route, car rental.
        4. "General": Weather, Policy.
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
        engine = self.model_pro if model == "pro" else self.model_flash
        
        business_rules = """
        [BUSINESS RULES]
        1. Unknown Product: "请联系对应产品经理了解".
        2. Multi-day: Quote starting price. "价格仅供参考，最终库存和售价请点击链接二次确认".
        3. Day Tour: Quote calculated price. "价格已确认，但余位需后台二次确认".
        4. No Hallucination: Do not invent prices.
        """

        base_prompt = f"""
        Role: Ctrip Senior Consultant.
        Query: "{query}"
        Intent: {intent}
        Context: {context}
        Multi-day Docs: {self.kb.multiday_text}
        General Docs: {self.kb.docs_text[:8000]}
        {business_rules}
        
        OUTPUT FORMAT (Strict):
        <<<REPLY_A>>>
        (Quick Reply)
        <<<END_A>>>
        <<<REPLY_B>>>
        (Pro Reply)
        <<<END_B>>>
        <<<THOUGHTS>>>
        (Logic)
        <<<END_THOUGHTS>>>
        """
        try: return engine.generate_content(base_prompt).text
        except: return "AI Error"

# ==========================================
# 4. 前端界面
# ==========================================
def main():
    if 'kb' not in st.session_state:
        kb = KnowledgeBase()
        kb.load_all()
        st.session_state.kb = kb
        st.session_state.agent = SmartAgent(kb)
        st.session_state.loaded = True

    with st.sidebar:
        st.title("🕵️‍♂️ 数据诊断室")
        if 'kb' in st.session_state:
            # 这里的显示非常关键，能告诉我们到底读到了什么
            st.write(f"🔢 一日游产品数: **{len(st.session_state.kb.prices)}**")
            with st.expander("📄 查看详细加载日志 (Debug)", expanded=True):
                for log in st.session_state.kb.load_status:
                    if "✅" in log: st.success(log)
                    elif "❌" in log: st.error(log)
                    else: st.write(log)
                st.write("--- 调试信息 ---")
                for dbg in st.session_state.kb.debug_info[:10]: # 只显示前10条
                    st.caption(dbg)
        
        st.divider()
        date = st.date_input("出行日期", datetime(2026, 2, 1))
        pax = st.number_input("人数", 1, 15, 2)
        model = st.radio("AI 模型", ["Gemini 3 Fast", "Gemini 3 Pro"])

    st.title("👩‍💼 Ctrip 客服 Copilot")

    user_input = st.chat_input("输入问题... (如: 富士山一日游多少钱？)")

    if user_input and 'kb' in st.session_state:
        agent = st.session_state.agent
        kb = st.session_state.kb
        
        with st.chat_message("user"): st.write(user_input)
        
        with st.status("🧠 思考中...", expanded=True) as status:
            raw_intent = agent.semantic_match_product(user_input)
            st.write(f"🔍 意图: **{raw_intent}**")
            
            context = ""
            if "DayTour:" in raw_intent:
                p_name = raw_intent.split(":")[1]
                if p_name in kb.prices:
                    info = kb.prices[p_name]
                    # 暴力匹配价格列
                    p_col = next((c for c in info if '结算' in c or '价格' in c), None)
                    if p_col:
                        unit = float(info[p_col])
                        total = unit * pax
                        context = f"Product: {p_name}, Unit: {unit}, Pax: {pax}, Total: {total}. Disclaimer: Price OK, Check Seats."
                    else:
                        context = f"Product found but price column ambiguous. Columns: {list(info.keys())}"
                else: context = "Product recognized but not in database."
            elif raw_intent == "MultiDay":
                context = "Multi-day intent. Check multiday_text."
            elif raw_intent == "Charter":
                context = "Charter intent."
            else:
                context = "General Q&A."
            
            status.update(label="✅ 完成", state="complete", expanded=False)

        with st.chat_message("assistant"):
            sel_model = "flash" if "Fast" in model else "pro"
            raw_text = agent.generate_response(user_input, raw_intent, context, sel_model)
            
            # 强壮的正则解析
            try: part_a = re.search(r'<<<REPLY_A>>>([\s\S]*?)<<<END_A>>>', raw_text).group(1).strip()
            except: part_a = None
            try: part_b = re.search(r'<<<REPLY_B>>>([\s\S]*?)<<<END_B>>>', raw_text).group(1).strip()
            except: part_b = None
            try: thoughts = re.search(r'<<<THOUGHTS>>>([\s\S]*?)<<<END_THOUGHTS>>>', raw_text).group(1).strip()
            except: thoughts = None

            st.subheader("📋 选项 A：极简版")
            if part_a: st.code(part_a, language=None)
            else: st.code(raw_text, language=None)

            if part_b:
                st.subheader("💼 选项 B：专业版")
                st.code(part_b, language=None)

            with st.expander("🧠 AI 思考"):
                if thoughts: st.markdown(thoughts)
                else: st.write("无")

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
