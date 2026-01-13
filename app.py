import streamlit as st
import glob
import os
import google.generativeai as genai

# ==========================================
# 1. 初始化
# ==========================================
st.set_page_config(page_title="Ctrip CS Copilot", page_icon="👩‍💼", layout="wide")

# 获取密钥
AI_KEY = st.secrets.get("GOOGLE_API_KEY", "")

# 配置 AI
if AI_KEY:
    try:
        genai.configure(api_key=AI_KEY)
    except Exception as e:
        st.error(f"API Key 配置错误: {e}")

# ==========================================
# 2. 纯文本知识库加载器 (Markdown Only)
# ==========================================
class KnowledgeBase:
    def __init__(self):
        self.full_knowledge = ""
        self.file_list = []

    def load_markdowns(self):
        """读取所有 .md 文件，拼接成一个巨大的知识文本"""
        md_files = glob.glob('*.md')
        all_content = []
        
        for f in md_files:
            try:
                with open(f, 'r', encoding='utf-8') as file:
                    # 给每个文件加个标题，方便 AI 知道来源
                    content = f"\n\n=== 来源文档: {f} ===\n{file.read()}"
                    all_content.append(content)
                    self.file_list.append(f)
            except Exception as e:
                st.warning(f"无法读取文件 {f}: {e}")
        
        self.full_knowledge = "\n".join(all_content)
        return len(md_files)

# ==========================================
# 3. 业务逻辑 (Agent)
# ==========================================
def get_ai_response(query, knowledge_text, model_name="flash"):
    if not AI_KEY: return "❌ 请先配置 GOOGLE_API_KEY"
    
    # 映射模型
    model_id = "gemini-3-pro-preview" if model_name == "pro" else "gemini-3-flash-preview"
    
    try:
        model = genai.GenerativeModel(model_id)
        
        # 核心 Prompt：赋予 AI 销售身份和业务规则
        prompt = f"""
        Role: You are a Senior Travel Consultant at **Ctrip (携程)**.
        Target: Answer customer questions based ONLY on the provided Knowledge Base.
        
        [Knowledge Base]:
        {knowledge_text}
        
        [Business Rules]:
        1. **Price Integrity**: Only quote prices found in the documents. If not found, say "请联系产品经理确认".
        2. **Disclaimer**: 
           - For One-day tours, add: "价格已含车费，最终余位需后台确认".
           - For Multi-day tours, add: "起价仅供参考，请点击链接确认实时库存".
        3. **Tone**: Professional, warm, helpful.
        
        [User Question]:
        "{query}"
        
        [Output Requirements]:
        Please output TWO parts strictly separated by lines:
        
        ---REPLY_A---
        (A short version for quick copy-paste, under 60 words)
        ---END_A---
        
        ---REPLY_B---
        (A professional detailed version with emojis and selling points)
        ---END_B---
        """
        
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"AI 思考超时或出错: {e}"

# ==========================================
# 4. 前端界面
# ==========================================
def main():
    # 侧边栏：状态与设置
    with st.sidebar:
        st.title("⚙️ 控制台")
        
        # 加载知识库
        if 'kb' not in st.session_state:
            kb = KnowledgeBase()
            count = kb.load_markdowns()
            st.session_state.kb = kb
            st.session_state.file_count = count
        
        st.success(f"📚 已加载 {st.session_state.file_count} 个知识文档")
        with st.expander("查看已加载文件列表"):
            for f in st.session_state.kb.file_list:
                st.write(f"- {f}")
        
        st.divider()
        model_choice = st.radio("AI 模型", ["gemini-3-flash-preview", "gemini-3-pro-preview"])
        model_key = "flash" if "Fast" in model_choice else "pro"
        
        if st.button("🔄 刷新知识库"):
            st.cache_data.clear()
            st.session_state.pop('kb', None)
            st.rerun()

    # 主界面
    st.title("👩‍💼 Ctrip 客服 Copilot (纯净版)")
    st.caption("💡 核心逻辑：读取所有 Markdown 文档 -> AI 理解并回答")

    # 聊天窗口
    user_input = st.chat_input("输入客人问题... (如: 富士山一日游多少钱？包车怎么算？)")

    if user_input:
        with st.chat_message("user"):
            st.write(user_input)

        with st.chat_message("assistant"):
            with st.spinner("🧠 正在检索文档并生成话术..."):
                # 直接调用 AI
                response_text = get_ai_response(
                    user_input, 
                    st.session_state.kb.full_knowledge, 
                    model_key
                )
                
                # 简单的文本切割显示
                if "---REPLY_A---" in response_text:
                    parts = response_text.split("---REPLY_A---")
                    part_rest = parts[1].split("---END_A---")
                    reply_a = part_rest[0].strip()
                    
                    if "---REPLY_B---" in part_rest[1]:
                        part_b_rest = part_rest[1].split("---REPLY_B---")
                        part_b_end = part_b_rest[1].split("---END_B---")
                        reply_b = part_b_end[0].strip()
                    else:
                        reply_b = part_rest[1].strip()
                else:
                    # 兜底：如果 AI 没按格式输出，直接显示全文
                    reply_a = response_text
                    reply_b = "AI 未生成详细版"

            # 显示结果 (强制代码块，保证有复制按钮)
            st.subheader("📋 极简回复 (点击右上角复制)")
            st.code(reply_a, language=None)
            
            st.subheader("💼 详细回复")
            st.code(reply_b, language=None)
            
            # 调试信息：看看 AI 参考了哪些内容
            with st.expander("🔍 AI 参考的原文片段 (Debug)"):
                # 简单高亮一下包含关键词的文档片段
                keywords = user_input[:2]
                st.write(f"正在文档中寻找 '{keywords}' 相关内容...")

if __name__ == "__main__":
    main()


