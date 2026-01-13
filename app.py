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
# 2. 智能数据加载器
# ==========================================
class KnowledgeBase:
    def __init__(self):
        self.prices = {}         
        self.itineraries = {}    
        self.charter = {}        
        self.airport = []        
        self.hotel_rates = {}    
        self.hotel_cal = {}      
        self.tokyo_rates = {}    
        self.docs_text = ""      
        self.load_status = [] 

    def _read_any(self, pattern_keywords, header=0):
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
            # 1. 价格
            data, ftype = self._read_any(['商品价格', 'Price'])
            if data is not None:
                if ftype == 'excel': df = pd.read_excel(data, sheet_name=0, header=0)
                else: df = data
                df.columns = [str(c).strip() for c in df.columns]
                if '产品名称' in df.columns:
                    self.prices = df.set_index('产品名称').to_dict('index')
                    self.load_status.append(f"✅ 价格表: {len(self.prices)}条")
            # 2. 行程
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
            # 4. 酒店
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
            # 5. Docs
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
            self.load_status.append(f"❌ 系统错误: {str(e)}")
            return False

# ==========================================
# 3. Agent (稳健版)
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
        if not self.kb.prices: return "General"
        product_list = list(self.kb.prices.keys())
        prompt = f"User Input: '{user_query}'\nProducts: {product_list}\nMatch exact product name. If Charter, return 'Charter'. Else 'General'."
        try:
            res = self.model_flash.generate_content(prompt)
            matched = res.text.strip()
            for p in product_list:
                if p in matched: return p
            if "Charter" in matched or "包车" in matched: return "Charter"
            return "General"
        except: return "General"

    def generate_response(self, query, context_data, model="flash"):
        if not AI_KEY: return "❌ 错误: 未配置 GOOGLE_API_KEY，无法生成回复。"
        
        engine = self.model_pro if model == "pro" else self.model_flash
        
        # 强制格式 Prompt
        base_prompt = f"""
        Role: Ctrip Senior Consultant.
        Question: "{query}"
        Context: {context_data}
        Docs: {self.kb.docs_text[:12000]}
        
        INSTRUCTION: You MUST format your output using these exact separators:
        
        <<<REPLY_A>>>
        (Write a Quick Reply here: < 50 words)
        <<<END_A>>>

        <<<REPLY_B>>>
        (Write a Professional Reply here: Detail, Warm tone)
        <<<END_B>>>

        <<<THOUGHTS>>>
        (Write logic/internal notes here)
        <<<END_THOUGHTS>>>
        """
        try:
            return engine.generate_content(base_prompt).text
        except Exception as e:
            return f"❌ AI 调用失败: {str(e)}"

# ==========================================
# 4. 前端界面 (带安全网)
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
    st.caption("🚀 提示：如果生成失败，请检查右侧 '知识库状态' 是否正常")

    user_input = st.chat_input("输入问题... (如: 富士山一日游含餐吗？)")

    if user_input and 'kb' in st.session_state:
        agent = st.session_state.agent
        kb = st.session_state.kb
        
        with st.chat_message("user"): st.write(user_input)
        
        with st.status("🧠 Ctrip AI 正在计算...", expanded=True) as status:
            intent = agent.semantic_match_product(user_input)
            st.write(f"🔍 意图: **{intent}**")
            
            context = ""
            if intent in kb.prices:
                info = kb.prices[intent]
                price_col = next((c for c in info if '结算' in c), None)
                unit = float(info.get(price_col, 350))
                tour = unit * pax
                city = "Osaka" if "大阪" in intent else "Tokyo"
                h_price = 20200 if city == "Tokyo" else 13500
                total = h_price + tour
                context = f"Product: {intent}, Total Price: {total}, Pax: {pax}, Date: {date}"
            elif intent == "Charter":
                context = "User wants Charter/Custom tour."
            else:
                context = "General Query. Check docs."
            
            status.update(label="✅ 生成完毕", state="complete", expanded=False)

        with st.chat_message("assistant"):
            sel_model = "flash" if "Fast" in model else "pro"
            raw_text = agent.generate_response(user_input, context, sel_model)
            
            # --- 核心修复：更强壮的解析逻辑 ---
            # 1. 检查是否报错
            if "❌" in raw_text:
                st.error(raw_text)
            else:
                # 2. 尝试正则提取 (注意 Prompt 改成了 <<< >>>)
                part_a = re.search(r'<<<REPLY_A>>>(.*?)<<<END_A>>>', raw_text, re.DOTALL)
                part_b = re.search(r'<<<REPLY_B>>>(.*?)<<<END_B>>>', raw_text, re.DOTALL)
                part_thoughts = re.search(r'<<<THOUGHTS>>>(.*?)<<<END_THOUGHTS>>>', raw_text, re.DOTALL)
                
                # 3. 成功提取 -> 显示分栏
                if part_a and part_b:
                    st.subheader("📋 选项 A：极简版")
                    st.code(part_a.group(1).strip(), language=None) # 复制按钮在此

                    st.subheader("💼 选项 B：专业版")
                    st.code(part_b.group(1).strip(), language=None) # 复制按钮在此

                    with st.expander("🧠 AI 思考与销售建议"):
                        if part_thoughts: st.markdown(part_thoughts.group(1).strip())
                        else: st.write("无建议")
                
                # 4. [安全网] 提取失败 -> 显示全量内容 (保底方案)
                else:
                    st.warning("⚠️ AI 未按标准格式输出，已切换到全量复制模式：")
                    st.code(raw_text, language=None) # 确保有复制按钮

        if intent == "Charter" or "定制" in user_input:
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
                            t = res[0]['legs'][0]['duration']['text']
                            st.success(f"距离: {d}, 耗时: {t}")
                        else: st.error("未找到路线")

if __name__ == "__main__":
    main()
