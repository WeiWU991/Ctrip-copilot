import streamlit as st
import glob
import os
import google.generativeai as genai
import re

# ==========================================
# 1. 基础配置
# ==========================================
st.set_page_config(page_title="携程产品服务专家 Co-Pilot", page_icon="👩‍💼", layout="wide")

# 获取 API Key
AI_KEY = st.secrets.get("GOOGLE_API_KEY", "")

if AI_KEY:
    try: 
        genai.configure(api_key=AI_KEY)
    except: 
        pass

# ==========================================
# 2. 知识库加载器 (仅保留 Markdown 全量文档)
# ==========================================
class KnowledgeBase:
    def __init__(self):
        self.knowledge_text = ""
        self.file_status = []

    def load_all(self):
        self.knowledge_text = ""
        self.file_status = []
        
        # 核心：读取当前目录下所有的 .md 文件 (如：携程产品行程详情_全量V2.md)
        md_files = glob.glob('*.md')
        for f in md_files:
            try:
                with open(f, 'r', encoding='utf-8') as file:
                    content = file.read()
                self.knowledge_text += f"\n\n=== 知识库文档: {os.path.basename(f)} ===\n{content}"
                self.file_status.append(f"✅ 成功加载产品明细: {f}")
            except Exception as e: 
                self.file_status.append(f"❌ 读取失败: {f} (错误信息: {str(e)})")
        
        if not md_files:
            self.file_status.append("⚠️ 未在目录下检测到 .md 知识库文件，请确保全量产品文件已上传。")

# ==========================================
# 3. AI 核心大脑
# ==========================================
def get_ai_reply(query, history, kb_text):
    if not AI_KEY: 
        return "❌ 错误：未配置 API Key，请检查配置。"
    
    # 按照需求：全局统一使用 gemini-3.5-flash
    model_id = "models/gemini-3.5-flash"
    
    try: 
        model = genai.GenerativeModel(model_id)
    except Exception as e: 
        return f"模型初始化失败: {e}"
    
    # 组装上下文记忆 (保留最近 5 轮对话以控制 Token)
    history_str = ""
    for msg in history[-5:]: 
        if msg['role'] == 'user': 
            history_str += f"客人: {msg['content']}\n"
        elif msg['role'] == 'assistant': 
            history_str += f"客服(你): {msg.get('reply', '')}\n"

    # [核心 PROMPT 设计]
    system_prompt = f"""
    你是携程资深、金牌产品服务与推荐专家。你的唯一目标是为咨询的客人提供热情、周到、完美的服务体验。

    [当前对话记忆]:
    {history_str}

    [内部产品知识库 (包含全量产品信息)]:
    {kb_text}

    [你的核心职责与规则]:
    1. **精准解答与推荐**：仔细分析客人的需求（如目的地、游玩天数、酒店偏好、是否有长辈/儿童等），从【内部产品知识库】中检索出最符合的产品进行推荐。
    2. **绝不捏造产品**：所有推荐的产品详情（如产品编号、行程安排、包含内容等）必须严格来源于【内部产品知识库】，不可瞎编。如果找不到完全匹配的，请委婉向客人说明并推荐最相近的产品。
    3. **话术分离设计**：你的输出需要拆分为两部分：
       - 第一部分是给人工客服看的【诊断与内部思路】；
       - 第二部分是人工客服【直接复制发送给客人的话术】。
    4. **话术标准**：发送给客人的话术必须语气亲切、专业。善用 Emoji 表情，使用清晰的列表或分段排版。务必在话术中带上推荐的**产品编号**或明确的**行程天数及亮点**，方便促单。

    [客人当前消息]: "{query}"

    [严格的输出格式]:
    请严格遵循以下标签格式输出，不要更改标签：
    <<<THOUGHTS>>>
    (在这里写下你的内部诊断：客人需求是什么？你在知识库里找到了哪些产品ID？为什么推荐它们？有没有需要提醒客服注意的排雷点？)
    <<<END_THOUGHTS>>>
    
    <<<REPLY>>>
    (在这里写下直接发给客人的话术。包含：亲切问候 -> 需求认同 -> 产品推荐(附带产品编号和核心卖点) -> 进一步的贴心服务或发问引导。)
    <<<END_REPLY>>>
    """
    
    try: 
        response = model.generate_content(system_prompt)
        return response.text
    except Exception as e: 
        return f"AI 响应错误: {str(e)}"

