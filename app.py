"""
携程产品服务专家 Co-Pilot - 最终版
- DeepSeek V4 Pro/Flash
- 智能预筛选模式
- 三段式输出 + 内部诊断
- Context Caching 自动启用
- 语音输入 + 10轮上下文记忆
- 知识库加载失败诊断 + 优雅降级
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
# 2. 知识库加载（带诊断 + 失败不缓存）
# ==========================================
@st.cache_resource(show_spinner="📚 正在加载并解析知识库...")
def load_knowledge_base():
    """成功才缓存；失败时返回None但日志带详细诊断"""
    file_status = []
    full_text = ""

    cwd = os.getcwd()
    file_status.append(f"📂 工作目录: `{cwd}`")

    try:
        all_files = sorted(os.listdir(cwd))
        file_status.append(f"📁 目录下共 {len(all_files)} 个条目")
        for f in all_files[:30]:
            file_status.append(f"   • {f}")
    except Exception as e:
        file_status.append(f"❌ 无法列出目录: {e}")

    # 多策略搜索 MD 文件
    md_files = list(set(
        glob.glob('*.md') +
        glob.glob('**/*.md', recursive=True) +
        glob.glob('*.MD') +
        glob.glob('*.markdown')
    ))

    if not md_files:
        file_status.append("⚠️ **未找到任何 .md 文件！**")
        file_status.append("💡 请检查 MD 文件是否在仓库根目录")
        return None, file_status

    file_status.append(f"🔍 找到 {len(md_files)} 个 MD 文件:")

    for f in md_files:
        try:
            with open(f, 'r', encoding='utf-8') as fp:
                content = fp.read()
            full_text += "\n\n" + content
            size_kb = round(len(content) / 1024, 1)
            file_status.append(f"✅ {f} | {size_kb} KB")
        except Exception as e:
            file_status.append(f"❌ {f}: {e}")

    if not full_text.strip():
        file_status.append("❌ 所有 MD 文件均为空")
        return None, file_status

    try:
        pf = ProductFilter(full_text)
        if len(pf.products) == 0:
            file_status.append("⚠️ 解析完成但未识别到产品")
            file_status.append("💡 请确认 MD 内含 `**产品编号**: 数字` 格式")
            return None, file_status
        file_status.append(f"🎯 成功解析 {len(pf.products)} 个产品")
        return pf, file_status
    except Exception as e:
        file_status.append(f"❌ 解析失败: {e}")
        return None, file_status


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
SYSTEM_PROMPT = """你是一名携程国旅高端定制团队的资深产品服务专家。
你的工作是根据客户咨询，从【精筛产品池】中匹配最合适的产品，并生成可直接发送给客人的专业话术。

【工作准则】：
1. 严格基于精筛产品池回答，绝不编造产品信息、价格或行程细节。
2. 如果精筛池中没有完全匹配的产品，要诚实告知客户，并推荐池中最接近的1-2个替代方案。
3. 产品链接统一使用格式：https://vacations.ctrip.com/travel/detail/p{产品ID}
4. 单次回复最多推荐 3 个产品，避免选择困难。
5. 推荐时优先考虑：目的地匹配 > 天数匹配 > 主题/卖点匹配 > 钻级匹配。
6. 如果精筛池为空或提示"未找到匹配"，要主动询问客户补充信息（目的地、天数、预算、出行日期、人数等）。

【输出格式 - 严格遵守这4个标签段】：

<<<对客话术>>>
（直接发给客人的话术，要求：礼貌专业、有亲和力、控制在200字以内、自然口语化、可直接复制粘贴到IM。包含核心推荐+1-2个亮点+引导追问。）
<<<END_对客话术>>>

<<<产品链接>>>
（按推荐优先级列出，每行一个，格式：
- 【产品标题】 https://vacations.ctrip.com/travel/detail/p产品ID
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


def get_ai_reply(query, history, filtered_context, model_id):
    """调用 DeepSeek，filtered_context 只包含筛选后的候选产品"""
    if not ds_client:
        return None, None

    user_content = f"""【精筛产品池】（系统已为本次查询预筛选）：
{filtered_context}

---

【客户本次咨询】: {query}

请按指定格式输出4段内容。"""

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    for msg in history[-20:]:
        if msg['role'] == 'user':
            messages.append({"role": "user", "content": msg['content']})
        elif msg['role'] == 'assistant':
            messages.append({"role": "assistant", "content": msg.get('script', '')})

    messages.append({"role": "user", "content": user_content})

    try:
        response = ds_client.chat.completions.create(
            model=model_id,
            messages=messages,
            temperature=0.4,
            max_tokens=4000,
            stream=False,
        )

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
    if not raw_text:
        return {
            "script": "（AI 无返回）", "links": "", "reason": "",
            "diagnosis": "", "raw": ""
        }

    def extract(tag):
        m = re.search(rf'<<<{tag}>>>([\s\S]*?)<<<END_{tag}>>>', raw_text)
        return m.group(1).strip() if m else ""

    return {
        "script": extract("对客话术") or raw_text[:300],
        "links": extract("产品链接") or "（无匹配产品）",
        "reason": extract("推荐理由") or "（无推荐理由）",
        "diagnosis": extract("内部诊断") or "（无诊断信息）",
        "raw": raw_text,
    }


