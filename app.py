import streamlit as st
import pandas as pd
import glob
import os
import googlemaps
import google.generativeai as genai
from datetime import datetime
import io
import time

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
# 2. 知识库 (分类加载)
# ==========================================
class KnowledgeBase:
    def __init__(self):
        self.day_tour_text = ""   # 一日游数据
        self.multi_day_text = "" # 多日游数据
        self.files_loaded = []

    def load_all(self):
        # 1. 加载 Excel/CSV -> 归类为一日游/单品
        data_files = glob.glob('*.xlsx') + glob.glob('*.xls') + glob.glob('*.csv')
        for f in data_files:
            try:
                if f.endswith('.csv'): df = pd.read_csv(f, header=None)
                else: df = pd.read_excel(f, header=None)
                df = df.dropna(how='all')
                content = f"\n\n=== 单日产品表: {os.path.basename(f)} ===\n{df.to_markdown(index=False)}"
                self.day_tour_text += content
                self.files_loaded.append(f)
            except: pass
        
        # 2. 加载 Markdown -> 根据文件名归类
        md_files = glob.glob('*.md')
        for f in md_files:
            try:
                content = open(f, 'r', encoding='utf-8').read()
                file_content = f"\n\n=== 业务文档: {os.path.basename(f)} ===\n{content}"
                if "多日游" in f:
                    self.multi_day_text += file_content
                else:
                    # 通用文档（如预订须知）两边都要用
                    self.day_tour_text += file_content
                    self.multi_day_text += file_content
                self.files_loaded.append(f)
            except: pass

# ==========================================
# 3. AI 核心 (Gemini 3 Native)
# ==========================================
def get_ai_reply(query, history, kb, model_choice):
    if not AI_KEY: return "❌ 错误：未配置 API Key"
    
    # [核心修正] 严格使用 Gemini 3 Preview
    model_id = "gemini-3-flash-preview" if "Flash" in model_choice else "gemini-3-pro-preview"
    
    try:
        model = genai.GenerativeModel(model_id)
    except Exception as e:
        return f"模型初始化失败 ({model_id}): {e}"
    
    # 构建历史记录字符串 (最近 5 轮)
    history_str = ""
    for msg in history[-5:]: 
        history_str += f"{msg['role']}: {msg['content']}\n"

    # 核心系统指令 (System Prompt)
    system_prompt = f"""
    Role: You are a Senior Travel Consultant at Ctrip (携程).
    Goal: Convert customers by recommending products from the database accurately.

    [MEMORY / CONTEXT]
    {history_str}

    [KNOWLEDGE BASE - DAY TOURS & PRICES]
    {kb.day_tour_text[:20000]}
    
    [KNOWLEDGE BASE - MULTI-DAY PACKAGES]
    {kb.multi_day_text[:20000]}

    [CRITICAL BUSINESS LOGIC - READ CAREFULLY]
    1. **Duration Check**:
       - If user asks for **> 4 Days**: Search ONLY in [MULTI-DAY PACKAGES].
       - If user asks for **<= 3 Days**: Search ONLY in [DAY TOURS].
    
    2. **Atomic Product Rule (Strict)**:
       - Products (Routes/IDs) are FIXED. You CANNOT mix stops from Product A into Product B.
       - You CANNOT delete stops from a fixed product.
       - You **CAN** recommend sequence: "Day 1 take Product A, Day 2 take Product B".
    
    3. **Customization Trigger (The "Charter" Trap)**:
       - If user wants to change stops, add/remove attractions, or mentions specific spots not in any route:
       - **STOP recommending standard products.**
       - **REPLY EXACTLY**: "您的需求涉及个性化定制，标准线路无法调整。建议您使用上方的【包车规划】功能进行定制。"
    
    4. **Scope Boundary**:
       - Only answer what is in the docs. Do not make up prices.
       - If unsure, refer to "Product Manager".

    [USER QUERY]: "{query}"

    [OUTPUT FORMAT]:
    Please separate your answer into two parts:
    
    <<<REPLY_A>>>
    (Quick Reply: < 60 words. Conclusion + Price + Disclaimer)
    <<<END_A>>>
    
    <<<REPLY_B>>>
    (Professional Reply: 
     - If > 4 days: Recommend Package ID.
     - If <= 3 days: Recommend Route Name (or Sequence of Routes).
     - If Custom: Guide to Charter Tab.
     - Always distinguish Currency (RMB/JPY/HKD).)
    <<<END_B>>>
    """
    
    try:
        response = model.generate_content(system_prompt)
        return response.text
    except Exception as e:
        return f"AI 接口报错 ({model_id}): {str(e)}"

