import streamlit as st
import pandas as pd
import glob
import os
import googlemaps
import google.generativeai as genai
from datetime import datetime, timedelta
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
        self.knowledge_text = ""
        self.file_status = []

    def load_all(self):
        self.knowledge_text = ""
        self.file_status = []
        
        # 1. Markdown (核心)
        md_files = glob.glob('*.md')
        for f in md_files:
            try:
                content = open(f, 'r', encoding='utf-8').read()
                self.knowledge_text += f"\n\n=== 核心知识库: {os.path.basename(f)} ===\n{content}"
                self.file_status.append(f"✅ 已加载文档: {f}")
            except: 
                self.file_status.append(f"❌ 读取失败: {f}")

        # 2. Simple Excel (列表)
        data_files = glob.glob('*.xlsx') + glob.glob('*.csv')
        for f in data_files:
            # 跳过复杂的酒店Excel，防止干扰
            if any(k in f.upper() for k in ["HOTEL", "OSAKA", "SHINJUKU", "PLAZA", "WASHINGTON"]):
                self.file_status.append(f"⚠️ 跳过复杂Excel: {f} (请用 Markdown 维护)")
                continue
                
            try:
                if f.endswith('.csv'): df = pd.read_csv(f, header=None)
                else: df = pd.read_excel(f, header=None)
                df = df.dropna(how='all')
                self.knowledge_text += f"\n\n=== 价格表: {os.path.basename(f)} ===\n{df.to_markdown(index=False)}"
                self.file_status.append(f"✅ 已加载表格: {f}")
            except: pass

# ==========================================
# 3. AI 核心 (对话 & 规划)
# ==========================================

# --- A. 智能对话 AI ---
def get_ai_reply(query, history, kb_text, model_choice):
    if not AI_KEY: return "❌ 错误：未配置 API Key"
    model_id = "gemini-3-flash-preview" if "Flash" in model_choice else "gemini-3-pro-preview"
    try: model = genai.GenerativeModel(model_id)
    except Exception as e: return f"模型初始化失败: {e}"
    
    history_str = ""
    for msg in history[-5:]: 
        if msg['role'] == 'user': history_str += f"User: {msg['content']}\n"
        elif msg['role'] == 'assistant': history_str += f"AI: {msg.get('short', '')}\n"

    # 系统指令：逻辑隔离 & URL 生成
    system_prompt = f"""
    Role: Senior Travel Planner & Consultant at Ctrip.
    
    [MEMORY]:
    {history_str}

    [KNOWLEDGE BASE]:
    {kb_text}

    [LOGIC BRANCHING - CRITICAL]:
    1. **IF User asks about MULTI-DAY TOURS (>4 days)**:
       - **Focus**: Search ONLY in Multi-day package docs.
       - **ISOLATION**: DO NOT reference or query the standalone "Hotel Rates" or "Charter" tables. Ignore them.
       - **ACTION**: You MUST extract the **Product ID** (e.g., 66316588) and generate a URL in Reply B.
       - **URL Format**: `https://vacations.ctrip.com/travel/detail/{{ID}}`
    
    2. **IF User asks about DAY TOURS (<=3 days)**:
       - Focus: Search Day Tour tables.
       - Logic: Atomic products. Don't mix stops.
       - Inclusions: Mentioned spots = Included tickets. Lunch = Included.

    3. **IF User asks about CUSTOMIZATION / CHARTER**:
       - Focus: Charter pricing rules.
       - Formula: Base Price + **1000 RMB Guide Fee**.
       - Requirement: Provide Timeline (09:00 -> 10:00...).

    [USER QUERY]: "{query}"

    [OUTPUT FORMAT]:
    <<<REPLY_A>>>
    (Quick Conclusion + Price + Key Disclaimer. < 60 words)
    <<<END_A>>>
    
    <<<REPLY_B>>>
    (Professional Plan:
     - **Product Details**: ...
     - **Cost Breakdown**: ...
     - **LINK** (For Multi-day): 🔗 [点击查看实时库存 & 价格](https://vacations.ctrip.com/travel/detail/{{ID}})
    )
    <<<END_B>>>

    <<<THOUGHTS>>>
    (中文诊断: 
     1. 意图识别 (多日/一日/包车)?
     2. 是否屏蔽了酒店库? (如果是多日游)
     3. 是否提取到了产品ID并生成了链接?
     4. 价格/行程逻辑?)
    <<<END_THOUGHTS>>>
    """
    try: return model.generate_content(system_prompt).text
    except Exception as e: return f"AI Error: {str(e)}"