# ==========================================
# 5. UI 渲染辅助
# ==========================================
def render_assistant_msg(msg):
    """统一渲染 AI 回复"""
    st.markdown("##### 📋 对客话术（一键复制发送给客人）")
    st.code(msg.get('script', ''), language=None)

    col1, col2 = st.columns([1, 1])
    with col1:
        st.markdown("##### 🔗 推荐产品链接")
        st.markdown(msg.get('links', '') or "_（无）_")
    with col2:
        st.markdown("##### 💡 推荐理由")
        st.markdown(msg.get('reason', '') or "_（无）_")

    with st.expander("🔍 内部诊断（仅客服可见）"):
        st.info(msg.get('diagnosis', '') or "_（无）_")

        usage = msg.get('usage_info')
        if usage:
            st.caption("**本轮 Token 使用：**")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("输入", f"{usage['prompt_tokens']:,}")
            c2.metric("输出", f"{usage['completion_tokens']:,}")
            c3.metric("缓存命中", f"{usage['cache_hit']:,}")
            c4.metric("命中率", f"{usage['hit_rate']:.1f}%")

        hits = msg.get('hit_details')
        if hits:
            st.caption("**预筛选命中产品：**")
            for d in hits:
                st.caption(
                    f"`{d['id']}` | {d['score']}分 | {d['days']}天 | "
                    f"{d['destination']} | {' '.join(d['reasons'])}"
                )