# ==========================================
# 4. 前端 UI
# ==========================================
def main():
    # 初始化状态
    if 'kb' not in st.session_state:
        kb = KnowledgeBase()
        kb.load_all()
        st.session_state.kb = kb
    if 'messages' not in st.session_state: 
        st.session_state.messages = []

    # --- 侧边栏 ---
    with st.sidebar:
        st.title("⚙️ 专家控制台")
        with st.expander("📚 知识库状态", expanded=True):
            for s in st.session_state.kb.file_status:
                if "✅" in s: st.success(s)
                elif "❌" in s: st.error(s)
                else: st.warning(s)
        
        st.divider()
        if st.button("🗑️ 清空对话 (接待下一位客人)", type="primary", use_container_width=True):
            st.session_state.messages = []
            st.rerun()
        
        st.divider()
        st.markdown("### 🤖 模型设置")
        # 直接写死显示当前使用的模型，不需要选择器了
        st.info("⚡ 当前驱动模型: **Gemini 3.5 Flash**\n\n(已优化长文本检索与响应速度)")

    # --- 主界面 ---
    st.title("👩‍💼 携程产品服务专家 Co-Pilot")
    st.caption("智能读取全量产品库，精准推荐并一键生成高转化客服话术。")
    st.divider()

    # 渲染历史对话
    for msg in st.session_state.messages:
        if msg['role'] == 'user':
            with st.chat_message("user"): 
                st.write(msg['content'])
        elif msg['role'] == 'assistant':
            with st.chat_message("assistant"):
                st.markdown("📋 **提供给客人的话术 (可直接复制):**")
                # 使用 markdown 语言框方便右上角一键复制
                st.code(msg.get('reply', '...'), language="markdown") 
                with st.expander("🧠 查看 AI 内部诊断与匹配思路"):
                    st.markdown(msg.get('thoughts', '无记录'))

    # 输入框
    user_query = st.chat_input("输入客人的问题或需求... (例如：客人带小孩去大阪，求推荐有环球影城的线路)")

    if user_query:
        # 显示用户输入
        st.session_state.messages.append({"role": "user", "content": user_query})
        with st.chat_message("user"): 
            st.write(user_query)

        # 处理 AI 回复
        with st.chat_message("assistant"):
            with st.spinner("专家正在检索所有产品，为您定制服务话术..."):
                # 调用时去掉了 model_choice 参数
                raw_res = get_ai_reply(user_query, st.session_state.messages, st.session_state.kb.knowledge_text)
                
                # 正则提取内部思路和话术
                try: 
                    thoughts = re.search(r'<<<THOUGHTS>>>([\s\S]*?)<<<END_THOUGHTS>>>', raw_res).group(1).strip()
                except: 
                    thoughts = "解析诊断思路失败，可能是 AI 格式未严格遵守。"
                
                try: 
                    reply = re.search(r'<<<REPLY>>>([\s\S]*?)<<<END_REPLY>>>', raw_res).group(1).strip()
                except: 
                    # 容错：如果没按格式生成，则直接显示全量文本
                    reply = raw_res.replace('<<<THOUGHTS>>>', '').replace('<<<END_THOUGHTS>>>', '')

                # 渲染结果
                st.markdown("📋 **提供给客人的话术 (点击右上角图标一键复制):**")
                st.code(reply, language="markdown")
                
                with st.expander("🧠 查看 AI 内部诊断与匹配思路", expanded=True):
                    st.markdown(thoughts)
                
                # 存入历史状态
                st.session_state.messages.append({
                    "role": "assistant", 
                    "reply": reply, 
                    "thoughts": thoughts
                })

if __name__ == "__main__":
    main()