# --- B. 专属行程生成 AI (用于 Tab 2) ---
# [核心更新]: 加入了营业时间校验 (Operating Hours Check)
def generate_itinerary_text(start, end, optimized_stops, dist, dur, price, model_choice):
    """专门用于 Tab 2：生成分钟级行程表，并校验营业时间"""
    if not AI_KEY: return "API Key Missing"
    model_id = "gemini-3-flash-preview" if "Flash" in model_choice else "gemini-3-pro-preview"
    model = genai.GenerativeModel(model_id)
    
    prompt = f"""
    Role: Professional Travel Planner & Risk Control Specialist.
    Task: Create a detailed, attractive daily itinerary based on the provided technical route data.
    
    [TECHNICAL DATA]:
    - Start: {start}
    - End: {end}
    - Optimized Sequence of Stops: {' -> '.join(optimized_stops)}
    - Total Distance: {dist} km
    - Total Driving Time: {dur} hours
    - Total Price: {price} RMB (Car + Guide)
    
    [CRITICAL REQUIREMENT - OPERATING HOURS CHECK]:
    1. **Knowledge Retrieval**: Use your internal knowledge to check the standard opening/closing hours for each stop.
    2. **Logic Check**: 
       - If the timeline suggests arriving at a spot after it closes (e.g., Nijo Castle at 18:00), you MUST mark it with "🔴 风险: 可能已闭馆".
       - If the spot is open 24h (e.g., parks, streets), no warning needed.
    3. **Toll Hint**: Since precise toll prices are unavailable via API, just add a generic note: "*(行程涉及高速，如有过路费请实报实销)*".

    [OUTPUT TEMPLATE]:
    ### 🗓️ 推荐行程安排 (已优化路线)
    * **09:00** 🏨 酒店出发: 司机在 {start} 大堂等候
    * **09:00 - 10:00** 🚗 前往第一站...
    * **10:00 - 11:30** 🏯 **[景点名]** (游玩约 1.5h)
       * *[Optional: Add a 1-sentence highlight]*
    * ... (Generate the rest)
    * **19:00** 🏁 结束行程: 送回 {end}
    
    ---
    💰 **费用明细**: {price}元 (含车+导，10小时/300公里)
    🚧 **路况与服务提示**: 
    1. 行程全长约 {dist}km，预计行车 {dur}小时。
    2. *(行程涉及高速，如有过路费请实报实销)*
    💡 **规划师建议**: (Why is this route good? Any closure warnings?)
    """
    try: return model.generate_content(prompt).text
    except: return "行程生成超时，请重试。"

# ==========================================
# 4. 地图工具 (含路线优化)
# ==========================================
def calc_map_route(start, end, stops):
    if not gmaps: return {"valid": False, "msg": "API未连接"}
    try:
        now = datetime.now()
        # 关键参数: optimize_waypoints=True (自动排序)
        res = gmaps.directions(start, end, waypoints=stops, mode="driving", departure_time=now, optimize_waypoints=True)
        if not res: return {"valid": False, "msg": "无路线"}
        
        route = res[0]
        # 提取优化后的顺序
        order_indices = route.get('waypoint_order', list(range(len(stops))))
        optimized_stops = [stops[i] for i in order_indices]
        
        dist = round(sum(l['distance']['value'] for l in route['legs'])/1000, 1)
        dur = round(sum(l['duration']['value'] for l in route['legs'])/3600, 1)
        
        poly = route['overview_polyline']['points']
        markers = [{'color':'green','label':'S','locations':[start]},{'color':'red','label':'E','locations':[end]}]
        if stops: markers.append({'color':'blue','label':'P','locations':stops})
        
        img_raw = gmaps.static_map(size=(800,400), path=f"enc:{poly}", markers=markers, format="png")
        
        return {
            "valid": True, 
            "dist": dist, 
            "dur": dur, 
            "img": io.BytesIO(b"".join(img_raw)),
            "optimized_stops": optimized_stops 
        }
    except Exception as e: return {"valid": False, "msg": str(e)}

