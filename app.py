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

# 初始化客户端
gmaps = None
if MAPS_KEY:
    try: gmaps = googlemaps.Client(key=MAPS_KEY)
    except: pass

if AI_KEY:
    try: genai.configure(api_key=AI_KEY)
    except: pass

# ==========================================
# 2. 知识库加载器 (万能文本模式)
# ==========================================
class KnowledgeBase:
    def __init__(self):
        self.full_text = ""
        self.files_loaded = []

    def load_all(self):
        combined = []
        # 1. 读表格 (Excel/CSV) -> 转 Markdown 文本
        data_files = glob.glob('*.xlsx') + glob.glob('*.xls') + glob.glob('*.csv')
        for f in data_files:
            try:
                if f.endswith('.csv'): df = pd.read_csv(f, header=None)
                else: df = pd.read_excel(f, header=None)
                df = df.dropna(how='all') # 去空行
                # 转换成文本表格喂给AI
                combined.append(f"\n\n=== 数据表: {os.path.basename(f)} ===\n{df.to_markdown(index=False)}")
                self.files_loaded.append(f)
            except: pass
        
        # 2. 读文档 (Markdown)
        md_files = glob.glob('*.md')
        for f in md_files:
            try:
                content = open(f, 'r', encoding='utf-8').read()
                combined.append(f"\n\n=== 业务文档: {os.path.basename(f)} ===\n{content}")
                self.files_loaded.append(f)
            except: pass
            
        self.full_text = "\n".join(combined)

# ==========================================
# 3. 核心功能函数
# ==========================================
def get_ai_reply(query, context, model_type="pro"):
    """AI 对话核心"""
    if not AI_KEY: return "❌ 未配置 API Key"
    
    model_id = "gemini-1.5-pro" if model_type == "pro" else "gemini-1.5-flash"
    model = genai.GenerativeModel(model_id)
    
    prompt = f"""
    Role: Ctrip Senior Travel Consultant.
    
    [STRICT KNOWLEDGE BASE]:
    {context[:28000]} 
    
    [BUSINESS RULES]:
    1. **Boundary**: Answer ONLY based on the Knowledge Base. If user asks about a product not listed, reply: "该产品需单询，请联系产品经理".
    2. **Multi-day Tours**: Quote 'Starting Price' found in docs. MUST add disclaimer: "起价仅供参考，最终库存和售价请点击链接二次确认".
    3. **Day Tours**: Quote the price found in tables. MUST add disclaimer: "价格已含车费，但余位需后台二次确认".
    4. **Currency**: Pay attention to RMB vs JPY vs HKD in the docs. Output clearly.
    
    [USER QUESTION]: "{query}"
    
    [OUTPUT FORMAT]:
    Please separate your answer into two parts for copy-paste:
    
    <<<REPLY_A>>>
    (Quick Reply: < 60 words. Conclusion + Price + Link/Disclaimer)
    <<<END_A>>>
    
    <<<REPLY_B>>>
    (Professional Reply: Warm greeting -> Details -> Upsell -> Disclaimer)
    <<<END_B>>>
    """
    try: return model.generate_content(prompt).text
    except Exception as e: return f"AI Error: {e}"

def calc_map_route(start, end, stops):
    """地图计算核心"""
    if not gmaps: return {"valid": False, "msg": "API未连接"}
    try:
        now = datetime.now()
        res = gmaps.directions(start, end, waypoints=stops, mode="driving", departure_time=now, optimize_waypoints=True)
        if not res: return {"valid": False, "msg": "未找到路线，请检查地名"}
        
        route = res[0]
        dist_km = round(sum(l['distance']['value'] for l in route['legs'])/1000, 1)
        dur_h = round(sum(l['duration']['value'] for l in route['legs'])/3600, 1)
        
        # 静态地图
        poly = route['overview_polyline']['points']
        markers = [{'color':'green','label':'S','locations':[start]},{'color':'red','label':'E','locations':[end]}]
        if stops: markers.append({'color':'blue','label':'P','locations':stops})
        
        img_raw = gmaps.static_map(size=(800,400), path=f"enc:{poly}", markers=markers, format="png")
        img_bytes = io.BytesIO()
        for chunk in img_raw: img_bytes.write(chunk)
        
        return {"valid":True, "dist":dist_km, "dur":dur_h, "img":img_bytes, "legs": len(route['legs'])}
    except Exception as e: return {"valid": False, "msg": str(e)}

