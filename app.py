import streamlit as st
import pandas as pd
import glob
import os
import googlemaps
import google.generativeai as genai
from datetime import datetime
import io

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
# 2. 万能知识库 (Excel/MD -> 纯文本)
# ==========================================
class KnowledgeBase:
    def __init__(self):
        self.full_text = ""
        self.source_files = []

    def load_all(self):
        """将所有文件(Excel/CSV/MD)都转化为文本喂给AI"""
        combined_text = []
        
        # 1. 读取 Excel/CSV (转化为 Markdown 表格文本)
        data_files = glob.glob('*.xlsx') + glob.glob('*.xls') + glob.glob('*.csv')
        for f in data_files:
            try:
                # 简单读取，不纠结表头，全部转为字符串
                if f.endswith('.csv'): df = pd.read_csv(f, header=None)
                else: df = pd.read_excel(f, header=None)
                
                # 清洗空数据
                df = df.dropna(how='all')
                
                # 转为 Markdown 文本
                table_md = df.to_markdown(index=False)
                combined_text.append(f"\n\n=== 数据来源: {os.path.basename(f)} ===\n{table_md}")
                self.source_files.append(f)
            except Exception as e:
                print(f"Error loading {f}: {e}")

        # 2. 读取 Markdown 文档
        md_files = glob.glob('*.md')
        for f in md_files:
            try:
                with open(f, 'r', encoding='utf-8') as file:
                    content = file.read()
                    combined_text.append(f"\n\n=== 知识文档: {os.path.basename(f)} ===\n{content}")
                    self.source_files.append(f)
            except: pass
            
        self.full_text = "\n".join(combined_text)
        return len(self.source_files)

# ==========================================
# 3. 业务工具 (地图 & AI)
# ==========================================
def calculate_route(start, end, waypoints):
    """Google Maps 距离/时间计算 + 画图"""
    if not gmaps: return None
    try:
        now = datetime.now()
        # 路线规划
        directions = gmaps.directions(
            start, end, 
            waypoints=waypoints, 
            mode="driving", 
            departure_time=now,
            optimize_waypoints=True
        )
        
        if not directions: return {"valid": False, "msg": "未找到路线，请检查地名"}
        
        leg = directions[0]['legs'][0] # 简化处理，取第一段汇总
        total_dist_m = sum(l['distance']['value'] for l in directions[0]['legs'])
        total_dur_s = sum(l['duration']['value'] for l in directions[0]['legs'])
        
        dist_km = round(total_dist_m / 1000, 1)
        dur_h = round(total_dur_s / 3600, 1)
        
        # 静态地图
        poly = directions[0]['overview_polyline']['points']
        markers = [
            {'color':'green', 'label':'S', 'locations':[start]},
            {'color':'red', 'label':'E', 'locations':[end]}
        ]
        if waypoints:
            markers.append({'color':'blue', 'label':'W', 'locations':waypoints})
            
        map_img = gmaps.static_map(
            size=(600, 300), 
            path=f"enc:{poly}", 
            markers=markers, 
            maptype="roadmap", 
            format="png"
        )
        
        return {
            "valid": True,
            "dist_km": dist_km,
            "dur_h": dur_h,
            "map_img": map_img,
            "raw_route": f"{start} -> {waypoints} -> {end}"
        }
    except Exception as e:
        return {"valid": False, "msg": str(e)}

def get_ai_response(query, context, kb_text, model_choice):
    if not AI_KEY: return "❌ 请配置 Google API Key"
    
    model_id = "gemini-1.5-pro" if model_choice == "pro" else "gemini-1.5-flash"
    model = genai.GenerativeModel(model_id)
    
    # 核心业务逻辑 Prompt
    prompt = f"""
    Role: Senior Travel Consultant at Ctrip (携程).
    Mission: Provide professional, accurate, and safe travel solutions.
    
    [STRICT KNOWLEDGE BOUNDARY]
    1. Answer ONLY based on the provided [Knowledge Base].
    2. If the user asks about something NOT in the Knowledge Base (e.g., a city or product not listed), reply exactly: "该回答超出知识库范畴，请咨询对应产品经理".
    3. Do NOT hallucinate prices or services.

    [CURRENCY & PRICE RULES]
    1. STRICTLY distinguish currencies found in the data (RMB, JPY, HKD). Label them clearly.
    2. Charter Price Formula: (Car Base Price found in data) + 1000 RMB (Guide Fee).
    
    [CHARTER SAFETY RULES] (Apply ONLY if Context indicates Charter/Custom tour)
    1. Max Duration: 10 Hours/day.
    2. Max Distance: 300 KM/day.
    3. If user's plan exceeds these limits (see Context for Google Maps data), you MUST:
       - Warn the user clearly.
       - Act as a consultant: Suggest splitting the trip into 2 days or removing distant spots.
    
    [Knowledge Base]:
    {kb_text[:25000]} 
    
    [Current Context / Map Data]:
    {context}
    
    [User Question]:
    "{query}"
    
    [Output Format]:
    Please output TWO parts separated by lines:
    
    ---REPLY_A---
    (Quick Reply: Conclusion + Price + Key Safety Warning if any. < 80 words)
    ---END_A---
    
    ---REPLY_B---
    (Professional Consultant Reply:
     - Clear breakdown of costs (Car + Guide).
     - Route analysis (based on map data).
     - Safety/Time management advice.
     - Upsell or Adjustments if needed.)
    ---END_B---
    """
    
    try:
        return model.generate_content(prompt).text
    except Exception as e:
        return f"AI Error: {e}"

