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
# 2. 知识库加载器 (智能表头定位 + 双格式兼容)
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

    def _smart_read_df(self, df, required_col_kw):
        """
        智能寻找表头：
        扫描前10行，找到包含 required_col_kw (如'产品名称') 的那一行作为Header
        """
        # 1. 尝试直接检查列名
        if any(required_col_kw in str(c) for c in df.columns):
            return df
        
        # 2. 扫描前10行内容
        for idx, row in df.head(10).iterrows():
            # 将整行转为字符串查找关键词
            row_str = " ".join([str(x) for x in row.values])
            if required_col_kw in row_str:
                # 找到了！以这一行为header重新整理数据
                df.columns = df.iloc[idx] # 设为列名
                df = df.iloc[idx+1:]      # 取下面的数据
                df = df.reset_index(drop=True)
                return df
        
        return None # 没找到

    def load_all(self):
        self.load_status = []
        try:
            # =========================================
            # 策略 A: 优先扫描 Excel (.xlsx / .xls)
            # =========================================
            xlsx_files = glob.glob('*.xlsx') + glob.glob('*.xls')
            
            for f in xlsx_files:
                try:
                    xls = pd.ExcelFile(f)
                    filename = os.path.basename(f)
                    
                    for sheet in xls.sheet_names:
                        sheet_clean = sheet.strip()
                        
                        # --- 1. 一日游价格 ---
                        if "商品价格" in sheet_clean or "Price" in sheet_clean:
                            df = pd.read_excel(xls, sheet_name=sheet, header=None) # 先不设Header
                            # 智能寻找包含"产品名称"的行
                            df_smart = self._smart_read_df(df, "产品名称")
                            
                            if df_smart is not None:
                                df_smart.columns = [str(c).strip() for c in df_smart.columns]
                                self.prices.update(df_smart.set_index('产品名称').to_dict('index'))
                                self.load_status.append(f"✅ Excel提取价格: {filename} - {sheet}")
                            else:
                                self.load_status.append(f"⚠️ 发现价格表但未找到'产品名称'列: {sheet}")

                        # --- 2. 包车 ---
                        elif "包车" in sheet_clean:
                            df = pd.read_excel(xls, sheet_name=sheet, header=None)
                            df_smart = self._smart_read_df(df, "包车线路") # 找"包车线路"
                            
                            if df_smart is not None:
                                df_smart.columns = [str(c).strip() for c in df_smart.columns]
                                self.charter.update(df_smart.set_index('包车线路')['包车价格（人民币）'].to_dict())
                                self.load_status.append(f"✅ Excel提取包车: {sheet}")
                        
                        # --- 3. 接送机 ---
                        elif "接送机" in sheet_clean:
                            df = pd.read_excel(xls, sheet_name=sheet, header=None)
                            df_smart = self._smart_read_df(df, "编号")
                            if df_smart is not None:
                                self.airport = df_smart.to_dict('records')
                                self.load_status.append(f"✅ Excel提取接送机: {sheet}")

                        # --- 4. 酒店 & 行程 ---
                        elif "OSAKA" in sheet_clean and "Sheet1" not in sheet_clean:
                            self.hotel_rates = pd.read_excel(xls, sheet_name=sheet)
                            self.loaded_counts["Hotel"] += 1
                        
                        elif "Sheet1" in sheet_clean and ("OSAKA" in filename.upper() or "PLAZA" in filename.upper()):
                            df = pd.read_excel(xls, sheet_name=sheet, header=None)
                            self._parse_calendar(df)
                            self.load_status.append(f"✅ 提取大阪日历: {filename}")

                        elif "SHINJUKU" in filename.upper():
                            df = pd.read_excel(xls, sheet_name=sheet, header=None)
                            self._parse_shinjuku(df)
                            self.load_status.append(f"✅ 提取东京酒店: {filename}")
                            self.loaded_counts["Hotel"] += 1

                        elif "日游" in filename and "商品价格" not in sheet_clean:
                            self.itineraries[sheet_clean] = pd.read_excel(xls, sheet_name=sheet, header=None)

                except Exception as e:
                    self.load_status.append(f"⚠️ Excel读取警告 {f}: {e}")

            # =========================================
            # 策略 B: 补充扫描 CSV (.csv)
            # =========================================
            csv_files = glob.glob('*.csv')
            for f in csv_files:
                try:
                    filename = os.path.basename(f)
                    
                    # 1. 价格表 CSV
                    if "商品价格" in filename or "Price" in filename:
                        df = pd.read_csv(f, header=None)
                        df_smart = self._smart_read_df(df, "产品名称")
                        if df_smart is not None:
                            df_smart.columns = [str(c).strip() for c in df_smart.columns]
                            self.prices.update(df_smart.set_index('产品名称').to_dict('index'))
                            self.load_status.append(f"✅ CSV提取价格: {filename}")

                    # 2. 包车 CSV
                    elif "包车" in filename:
                        df = pd.read_csv(f, header=None)
                        df_smart = self._smart_read_df(df, "包车线路")
                        if df_smart is not None:
                            df_smart.columns = [str(c).strip() for c in df_smart.columns]
                            self.charter.update(df_smart.set_index('包车线路')['包车价格（人民币）'].to_dict())
                            self.load_status.append(f"✅ CSV提取包车: {filename}")
                    
                    # 3. 行程 CSV
                    elif "日游" in filename and "商品价格" not in filename:
                        clean_name = filename.split(' - ')[-1].replace('.csv', '').strip()
                        self.itineraries[clean_name] = pd.read_csv(f, header=None)
                
                except Exception as e:
                    self.load_status.append(f"⚠️ CSV读取警告 {f}: {e}")

            # =========================================
            # 策略 C: 文档加载
            # =========================================
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
            
            # 更新计数
            self.loaded_counts["DayTour"] = len(self.prices)
            self.loaded_counts["Charter"] = len(self.charter)
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
        day_products = list(self.kb.prices.keys())
        prompt = f"""
        User Query: "{user_query}"
        Day Tour Products: {day_products}
        Task: Classify intent.
        1. "MultiDay": Package tours (5+ days).
        2. "DayTour: [Name]": Specific day trip.
        3. "Charter": Custom route.
        4. "General": Others.
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
        
        business_rules = """
        RULES:
        1. If not in docs, output: "请联系对应产品经理了解".
        2. Multi-day: Quote starting price + "点击链接二次确认".
        3. Day Tour: Quote calculated price + "余位需后台确认".
        4. Charter: Estimate range + PM check.
        """

        base_prompt = f"""
        Role: Ctrip Senior Sales Consultant.
        Question: "{query}"
        Intent: {intent}
        Context: {context}
        Multi-day: {self.kb.multiday_text}
        General: {self.kb.docs_text[:10000]}
        {business_rules}
        
        OUTPUT FORMAT:
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
        st.title("⚙️ 知识库监控")
        if 'kb' in st.session_state:
            counts = st.session_state.kb.loaded_counts
            c1, c2 = st.columns(2)
            c1.metric("一日游线路", counts["DayTour"])
            c2.metric("包车线路", counts["Charter"])
            
            with st.expander("📊 查看加载日志 (排查错误)"):
                for log in st.session_state.kb.load_status:
                    if "❌" in log: st.error(log)
                    elif "⚠️" in log: st.warning(log)
                    elif "✅" in log: st.success(log)
                    else: st.write(log)
        
        st.divider()
        date = st.date_input("出行日期", datetime(2026, 2, 1))
        pax = st.number_input("人数", 1, 15, 2)
        model = st.radio("AI 模型", ["Gemini 3 Fast", "Gemini 3 Pro"])

    st.title("👩‍💼 Ctrip 客服 Copilot")
    st.caption("✅ 已加载: 多日游文档 | 一日游价格表 | 包车表 | 酒店日历")

    user_input = st.chat_input("输入问题... (如: 富士山一日游多少钱？)")

    if user_input and 'kb' in st.session_state:
        agent = st.session_state.agent
        kb = st.session_state.kb
        
        with st.chat_message("user"): st.write(user_input)
        
        with st.status("🧠 分析意图与库存...", expanded=True) as status:
            raw_intent = agent.semantic_match_product(user_input)
            st.write(f"🔍 意图: **{raw_intent}**")
            
            context = ""
            if "DayTour:" in raw_intent:
                p_name = raw_intent.split(":")[1]
                if p_name in kb.prices:
                    info = kb.prices[p_name]
                    # 智能寻找价格列 (找'结算')
                    p_col = next((c for c in info if '结算' in c), None)
                    if p_col:
                        unit = float(info[p_col])
                        total = unit * pax
                        context = f"Product: {p_name}, Unit: {unit}, Pax: {pax}, Total: {total}. Status: Price OK, Check Seats."
                    else:
                        context = f"Found {p_name} but NO price column."
                else: context = "Product name recognized but not in Excel."
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
            
            # 解析
            try: part_a = re.search(r'<<<REPLY_A>>>([\s\S]*?)<<<END_A>>>', raw_text).group(1).strip()
            except: part_a = None
            try: part_b = re.search(r'<<<REPLY_B>>>([\s\S]*?)<<<END_B>>>', raw_text).group(1).strip()
            except: part_b = None
            try: thoughts = re.search(r'<<<THOUGHTS>>>([\s\S]*?)<<<END_THOUGHTS>>>', raw_text).group(1).strip()
            except: thoughts = None

            st.subheader("📋 选项 A：极简版")
            if part_a: st.code(part_a, language=None)
            else: 
                st.warning("⚠️ 格式自适应")
                st.code(raw_text, language=None)

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
