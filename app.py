import streamlit as st
import pandas as pd
import glob
import os
import googlemaps
import google.generativeai as genai
from datetime import datetime
import io
import re

# ==========================================
# 1. 基础配置
# ==========================================
st.set_page_config(page_title="Ctrip CS Copilot", page_icon="👩‍💼", layout="wide")

MAPS_KEY = st.secrets.get("GOOGLE_MAPS_API_KEY", "")
AI_KEY = st.secrets.get("GOOGLE_API_KEY", "")

# 初始化
gmaps = None
if MAPS_KEY:
    try: gmaps = googlemaps.Client(key=MAPS_KEY)
    except: pass

if AI_KEY:
    try: genai.configure(api_key=AI_KEY)
    except: pass

# ==========================================
# 2. 知识库加载器 (优先读取 Markdown)
# ==========================================
class KnowledgeBase:
    def __init__(self):
        self.knowledge_text = ""
        self.file_status = []

    def load_all(self):
        self.knowledge_text = ""
        self.file_status = []
        
        # 1. 优先读取 Markdown (酒店、多日游、规则)
        md_files = glob.glob('*.md')
        for f in md_files:
            try:
                content = open(f, 'r', encoding='utf-8').read()
                self.knowledge_text += f"\n\n=== 核心知识库: {os.path.basename(f)} ===\n{content}"
                self.file_status.append(f"✅ 已加载文档: {f}")
            except: 
                self.file_status.append(f"❌ 读取失败: {f}")

        # 2. 辅助读取简单 Excel (一日游/包车列表)
        # 只要不是酒店那种复杂的 Excel，都读进来
        data_files = glob.glob('*.xlsx') + glob.glob('*.csv')
        for f in data_files:
            if "HOTEL" in f.upper() or "OSAKA" in f.upper() or "SHINJUKU" in f.upper():
                self.file_status.append(f"⚠️ 跳过复杂Excel: {f} (请使用 Markdown 维护酒店价格)")
                continue
                
            try:
                if f.endswith('.csv'): df = pd.read_csv(f, header=None)
                else: df = pd.read_excel(f, header=None)
                df = df.dropna(how='all')
                self.knowledge_text += f"\n\n=== 价格表: {os.path.basename(f)} ===\n{df.to_markdown(index=False)}"
                self.file_status.append(f"✅ 已加载表格: {f}")
            except: pass

# ==========================================
# 3. AI 核心
# ==========================================
def get_ai_reply(query, history, kb_text, model_choice):
    if not AI_KEY: return "❌ 错误：未配置 API Key"
    
    model_id = "gemini-3-flash-preview" if "Flash" in model_choice else "gemini-3-pro-preview"
    
    try:
        model = genai.GenerativeModel(model_id)
    except Exception as e:
        return f"模型初始化失败: {e}"
    
    # 历史上下文
    history_str = ""
    for msg in history[-5:]: 
        if msg['role'] == 'user': history_str += f"User: {msg['content']}\n"
        elif msg['role'] == 'assistant': history_str += f"AI: {msg.get('short', '')}\n"

    # --- System Prompt ---
    system_prompt = f"""
    Role: Senior Travel Consultant at Ctrip.
    
    [MEMORY]:
    {history_str}

    [KNOWLEDGE BASE]:
    {kb_text}

    [CRITICAL BUSINESS RULES]:
    1. **Hotel Pricing**: 
       - Look for "Hotel" or "酒店" in the Knowledge Base. 
       - Quote the price in **JPY (日币)** explicitly. 
       - Disclaimer: "房态实时变动，需二次确认".
    2. **Charter Pricing**:
       - Formula: Base Car Price (RMB) + **1000 RMB Guide Fee**.
       - Limit: 10 Hours / 300 KM.
    3. **Day Tour**:
       - Quote price in RMB. 
       - Disclaimer: "价格已含车费，余位需后台确认".
    4. **Scope**: Only answer based on provided text.

    [USER QUERY]: "{query}"

    [OUTPUT FORMAT]:
    <<<REPLY_A>>>
    (Quick Reply: < 60 words)
    <<<END_A>>>
    
    <<<REPLY_B>>>
    (Professional Reply)
    <<<END_B>>>

    <<<THOUGHTS>>>
    (中文诊断: 
     1. 识别意图
     2. 引用了哪个文档? 
     3. 价格计算逻辑 (特别是包车/酒店)?
    )
    <<<END_THOUGHTS>>>
    """
    
    try:
        return model.generate_content(system_prompt).text
    except Exception as e:
        return f"AI Error: {str(e)}"

