"""
携程产品服务专家 Co-Pilot V4
- 双知识库：产品 + 签证
- DeepSeek V4 Pro/Flash
- 自动意图分流（产品咨询 / 签证咨询）
- 签证问答严格锁定原文，零编造
- 支持追问、序号指代、产品ID精确查询
"""

import streamlit as st
import glob
import os
import re
from openai import OpenAI
import google.generativeai as genai

from product_filter import ProductFilter
from visa_filter import VisaFilter


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
        ds_client = OpenAI(api_key=DEEPSEEK_KEY, base_url="https://api.deepseek.com/v1")
    except Exception as e:
        st.error(f"DeepSeek 初始化失败: {e}")

if GEMINI_KEY:
    try:
        genai.configure(api_key=GEMINI_KEY)
    except:
        pass


# ==========================================
# 2. 双知识库加载
# ==========================================
@st.cache_resource(show_spinner="📚 正在加载知识库...")
def load_knowledge_base():
    """同时加载产品库 + 签证库"""
    file_status = []
    product_text = ""
    visa_text = ""

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
        glob.glob('*.md') + glob.glob('**/*.md', recursive=True) +
        glob.glob('*.MD') + glob.glob('*.markdown')
    ))

    if not md_files:
        file_status.append("⚠️ 未找到 .md 文件")
        return None, None, file_status

    file_status.append(f"🔍 找到 {len(md_files)} 个 MD 文件")

    for f in md_files:
        try:
            with open(f, 'r', encoding='utf-8') as fp:
                content = fp.read()

            is_visa = "**签证编号**" in content
            is_product = "**产品编号**" in content

            if is_visa and not is_product:
                visa_text += "\n\n" + content
                file_status.append(f"📋 [签证库] {f} | {round(len(content)/1024,1)} KB")
            elif is_product and not is_visa:
                product_text += "\n\n" + content
                file_status.append(f"📦 [产品库] {f} | {round(len(content)/1024,1)} KB")
            elif is_visa and is_product:
                visa_text += "\n\n" + content
                product_text += "\n\n" + content
                file_status.append(f"📦📋 [混合] {f} | {round(len(content)/1024,1)} KB")
            else:
                product_text += "\n\n" + content
                file_status.append(f"❓ [未识别→产品] {f} | {round(len(content)/1024,1)} KB")
        except Exception as e:
            file_status.append(f"❌ {f}: {e}")

    pf = None
    vf = None

    if product_text.strip():
        try:
            pf = ProductFilter(product_text)
            file_status.append(f"🎯 产品库解析: {len(pf.products)} 个产品")
        except Exception as e:
            file_status.append(f"❌ 产品解析失败: {e}")

    if visa_text.strip():
        try:
            vf = VisaFilter(visa_text)
            file_status.append(f"🎯 签证库解析: {len(vf.entries)} 个签证文档")
        except Exception as e:
            file_status.append(f"❌ 签证解析失败: {e}")

    return pf, vf, file_status


# ==========================================
# 3. 工具
# ==========================================
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


# ==========================================
# 4. 两套 System Prompt
# ==========================================
PRODUCT_SYSTEM_PROMPT = """
你是一名携程产品服务推荐专家，负责根据【精筛产品池】回答客户产品咨询。

【核心规则】
1. 只允许基于精筛产品池中的原文回答，严禁编造信息。
2. 产品ID本体必须是纯数字，如 23593708。
3. 携程详情链接格式必须是：https://vacations.ctrip.com/travel/detail/p23593708
4. 对客话术中，产品名后必须带【ID:23593708】格式。
5. 若问"费用包含/不含/酒店/签证/预订限制"等详情，优先从产品原文提炼答案。
6. 如果产品原文未明确写出某项信息，要明确说"当前产品资料中未列明此项"，不要编造。
7. 单次最多推荐 3 个产品。
8. 输出对客话术必须简洁、自然、礼貌，可直接发给客人。

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
（给客服自己看：1.需求理解 2.匹配逻辑 3.关键卖点 4.潜在追问预判）
<<<END_推荐理由>>>

<<<内部诊断>>>
（中文简述：1.问题类型 2.使用ID 3.是否可直接回答 4.信息缺失项）
<<<END_内部诊断>>>
"""


