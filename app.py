"""
携程产品服务专家 Co-Pilot
- DeepSeek V4 Pro/Flash
- 智能预筛选模式（不发全量知识库）
- 三段式输出 + 内部诊断
- Context Caching 自动启用
- 语音输入 + 10轮上下文记忆
"""
import streamlit as st
import glob
import os
import re
from openai import OpenAI
import google.generativeai as genai

from product_filter import ProductFilter

# ==========================================
# 1. 基础配置
# ==========================================
st.set_page_config(
    page_title="携程产品服务专家 Co-Pilot",
    page_icon="👩‍💼",
    layout="wide"
)

DEEPSEEK_KEY = st.secrets.get("DEEPSEEK_API_KEY", "")
GEMINI_KEY = st.secrets.get("GOOGLE_API_KEY", "")

ds_client = None
if DEEPSEEK_KEY:
    try:
        ds_client = OpenAI(
            api_key=DEEPSEEK_KEY,
            base_url="https://api.deepseek.com/v1"
        )
    except Exception as e:
        st.error(f"DeepSeek 初始化失败: {e}")

if GEMINI_KEY:
    try:
        genai.configure(api_key=GEMINI_KEY)
    except:
        pass


# ==========================================
# 2. 知识库加载（带缓存）
# ==========================================
@st.cache_resource(show_spinner="📚 正在加载并解析知识库...")
def load_knowledge_base():
    """全局只加载一次，避免每次rerun都重新解析"""
    md_files = glob.glob('*.md')
    file_status = []
    full_text = ""

    if not md_files:
        return None, ["⚠️ 当前目录未找到 .md 文件"]

    for f in md_files:
        try:
            content = open(f, 'r', encoding='utf-8').read()
            full_text += "\n\n" + content
            size_kb = round(len(content) / 1024, 1)
            file_status.append(f"✅ {f} | {size_kb} KB")
        except Exception as e:
            file_status.append(f"❌ {f}: {e}")

    pf = ProductFilter(full_text)
    return pf, file_status


# ==========================================
# 3. 语音转文字
# ==========================================
def transcribe_audio(audio_bytes):
    if not GEMINI_KEY:
        return None
    try:
        model = genai.GenerativeModel("gemini-2.0-flash-exp")
        response = model.generate_content([
            {"mime_type": "audio/wav", "data": audio_bytes},
            "请严格转写音频内容为文字，不要回答任何问题。中文输出中文。"
        ])
        return response.text.strip()
    except Exception as e:
        st.error(f"语音识别失败: {e}")
        return None


# ==========================================
# 4. DeepSeek 对话核心
# ==========================================
def get_ai_reply(query, history, filtered_context, model_id):
    """调用 DeepSeek，filtered_context 只包含筛选后的候选产品"""
    if not ds_client:
        return None, None

    # System Prompt：放在最前面，命中 Context Caching
    system_prompt = """你是一名携程国旅高端定制团队的资深产品服务专家。
你的工作是根据客户咨询，从【精筛产品池】中匹配最合适的产品，并生成可直接发送给客人的专业话术。

【工作准则】：
1. 严格基于精筛产品池回答，绝不编造产品信息、价格或行程细节。
2. 如果精筛池中没有完全匹配的产品，要诚实告知客户，并推荐池中最接近的1-2个替代方案。
3. 产品链接统一使用格式：https://vacations.ctrip.com/travel/detail/{产品ID}
4. 单次回复最多推荐 3 个产品，避免选择困难。
5. 推荐时优先考虑：目的地匹配 > 天数匹配 > 主题/卖点匹配 > 钻级匹配。
6. 如果精筛池为空或提示"未找到匹配"，要主动询问客户补充信息（目的地、天数、预算、出行日期、人数等）。

【输出格式 - 严格遵守这4个标签段】：

<<<对客话术>>>
（直接发给客人的话术，要求：礼貌专业、有亲和力、控制在200字以内、自然口语化、可直接复制粘贴到IM。包含核心推荐+1-2个亮点+引导追问。）
<<<END_对客话术>>>

<<<产品链接>>>
（按推荐优先级列出，每行一个，格式：
- 【产品标题】 https://vacations.ctrip.com/travel/detail/产品ID
）
<<<END_产品链接>>>

<<<推荐理由>>>
（给客服自己看，简明列出：
1. 客户需求理解
2. 为什么推荐这几个产品（匹配点）
3. 关键卖点/注意事项（行程亮点、酒店、签证、餐食等）
4. 潜在追问预判）
<<<END_推荐理由>>>

<<<内部诊断>>>
（中文简要：
1. 意图识别结果
2. 精筛池命中情况
3. 信息完整度评估
4. 若信息缺失需要补充什么）
<<<END_内部诊断>>>
"""

    # 把筛选后的产品上下文作为 user 消息的一部分发送
    # （而不是塞进 system prompt，这样 system prompt 保持稳定，更容易命中缓存）
    user_content = f"""【精筛产品池】（系统已为本次查询预筛选）：
{filtered_context}

---

【客户本次咨询】: {query}

请按指定格式输出4段内容。"""

    messages = [{"role": "system", "content": system_prompt}]

    # 历史对话（最近10轮 = 20条消息）
    for msg in history[-20:]:
        if msg['role'] == 'user':
            messages.append({"role": "user", "content": msg['content']})
        elif msg['role'] == 'assistant':
            messages.append({
                "role": "assistant",
                "content": msg.get('script', '')
            })

    messages.append({"role": "user", "content": user_content})

    try:
        response = ds_client.chat.completions.create(
            model=model_id,
            messages=messages,
            temperature=0.4,
            max_tokens=4000,
            stream=False,
        )

        # 提取 usage（包含缓存命中信息）
        usage = response.usage
        usage_info = {
            'prompt_tokens': usage.prompt_tokens,
            'completion_tokens': usage.completion_tokens,
            'cache_hit': getattr(usage, 'prompt_cache_hit_tokens', 0),
            'cache_miss': getattr(usage, 'prompt_cache_miss_tokens', 0),
        }
        usage_info['hit_rate'] = (
            usage_info['cache_hit'] / usage_info['prompt_tokens'] * 100
            if usage_info['prompt_tokens'] else 0
        )

        return response.choices[0].message.content, usage_info

    except Exception as e:
        return f"❌ DeepSeek API 调用失败: {str(e)}", None


def parse_reply(raw_text):
    """解析四段式输出"""
    def extract(tag):
        m = re.search(rf'<<<{tag}>>>([\s\S]*?)<<<END_{tag}>>>', raw_text)
        return m.group(1).strip() if m else ""

    return {
        "script": extract("对客话术") or "（未生成话术，请重试）",
        "links": extract("产品链接") or "（无匹配产品）",
        "reason": extract("推荐理由") or "（