# ==========================================
# 4. 地图工具
# ==========================================
def calc_map_route(start, end, stops):
    if not gmaps: return {"valid": False, "msg": "API未连接"}
    try:
        now = datetime.now()
        res = gmaps.directions(start, end, waypoints=stops, mode="driving", departure_time=now)
        if not res: return {"valid": False, "msg": "无路线"}
        route = res[0]
        dist = round(sum(l['distance']['value'] for l in route['legs'])/1000, 1)
        dur = round(sum(l['duration']['value'] for l in route['legs'])/3600, 1)
        poly = route['overview_polyline']['points']
        markers = [{'color':'green','label':'S','locations':[start]},{'color':'red','label':'E','locations':[end]}]
        if stops: markers.append({'color':'blue','label':'P','locations':stops})
        img_raw = gmaps.static_map(size=(800,400), path=f"enc:{poly}", markers=markers, format="png")
        return {"valid":True, "dist":dist, "dur":dur, "img":io.BytesIO(b"".join(img_raw))}
    except Exception as e: return {"valid": False, "msg": str(e)}

# ==========================================
# 5. 前端界面
# ==========================================
def main():
    if 'kb' not in st.session_state:
        kb = KnowledgeBase()
        kb.load_all()
        st.session_state.kb = kb
    
    if 'messages' not in st.session_state:
        st.session_state.messages = []

    # --- 侧边栏 ---
    with st.sidebar:
        st.title("⚙️ 控制台")
        
        # 1. 知识库透视 (Debug)
        with st.expander("📚 知识库加载状态", expanded=True):
            for status in st.session_state.kb.file_status:
                if "✅" in status: st.success(status)
                elif "⚠️" in status: st.warning(status)
                else: st.error(status)
        
        # 2. 这里的文本框可以让您看到 AI 到底读到了什么 (非常有用！)
        with st.expander("🧠 AI 大脑透视 (Raw Data)"):
            st.text_area("AI 读到的所有内容:", st.session_state.kb.knowledge_text, height=200)

        st.divider()
        if st.button("🗑️ 清空对话", type="primary"):
            st.session_state.messages = []
            st.rerun()
        
        model_choice = st.radio("AI 模型", ["Gemini 3 Flash", "Gemini 3 Pro"])

    st.title("👩‍💼 Ctrip 客服 Copilot")

    tab_chat, tab_plan = st.tabs(["💬 智能问答", "🗺️ 包车规划"])

    # === TAB 1: 问答 ===
    with tab_chat:
        for msg in st.session_state.messages:
            if msg['role'] == 'user':
                with st.chat_message("user"): st.write(msg['content'])
            elif msg['role'] == 'assistant':
                with st.chat_message("assistant"):
                    st.code(msg['short'], language=None)
                    with st.expander("🔽 查看详情 & 思考"):
                        st.code(msg['long'], language=None)
                        st.info(msg['thoughts'])

        user_input = st.chat_input("输入... (例: 大阪广场酒店2月1号多少钱？)")

        if user_input:
            st.session_state.messages.append({"role": "user", "content": user_input})
            with st.chat_message("user"): st.write(user_input)

            with st.chat_message("assistant"):
                with st.spinner("AI 正在思考..."):
                    raw_res = get_ai_reply(user_input, st.session_state.messages, st.session_state.kb.knowledge_text, model_choice)
                    
                    try: reply_a = re.search(r'<<<REPLY_A>>>([\s\S]*?)<<<END_A>>>', raw_res).group(1).strip()
                    except: reply_a = raw_res
                    try: reply_b = re.search(r'<<<REPLY_B>>>([\s\S]*?)<<<END_B>>>', raw_res).group(1).strip()
                    except: reply_b = "无"
                    try: thoughts = re.search(r'<<<THOUGHTS>>>([\s\S]*?)<<<END_THOUGHTS>>>', raw_res).group(1).strip()
                    except: thoughts = "无"

                    st.code(reply_a, language=None)
                    with st.expander("🔽 查看详情 & 思考", expanded=True):
                        st.code(reply_b, language=None)
                        st.info(thoughts)
                    
                    st.session_state.messages.append({"role": "assistant", "short": reply_a, "long": reply_b, "thoughts": thoughts})

    # === TAB 2: 包车规划 ===
    with tab_plan:
        c1, c2 = st.columns([1, 2])
        with c1:
            with st.form("charter"):
                start = st.text_input("起点", "大阪")
                end = st.text_input("终点", "大阪")
                stops = st.text_area("途经", "清水寺")
                base = st.number_input("车费", 2500)
                if st.form_submit_button("🚀 计算"):
                    res = calc_map_route(start, end, [s for s in stops.split('\n') if s])
                    if res['valid']:
                        st.session_state.plan = res
                        st.session_state.price = base + 1000
        with c2:
            if 'plan' in st.session_state:
                res = st.session_state.plan
                st.image(res['img'], use_container_width=True)
                st.code(f"【包车方案】\n路线: {start}->...->{end}\n距离: {res['dist']}km | {res['dur']}h\n报价: ¥{st.session_state.price} (含导游)\n限制: 10h/300km", language=None)

if __name__ == "__main__":
    main()
