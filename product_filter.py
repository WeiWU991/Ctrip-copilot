"""
携程产品智能预筛选器 V3.2
- 支持新查询 / 产品ID精确查询 / 追问复用 / 序号指代
- 兼容携程URL格式：/detail/p{产品ID}
- 自动跳过签证条目（防止误解析）
"""

import re
from typing import List, Dict, Tuple, Optional


class ProductFilter:
    DEST_KEYWORDS = {
        '济州岛': ['济州', '济州岛', 'jeju'],
        '首尔': ['首尔', '韩国首尔'],
        '釜山': ['釜山'],
        '韩国': ['韩国'],
        '东京': ['东京', '新宿', '银座', '浅草'],
        '大阪': ['大阪', '心斋桥', '难波'],
        '京都': ['京都'],
        '北海道': ['北海道', '札幌', '函馆', '小樽'],
        '冲绳': ['冲绳'],
        '日本': ['日本'],
        '曼谷': ['曼谷'],
        '普吉': ['普吉', '普吉岛'],
        '清迈': ['清迈'],
        '芭提雅': ['芭提雅'],
        '泰国': ['泰国'],
        '河内': ['河内'],
        '岘港': ['岘港', '芽庄'],
        '越南': ['越南'],
        '新加坡': ['新加坡', '狮城'],
        '吉隆坡': ['吉隆坡'],
        '马来西亚': ['马来', '马来西亚'],
        '巴厘岛': ['巴厘', '巴厘岛', '巴里岛'],
        '印尼': ['印尼', '印度尼西亚'],
        '菲律宾': ['菲律宾', '长滩', '宿务'],
        '台湾': ['台湾', '台北', '高雄', '台中'],
        '香港': ['香港'],
        '澳门': ['澳门'],
        '迪拜': ['迪拜', '阿联酋'],
        '土耳其': ['土耳其', '伊斯坦布尔'],
        '法国': ['法国', '巴黎'],
        '意大利': ['意大利', '罗马', '威尼斯'],
        '瑞士': ['瑞士'],
        '德国': ['德国'],
        '英国': ['英国', '伦敦'],
        '欧洲': ['欧洲', '欧'],
        '美国': ['美国', '纽约', '洛杉矶'],
        '澳大利亚': ['澳洲', '澳大利亚', '悉尼', '墨尔本'],
        '新西兰': ['新西兰'],
        '埃及': ['埃及'],
        '马尔代夫': ['马代', '马尔代夫'],
        '富士山': ['富士山', '河口湖', '山中湖'],
        '环球影城': ['环球影城', 'USJ', '日本环球影城'],
    }

    REGION_MAP = {
        '东南亚': ['泰国', '越南', '新加坡', '马来西亚', '印尼', '菲律宾'],
        '东北亚': ['日本', '韩国'],
        '港澳台': ['香港', '澳门', '台湾'],
        '中东': ['迪拜', '土耳其', '埃及'],
        '大洋洲': ['澳大利亚', '新西兰'],
    }

    THEME_KEYWORDS = [
        '樱花', '赏花', '赏樱', '红叶', '滑雪', '温泉', '海岛', '海滩',
        '亲子', '蜜月', '老人', '银发', '购物', '美食', '环球影城',
        '迪士尼', '雪山', '邮轮', '自由行', '跟团', '半自由', '深度',
        '小团', '私家', '豪华', '经济', '奢华', '商务',
    ]

    FOLLOW_UP_KEYWORDS = [
        '这款', '这个产品', '这两个产品', '这两款', '这几款', '这些',
        '它', '它们', '上面', '上述', '刚才', '刚刚', '之前', '前面',
        '上一轮', '前一轮', '那个', '那个产品', '这条线路', '那条线路',
        '第一个', '第二个', '第三个', '第1个', '第2个', '第3个',
        '第一款', '第二款', '第三款', '①', '②', '③',
        '便宜的那个', '贵的那个', '豪华的那个', '经济的那个'
    ]

    DETAIL_KEYWORDS = [
        '费用', '价格', '多少钱', '报价', '预算', '花费',
        '包含', '包括', '价格包含', '费用包含', '费用不含', '不含',
        '酒店', '住宿', '住哪', '房型',
        '行程', '安排', '日程', '游玩',
        '餐', '餐食', '早餐', '午餐', '晚餐',
        '交通', '航班', '机票', '火车', '巴士', '接送',
        '保险',
        '退改', '取消', '退款',
        '区别', '差别', '对比', '比较', '哪个好',
        '详情', '细节', '具体',
        '预订限制', '预订须知', '预定', '预订',
    ]

    def __init__(self, md_text: str):
        self.md_text = md_text
        self.products = self._parse_products(md_text)
        self.id_index: Dict[str, Dict] = {p['id']: p for p in self.products}

    def _parse_products(self, md: str) -> List[Dict]:
        products = []
        chunks = re.split(r'\n(?=# [^\n]+\n\n\*\*产品编号)', md)

        for chunk in chunks:
            if "**产品编号**" not in chunk:
                continue
            # 跳过签证条目（防止被当作产品解析）
            if "**签证编号**" in chunk:
                continue

            pid_m = re.search(r'\*\*产品编号\*\*:\s*(\d+)', chunk)
            if not pid_m:
                continue
            pid = pid_m.group(1)

            title_m = re.search(r'^# (.+)$', chunk, re.MULTILINE)
            title = title_m.group(1).strip() if title_m else f"产品{pid}"

            days, nights = 0, 0
            days_m = re.search(r'\*\*行程天数\*\*:\s*(\d+)\s*天\s*(\d+)?\s*晚?', chunk)
            if days_m:
                days = int(days_m.group(1))
                nights = int(days_m.group(2)) if days_m.group(2) else max(days - 1, 0)

            dest_m = re.search(r'\*\*目的地\*\*:\s*(.+)', chunk)
            destination = dest_m.group(1).strip() if dest_m else ''

            level_m = re.search(r'\*\*产品钻级\*\*:\s*(.+)', chunk)
            level = level_m.group(1).strip() if level_m else ''

            tags_m = re.search(r'\*\*产品标签\*\*:\s*(.+)', chunk)
            tags = tags_m.group(1).strip() if tags_m else ''

            products.append({
                'id': pid,
                'title': title,
                'days': days,
                'nights': nights,
                'destination': destination,
                'level': level,
                'tags': tags,
                'full_text': chunk.strip(),
                'searchable': f"{title} {destination} {tags} {chunk[:4000]}".lower(),
            })

        return products

    def _extract_days(self, query: str) -> int:
        m = re.search(r'(\d+)\s*[天日]', query)
        return int(m.group(1)) if m else 0

    def _extract_destinations(self, query: str) -> List[str]:
        q = query.lower()
        hits = []
        for dest, aliases in self.DEST_KEYWORDS.items():
            if any(alias.lower() in q for alias in aliases):
                hits.append(dest)
        for region, sub_dests in self.REGION_MAP.items():
            if region in query:
                hits.extend(sub_dests)
        return list(set(hits))

    def _extract_themes(self, query: str) -> List[str]:
        return [t for t in self.THEME_KEYWORDS if t in query]

    def _extract_raw_ids(self, query: str) -> List[str]:
        ids = set()
        for x in re.findall(r'\b(\d{6,9})\b', query):
            ids.add(x)
        for x in re.findall(r'[pP](\d{6,9})', query):
            ids.add(x)
        for x in re.findall(r'/detail/[pP](\d{6,9})', query):
            ids.add(x)
        return list(ids)

    def _extract_product_ids(self, query: str) -> List[str]:
        candidates = self._extract_raw_ids(query)
        return [pid for pid in candidates if pid in self.id_index]

    def is_follow_up(self, query: str) -> Tuple[bool, str]:
        for kw in self.FOLLOW_UP_KEYWORDS:
            if kw in query:
                return True, kw

        has_dest = bool(self._extract_destinations(query))
        has_detail = any(kw in query for kw in self.DETAIL_KEYWORDS)
        if not has_dest and has_detail and len(query) <= 40:
            return True, "细节追问"

        return False, ""

    def parse_ordinal(self, query: str) -> Optional[int]:
        mapping = {
            '第一个': 0, '第1个': 0, '第一款': 0, '①': 0,
            '第二个': 1, '第2个': 1, '第二款': 1, '②': 1,
            '第三个': 2, '第3个': 2, '第三款': 2, '③': 2,
        }
        for kw, idx in mapping.items():
            if kw in query:
                return idx
        return None

    def get_by_ids(self, pids: List[str]) -> List[Dict]:
        return [self.id_index[pid] for pid in pids if pid in self.id_index]

    def filter(self, query: str, top_k: int = 8) -> List[Tuple[int, Dict, List[str]]]:
        destinations = self._extract_destinations(query)
        days = self._extract_days(query)
        themes = self._extract_themes(query)
        direct_ids = self._extract_product_ids(query)

        scored = []
        for p in self.products:
            score = 0
            reasons = []

            if p['id'] in direct_ids:
                score += 1000
                reasons.append("ID精确匹配")

            for dest in destinations:
                aliases = self.DEST_KEYWORDS.get(dest, [dest])
                if any(a.lower() in p['searchable'] for a in aliases):
                    score += 40
                    reasons.append(f"目的地:{dest}")
                    break

            if days > 0 and p['days'] > 0:
                if p['days'] == days:
                    score += 30
                    reasons.append(f"天数精确:{p['days']}天")
                elif abs(p['days'] - days) == 1:
                    score += 15
                    reasons.append(f"天数接近:{p['days']}天")
                elif abs(p['days'] - days) == 2:
                    score += 5

            for theme in themes:
                if theme in p['searchable']:
                    score += 15
                    reasons.append(f"主题:{theme}")

            for word in re.findall(r'[\u4e00-\u9fa5A-Za-z]{2,}', query):
                w = word.lower()
                if len(w) >= 2 and w in p['searchable']:
                    score += 5

            if score > 0:
                scored.append((score, p, reasons))

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:top_k]

    def build_context(
        self,
        query: str,
        top_k: int = 8,
        max_chars: int = 80000,
        recent_product_ids: Optional[List[str]] = None,
        session_product_ids: Optional[List[str]] = None,
    ) -> Tuple[str, List[str], List[Dict], str]:
        recent_product_ids = recent_product_ids or []
        session_product_ids = session_product_ids or []

        direct_ids = self._extract_product_ids(query)
        ordinal = self.parse_ordinal(query)
        is_followup, trigger = self.is_follow_up(query)

        mode = 'new_query'
        target_products = []

        if direct_ids:
            target_products = self.get_by_ids(direct_ids)
            mode = 'direct_id'

        elif ordinal is not None and recent_product_ids:
            if ordinal < len(recent_product_ids):
                target_products = self.get_by_ids([recent_product_ids[ordinal]])
                mode = 'ordinal'

        elif is_followup and recent_product_ids:
            target_products = self.get_by_ids(recent_product_ids[:5])
            mode = 'follow_up_recent'

        elif is_followup and session_product_ids:
            target_products = self.get_by_ids(session_product_ids[-5:])
            mode = 'follow_up_session'

        if not target_products:
            results = self.filter(query, top_k)
            if not results:
                context = (
                    f"【上下文模式】no_match\n"
                    f"【客户问题】{query}\n"
                    f"【分析】{'疑似追问，但未找到可复用产品池。' if is_followup else '未匹配到明确产品。'}\n"
                    f"请引导客户补充：目的地、天数、预算、出行日期、人数。"
                )
                return context, [], [], 'no_match'
            return self._format_context(query, results, max_chars, 'new_query')

        results = [(1000, p, [mode, f"触发:{trigger}" if trigger else mode]) for p in target_products]
        return self._format_context(query, results, max_chars, mode)

    def _format_context(self, query, results, max_chars, mode='new_query'):
        id_list = [p['id'] for _, p, _ in results]

        header = (
            f"【上下文模式】{mode}\n"
            f"【客户原始问题】{query}\n"
            f"【允许引用的产品ID白名单】{id_list}\n"
            f"【携程链接格式】https://vacations.ctrip.com/travel/detail/p{{产品ID}}\n"
            f"【对客展示ID格式】【ID:产品ID】\n"
            f"【严禁】引用白名单外的信息\n\n"
        )

        chunks = [header]
        total = len(header)
        hit_ids = []
        hit_details = []

        for i, (score, p, reasons) in enumerate(results, 1):
            block = (
                f"\n{'='*70}\n"
                f"📦 候选{i} ▍产品ID:【{p['id']}】 ▍匹配度:{score}分\n"
                f"▍命中维度:{', '.join(reasons)}\n"
                f"{'='*70}\n"
                f"{p['full_text']}\n"
            )
            if total + len(block) > max_chars:
                chunks.append(f"\n[...另有 {len(results)-i+1} 个产品因长度限制未展示...]")
                break

            chunks.append(block)
            total += len(block)
            hit_ids.append(p['id'])
            hit_details.append({
                'id': p['id'],
                'title': p['title'],
                'score': score,
                'reasons': reasons,
                'days': p['days'],
                'destination': p['destination'],
            })

        return ''.join(chunks), hit_ids, hit_details, mode

    def stats(self) -> Dict:
        return {
            'total': len(self.products),
            'destinations': len(set(p['destination'] for p in self.products if p['destination'])),
            'avg_days': round(sum(p['days'] for p in self.products) / len(self.products), 1) if self.products else 0,
        }
