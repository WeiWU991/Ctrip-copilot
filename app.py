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
# 1. 配置
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

st.set_page_config(page_title="Ctrip CS Copilot", page_icon="🔍", layout="wide")

# ==========================================
# 2. 诊断级数据加载器
# ==========================================
class KnowledgeBase:
    def __init__(self):
        self.prices = {}
        self.raw_dfs = {} # 存储原始数据用于诊断
        self.load_logs = []
        self.docs_text = ""
        self.multiday_text = ""

    def _find_header_and_data(self, df):
        """
        超级暴力的表头查找逻辑：
        只要一行里同时包含 '产品' (或'线路') 和 '价' (或'结算')，这一行就是表头！
        """
        for idx, row in df.head(20).iterrows(): # 扫描前20行
            row_str = " ".join([str(val) for val in row.values if pd.notna(val)])
            
            # 判定条件：同时包含产品标识和价格标识
            has_name = any(k in row_str for k in ['产品', '线路', '名称', 'Product', 'Route'])
            has_price = any(k in row_str for k in ['价', '结算', '成人', 'Price', 'RMB', 'Cost'])
            
            if has_name and has_price:
                # 找到表头了！
                df.columns = df.iloc[idx] # 这一行是列名
                df = df.iloc[idx+1:]      # 下面是数据
                df = df.reset_index(drop=True)
                return df, idx, list(df.columns)
        
        return None, -1, []

    def load_all_diagnostic(self):
        self.load_logs = []
        xlsx_files = glob.glob('*.xlsx') + glob.glob('*.xls')
        
        for f in xlsx_files:
            try:
                xls = pd.ExcelFile(f)
                for sheet in xls.sheet_names:
                    # 读取原始数据（不带Header）
                    df_raw = pd.read_excel(xls, sheet_name=sheet, header=None)
                    self.raw_dfs[f"{os.path.basename(f)} | {sheet}"] = df_raw
                    
                    # 尝试解析价格
                    if "商品价格" in sheet or "Price" in sheet:
                        df_clean, header_row, cols = self._find_header_and_data(df_raw.copy())
                        
                        if df_clean is not None:
                            # 尝试找产品名列
                            name_col = next((c for c in cols if "产品" in str(c) or "线路" in str(c) or "名称" in str(c)), None)
                            # 尝试找价格列
                            price_col = next((c for c in cols if "结算" in str(c) or "价格" in str(c) or "成人" in str(c)), None)
                            
                            if name_col and price_col:
                                # 清洗数据
                                df_clean = df_clean.dropna(subset=[name_col]) # 去掉空行
                                records = df_clean.set_index(name_col)[price_col].to_dict()
                                self.prices.update(records)
                                self.load_logs.append(f"✅ 成功解析: {sheet} (表头在第{header_row}行) -> 抓取到 {len(records)} 个产品")
                            else:
                                self.load_logs.append(f"⚠️ {sheet}: 找到了表头行，但没认出列名。列名是: {cols}")
                        else:
                            self.load_logs.append(f"❌ {sheet}: 扫描了前20行，没找到包含'产品'和'价格'的表头行。")
            
            except Exception as e:
                self.load_logs.append(f"❌ 文件读取错误 {f}: {e}")

        # 加载文档
        for f in glob.glob('*.md'):
            try:
                c = open(f, 'r', encoding='utf-8').read()
                if "多日游" in f: self.multiday_text += c
                else: self.docs_text += c
            except: pass

# ==========================================
# 3. Agent
# ==========================================
class SmartAgent:
    def __init__(self, kb):
        self.kb = kb
        try:
            self.model = genai.GenerativeModel("gemini-1.5-pro") # 使用最稳的 Pro
        except:
            self.model = genai.GenerativeModel("gemini-1.5-flash")

    def match(self, query):
        if not self.kb.prices: return "General"
        prods = list(self.kb.prices.keys())
        prompt = f"Query: '{query}'. Products: {prods}. Return matched product name exactly. If Charter, return 'Charter'. Else 'General'."
        try: return self.model.generate_content(prompt).text.strip()
        except: return "General"

    def reply(self, query, intent, context):
        if not AI_KEY: return "Error: No API Key"
        prompt = f"""
        Role: Ctrip Expert. Query: "{query}". Intent: {intent}. Context: {context}.
        Docs: {self.kb.docs_text[:5000]}
        OUTPUT FORMAT:
        <<<REPLY_A>>> (Short) <<<END_A>>>
        <<<REPLY_B>>> (Pro) <<<END_B>>>
        """
        try: return self.model.generate_content(prompt).text
        except: return "AI Error"

# ==========================================
# 4. 前端 (含诊断面板)
# ==========================================
def main():
    if 'kb' not in st.session_state:
        kb = KnowledgeBase()
        kb.load_all_diagnostic()
        st.session_state.kb = kb
        st.session_state.agent = SmartAgent(kb)

    st.title("👩‍💼 Ctrip Copilot (v9.0 诊断版)")

    tab_chat, tab_debug = st.tabs(["💬 客服对话助手", "🛠️ 数据源诊断面板"])

    # --- 诊断面板 (核心功能) ---
    with tab_debug:
        st.header("Excel 数据透视")
        st.info("如果一日游没加载出来，请在这里查看 Excel 到底长什么样。")
        
        # 1. 显示加载日志
        with st.expander("查看加载日志 (Logs)", expanded=True):
            for log in st.session_state.kb.load_logs:
                if "✅" in log: st.success(log)
                elif "❌" in log: st.error(log)
                else: st.warning(log)

        # 2. 原始数据查看器
        st.subheader("原始 Excel 内容查看器")
        kb = st.session_state.kb
        if kb.raw_dfs:
            sheet_choice = st.selectbox("选择要查看的 Sheet:", list(kb.raw_dfs.keys()))
            
            df_view = kb.raw_dfs[sheet_choice]
            st.write(f"正在查看: **{sheet_choice}** (前 50 行)")
            st.dataframe(df_view.head(50), use_container_width=True)
            
            st.markdown("---")
            st.write("**🤖 AI 的分析：**")
            st.write("请检查上面的表格：")
            st.write("1. **表头在哪一行？** (代码默认在前20行寻找包含'产品'和'价格'的行)")
            st.write("2. **列名对不对？** (代码需要列名包含 '产品/线路' 和 '结算/价格/成人')")
        else:
            st.error("没有找到任何 Excel 文件！请检查 GitHub 上传。")

    # --- 对话助手 ---
    with tab_chat:
        user_input = st.chat_input("输入问题...")
        if user_input:
            kb = st.session_state.kb
            agent = st.session_state.agent
            
            with st.chat_message("user"): st.write(user_input)
            
            # 简单逻辑展示
            raw_intent = agent.match(user_input)
            context = ""
            
            # 意图匹配
            matched_prod = None
            for p in kb.prices:
                if p in raw_intent: matched_prod = p; break
            
            if matched_prod:
                price = kb.prices[matched_prod]
                context = f"Product: {matched_prod}, Price: {price}"
                st.success(f"✅ 成功匹配价格: {matched_prod} -> {price}")
            elif "Charter" in raw_intent:
                context = "Charter"
            else:
                context = "General"
                if not kb.prices: st.error("⚠️ 警告：价格库为空！请去诊断面板排查。")

            with st.chat_message("assistant"):
                resp = agent.reply(user_input, raw_intent, context)
                try: 
                    final = re.search(r'<<<REPLY_A>>>([\s\S]*?)<<<END_A>>>', resp).group(1)
                    st.code(final, language=None)
                except: 
                    st.code(resp, language=None)

if __name__ == "__main__":
    main()
