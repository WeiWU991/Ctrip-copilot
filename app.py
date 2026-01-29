import streamlit as st
import pandas as pd
import glob
import os
import google.generativeai as genai
import re

# ==========================================
# 1. 基础配置
# ==========================================
st.set_page_config(page_title="Ctrip 服务专家 Co-Pilot", page_icon="👩‍💼", layout="wide")

# 地图 Key 已移除，因为不做包车了
AI_KEY = st.secrets.get("GOOGLE_API_KEY", "")

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
        
        # 1. Markdown (核心：多日游产品)
        md_files = glob.glob('*.md')
        for f in md_files:
            try:
                content = open(f, 'r', encoding='utf-8').read()
                self.knowledge_text += f"\n\n=== 核心知识库: {os.path.basename(f)} ===\n{content}"
                self.file_status.append(f"✅ 已加载文档: {f}")
            except: 
                self.file_status.append(f"❌ 读取失败: {f}")

        # 2. Simple Excel (虽然不做单项，但可能需要参考价格，暂保留或根据需求移除)
        data_files = glob.glob('*.xlsx') + glob.glob('*.csv')
        for f in data_files:
            # 过滤掉酒店/单项资源，因为我们只卖打包
            if any(k in f.upper() for k in ["HOTEL", "OSAKA", "SHINJUKU"]):
                self.file_status.append(f"⚠️ 已忽略单项资源表: {f}")
                continue
            try:
                if f.endswith('.csv'): df = pd.read_csv(f, header=None)
                else: df = pd.read_excel(f, header=None)
                df = df.dropna(how='all')
                self.knowledge_text += f"\n\n=== 参考资料: {os.path.basename(f)} ===\n{df.to_markdown(index=False)}"
                self.file_status.append(f"✅ 已加载表格: {f}")
            except: pass

# ==========================================
# 3. AI 核心 (语音 & 纯多日游对话)
# ==========================================

# --- A. 语音转文字 ---
def transcribe_audio(audio_bytes):
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

# --- B. 智能对话 AI (严格拦截一日游/包车) ---
def get_ai_reply(query, history, kb_text, model_choice):
    if not AI_KEY: return "❌ 错误：未配置 API Key"
    model_id = "gemini-3-flash-preview" if "Flash" in model_choice else "gemini-3-pro-preview"
    
    try: model = genai.GenerativeModel(model_id)
    except Exception as e: return f"模型初始化失败: {e}"
    
    history_str = ""
    for msg in history[-5:]: 
        if msg['role'] == 'user': history_str += f"User: {msg['content']}\n"
        elif msg['role'] == 'assistant': history_str += f"AI: {msg.get('short', '')}\n"

    # [UPDATED PROMPT]: 严格的业务边界设定
    system_prompt = f"""
    Role: Senior Travel Consultant at Ctrip (Multi-Day Package Team).
    
    [MEMORY]:
    {history_str}

    [KNOWLEDGE BASE]:
    {kb_text}

    [BUSINESS RULES - STRICT SCOPE]:
    1. **SCOPE**: This team ONLY sells **Multi-Day Packages (Group Tours / Private Packages > 3 days)**.
    2. **OUT OF SCOPE**: 
       - **Day Tours (一日游)**: REJECT.
       - **Charter Services (包车/用车)**: REJECT.
       - **Single Items (Ticket/Hotel only)**: REJECT.
    
    [LOGIC BRANCHING]:
    1. **IF User asks for DAY TOUR / CHARTER / SHORT TRIP**:
       - **Action**: Politely DECLINE.
       - **Script**: "抱歉，我们部门专注于【长线多日打包行程】（如5日游、7日游等），暂不承接单独的一日游或包车业务。如果您需要规划完整行程，我很乐意为您介绍我们的热销线路。"
       - **Do NOT** provide prices or plans for charters. Stop there.

    2. **IF User asks about MULTI-DAY PACKAGES**:
       - **Focus**: Search in knowledge base.
       - **Action**: Answer detailly + Extract Product ID -> URL `https://vacations.ctrip.com/travel/detail/{{ID}}`. 
       - **Tone**: Professional, sales-oriented.

    [USER QUERY]: "{query}"

    [OUTPUT FORMAT]:
    <<<REPLY_A>>>
    (Quick Conclusion. If declined, keep it polite but firm. < 60 words)
    <<<END_A>>>
    
    <<<REPLY_B>>>
    (Professional Response:
     - **If Out of Scope**: Explain scope + Ask if user wants a multi-day trip instead.
     - **If In Scope**: Product Details + Cost + LINK.
    )
    <<<END_B>>>

    <<<THOUGHTS>>>
    (中文诊断: 
     1. 意图识别 (包车/一日游 vs 多日游)?
     2. 是否执行了拦截逻辑? 
     3. 链接生成?)
    <<<END_THOUGHTS>>>
    """
    try: return model.generate_content(system_prompt).text
    except Exception as e: return f"AI Error: {str(e)}"

# ==========================================
# 4. 前端主程序 (移除 Tab 2)
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
    st.caption("🎙️ 语音输入: 点击录音，AI 自动识别")
    audio_val = st.audio_input("按住说话 (支持中/英/日混说)")

    # [移除 Tabs] 只保留单一聊天界面
    # tab_chat, tab_plan = st.tabs(["💬 智能问答", "🗺️ 包车规划"]) 
    
    # 直接展示聊天界面
    for msg in st.session_state.messages:
        if msg['role'] == 'user':
            with st.chat_message("user"): st.write(msg['content'])
        elif msg['role'] == 'assistant':
            with st.chat_message("assistant"):
                st.code(msg.get('short', '...'), language=None)
                with st.expander("🔽 查看详细行程 & 诊断"):
                    st.markdown(msg.get('long', ''))
                    st.info(msg.get('thoughts', ''))

    final_query = None
    
    # 优先级 A: 语音输入
    if audio_val:
        with st.spinner("🎙️ 正在听写..."):
            transcribed_text = transcribe_audio(audio_val.getvalue())
            if transcribed_text:
                final_query = transcribed_text
                st.info(f"🗣️ 识别结果: {final_query}")

    # 优先级 B: 文本输入
    text_input = st.chat_input("输入需求... (例: 有没有关西5日游？)")
    if text_input and not final_query:
        final_query = text_input

    if final_query:
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
                    with st.expander("🔽 查看详细回复 & 诊断", expanded=True):
                        st.markdown(r_b) 
                        st.info(th)
                    
                    st.session_state.messages.append({"role": "assistant", "short": r_a, "long": r_b, "thoughts": th})

if __name__ == "__main__":
    main()