# ==========================================
# 4. 前端交互
# ==========================================
def main():
    # --- 初始化 ---
    if 'kb' not in st.session_state:
        kb = KnowledgeBase()
        kb.load_all()
        st.session_state.kb = kb
        st.session_state.map_result = None # 存储地图结果

    # --- 侧边栏 ---
    with st.sidebar:
        st.title("⚙️ 控制台")
        st.write(f"📚 知识库: 已加载 {len(st.session_state.kb.source_files)} 个文件")
        
        st.divider()
        st.subheader("🗺️ 包车行程规划工具")
        st.caption("当一日游无法满足时，请在此规划包车")
        
        with st.form("map_form"):
            start = st.text_input("起点", "大阪市区酒店")
            end = st.text_input("终点", "大阪市区酒店")
            waypoints_txt = st.text_area("途经点 (一行一个)", "清水寺\n奈良公园")
            submitted = st.form_submit_button("🚀 校验行程 & 测距")
            
            if submitted:
                if not gmaps:
                    st.error("Google Maps API 未连接")
                else:
                    wps = [w.strip() for w in waypoints_txt.split('\n') if w.strip()]
                    with st.spinner("正在测算路况..."):
                        res = calculate_route(start, end, wps)
                        st.session_state.map_result = res
        
        # 显示地图结果 (侧边栏)
        if st.session_state.map_result:
            res = st.session_state.map_result
            if res.get('valid'):
                st.image(io.BytesIO(b"".join(res['map_img'])), caption="行程路线图", use_container_width=True)
                
                # 距离/时间 颜色警示
                d_color = "red" if res['dist_km'] > 300 else "green"
                t_color = "red" if res['dur_h'] > 10 else "green"
                
                st.markdown(f"**距离**: :{d_color}[{res['dist_km']} km] (限300)")
                st.markdown(f"**耗时**: :{t_color}[{res['dur_h']} h] (限10)")
                
                if res['dist_km'] > 300 or res['dur_h'] > 10:
                    st.error("⚠️ 行程超限！建议调整或拆分为2天")
                else:
                    st.success("✅ 行程合理")
            else:
                st.error(f"地图错误: {res.get('msg')}")

    # --- 主界面 ---
    st.title("👩‍💼 Ctrip 智能顾问")
    st.caption("多日游/一日游/包车全能助手 | 严格遵循知识库 | 智能风控")

    # 聊天记录容器
    if "messages" not in st.session_state:
        st.session_state.messages = []

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("image"):
                st.image(msg["image"])

    # 输入框
    prompt = st.chat_input("输入客人咨询... (例: 大阪去京都奈良包车多少钱？)")

    if prompt:
        # 1. 用户提问
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.write(prompt)

        # 2. 准备上下文 (Context)
        context_str = ""
        map_image = None
        
        # 如果刚才进行了地图测算，把测算结果喂给 AI
        if st.session_state.map_result and st.session_state.map_result.get('valid'):
            res = st.session_state.map_result
            context_str += f"""
            [User Charter Plan Data from Google Maps]
            - Route: {res['raw_route']}
            - Total Distance: {res['dist_km']} km
            - Total Duration: {res['dur_h']} hours
            - Safety Status: {'RISKY (Exceeds Limit)' if (res['dist_km']>300 or res['dur_h']>10) else 'SAFE'}
            """
            map_image = io.BytesIO(b"".join(res['map_img']))
        
        # 3. AI 回答
        with st.chat_message("assistant"):
            with st.spinner("AI 正在查阅知识库并计算..."):
                raw_reply = get_ai_response(
                    prompt, 
                    context_str, 
                    st.session_state.kb.full_text, 
                    "pro" # 默认用 Pro 模型以保证逻辑
                )
                
                # 尝试解析
                try:
                    reply_a = raw_reply.split("---REPLY_A---")[1].split("---END_A---")[0].strip()
                    reply_b = raw_reply.split("---REPLY_B---")[1].split("---END_B---")[0].strip()
                except:
                    reply_a = "解析格式失败，请参考下方全文"
                    reply_b = raw_reply

                # 展示逻辑
                st.subheader("📋 极简回复 (可复制)")
                st.code(reply_a, language=None)
                
                st.subheader("💼 专业顾问回复")
                st.code(reply_b, language=None)
                
                # 如果是包车场景且有地图，显示地图
                if map_image and ("包车" in prompt or "charter" in prompt.lower() or "定制" in prompt):
                    st.image(map_image, caption="AI 生成的行程规划图")
                    st.caption("✅ 已基于 Google Maps 实际路况生成")
        
        # 存入历史 (简化存储)
        st.session_state.messages.append({"role": "assistant", "content": reply_b, "image": map_image if ("包车" in prompt) else None})

if __name__ == "__main__":
    main()
