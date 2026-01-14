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
# 3. AI 核心 (语音 & 对话 & 规划)
# ==========================================

# --- A. 语音转文字 (Gemini Native) ---
def transcribe_audio(audio_bytes):
    """直接利用 Gemini 的多模态能力听懂语音"""
    if not AI_KEY: return None
    try:
        # [修正] 严格使用 gemini-3-flash-preview
        model = genai.GenerativeModel("gemini-3-flash-preview") 
        response = model.generate_content([
            {"mime_type": "audio/wav", "data": audio_bytes},
            "Please transcribe what the user said in this audio strictly into text. Do not answer the question, just transcribe. If it's Chinese, output Chinese."
        ])
        return response.text.strip()
    except Exception as e:
        st.error(f"语音识别失败: {e}")
        return None

# --- B. 智能对话 AI ---
def get_ai_reply(query, history, kb_text, model_choice):
    if not AI_KEY: return "❌ 错误：未配置 API Key"
    # [修正] 严格锁定模型 ID
    model_id = "gemini-3-flash-preview" if "Flash" in model_choice else "gemini-3-pro-preview"
    
    try: model = genai.GenerativeModel(model_id)
    except Exception as e: return f"模型初始化失败: {e}"
    
    history_str = ""
    for msg in history[-5:]: 
        if msg['role'] == 'user': history_str += f"User: {msg['content']}\n"
        elif msg['role'] == 'assistant': history_str += f"AI: {msg.get('short', '')}\n"

    system_prompt = f"""
    Role: Senior Travel Planner & Consultant at Ctrip.
    
    [MEMORY]:
    {history_str}

    [KNOWLEDGE BASE]:
    {kb_text}

    [LOGIC BRANCHING]:
    1. **MULTI-DAY (>4 days)**: Search Multi-day docs only. Extract Product ID -> URL `https://vacations.ctrip.com/travel/detail/{{ID}}`. Ignore hotels/charters.
    2. **DAY TOURS (<=3 days)**: Search Day Tour tables. Atomic products. Mentioned spots=Included. Lunch=Included unless 'Self-pay'.
    3. **CUSTOM/CHARTER**: Base Price + **1000 RMB Guide Fee**. Provide Timeline (09:00 -> 10:00...).

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
    (中文诊断: 1.意图 2.屏蔽酒店? 3.链接生成? 4.价格逻辑?)
    <<<END_THOUGHTS>>>
    """
    try: return model.generate_content(system_prompt).text
    except Exception as e: return f"AI Error: {str(e)}"

# --- C. 专属行程生成 AI ---
def generate_itinerary_text(start, end, optimized_stops, dist, dur, price, model_choice):
    if not AI_KEY: return "API Key Missing"
    # [修正] 严格锁定模型 ID
    model_id = "gemini-3-flash-preview" if "Flash" in model_choice else "gemini-3-pro-preview"
    
    model = genai.GenerativeModel(model_id)
    
    prompt = f"""
    Role: Professional Travel Planner & Risk Control Specialist.
    Task: Create a daily itinerary.
    
    [DATA]: Start:{start}, End:{end}, Route:{'->'.join(optimized_stops)}, Dist:{dist}km, Time:{dur}h, Price:{price}RMB.
    
    [RULES]:
    1. **Operating Hours**: Check if arrival time matches spot opening hours. If risky (e.g. arrive at 18:00), mark "🔴 风险: 可能已闭馆".
    2. **Toll Note**: Add "*(行程涉及高速，如有过路费请实报实销)*".
    
    [OUTPUT TEMPLATE]:
    ### 🗓️ 推荐行程安排 (已优化路线)
    * **09:00** 🏨 酒店出发: 司机在 {start} 大堂等候
    * **09:00 - 10:00** 🚗 前往第一站...
    * **10:00 - 11:30** 🏯 **[景点名]** (游玩约 1.5h)
    * ...
    * **19:00** 🏁 结束行程: 送回 {end}
    
    ---
    💰 **费用明细**: {price}元 (含车+导，10小时/300公里)
    🚧 **路况与服务提示**: 
    1. 行程全长约 {dist}km，预计行车 {dur}小时。
    2. *(行程涉及高速，如有过路费请实报实销)*
    💡 **规划师建议**: ...
    """
    try: return model.generate_content(prompt).text
    except: return "行程生成超时，请重试。"

