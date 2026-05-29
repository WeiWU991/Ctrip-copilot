"""
携程产品智能预筛选器
不依赖向量数据库，纯Python多维度打分匹配
"""
import re
from typing import List, Dict, Tuple


class ProductFilter:
    """产品筛选器：解析MD知识库 + 多维度打分检索"""

    # 目的地词典（key=标准名，value=别名列表）
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

    # 区域聚合（用户问"东南亚"时扩展匹配）
    REGION_MAP = {
        '东南亚': ['泰国', '越南', '新加坡', '马来西亚', '印尼', '菲律宾', '柬埔寨', '老挝'],
        '东北亚': ['日本', '韩国'],
        '港澳台': ['香港', '澳门', '台湾'],
        '中东': ['迪拜', '土耳其', '埃及', '以色列'],
        '大洋洲': ['澳大利亚', '新西兰'],
    }

    # 主题标签词
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
        """解析 MD，按产品切分为结构化列表"""
        products = []
        # 按 "# 产品标题" 切分（排除一级目录标题）
        chunks = re.split(r'\n(?=# [^\n]+\n\n\*\*产品编号)', md)

        for chunk in chunks:
            if "**产品编号**" not in chunk:
                continue

            # 产品ID
            pid_m = re.search(r'\*\*产品编号\*\*:\s*(\d+)', chunk)
            if not pid_m:
                continue
            pid = pid_m.group(1)

            # 标题（# 开头那一行）
            title_m = re.search(r'^# (.+)$', chunk, re.MULTILINE)
            title = title_m.group(1).strip() if title_m else f"产品{pid}"

            # 天数
            days, nights = 0, 0
            days_m = re.search(r'\*\*行程天数\*\*:\s*(\d+)\s*天\s*(\d+)?\s*晚?', chunk)
            if days_m:
                days = int(days_m.group(1))
                nights = int(days_m.group(2)) if days_m.group(2) else days - 1

            # 目的地
            dest_m = re.search(r'\*\*目的地\*\*:\s*(.+)', chunk)
            destination = dest_m.group(1).strip() if dest_m else ''

            # 钻级
            level_m = re.search(r'\*\*产品钻级\*\*:\s*(.+)', chunk)
            level = level_m.group(1).strip() if level_m else ''

            # 标签
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

    # ----------- 查询解析 -----------
    def _extract_days(self, query: str) -> int:
        """从查询中提取天数，如'4天3晚' -> 4"""
        m = re.search(r'(\d+)\s*[天日]', query)
        return int(m.group(1)) if m else 0

    def _extract_destinations(self, query: str) -> List[str]:
        """从查询中提取目的地"""
        q = query.lower()
        hits = []
        for dest, aliases in self.DEST_KEYWORDS.items():
            if any(alias.lower() in q for alias in aliases):
                hits.append(dest)

        # 区域扩展
        for region, sub_dests in self.REGION_MAP.items():
            if region in query:
                hits.extend(sub_dests)

        return list(set(hits))

    def _extract_themes(self, query: str) -> List[str]:
        """从查询中提取主题/卖点"""
        return 