VISA_SYSTEM_PROMPT = """
你是一名携程签证业务专员，负责严格依据【签证操作手册原文】回答客户签证咨询。

【🔴 绝对铁律 - 违反任何一条都属严重事故】
1. **零编造**：所有材料、金额、有效期、停留天数、领区划分必须100%引用文档原文。
2. **零承诺**：绝对不能承诺出签率、出签时间、加急、保过、内部渠道等任何文档未明示的内容。
3. **缺失即转人工**：如果客户问的细节文档中没有明确写，必须回复"该项信息暂未明确，建议转接签证专员核实"，不要凭经验或想象补充。
4. **先确认领区**：不同领区材料差异巨大，必须先确认客户户籍/常住地，再回复对应领区要求。
5. **不混淆类型**：单次/三年/五年/团签/个签 是完全不同的标准，不要张冠李戴。

【信息收集流程】
当客户咨询签证时，必须按文档流程先收集以下基础信息：
1. 户籍地（身份证和户口本上显示的）
2. 常住地（生活工作的地方）
3. 办理什么国家签证？
4. 签证类型（个人签证/团签 / 单次/三年多次/五年多次）
5. 客户身份（在职/学生/退休/家庭主妇/自由职业）

【话术风格】
- 礼貌专业、有耐心
- 涉及金额、天数、材料名称时必须精确（不能模糊说"大概"、"差不多"）
- 单次回复控制在 300 字以内
- 复杂场景分点列出，方便客户对照执行

【输出格式 - 严格使用下面4段标签】

<<<对客话术>>>
（直接发给客人的话术，要专业、精确、可一键复制粘贴。涉及多步流程时分点列出。
若信息不足，礼貌引导客户补充：户籍地、常住地、办理国家、签证类型）
<<<END_对客话术>>>

<<<产品链接>>>
（签证问题通常不附产品链接。如必要可填：
- 【相关签证文档】 编号：VISA-XX-XXX
）
<<<END_产品链接>>>

<<<推荐理由>>>
（给客服自己看：
1. 客户问题核心
2. 引用的文档编号及关键段落
3. 是否需要补充收集信息
4. 风险点提示（敏感地区/敏感人群/特殊领区要求）
）
<<<END_推荐理由>>>

<<<内部诊断>>>
（中文简述：
1. 问题类型：签证-哪个国家-哪类问题
2. 引用了哪些签证文档ID
3. 是否能从文档中直接回答
4. 若不能，缺什么信息需要客户补充
）
<<<END_内部诊断>>>
"""


