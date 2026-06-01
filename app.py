"""
携程产品服务专家 Co-Pilot V3.1
- 修复 build_context 参数不一致问题
- 支持携程 p{产品ID} 链接
- 支持追问 / 序号指代 / 产品ID精确查询
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
# 2. 知识库加载
# ==========================================
@st.cache_resource(show_spinner="📚 正在加载并解析知识库...")
def load_knowledge_base():
    file_status = []
    full_text = ""

    cwd = os.getcwd()
    file_status.append(f"📂 工作目录: `{cwd}`")

    try:
        all_files = sorted(os.listdir(cwd))
        file_status.append(f"📁 共 {len(all_files)} 个条目")
        for f in all_files[:30]:
            file_status.append(f"   • {f}")
    except Exception as e:
        file_status.append(f"❌ 列目录失败: {e}")

    md_files = list(set(
        glob.glob('*.md') +
        glob.glob('**/*.md', recursive=True) +
        glob.glob('*.MD') +
        glob.glob('*.markdown')
    ))

    if not md_files:
        file_status.append("⚠️ 未找到 .md 文件")
        return None, file_status

    file_status.append(f"🔍 找到 {len(md_files)} 个 MD 文件")

    for f in md_files:
        try:
            with open(f, 'r', encoding='utf-8') as fp:
                content = fp.read()
            full_text += "\n\n" + content
            file_status.append(f"✅ {f} | {round(len(content)/1024, 1)} KB")
        except Exception as e:
            file_status.append(f"❌ {f}: {e}")

    if not full_text.strip():
        file_status.append("❌ MD 文件内容为空")
        return None, file_status

    try:
        pf = ProductFilter(full_text)
        if not pf.products:
            file_status.append("⚠️ 未识别到产品")
            return None, file_status
        file_status.append(f"🎯 已解析 {len(pf.products)} 个产品")
        return pf, file_status
    except Exception as e:
        file_status.append(f"❌ 解析失败: {e}")
        return None, file_status


# ==========================================
# 3. 工具函数
# ==========================================
def build_ctrip_url(pid: str) -> str:
    return f"https://vacations.ctrip.com/travel/detail/p{pid}"


def transcribe_audio(audio_bytes):
    if not GEMINI_KEY:
        return None
    try:
        model = genai.GenerativeModel("gemini-2.0-flash-exp")
        response = model.generate_content([
            {"mime_type": "audio/wav", "data": audio_bytes},
            "请严格转写音频内容为文字，不要回答问题。中文输出中文。"
        ])
        return response.text.strip()
    except Exception as e:
        st.error(f"语音识别失败: {e}")
        return None


SYSTEM_PROMPT = """
你是一名携程产品服务推荐专家，负责根据【精筛产品池】回答客户咨询。

【核心规则】
1. 只允许基于精筛产品池中的原文回答，严禁编造信息。
2. 产品ID本体必须是纯数字，如 23593708。
3. 携程详情链接格式必须是：https://vacations.ctrip.com/travel/detail/p23593708
4. 对客话术中，只展示数字ID，格式必须是【ID:23593708】。
5. 如果问题是“费用包含/费用不含/价格包含/酒店/签证/预订限制/行程详情”等，请优先从产品原文中提炼答案。
6. 如果产品原文未明确写出某项信息，要明确说“当前产品资料中未列明此项”，不要编造。
7. 如果当前上下文模式是追问模式，说明客户在追问上一轮推荐的产品，请直接围绕这些产品继续回答。
8. 单次最多推荐 3 个产品，避免过度发散。
9. 输出对客话术必须简洁、自然、礼貌，可直接发给客人。
10. 如果客户明确问某个产品ID详情，优先回答该产品，而不是重新推荐其他产品。

【输出格式 - 严格使用下面4段标签】
<<<对客话术>>>
（直接发给客人的话术。若提到产品，必须带【ID:数字ID】）
<<<END_对客话术>>>

<<<产品链接>>>
（每行一个，格式：
- 【产品标题】 ID: 23593708 | https://vacations.ctrip.com/travel/detail/p23593708
）
<<<END_产品链接>>>

<<<推荐理由>>>
（给客服自己看：
1. 客户需求理解
2. 命中产品及匹配逻辑
3. 关键卖点/注意事项
4. 潜在追问预判
）
<<<END_推荐理由>>>

<<<内部诊断>>>
（中文简述：
1. 当前问题类型：新咨询 / 产品追问 / ID精确查询 / 序号指代
2. 使用了哪些产品ID
3. 是否能直接回答
4. 若不能直接回答，缺什么信息
）
<<<END_内部诊断>>>
"""


def get_ai_reply(query, history, filtered_context, model_id, session_product_ids, recent_product_ids):
    if not ds_client:
        return None, None

    user_content = f"""{filtered_context}

---