# ==========================================
# 4. 地图工具
# ==========================================
def calc_map_route(start, end, stops):
    if not gmaps: return {"valid": False, "msg": "API未连接"}
    try:
        now = datetime.now()
        res = gmaps.directions(start, end, waypoints=stops, mode="driving", departure_time=now, optimize_waypoints=True)
        if not res: return {"valid": False, "msg": "无路线"}
        
        route = res[0]
        order_indices = route.get('waypoint_order', list(range(len(stops))))
        optimized_stops = [stops[i] for i in order_indices]
        
        dist = round(sum(l['distance']['value'] for l in route['legs'])/1000, 1)
        dur = round(sum(l['duration']['value'] for l in route['legs'])/3600, 1)
        
        poly = route['overview_polyline']['points']
        markers = [{'color':'green','label':'S','locations':[start]},{'color':'red','label':'E','locations':[end]}]
        if stops: markers.append({'color':'blue','label':'P','locations':stops})
        
        img_raw = gmaps.static_map(size=(800,400), path=f"enc:{poly}", markers=markers, format="png")
        
        return {"valid":True, "dist":dist, "dur":dur, "img":io.BytesIO(b"".join(img_raw)), "optimized_stops":optimized_stops}
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
        with st.expander("📚 知识库状态"):
            for s in st.session_state.kb.file_status:
                if "✅" in s: st.success(s)
                else: st.warning(s)
        
        if st.button("🗑️ 清空对话 (接待新客)", type="primary"):
            st.session_state.messages = []
            st.rerun()
        
        st.divider()
        model_choice = st.radio("AI 模型", ["Gemini 3 Flash", "Gemini 3 Pro"])
        st.caption("🚀 Model ID: `gemini-3-flash-preview` / `gemini-3-pro-preview`")

    st.title("Ctrip 服务专家 Co-Pilot")
    
    st.caption("🎙️ 语音输入 (Demo): 点击录音，AI 自动识别")
    audio_val = st.audio_input("按住说话 (支持中/英/日混说)")

    tab_chat, tab_plan = st.tabs(["💬 智能问答", "🗺️ 包车规划"])

    # === TAB 1: 智能问答 ===
    with tab_chat:
        for msg in st.session_state.messages:
            if msg['role'] == 'user':
                with st.chat_message("user"): st.write(msg['content'])
            elif msg['role'] == 'assistant':
                with st.chat_message("assistant"):
                    st.code(msg['short'], language=None)
                    with st.expander("🔽 查看详细行程 & 诊断"):
                        st.markdown(msg['long'])
                        st.info(msg['thoughts'])

        final_query = None
        
        # 优先级 A: 语音输入
        if audio_val:
            with st.spinner("🎙️ 正在听写..."):
                transcribed_text = transcribe_audio(audio_val.getvalue())
                if transcribed_text:
                    final_query = transcribed_text
                    st.info(f"🗣️ 识别结果: {final_query}")

        # 优先级 B: 文本输入
        text_input = st.chat_input("输入需求... (例: 大阪到京都包车)")
        if text_input and not final_query:
            final_query = text_input

        if final_query:
            # [核心修复] 使用 .get('content') 避免 KeyError
            if not st.session_state.messages or st.session_state.messages[-1].get('content') != final_query:
                st.session_state.messages.append({"role": "user", "content": final_query})
                
                if not audio_val:
                    with st.chat_message("user"): st.write(final_query)

                with st.chat_message("assistant"):
                    with st.spinner("AI 正在思考..."):
                        raw_res = get_ai_reply(final_query, st.session_state.messages, st.session_state.kb.knowledge_text, model_choice)
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

    # === TAB 2: 包车规划 ===
    with tab_plan:
        c1, c2 = st.columns([1, 1.5])
        with c1:
            with st.form("charter_v2"):
                start = st.text_input("📍 起点", "大阪希尔顿酒店")
                end = st.text_input("🏁 终点", "大阪希尔顿酒店")
                stops = st.text_area("🎡 途经景点 (一行一个)", "奈良公园\n二条城\n清水寺")
                price_base = st.number_input("💰 车辆底价 (RMB)", 2500, step=100)
                submitted = st.form_submit_button("🚀 生成优化后行程单")
        
        with c2:
            if submitted:
                wps = [s.strip() for s in stops.split('\n') if s.strip()]
                with st.spinner("正在计算最佳路线 (Google Maps)..."):
                    res = calc_map_route(start, end, wps)
                
                if res['valid']:
                    st.image(res['img'], use_container_width=True, caption="Google Maps 优化路线预览")
                    total_price = price_base + 1000
                    
                    with st.spinner("正在校验营业时间..."):
                        plan_text = generate_itinerary_text(
                            start, end, res['optimized_stops'], 
                            res['dist'], res['dur'], total_price, model_choice
                        )
                    
                    st.markdown("### 📋 定制行程单 (可复制)")
                    st.code(plan_text, language=None)
                    
                    if res['dist'] > 300 or res['dur'] > 10:
                        st.error(f"⚠️ 风险预警: 里程 {res['dist']}km / 耗时 {res['dur']}h。建议删减。")
                    else:
                        st.success("✅ 行程风控通过")
                else:
                    st.error(f"地图计算失败: {res['msg']}")

if __name__ == "__main__":
    main()
