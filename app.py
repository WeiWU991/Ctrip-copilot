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
st.set_page_config(page_title="Ctrip 服务专家 Co-Pilot", page_icon="👩‍💼", layout="wide")

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
model_id = "gemini-3-flash-preview" if "Flash" in model_choice else "gemini-3-pro-preview"

try: model = genai.GenerativeModel(model_id)
except Exception as e: return f"模型初始化失败: {e}"

history_str = ""
for msg in history[-5:]: 
        # 只拼接用户的输入和 AI 的简短回答，避免上下文过大
if msg['role'] == 'user': history_str += f"User: {msg['content']}\n"
elif msg['role'] == 'assistant': history_str += f"AI: {msg.get('short', '')}\n"

@@ -180,7 +181,8 @@
try:
now = datetime.now()
res = gmaps.directions(start, end, waypoints=stops, mode="driving", departure_time=now, optimize_waypoints=True)
        if not res: return {"valid": False, "msg": "无路线"}
        # 如果返回空列表，通常是地名无法识别或无路可走
        if not res: return {"valid": False, "msg": "无路线 (请检查地名是否准确，如 '富士山' 可改为 '河口湖')"}

route = res[0]
order_indices = route.get('waypoint_order', list(range(len(stops))))
@@ -216,6 +218,7 @@
if "✅" in s: st.success(s)
else: st.warning(s)

        # [建议] 报错后请点击此按钮
if st.button("🗑️ 清空对话 (接待新客)", type="primary"):
st.session_state.messages = []
st.rerun()
@@ -238,10 +241,11 @@
with st.chat_message("user"): st.write(msg['content'])
elif msg['role'] == 'assistant':
with st.chat_message("assistant"):
                    st.code(msg['short'], language=None)
                    # 使用 .get() 避免报错
                    st.code(msg.get('short', '...'), language=None)
with st.expander("🔽 查看详细行程 & 诊断"):
                        st.markdown(msg['long'])
                        st.info(msg['thoughts'])
                        st.markdown(msg.get('long', ''))
                        st.info(msg.get('thoughts', ''))

final_query = None

@@ -259,6 +263,7 @@
final_query = text_input

if final_query:
            # [核心修复点] 使用 .get('content') 防止 KeyError
if not st.session_state.messages or st.session_state.messages[-1].get('content') != final_query:
st.session_state.messages.append({"role": "user", "content": final_query})

@@ -289,7 +294,7 @@
with st.form("charter_v2"):
start = st.text_input("📍 起点", "大阪希尔顿酒店")
end = st.text_input("🏁 终点", "大阪希尔顿酒店")
                stops = st.text_area("🎡 途经景点 (一行一个)", "富士山\n清水寺\n奈良公园")
                stops = st.text_area("🎡 途经景点 (一行一个)", "富士山五合目\n清水寺\n奈良公园")
price_base = st.number_input("💰 车辆底价 (RMB)", 2500, step=100)
submitted = st.form_submit_button("🚀 生成优化后行程单")
