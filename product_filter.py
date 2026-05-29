"""
携程产品智能预筛选器
不依赖向量数据库，纯Python多维度打分匹配
"""
import re
from typing import List, Dict, Tuple


class ProductFilter:
    """产品筛选器：解析MD知识库 + 多维度打分检索"""

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
    }

    REGION_MAP = {
        '东南亚': ['泰国', '越南', '新加坡', '马来西亚', '印尼', '菲律宾', '柬埔寨', '老挝'],
        '东北亚': ['日本', '韩国'],
        '港澳台': ['香港', '澳门', '台湾'],
        '中东': ['迪拜', '土耳其', '埃及', '以色列'],
        '大洋洲': ['澳大利亚', '新西兰'],
    }

    THEME_KEYWORDS = [
        '樱花', '赏花', '赏樱', '红叶', '滑雪', '温泉', '海岛', '海滩',
        '亲子', '蜜月', '老人', '银发', '购物', '美食', '环球影城',
        '迪士尼', '雪山', '邮轮', '自由行', '跟团', '半自由', '深度',
        '小团', '私家', '豪华', '经济', '奢华', '商务',
    ]

    def __init__(self, md_text: str):
        self.md_text = md_text
        self.products = self._parse_products(md_text)

    def _parse_products(self, md: str) -> List[Dict]:
        """解析 MD，按产品切分"""
        products = []
        chunks = re.split(r'\n(?=# [^\n]+\n\n\*\*产品编号)', md)

        for chunk in chunks:
            if "**产品编号**" not in chunk:
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
                'searchable': f"{title} {destination} {tags}".lower(),
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

    def _extract_product_ids(self, query: str) -> List[str]:
        return re.findall(r'\b(\d{6,9})\b', query)

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
                if theme in p['searchable'] or theme in p['full_text'][:3000]:
                    score += 15
                    reasons.append(f"主题:{theme}")

            for word in re.findall(r'[\u4e00-\u9fa5]{2,}', query):
                if len(word) >= 2 and word in p['searchable']:
                    score += 5

            if score > 0:
                scored.append((score, p, reasons))

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:top_k]

    def build_context(self, query: str, top_k: int = 8, max_chars: int = 80000):
        results = self.filter(query, top_k)

        if not results:
            context = (
                f"⚠️ 未在知识库中找到与“{query}”明确匹配的产品。\n"
                f"请基于以下情况回复客户：\n"
                f"1. 告知客户该需求暂无完全匹配产品\n"
                f"2. 主动询问客户更多信息（目的地/天数/预算/出行日期/人数）\n"
                f"3. 不要编造产品ID或行程"
            )
            return context, [], []

        header = f"【系统已从 {len(self.products)} 个产品中筛选出 {len(results)} 个最匹配的候选】\n"
        header += f"【客户原始问题】: {query}\n\n"

        chunks = [header]
        total = len(header)
        hit_ids = []
        hit_details = []

        for i, (score, p, reasons) in enumerate(results, 1):
            block = (
                f"\n{'='*70}\n"
                f"📦 候选 {i} | 匹配度: {score}分 | 命中维度: {', '.join(reasons)}\n"
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

        return ''.join(chunks), hit_ids, hit_details

    def stats(self) -> Dict:
        return {
            'total': len(self.products),
            'destinations': len(set(p['destination'] for p in self.products if p['destination'])),
            'avg_days': round(sum(p['days'] for p in self.products) / len(self.products), 1) if self.products else 0,
        }
