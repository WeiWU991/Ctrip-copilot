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
# 2. 知识库加载器 (分类+无限上下文)
# ==========================================
class KnowledgeBase:
    def __init__(self):
        self.day_tour_text = ""   # 一日游
        self.multi_day_text = "" # 多日游
        self.hotel_text = ""     # 酒店 (新增)
        self.files_loaded = []

    def load_all(self):
        # 1. 表格数据 (Excel/CSV)
        data_files = glob.glob('*.xlsx') + glob.glob('*.xls') + glob.glob('*.csv')
        for f in data_files:
            try:
                # 读取并转换为 Markdown 表格
                if f.endswith('.csv'): df = pd.read_csv(f, header=None)
                else: df = pd.read_excel(f, header=None)
                df = df.dropna(how='all')
                
                # 构建内容块
                fname = os.path.basename(f).upper()
                content = f"\n\n=== 数据表: {fname} ===\n{df.to_markdown(index=False)}"
                
                # 智能归类
                if any(k in fname for k in ["HOTEL", "OSAKA", "SHINJUKU", "酒店", "PLAZA", "WASHINGTON"]):
                    self.hotel_text += content
                elif "多日游" in fname:
                    self.multi_day_text += content
                else:
                    self.day_tour_text += content
                
                self.files_loaded.append(f)
            except: pass
        
        # 2. 文档数据 (Markdown)
        md_files = glob.glob('*.md')
        for f in md_files:
            try:
                content = open(f, 'r', encoding='utf-8').read()
                fname = os.path.basename(f)
                file_content = f"\n\n=== 业务文档: {fname} ===\n{content}"
                
                if "多日游" in f:
                    self.multi_day_text += file_content
                else:
                    # 通用文档全覆盖
                    self.day_tour_text += file_content
                    self.multi_day_text += file_content
                    self.hotel_text += file_content # 酒店也需要看通用规则
                self.files_loaded.append(f)
            except: pass

# ==========================================
# 3. AI 核心 (Prompt 注入酒店逻辑)
# ==========================================
def get_ai_reply(query, history, kb, model_choice):
    if not AI_KEY: return "❌ 错误：未配置 API Key"
    
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

    # --- 核心 Prompt ---
    # 注意：这里不再使用 [:20000]，而是全量喂入
    system_prompt = f"""
    Role: Senior Travel Consultant at Ctrip.
    
    [MEMORY]:
    {history_context}

    [KNOWLEDGE - HOTELS & RATES]:
    {kb.hotel_text}

    [KNOWLEDGE - DAY TOURS]:
    {kb.day_tour_text}
    
    [KNOWLEDGE - MULTI-DAY]:
    {kb.multi_day_text}

    [CRITICAL RULES]:
    1. **Hotel Queries**: 
       - Check [KNOWLEDGE - HOTELS] first.
       - If tables show "Rank A/B/C" dates, try to match the user's date to a Rank, then find the price.
       - Quote the specific currency (JPY/RMB) exactly as in the table.
       - Disclaimer: "房态和价格实时变动，请以二次确认为准".
    2. **Day Tour Inclusions**: 
       - Spots mentioned = Ticket Included (unless 'Self-pay').
       - Lunch mentioned = Included. 'Lunch Self-pay' = Excluded.
    3. **Charter Logic**: 
       - Base Car Price (from table) + **1000 RMB Guide Fee**.
       - Limit: 10 Hours / 300 KM.
    4. **Scope**: If answer not found, say "请联系产品经理".

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
     1. 识别意图: 酒店/一日游/包车?
     2. 数据来源: 读了哪个文件?
     3. 酒店逻辑: 如何匹配日期和等级? (如适用)
     4. 价格计算过程?)
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

    # === TAB 1: 问答 ===
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

        user_input = st.chat_input("输入客人需求... (例: 大阪广场酒店3月1号多少钱？)")

        if user_input:
            st.session_state.messages.append({"role": "user", "content": user_input})
            with st.chat_message("user"): st.write(user_input)

            with st.chat_message("assistant"):
                with st.spinner("AI 正在查阅价格表..."):
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
💰 报价：¥{total} (含车+导)
⚠️ 评估：{'✅ 行程合理' if res['dist']<=300 else '⚠️ 距离过长，建议调整'}"""
                st.code(msg, language=None)

if __name__ == "__main__":
    main()