【会话内已推荐过的产品ID】{session_product_ids if session_product_ids else "（暂无）"}
【上一轮推荐产品ID】{recent_product_ids if recent_product_ids else "（暂无）"}

【客户本次咨询】
{query}

请严格按4段标签输出。
再次强调：
- 话术里展示产品时必须写成【ID:23593708】
- 链接必须写成 https://vacations.ctrip.com/travel/detail/p23593708
"""

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    for msg in history[-20:]:
        if msg['role'] == 'user':
            messages.append({"role": "user", "content": msg['content']})
        elif msg['role'] == 'assistant':
            summary = msg.get('script', '')
            prev_ids = msg.get('hit_ids', [])
            if prev_ids:
                summary += f"\n[该轮关联产品ID: {prev_ids}]"
            messages.append({"role": "assistant", "content": summary})

    messages.append({"role": "user", "content": user_content})

    try:
        response = ds_client.chat.completions.create(
            model=model_id,
            messages=messages,
            temperature=0.3,
            max_tokens=4000,
            stream=False
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
    if not raw_text:
        return {
            "script": "（AI 无返回）",
            "links": "",
            "reason": "",
            "diagnosis": "",
            "raw": ""
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


def validate_output_ids(text, pf):
    numeric_ids = set(re.findall(r'\b(\d{6,9})\b', text))
    p_url_ids = set(re.findall(r'/detail/[pP](\d{6,9})', text))
    p_prefixed_ids = set(re.findall(r'[pP](\d{6,9})', text))

    all_ids = numeric_ids | p_url_ids | p_prefixed_ids
    valid = []
    invalid = []

    for pid in all_ids:
        if pid in pf.id_index:
            valid.append(pid)
        else:
            invalid.append(pid)

    return sorted(set(valid)), sorted(set(invalid))


# ==========================================
# 4. 渲染
# ==========================================
def render_assistant_msg(msg, pf=None):
    if pf:
        valid_ids, invalid_ids = validate_output_ids(
            (msg.get('script', '') or '') + "\n" + (msg.get('links', '') or ''),
            pf
        )
        if invalid_ids:
            st.error(f"⚠️ 输出中存在知识库未识别的产品ID，请人工核对：{invalid_ids}")

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

        mode = msg.get('filter_mode', 'unknown')
        st.caption(f"筛选模式：`{mode}`")

        hit_ids = msg.get('hit_ids', [])
        if hit_ids:
            st.caption(f"本轮产品ID：{hit_ids}")

        usage = msg.get('usage_info')
        if usage:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("输入", f"{usage['prompt_tokens']:,}")
            c2.metric("输出", f"{usage['completion_tokens']:,}")
            c3.metric("缓存命中", f"{usage['cache_hit']:,}")
            c4.metric("命中率", f"{usage['hit_rate']:.1f}%")


# ==========================================
# 5. 主程序
# ==========================================
def main():
    pf, file_status = load_knowledge_base()

    if 'messages' not in st.session_state:
        st.session_state.messages = []

    if 'session_product_ids' not in st.session_state:
        st.session_state.session_product_ids = []

    if 'recent_product_ids' not in st.session_state:
        st.session_state.recent_product_ids = []

    if 'total_cost_stats' not in st.session_state:
        st.session_state.total_cost_stats = {
            'calls': 0,
            'total_tokens': 0,
            'cache_hit_total': 0,
            'cache_miss_total': 0
        }

    with st.sidebar:
        st.title("⚙️ 控制台")

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

        st.divider()

        with st.expander(f"🗂️ 上一轮产品池 ({len(st.session_state.recent_product_ids)})", expanded=False):
            if st.session_state.recent_product_ids and pf:
                for pid in st.session_state.recent_product_ids:
                    p = pf.id_index.get(pid)
                    if p:
                        st.caption(f"`{pid}` - {p['title'][:40]}")
            else:
                st.caption("（暂无）")

        with st.expander(f"🧠 会话记忆产品池 ({len(st.session_state.session_product_ids)})", expanded=False):
            if st.session_state.session_product_ids and pf:
                for pid in st.session_state.session_product_ids[-10:]:
                    p = pf.id_index.get(pid)
                    if p:
                        st.caption(f"`{pid}` - {p['title'][:40]}")
            else:
                st.caption("（暂无）")

        st.divider()

        c1, c2 = st.columns(2)
        if c1.button("🗑️ 清空对话", type="primary", use_container_width=True):
            st.session_state.messages = []
            st.session_state.session_product_ids = []
            st.session_state.recent_product_ids = []
            st.rerun()

        if c2.button("📊 重置统计", use_container_width=True):
            st.session_state.total_cost_stats = {
                'calls': 0,
                'total_tokens': 0,
                'cache_hit_total': 0,
                'cache_miss_total': 0
            }
            st.rerun()

        st.caption(f"💬 当前记忆: {len(st.session_state.messages)} 条消息")

        st.divider()

        model_choice = st.radio(
            "🤖 AI 模型",
            ["DeepSeek V4 Pro", "DeepSeek V4 Flash"]
        )
        model_id = "deepseek-v4-pro" if "Pro" in model_choice else "deepseek-v4-flash"
        st.caption(f"Model: `{model_id}`")

        st.divider()
        st.markdown("**🎯 筛选参数**")
        top_k = st.slider("候选产品数量", 3, 15, 8)
        max_chars = st.slider("上下文最大字符", 20000, 150000, 80000, step=10000)

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

        st.divider()
        st.caption(f"DeepSeek: {'🟢 已连接' if ds_client else '🔴 未配置'}")
        st.caption(f"Gemini语音: {'🟢 已连接' if GEMINI_KEY else '🔴 未配置'}")

    st.title("👩‍💼 携程产品服务专家 Co-Pilot")

    if not pf:
        st.error("❌ 知识库未加载成功")
        return

    if not ds_client:
        st.error("❌ DeepSeek API 未配置，请检查 secrets")
        return

    st.caption(
        f"💡 智能预筛选 + 追问记忆模式 | 在售产品 {pf.stats()['total']} 个 | "
        f"支持推荐、详情追问、产品ID精确查询"
    )

    with st.expander("🎙️ 语音输入（可选）"):
        audio_val = st.audio_input("按住录音")

    for msg in st.session_state.messages:
        if msg['role'] == 'user':
            with st.chat_message("user"):
                st.write(msg['content'])
        elif msg['role'] == 'assistant':
            with st.chat_message("assistant"):
                render_assistant_msg(msg, pf)

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
        if not st.session_state.messages or st.session_state.messages[-1].get('content') != final_query:
            st.session_state.messages.append({
                "role": "user",
                "content": final_query
            })

            with st.chat_message("user"):
                st.write(final_query)

            with st.chat_message("assistant"):
                with st.spinner("🔍 正在匹配产品上下文..."):
                    filtered_context, hit_ids, hit_details, mode = pf.build_context(
                        query=final_query,
                        top_k=top_k,
                        max_chars=max_chars,
                        recent_product_ids=st.session_state.recent_product_ids,
                        session_product_ids=st.session_state.session_product_ids,
                    )

                mode_tips = {
                    "new_query": f"🆕 新查询，预筛选命中 {len(hit_details)} 个候选产品",
                    "direct_id": "🎯 检测到产品ID精确查询",
                    "ordinal": "🔢 检测到序号指代，已定位到具体产品",
                    "follow_up_recent": "🔁 检测到追问，已复用上一轮推荐产品",
                    "follow_up_session": "🧠 检测到追问，已复用会话历史产品池",
                    "no_match": "⚠️ 暂未命中明确产品，AI 将引导客户补充信息",
                }

                if mode in ["direct_id", "ordinal", "follow_up_recent", "follow_up_session"]:
                    st.success(mode_tips.get(mode, mode))
                elif mode == "no_match":
                    st.warning(mode_tips.get(mode, mode))
                else:
                    st.info(mode_tips.get(mode, mode))

                if hit_details:
                    with st.expander(f"🎯 本轮产品上下文 ({len(hit_details)})", expanded=False):
                        for d in hit_details:
                            st.caption(
                                f"`{d['id']}` | {d['score']}分 | {d['days']}天 | "
                                f"{d['destination']} | {' '.join(d['reasons'])}"
                            )
                            st.caption(f"  └─ {d['title'][:80]}")

                with st.spinner(f"🤖 {model_choice} 正在生成答复..."):
                    raw, usage_info = get_ai_reply(
                        final_query,
                        st.session_state.messages[:-1],
                        filtered_context,
                        model_id,
                        st.session_state.session_product_ids,
                        st.session_state.recent_product_ids,
                    )

                parsed = parse_reply(raw)

                st.session_state.recent_product_ids = hit_ids[:]

                for pid in hit_ids:
                    if pid not in st.session_state.session_product_ids:
                        st.session_state.session_product_ids.append(pid)

                st.session_state.session_product_ids = st.session_state.session_product_ids[-20:]

                if usage_info:
                    cs = st.session_state.total_cost_stats
                    cs['calls'] += 1
                    cs['total_tokens'] += usage_info['prompt_tokens']
                    cs['cache_hit_total'] += usage_info['cache_hit']
                    cs['cache_miss_total'] += usage_info['cache_miss']

                msg_data = {
                    "role": "assistant",
                    **parsed,
                    "hit_ids": hit_ids,
                    "hit_details": hit_details,
                    "usage_info": usage_info,
                    "filter_mode": mode,
                }

                st.session_state.messages.append(msg_data)
                render_assistant_msg(msg_data, pf)


if __name__ == "__main__":
    main()