# ==========================================
# 4. 地图计算工具
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
# 5. 前端主程序
# ==========================================
def main():
    # 初始化 Session
    if 'kb' not in st.session_state:
        kb = KnowledgeBase()
        kb.load_all()
        st.session_state.kb = kb
    
    if 'messages' not in st.session_state:
        st.session_state.messages = []

    # --- 侧边栏：控制台 ---
    with st.sidebar:
        st.title("⚙️ 接待控制台")
        
        # 1. 记忆清除功能
        if st.button("🗑️ 接待下一位客人 (清空记录)", type="primary"):
            st.session_state.messages = []
            st.rerun()
            
        st.divider()
        st.write(f"📚 知识库: {len(st.session_state.kb.files_loaded)} 文件")
        
        # [核心修正] 模型选择器
        model_choice = st.radio("AI 模型内核", ["Gemini 3 Flash", "Gemini 3 Pro"])
        # 显示当前使用的真实 ID 以便核对
        current_id = "gemini-3-flash-preview" if "Flash" in model_choice else "gemini-3-pro-preview"
        st.caption(f"🚀 Current ID: `{current_id}`")

    st.title("👩‍💼 Ctrip 客服 Copilot")

    # --- 双 Tab 架构 ---
    tab_chat, tab_plan = st.tabs(["💬 智能问答 (标准品)", "🗺️ 包车规划 (定制)"])

    # === TAB 1: 问答 ===
    with tab_chat:
        # 显示历史对话
        for msg in st.session_state.messages:
            if msg["type"] != "hidden":
                with st.chat_message(msg["role"]):
                    if msg["type"] == "code": st.code(msg["content"], language=None)
                    else: st.write(msg["content"])

        # 输入框
        user_input = st.chat_input("输入客人需求... (如: 想要一个5天的关西行程)")

        if user_input:
            # 记录用户输入
            st.session_state.messages.append({"role": "user", "type": "text", "content": user_input})
            with st.chat_message("user"): st.write(user_input)

            # 调用 AI
            with st.chat_message("assistant"):
                with st.spinner(f"Gemini 3 ({'Pro' if 'Pro' in model_choice else 'Flash'}) 正在思考..."):
                    # 传入历史记录
                    raw_res = get_ai_reply(user_input, st.session_state.messages, st.session_state.kb, model_choice)
                    
                    # 解析 A/B
                    try:
                        reply_a = raw_res.split("<<<REPLY_A>>>")[1].split("<<<END_A>>>")[0].strip()
                        reply_b = raw_res.split("<<<REPLY_B>>>")[1].split("<<<END_B>>>")[0].strip()
                    except:
                        reply_a = raw_res
                        reply_b = None

                    st.subheader("📋 极简回复")
                    st.code(reply_a, language=None)
                    st.session_state.messages.append({"role": "assistant", "type": "code", "content": reply_a})

                    if reply_b:
                        st.subheader("💼 专业回复")
                        st.code(reply_b, language=None)
                        # 专业版存入历史但隐藏显示（避免重复）
                        st.session_state.messages.append({"role": "assistant", "type": "hidden", "content": reply_b})

    # === TAB 2: 包车 ===
    with tab_plan:
        c1, c2 = st.columns([1, 2])
        with c1:
            st.info("💡 提示：当 AI 在对话中建议【包车】时，请使用此面板。")
            with st.form("charter_form"):
                start = st.text_input("起点", "大阪市区")
                end = st.text_input("终点", "大阪市区")
                stops = st.text_area("途经景点", "清水寺\n奈良公园")
                price_base = st.number_input("预估车费 (查表得)", 2500)
                if st.form_submit_button("🚀 生成定制方案"):
                    wps = [s.strip() for s in stops.split('\n') if s.strip()]
                    res = calc_map_route(start, end, wps)
                    if res['valid']:
                        st.session_state.plan_res = res
                        st.session_state.plan_price = price_base + 1000
                    else:
                        st.error(res['msg'])

        with c2:
            if 'plan_res' in st.session_state:
                res = st.session_state.plan_res
                total = st.session_state.plan_price
                
                st.image(res['img'], caption="行程预览", use_container_width=True)
                
                msg = f"""【定制包车方案】
📍 路线：{start} -> ... -> {end}
📏 统计：{res['dist']}km | {res['dur']}小时
💰 报价：¥{total} (含车+导，不含超时)
⚠️ 评估：{'✅ 行程合理' if res['dist']<=300 else '⚠️ 距离过长，建议调整'}"""
                
                st.code(msg, language=None)

if __name__ == "__main__":
    main()