def get_ai_reply(query, history, filtered_context, model_id, mode_category):
    """mode_category: 'visa' 或 'product'"""
    if not ds_client:
        return None, None

    system_prompt = VISA_SYSTEM_PROMPT if mode_category == 'visa' else PRODUCT_SYSTEM_PROMPT

    user_content = f"""{filtered_context}

---

【客户本次咨询】
{query}

请严格按4段标签输出。"""

    if mode_category == 'product':
        user_content += "\n再次强调：话术里展示产品时必须写成【ID:23593708】，链接必须写成 https://vacations.ctrip.com/travel/detail/p23593708"
    else:
        user_content += "\n再次强调：签证回答必须严格引用文档原文，文档没写的信息一律回复'建议转接签证专员核实'。"

    messages = [{"role": "system", "content": system_prompt}]

    for msg in history[-20:]:
        if msg['role'] == 'user':
            messages.append({"role": "user", "content": msg['content']})
        elif msg['role'] == 'assistant':
            summary = msg.get('script', '')
            prev_ids = msg.get('hit_ids', [])
            if prev_ids:
                summary += f"\n[关联ID: {prev_ids}]"
            messages.append({"role": "assistant", "content": summary})

    messages.append({"role": "user", "content": user_content})

    try:
        response = ds_client.chat.completions.create(
            model=model_id,
            messages=messages,
            temperature=0.2 if mode_category == 'visa' else 0.3,
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
        return {"script": "（AI无返回）", "links": "", "reason": "", "diagnosis": "", "raw": ""}

    def extract(tag):
        m = re.search(rf'<<<{tag}>>>([\s\S]*?)<<<END_{tag}>>>', raw_text)
        return m.group(1).strip() if m else ""

    return {
        "script": extract("对客话术") or raw_text[:300],
        "links": extract("产品链接") or "",
        "reason": extract("推荐理由") or "",
        "diagnosis": extract("内部诊断") or "",
        "raw": raw_text,
    }


def validate_output_ids(text, pf):
    if not pf:
        return [], []
    numeric_ids = set(re.findall(r'\b(\d{6,9})\b', text))
    p_url_ids = set(re.findall(r'/detail/[pP](\d{6,9})', text))
    p_prefixed_ids = set(re.findall(r'[pP](\d{6,9})', text))
    all_ids = numeric_ids | p_url_ids | p_prefixed_ids
    valid, invalid = [], []
    for pid in all_ids:
        if pid in pf.id_index:
            valid.append(pid)
        else:
            invalid.append(pid)
    return sorted(set(valid)), sorted(set(invalid))


# ==========================================
# 5. 渲染
# ==========================================
def render_assistant_msg(msg, pf=None):
    mode_cat = msg.get('mode_category', 'product')

    if mode_cat == 'visa':
        st.warning("🛂 **签证问答模式** - 回答严格依据携程签证操作手册")

    if pf and mode_cat == 'product':
        valid, invalid = validate_output_ids(
            (msg.get('script', '') or '') + "\n" + (msg.get('links', '') or ''),
            pf
        )
        if invalid:
            st.error(f"⚠️ 输出中存在知识库未识别的产品ID，请人工核对：{invalid}")

    st.markdown("##### 📋 对客话术（一键复制发送给客人）")
    st.code(msg.get('script', ''), language=None)

    col1, col2 = st.columns([1, 1])
    with col1:
        st.markdown("##### 🔗 相关链接/编号")
        st.markdown(msg.get('links', '') or "_（无）_")
    with col2:
        st.markdown("##### 💡 推荐理由 / 处理依据")
        st.markdown(msg.get('reason', '') or "_（无）_")

    with st.expander("🔍 内部诊断（仅客服可见）"):
        st.info(msg.get('diagnosis', '') or "_（无）_")
        st.caption(f"模式: `{mode_cat}` | 筛选: `{msg.get('filter_mode', 'unknown')}`")
        hit_ids = msg.get('hit_ids', [])
        if hit_ids:
            st.caption(f"本轮引用ID: {hit_ids}")

        usage = msg.get('usage_info')
        if usage:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("输入", f"{usage['prompt_tokens']:,}")
            c2.metric("输出", f"{usage['completion_tokens']:,}")
            c3.metric("缓存命中", f"{usage['cache_hit']:,}")
            c4.metric("命中率", f"{usage['hit_rate']:.1f}%")


# ==========================================
# 6. 主程序
# ==========================================
def main():
    pf, vf, file_status = load_knowledge_base()

    for key, default in [
        ('messages', []),
        ('session_product_ids', []),
        ('recent_product_ids', []),
        ('total_cost_stats', {'calls': 0, 'total_tokens': 0, 'cache_hit_total': 0, 'cache_miss_total': 0}),
    ]:
        if key not in st.session_state:
            st.session_state[key] = default

    # ===== 侧边栏 =====
    with st.sidebar:
        st.title("⚙️ 控制台")

        with st.expander("📚 知识库状态", expanded=(pf is None and vf is None)):
            c1, c2 = st.columns(2)
            if pf:
                c1.metric("📦 产品", pf.stats()['total'])
            if vf:
                c2.metric("📋 签证文档", vf.stats()['total'])
            st.divider()
            for s in file_status:
                if "✅" in s or "🎯" in s or "📦" in s or "📋" in s:
                    st.success(s)
                elif "❌" in s:
                    st.error(s)
                elif "⚠️" in s:
                    st.warning(s)
                else:
                    st.caption(s)
            if st.button("🔄 重载知识库", use_container_width=True):
                load_knowledge_base.clear()
                st.rerun()

        st.divider()

        with st.expander(f"🗂️ 上一轮产品池 ({len(st.session_state.recent_product_ids)})"):
            if st.session_state.recent_product_ids and pf:
                for pid in st.session_state.recent_product_ids:
                    p = pf.id_index.get(pid)
                    if p:
                        st.caption(f"`{pid}` - {p['title'][:40]}")
            else:
                st.caption("（暂无）")

        with st.expander(f"🧠 会话记忆产品池 ({len(st.session_state.session_product_ids)})"):
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
            st.session_state.total_cost_stats = {'calls': 0, 'total_tokens': 0, 'cache_hit_total': 0, 'cache_miss_total': 0}
            st.rerun()

        st.caption(f"💬 当前记忆: {len(st.session_state.messages)} 条消息")

        st.divider()
        model_choice = st.radio("🤖 AI 模型", ["DeepSeek V4 Pro", "DeepSeek V4 Flash"])
        model_id = "deepseek-v4-pro" if "Pro" in model_choice else "deepseek-v4-flash"
        st.caption(f"Model: `{model_id}`")

        st.divider()
        st.markdown("**🎯 筛选参数**")
        top_k = st.slider("候选产品数量", 3, 15, 8)
        max_chars = st.slider("上下文最大字符", 20000, 150000, 80000, step=10000)

        st.divider()
        st.markdown("**💰 成本监控**")
        cs = st.session_state.total_cost_stats
        if cs['calls'] > 0:
            hit_rate = cs['cache_hit_total'] / max(cs['total_tokens'], 1) * 100
            st.metric("调用次数", cs['calls'])
            st.metric("累计Tokens", f"{cs['total_tokens']:,}")
            st.metric("缓存命中率", f"{hit_rate:.1f}%")
            cost = cs['cache_miss_total']*4/1e6 + cs['cache_hit_total']*0.4/1e6
            st.metric("估算成本(¥)", f"{cost:.4f}")

        st.divider()
        st.caption(f"DeepSeek: {'🟢' if ds_client else '🔴'}")
        st.caption(f"Gemini语音: {'🟢' if GEMINI_KEY else '🔴'}")

    # ===== 主界面 =====
    st.title("👩‍💼 携程产品服务专家 Co-Pilot")

    if not pf and not vf:
        st.error("❌ 知识库未加载成功，请检查 .md 文件")
        return

    if not ds_client:
        st.error("❌ DeepSeek API 未配置")
        return

    cap_parts = []
    if pf:
        cap_parts.append(f"📦 产品 {pf.stats()['total']} 个")
    if vf:
        cap_parts.append(f"📋 签证文档 {vf.stats()['total']} 份")
    st.caption(f"💡 智能预筛选 + 双知识库模式 | {' · '.join(cap_parts)} | 支持产品推荐、详情追问、签证咨询")

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
            t = transcribe_audio(audio_val.getvalue())
            if t:
                final_query = t
                st.info(f"🗣️ {t}")

    text_input = st.chat_input("输入客户咨询...（例：济州岛4天3晚 / 韩国签证需要什么材料）")
    if text_input and not final_query:
        final_query = text_input

    if final_query:
        if not st.session_state.messages or st.session_state.messages[-1].get('content') != final_query:
            st.session_state.messages.append({"role": "user", "content": final_query})
            with st.chat_message("user"):
                st.write(final_query)

            with st.chat_message("assistant"):
                # ===== 意图分流 =====
                is_visa = vf and vf.is_visa_question(final_query)

                if is_visa:
                    st.warning("🛂 检测到签证类问题，已切换到【签证严格模式】")
                    mode_category = 'visa'
                    with st.spinner("📋 正在匹配签证文档..."):
                        filtered_context, hit_ids, hit_details, mode = vf.build_context(
                            final_query, top_k=4, max_chars=max_chars
                        )

                    if hit_details:
                        with st.expander(f"📋 命中签证文档 ({len(hit_details)})"):
                            for d in hit_details:
                                st.caption(f"`{d['id']}` | {d['score']}分 | {d['country']} | {d['consulate']}")
                                st.caption(f"  └─ {d['title']}")
                    else:
                        st.warning("⚠️ 未精确匹配签证文档，AI 将引导客户补充信息")

                else:
                    if not pf:
                        st.error("❌ 当前未加载产品库，无法处理产品咨询")
                        return

                    mode_category = 'product'
                    with st.spinner("🔍 正在匹配产品上下文..."):
                        filtered_context, hit_ids, hit_details, mode = pf.build_context(
                            query=final_query,
                            top_k=top_k,
                            max_chars=max_chars,
                            recent_product_ids=st.session_state.recent_product_ids,
                            session_product_ids=st.session_state.session_product_ids,
                        )

                    mode_tips = {
                        "new_query": f"🆕 新查询，命中 {len(hit_details)} 个候选产品",
                        "direct_id": "🎯 产品ID精确查询",
                        "ordinal": "🔢 序号指代查询",
                        "follow_up_recent": "🔁 追问 - 复用上一轮产品",
                        "follow_up_session": "🧠 追问 - 复用会话产品池",
                        "no_match": "⚠️ 未命中产品，将引导客户补充",
                    }
                    if mode in ["direct_id", "ordinal", "follow_up_recent", "follow_up_session"]:
                        st.success(mode_tips.get(mode, mode))
                    elif mode == "no_match":
                        st.warning(mode_tips.get(mode, mode))
                    else:
                        st.info(mode_tips.get(mode, mode))

                    if hit_details:
                        with st.expander(f"🎯 本轮产品上下文 ({len(hit_details)})"):
                            for d in hit_details:
                                st.caption(
                                    f"`{d['id']}` | {d['score']}分 | {d['days']}天 | "
                                    f"{d['destination']} | {' '.join(d['reasons'])}"
                                )

                # ===== 调用 LLM =====
                with st.spinner(f"🤖 {model_choice} 生成中..."):
                    raw, usage_info = get_ai_reply(
                        final_query,
                        st.session_state.messages[:-1],
                        filtered_context,
                        model_id,
                        mode_category,
                    )

                parsed = parse_reply(raw)

                if mode_category == 'product':
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
                    "mode_category": mode_category,
                }
                st.session_state.messages.append(msg_data)
                render_assistant_msg(msg_data, pf)


if __name__ == "__main__":
    main()