# ==========================================
# 4. 前端界面 (双 Tab 架构)
# ==========================================
def main():
    if 'kb' not in st.session_state:
        kb = KnowledgeBase()
        kb.load_all()
        st.session_state.kb = kb
        st.session_state.messages = [] # 聊天记录

    st.title("👩‍💼 Ctrip 客服 Copilot")
    
    # --- 核心：两个独立 Tab ---
    tab_chat, tab_plan = st.tabs(["💬 智能问答 (标准品)", "🗺️ 包车规划 (定制)"])

    # ==================================
    # TAB 1: 智能问答 (AI Chat)
    # ==================================
    with tab_chat:
        st.caption(f"📚 知识库已就绪 ({len(st.session_state.kb.files_loaded)} 个文件) | 适用于：一日游/多日游/签证/天气/退改")
        
        # 聊天历史显示
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                if msg["type"] == "text": st.markdown(msg["content"])
                elif msg["type"] == "code": st.code(msg["content"], language=None)
        
        # 输入框
        user_input = st.chat_input("输入客人问题... (例: 富士山一日游含餐吗？)")
        
        if user_input:
            # 用户消息上屏
            st.session_state.messages.append({"role":"user", "type":"text", "content":user_input})
            with st.chat_message("user"): st.write(user_input)
            
            # AI 回答
            with st.chat_message("assistant"):
                with st.spinner("查询知识库中..."):
                    raw_res = get_ai_reply(user_input, st.session_state.kb.full_text)
                    
                    # 解析 A/B 两个版本
                    try:
                        reply_a = raw_res.split("<<<REPLY_A>>>")[1].split("<<<END_A>>>")[0].strip()
                        reply_b = raw_res.split("<<<REPLY_B>>>")[1].split("<<<END_B>>>")[0].strip()
                    except:
                        reply_a = raw_res # 兜底
                        reply_b = None
                    
                    st.subheader("📋 极简回复")
                    st.code(reply_a, language=None)
                    st.session_state.messages.append({"role":"assistant", "type":"code", "content":reply_a})
                    
                    if reply_b:
                        st.subheader("💼 专业回复")
                        st.code(reply_b, language=None)
                        # 专业版不存入历史，避免刷屏，仅供当前查看

    # ==================================
    # TAB 2: 包车规划 (Workbench)
    # ==================================
    with tab_plan:
        c1, c2 = st.columns([1, 2])
        
        with c1:
            st.markdown("### 🛠️ 行程参数")
            with st.form("plan_form"):
                start = st.text_input("起点", "大阪市区酒店")
                end = st.text_input("终点", "大阪市区酒店")
                stops_txt = st.text_area("途经景点 (一行一个)", "清水寺\n奈良公园")
                
                st.divider()
                st.markdown("#### 💰 价格计算器")
                base_price = st.number_input("车辆底价 (查表得)", value=2500, step=100)
                guide_fee = st.number_input("导游服务费 (固定)", value=1000, disabled=True)
                
                btn_calc = st.form_submit_button("🚀 校验行程 & 算价")
        
        with c2:
            st.markdown("### 🗺️ 规划结果")
            if btn_calc:
                stops = [s.strip() for s in stops_txt.split('\n') if s.strip()]
                res = calc_map_route(start, end, stops)
                
                if res['valid']:
                    # 1. 价格计算
                    total_price = base_price + guide_fee
                    
                    # 2. 风控检查
                    is_safe_dist = res['dist'] <= 300
                    is_safe_time = res['dur'] <= 10
                    
                    # 3. 显示地图
                    st.image(res['img'], use_container_width=True, caption="Google Maps 实时路况")
                    
                    # 4. 数据看板
                    k1, k2, k3 = st.columns(3)
                    k1.metric("总里程", f"{res['dist']} km", delta="正常" if is_safe_dist else "超限!", delta_color="normal" if is_safe_dist else "inverse")
                    k2.metric("预估耗时", f"{res['dur']} h", delta="正常" if is_safe_time else "超限!", delta_color="normal" if is_safe_time else "inverse")
                    k3.metric("参考总价", f"¥{total_price}", f"含导游 (底价{base_price})")
                    
                    st.divider()
                    
                    # 5. 生成话术
                    st.markdown("#### 📋 包车回复话术 (一键复制)")
                    
                    safety_msg = ""
                    if not is_safe_dist: safety_msg += f"\n⚠️ 警告：行程全长 {res['dist']}km，超过每日 300km 安全限制，建议拆分行程。"
                    if not is_safe_time: safety_msg += f"\n⚠️ 警告：预计耗时 {res['dur']}小时，接近或超过 10小时 服务时长限制，可能产生超时费。"
                    if is_safe_dist and is_safe_time: safety_msg = "\n✅ 行程合理，符合安全规范。"

                    reply_text = f"""【包车行程方案】
📍 路线：{start} -> {' -> '.join(stops)} -> {end}
🚗 车型：10座海狮（参考）
📏 数据：全程约 {res['dist']}公里，预计用时 {res['dur']}小时
💰 费用：¥{total_price} (含车辆底价+导游服务费，不含超时费/高速费)
📝 评估：{safety_msg}
💡 提示：最终价格请以此方案咨询产品经理进行锁位。"""
                    
                    st.code(reply_text, language=None)
                    
                else:
                    st.error(f"地图计算失败: {res['msg']}")
            else:
                st.info("👈 请在左侧输入行程并点击校验")

if __name__ == "__main__":
    main()
