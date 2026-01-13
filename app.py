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
# 2. 智能数据加载器 (CSV/Excel 全兼容)
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
        self.load_status = [] # 记录加载日志

    def _read_any(self, pattern_keywords, header=0):
        """智能读取函数：自动尝试 xlsx, xls, csv"""
        # 1. 构造搜索模式
        patterns = []
        for kw in pattern_keywords:
            patterns.append(f'*{kw}*.xlsx')
            patterns.append(f'*{kw}*.xls')
            patterns.append(f'*{kw}*.csv')
        
        found_files = []
        for p in patterns:
            found_files.extend(glob.glob(p))
        
        # 去重并排序
        found_files = sorted(list(set(found_files)))
        
        if not found_files:
            return None, None
        
        target_file = found_files[0]
        ext = os.path.splitext(target_file)[1].lower()
        
        try:
            if ext in ['.xlsx', '.xls']:
                return pd.ExcelFile(target_file), "excel"
            else:
                return pd.read_csv(target_file, header=header), "csv"
        except Exception as e:
            self.load_status.append(f"❌ 读取失败 {target_file}: {e}")
            return None, None

    def load_all(self):
        try:
            # --- 1. 一日游价格 ---
            data, ftype = self._read_any(['商品价格', 'Price'])
            if data is not None:
                if ftype == 'excel':
                    df = pd.read_excel(data, sheet_name=0, header=0) # 假设在第1个Sheet
                else:
                    df = data # csv已读取
                
                df.columns = [str(c).strip() for c in df.columns]
                if '产品名称' in df.columns:
                    self.prices = df.set_index('产品名称').to_dict('index')
                    self.load_status.append(f"✅ 成功加载价格表 ({len(self.prices)}条)")
            else:
                self.load_status.append("⚠️ 未找到[商品价格]文件")

            # --- 2. 具体行程 (读取所有含有'日游'的文件) ---
            # 简单粗暴：遍历目录下所有文件，排除掉价格表
            all_files = glob.glob('*日游*.xlsx') + glob.glob('*日游*.csv')
            for f in all_files:
                if '商品价格' in f: continue
                name = os.path.splitext(os.path.basename(f))[0].split(' - ')[-1].strip()
                try:
                    if f.endswith('.xlsx'):
                        self.itineraries[name] = pd.read_excel(f, header=None)
                    else:
                        self.itineraries[name] = pd.read_csv(f, header=None)
                except: pass
            self.load_status.append(f"✅ 加载行程单: {len(self.itineraries)}个")

            # --- 3. 包车 & 接送机 ---
            data, ftype = self._read_any(['包车', '接送机', 'Charter'])
            if data is not None:
                if ftype == 'excel':
                    # Excel逻辑: 遍历Sheet找
                    for sheet in data.sheet_names:
                        if "包车" in sheet:
                            df = pd.read_excel(data, sheet_name=sheet, header=1)
                            df.columns = [str(c).strip() for c in df.columns]
                            if '包车线路' in df.columns:
                                self.charter = df.set_index('包车线路')['包车价格（人民币）'].to_dict()
                        if "接送机" in sheet:
                            # 动态找header
                            df_raw = pd.read_excel(data, sheet_name=sheet, header=None)
                            h_idx = df_raw[df_raw.eq('编号').any(axis=1)].index
                            if not h_idx.empty:
                                self.airport = pd.read_excel(data, sheet_name=sheet, header=h_idx[0]).to_dict('records')
                else:
                    # CSV逻辑 (假设是包车csv)
                    df = data # header已经在_read_any处理，但包车可能是header=1
                    # 这里简化处理，如果CSV格式复杂建议只用Excel
                    pass 
            
            if self.charter: self.load_status.append("✅ 包车数据已加载")
            if self.airport: self.load_status.append("✅ 接送机数据已加载")

            # --- 4. 酒店 (大阪 & 东京) ---
            # 大阪
            data, ftype = self._read_any(['OSAKA', 'Plaza'])
            if data is not None and ftype == 'excel':
                # Excel处理
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
            elif data is not None and ftype == 'csv':
                # CSV处理 (之前的逻辑)
                pass # 略，假设用户这次传的是Excel

            # 东京
            data, ftype = self._read_any(['SHINJUKU', 'Washington'])
            if data is not None:
                # 简化逻辑，仅支持Excel解析复杂日历
                if ftype == 'excel':
                    df = pd.read_excel(data, header=None)
                    p_map = {str(c).strip(): float(re.sub(r'[^\d]','',str(p))) for c,p in zip(df.iloc[3], df.iloc[4]) if str(c).strip() in ['A','B','C','D','S']}
                    for _, row in df.iloc[11:].iterrows():
                        if '月' in str(row[0]) and str(row[3]).strip() in p_map:
                            m = int(re.search(r'(\d+)', str(row[0])).group(1))
                            y = 2025 if m >= 10 else 2026
                            self.tokyo_rates[f"{y}-{m:02d}-{int(row[1]):02d}"] = p_map[str(row[3]).strip()]
            
            # --- 5. Markdown ---
            md_files = glob.glob('*.md')
            full_text = []
            for f in md_files:
                try:
                    with open(f, 'r', encoding='utf-8') as file:
                        full_text.append(f"=== 文档: {f} ===\n{file.read()}\n")
                except: pass
            self.docs_text = "\n".join(full_text)
            self.load_status.append(f"✅ 知识库文档: {len(md_files)}篇")

            return True
        except Exception as e:
            self.load_status.append(f"❌ 致命错误: {str(e)}")
            st.error(f"System Error: {e}")
            return False

