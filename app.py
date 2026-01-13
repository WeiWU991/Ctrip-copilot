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
# 2. 知识库加载器
# ==========================================
class KnowledgeBase:
    def __init__(self):
        self.day_tour_text = ""
        self.multi_day_text = ""
        self.files_loaded = []

    def load_all(self):
        # 1. 表格数据 -> 文本
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
        
        # 2. 文档数据 -> 文本
        md_files = glob.glob('*.md')
        for f in md_files:
            try:
                content = open(f, 'r', encoding='utf-8').read()
                file_content = f"\n\n=== 业务文档: {os.path.basename(f)} ===\n{content}"
                if "多日游" in f:
                    self.multi_day_text += file_content
                else:
                    self.day_tour_text += file_content
                    self.multi_day_text += file_content
                self.files_loaded.append(f)
            except: pass

# ==========================================
# 3. AI 核心 (强制包车逻辑)
# ==========================================
def get_ai_reply(query, history, kb, model_choice):
    if not AI_KEY: return "❌ 错误：未配置 API Key"
    
    # 锁定模型 ID
    model_id = "gemini-3-flash-preview" if "Flash" in model_choice else "gemini-3-pro-preview"
    
    try:
        model = genai.GenerativeModel(model_id)
    except Exception as e:
        return f"模型初始化失败: {e}"
    
    # 历史上下文
    history_context = ""
    for msg in history[-5:]: 
        if msg['role'] == 'user':
            history_context += f"User: {msg['content']}\n"
        elif msg['role'] == 'assistant':
            history_context += f"AI: {msg.get('short', '')}\n"

    # --- 核心 Prompt (注入包车数学公式) ---
    system_prompt = f"""
    Role: Senior Travel Consultant at Ctrip.
    
    [MEMORY]:
    {history_context}

    [KNOWLEDGE - DAY TOURS & CHARTER]:
    {kb.day_tour_text[:20000]}
    
    [KNOWLEDGE - MULTI-DAY]:
    {kb.multi_day_text[:20000]}

    [CRITICAL BUSINESS RULES]:
    1. **Tickets**: Mentioned spots = Included (unless marked 'Self-pay').
    2. **Meals**: Mentioned 'Lunch' = Included. 'Self-pay' = Excluded.
    
    [CHARTER PRICING FORMULA (Must Follow)]:
    If user asks about "Charter" (包车) or "Custom" (定制) price:
    1. **Base Price**: Find the car price in the table (e.g., '10座海狮' in '包车.xlsx').
    2. **Guide Fee**: ALWAYS ADD fixed **1000 RMB** for the guide.
    3. **Total Price** = Base Price + 1000 RMB.
    4. **Service Limit**: Explicitly state "Includes 10 hours / 300 km per day".
    5. **Driver**: Note that "Driver price is included in Base Price".

    [LOGIC FLOW]:
    1. **Duration**: >4 Days -> Check Multi-Day Docs. <=3 Days -> Check Day Tour Tables.
    2. **Customization**: If user changes stops -> Switch to "Charter Mode" -> Use the Formula above.
    3. **Validation**: If user mentions specific charter routes, recommend checking the "Charter Plan Tab" for distance validation.

    [USER QUERY]: "{query}"

    [OUTPUT FORMAT - STRICT]:
    <<<REPLY_A>>>
    (Quick Reply: Conclusion + Calculated Price (Car+Guide) + Link. < 60 words)
    <<<END_A>>>
    
    <<<REPLY_B>>>
    (Professional Reply: Breakdown (Car + Guide 1000), Service Limits (10h/300km), Upsell)
    <<<END_B>>>

    <<<THOUGHTS>>>
    (中文诊断:
     1. 意图: 一日游 vs 包车?
     2. 价格计算: 如果是包车，是否执行了 (车价 + 1000) 的公式？
     3. 限制条款: 是否提到了 10h/300km？)
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
# 5. 前端主程序
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
        st.title("⚙️ 接待控制台")
        if st.button("🗑️ 接待下一位客人 (清空)", type="primary"):
            st.session_state.messages = []
            st.rerun()
        st.divider()
        st.write(f"📚 知识库: {len(st.session_state.kb.files_loaded)} 文件")
        
        model_choice = st.radio("AI 模型", ["Gemini 3 Flash", "Gemini 3 Pro"])

    st.title("👩‍💼 Ctrip 客服 Copilot")

    tab_chat, tab_plan = st.tabs(["💬 智能问答", "🗺️ 包车规划"])

    # === TAB 1: 问答 (含历史回溯) ===
    with tab_chat:
        for msg in st.session_state.messages:
            if msg['role'] == 'user':
                with st.chat_message("user"): st.write(msg['content'])
            elif msg['role'] == 'assistant':
                with st.chat_message("assistant"):
                    st.code(msg['short'], language=None)
                    with st.expander("🔽 查看详细回复 & AI 诊断过程"):
                        st.markdown("**💼 专业话术 (Reply B):**")
                        st.code(msg['long'], language=None)
                        st.divider()
                        st.markdown("**🧠 AI 诊断思考 (Diagnostics):**")
                        st.info(msg['thoughts'])

        user_input = st.chat_input("输入客人需求... (例: 大阪到京都包车多少钱？)")

        if user_input:
            st.session_state.messages.append({"role": "user", "content": user_input})
            with st.chat_message("user"): st.write(user_input)

            with st.chat_message("assistant"):
                with st.spinner("AI 正在计算报价 (车费+导游)..."):
                    raw_res = get_ai_reply(user_input, st.session_state.messages, st.session_state.kb, model_choice)
                    
                    try:
                        reply_a = re.search(r'<<<REPLY_A>>>([\s\S]*?)<<<END_A>>>', raw_res).group(1).strip()
                    except: reply_a = raw_res
                    
                    try:
                        reply_b = re.search(r'<<<REPLY_B>>>([\s\S]*?)<<<END_B>>>', raw_res).group(1).strip()
                    except: reply_b = "未生成"

                    try:
                        thoughts = re.search(r'<<<THOUGHTS>>>([\s\S]*?)<<<END_THOUGHTS>>>', raw_res).group(1).strip()
                    except: thoughts = "无记录"

                    st.code(reply_a, language=None)
                    
                    with st.expander("🔽 查看详细回复 & AI 诊断过程", expanded=True):
                        st.markdown("**💼 专业话术 (Reply B):**")
                        st.code(reply_b, language=None)
                        st.divider()
                        st.markdown("**🧠 AI 诊断思考 (Diagnostics):**")
                        st.info(thoughts)

                    st.session_state.messages.append({
                        "role": "assistant",
                        "short": reply_a,
                        "long": reply_b,
                        "thoughts": thoughts
                    })

    # === TAB 2: 包车规划 ===
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
                        st.session_state.plan_price = price_base + 1000 # 保持逻辑一致
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
💰 报价：¥{total} (含车+导)
📝 标准：10小时/300公里
⚠️ 评估：{'✅ 行程合理' if res['dist']<=300 else '⚠️ 距离过长，建议调整'}"""
                st.code(msg, language=None)

if __name__ == "__main__":
    main()