# ==========================================
# 5. 前端主程序
# ==========================================
def main():
    if 'kb' not in st.session_state:
        kb = KnowledgeBase()
        kb.load_all()
        st.session_state.kb = kb
    if 'messages' not in st.session_state: st.session_state.messages = []

    # --- 侧边栏 ---
    with st.sidebar:
        st.title("⚙️ 控制台")
        # 知识库状态
        with st.expander("📚 知识库状态"):
            for s in st.session_state.kb.file_status:
                if "✅" in s: st.success(s)
                else: st.warning(s)
        
        if st.button("🗑️ 清空对话 (接待新客)", type="primary"):
            st.session_state.messages = []
            st.rerun()
        
        st.divider()
        model_choice = st.radio("AI 模型", ["Gemini 3 Flash", "Gemini 3 Pro"])

    st.title("Ctrip 服务专家 Co-Pilot")
    tab_chat, tab_plan = st.tabs(["💬 智能问答", "🗺️ 包车规划 (资深版)"])

    # === TAB 1: 智能问答 ===
    with tab_chat:
        # 历史记录
        for msg in st.session_state.messages:
            if msg['role'] == 'user':
                with st.chat_message("user"): st.write(msg['content'])
            elif msg['role'] == 'assistant':
                with st.chat_message("assistant"):
                    st.code(msg['short'], language=None)
                    with st.expander("🔽 查看详细行程 & 诊断"):
                        st.markdown(msg['long'])
                        st.info(msg['thoughts'])

        # 输入
        user_input = st.chat_input("输入需求... (例: 有没有关西5日游？)")
        if user_input:
            st.session_state.messages.append({"role": "user", "content": user_input})
            with st.chat_message("user"): st.write(user_input)

            with st.chat_message("assistant"):
                with st.spinner("AI 正在检索产品库..."):
                    raw_res = get_ai_reply(user_input, st.session_state.messages, st.session_state.kb.knowledge_text, model_choice)
                    # 解析
                    try: r_a = re.search(r'<<<REPLY_A>>>([\s\S]*?)<<<END_A>>>', raw_res).group(1).strip()
                    except: r_a = raw_res
                    try: r_b = re.search(r'<<<REPLY_B>>>([\s\S]*?)<<<END_B>>>', raw_res).group(1).strip()
                    except: r_b = "未生成"
                    try: th = re.search(r'<<<THOUGHTS>>>([\s\S]*?)<<<END_THOUGHTS>>>', raw_res).group(1).strip()
                    except: th = "无"

                    st.code(r_a, language=None)
                    with st.expander("🔽 查看详细行程 & 诊断", expanded=True):
                        st.markdown(r_b) 
                        st.info(th)
                    
                    st.session_state.messages.append({"role": "assistant", "short": r_a, "long": r_b, "thoughts": th})

    # === TAB 2: 包车规划 (核心升级) ===
    with tab_plan:
        c1, c2 = st.columns([1, 1.5])
        with c1:
            st.info("🛠️ 请输入想去的景点（乱序没关系，AI 会自动优化不走回头路）")
            with st.form("charter_v2"):
                start = st.text_input("📍 起点", "大阪希尔顿酒店")
                end = st.text_input("🏁 终点", "大阪希尔顿酒店")
                stops = st.text_area("🎡 途经景点 (一行一个)", "奈良公园\n二条城\n清水寺")
                price_base = st.number_input("💰 车辆底价 (RMB)", 2500, step=100)
                
                submitted = st.form_submit_button("🚀 生成优化后行程单")
        
        with c2:
            if submitted:
                # 1. 算地图数据 (含路线优化)
                wps = [s.strip() for s in stops.split('\n') if s.strip()]
                with st.spinner("正在计算最佳路线 (Google Maps)..."):
                    res = calc_map_route(start, end, wps)
                
                if res['valid']:
                    st.image(res['img'], use_container_width=True, caption="Google Maps 优化路线预览")
                    
                    # 2. 算总价
                    total_price = price_base + 1000
                    
                    # 3. AI 生成详细时间表 (含营业时间风控)
                    with st.spinner("正在校验景点营业时间并排期..."):
                        plan_text = generate_itinerary_text(
                            start, end, res['optimized_stops'], 
                            res['dist'], res['dur'], total_price, model_choice
                        )
                    
                    # 4. 显示结果
                    st.markdown("### 📋 定制行程单 (可复制)")
                    st.code(plan_text, language=None)
                    
                    # 5. 风控提示
                    if res['dist'] > 300 or res['dur'] > 10:
                        st.error(f"⚠️ 风险预警: 里程 {res['dist']}km (限300) / 耗时 {res['dur']}h (限10)。建议删减景点。")
                    else:
                        st.success("✅ 行程风控通过：符合 10小时/300公里 标准。")
                else:
                    st.error(f"地图计算失败: {res['msg']}")

if __name__ == "__main__":
    main()