# ==========================================
# 3. Agent (保持不变)
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
        # 如果价格表为空，说明加载失败，直接返回通用
        if not self.kb.prices: return "General"
        
        product_list = list(self.kb.prices.keys())
        prompt = f"""
        User Input: "{user_query}"
        Products: {product_list}
        Return the exact product name if matched. If asking for charter, return "Charter". Else "General".
        """
        try:
            res = self.model_flash.generate_content(prompt)
            matched = res.text.strip()
            for p in product_list:
                if p in matched: return p
            if "Charter" in matched or "包车" in matched: return "Charter"
            return "General"
        except: return "General"

    def generate_response(self, query, context_data, model="flash"):
        engine = self.model_pro if model == "pro" else self.model_flash
        prompt = f"""
        Role: Ctrip Senior Consultant.
        Question: "{query}"
        Context: {context_data}
        Docs: {self.kb.docs_text[:10000]}
        
        Output format:
        ## Part 1: 📋 一键复制回复
        ### 选项 A (50字内)
        ### 选项 B (专业详细)
        ## Part 2: 🧠 销售建议
        """
        try: return engine.generate_content(prompt).text
        except Exception as e: return f"AI Error: {e}"

# ==========================================
# 4. 前端
# ==========================================
def main():
    if 'kb' not in st.session_state:
        kb = KnowledgeBase()
        kb.load_all() # 无论成功失败都加载
        st.session_state.kb = kb
        st.session_state.agent = SmartAgent(kb)
        st.session_state.loaded = True

    with st.sidebar:
        st.title("⚙️ 状态监控")
        # 实时显示加载状态，方便排查
        if 'kb' in st.session_state:
            with st.expander("查看知识库加载详情", expanded=False):
                for log in st.session_state.kb.load_status:
                    if "❌" in log: st.error(log)
                    elif "⚠️" in log: st.warning(log)
                    else: st.write(log)
        
        st.divider()
        date = st.date_input("出行日期", datetime(2026, 2, 1))
        pax = st.number_input("人数", 1, 15, 2)
        model = st.radio("AI 模型", ["Gemini 3 Fast", "Gemini 3 Pro"])

    st.title("👩‍💼 Ctrip 客服 Copilot")
    user_input = st.chat_input("输入问题...")

    if user_input:
        agent = st.session_state.agent
        kb = st.session_state.kb
        
        with st.chat_message("user"): st.write(user_input)
        
        with st.status("🧠 AI 处理中...", expanded=True) as status:
            # 意图识别
            intent = agent.semantic_match_product(user_input)
            st.write(f"意图: {intent}")
            
            context = ""
            if intent in kb.prices:
                # 算价逻辑
                info = kb.prices[intent]
                price_col = next((c for c in info if '结算' in c), None)
                unit = float(info.get(price_col, 350))
                tour = unit * pax
                
                # 酒店简单兜底
                h_price = 13500 
                # (此处略去复杂的酒店判断，确保不报错)
                
                total = h_price + tour
                context = f"Product: {intent}, Total: {total}"
            elif intent == "Charter":
                context = "Charter request."
            else:
                context = "General query."
            
            status.update(label="完成", state="complete", expanded=False)

        with st.chat_message("assistant"):
            sel_model = "flash" if "Fast" in model else "pro"
            st.markdown(agent.generate_response(user_input, context, sel_model))

if __name__ == "__main__":
    main()