# ==========================================
# 6. 主程序
# ==========================================
def main():
    # 加载知识库
    pf, file_status = load_knowledge_base()

    # 初始化状态
    if 'messages' not in st.session_state:
        st.session_state.messages = []
    if 'total_cost_stats' not in st.session_state:
        st.session_state.total_cost_stats = {
            'calls': 0, 'total_tokens': 0,
            'cache_hit_total': 0, 'cache_miss_total': 0
        }

    # ===================== 侧边栏 =====================
    with st.sidebar:
        st.title("⚙️ 控制台")

        # 知识库状态
        with st.expander("📚 知识库状态", expanded=(pf is None)):
            if pf:
                stats = pf.stats()
                c1, c2 = st.columns(2)
                c1.metric("产品总数", stats['total'])
                c2.metric("目的地数", stats['destinations'])
                st.caption(f"平均行程天数: {stats['avg_days']}天")
                st.divider()

            for s in file_status:
                if "✅" in s or "🎯" in s:
                    st.success(s)
                elif "❌" in s:
                    st.error(s)
                elif "⚠️" in s:
                    st.warning(s)
                else:
                    st.caption(s)

            if st.button("🔄 重新加载知识库", use_container_width=True):
                load_knowledge_base.clear()
                st.rerun()

        # 操作按钮
        st.divider()
        c1, c2 = st.columns(2)
        if c1.button("🗑️ 清空对话", type="primary", use_container_width=True):
            st.session_state.messages = []
            st.rerun()
        if c2.button("📊 重置统计", use_container_width=True):
            st.session_state.total_cost_stats = {
                'calls': 0, 'total_tokens': 0,
                'cache_hit_total': 0, 'cache_miss_total': 0
            }
            st.rerun()

        st.caption(f"💬 当前记忆: {len(st.session_state.messages)} 条（≈{len(st.session_state.messages)//2} 轮）")

        # 模型选择
        st.divider()
        model_choice = st.radio(
            "🤖 AI 模型",
            ["DeepSeek V4 Pro", "DeepSeek V4 Flash"],
            help="Pro：推理强、话术精；Flash：响应快、成本低"
        )
        model_id = "deepseek-v4-pro" if "Pro" in model_choice else "deepseek-v4-flash"
        st.caption(f"Model: `{model_id}`")

        # 筛选参数
        st.divider()
        st.markdown("**🎯 筛选参数**")
        top_k = st.slider("候选产品数量", 3, 15, 8)
        max_chars = st.slider("上下文最大字符", 20000, 150000, 80000, step=10000)

        # 成本监控
        st.divider()
        st.markdown("**💰 本次会话成本监控**")
        cs = st.session_state.total_cost_stats
        if cs['calls'] > 0:
            overall_hit_rate = cs['cache_hit_total'] / max(cs['total_tokens'], 1) * 100
            st.metric("调用次数", cs['calls'])
            st.metric("累计输入 Tokens", f"{cs['total_tokens']:,}")
            st.metric("缓存命中率", f"{overall_hit_rate:.1f}%")
            miss_cost = cs['cache_miss_total'] * 4 / 1_000_000
            hit_cost = cs['cache_hit_total'] * 0.4 / 1_000_000
            st.metric("估算成本(¥)", f"{miss_cost + hit_cost:.4f}")
        else:
            st.caption("暂无调用记录")

        # API 状态
        st.divider()
        st.caption("**🔌 API 状态**")
        st.caption(f"DeepSeek: {'🟢 已连接' if ds_client else '🔴 未配置'}")
        st.caption(f"Gemini 语音: {'🟢 已连接' if GEMINI_KEY else '🔴 未配置'}")

    # ===================== 主界面 =====================
    st.title("👩‍💼 携程产品服务专家 Co-Pilot")

    # 兜底：知识库未加载
    if not pf:
        st.error("❌ **知识库未加载**")
        st.markdown("""
        请检查：
        1. ✅ MD 文件已推送到 GitHub 仓库**根目录**
        2. ✅ Streamlit Cloud 已 Reboot（Manage app → Reboot app）
        3. ✅ 文件未被 `.gitignore` 忽略

        👈 **请展开左侧"知识库状态"查看详细诊断信息**，或点击"🔄 重新加载知识库"
        """)
        return

    if not ds_client:
        st.error("❌ DeepSeek API 未配置，请在 `.streamlit/secrets.toml` 设置 `DEEPSEEK_API_KEY`")
        return

    st.caption(f"💡 智能预筛选模式 | 在售产品 {pf.stats()['total']} 个 | 输入客户咨询，AI 自动匹配并生成可发送话术")

    # 语音输入
    with st.expander("🎙️ 语音输入（可选）"):
        audio_val = st.audio_input("按住录音 - 支持中/英/日混说")

    # 历史消息渲染
    for msg in st.session_state.messages:
        if msg['role'] == 'user':
            with st.chat_message("user"):
                st.write(msg['content'])
        elif msg['role'] == 'assistant':
            with st.chat_message("assistant"):
                render_assistant_msg(msg)

    # 输入处理
    final_query = None

    if audio_val:
        with st.spinner("🎙️ 语音识别中..."):
            transcribed = transcribe_audio(audio_val.getvalue())
            if transcribed:
                final_query = transcribed
                st.info(f"🗣️ 识别结果: {transcribed}")

    text_input = st.chat_input("输入客户咨询...（例：济州岛4天3晚有什么推荐）")
    if text_input and not final_query:
        final_query = text_input

    if final_query:
        # 去重
        if not st.session_state.messages or \
           st.session_state.messages[-1].get('content') != final_query:

            st.session_state.messages.append({"role": "user", "content": final_query})

            with st.chat_message("user"):
                st.write(final_query)

            with st.chat_message("assistant"):
                # Step 1: 预筛选
                with st.spinner("🔍 正在精筛匹配产品..."):
                    filtered_context, hit_ids, hit_details = pf.build_context(
                        final_query, top_k=top_k, max_chars=max_chars
                    )

                if hit_details:
                    with st.expander(f"🎯 预筛选命中 {len(hit_details)} 个候选产品", expanded=False):
                        for d in hit_details:
                            st.caption(
                                f"`{d['id']}` | {d['score']}分 | {d['days']}天 | "
                                f"{d['destination']} | {' '.join(d['reasons'])}"
                            )
                            st.caption(f"  └─ {d['title'][:60]}")
                else:
                    st.warning("⚠️ 预筛选未命中明确匹配，AI 将引导客户补充信息")

                # Step 2: 调用 DeepSeek
                with st.spinner(f"🤖 {model_choice} 正在生成话术..."):
                    raw, usage_info = get_ai_reply(
                        final_query,
                        st.session_state.messages[:-1],
                        filtered_context,
                        model_id
                    )

                if raw is None:
                    st.error("AI 调用失败")
                    return

                parsed = parse_reply(raw)

                # 更新成本统计
                if usage_info:
                    cs = st.session_state.total_cost_stats
                    cs['calls'] += 1
                    cs['total_tokens'] += usage_info['prompt_tokens']
                    cs['cache_hit_total'] += usage_info['cache_hit']
                    cs['cache_miss_total'] += usage_info['cache_miss']

                # 存入历史 + 渲染
                msg_data = {
                    "role": "assistant",
                    **parsed,
                    "hit_details": hit_details,
                    "usage_info": usage_info,
                }
                st.session_state.messages.append(msg_data)
                render_assistant_msg(msg_data)


if __name__ == "__main__":
    main()
