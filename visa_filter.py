"""
签证知识库筛选器
- 与 ProductFilter 完全独立
- 按"国家 + 领区 + 客户身份/职业 + 签证类型"匹配
- 严格锁定原文，禁止AI编造
"""
import re
from typing import List, Dict, Tuple


class VisaFilter:
    """签证文档筛选器"""

    COUNTRY_KEYWORDS = {
        '韩国': ['韩国', '首尔', '釜山', '济州'],
        '日本': ['日本', '东京', '大阪', '京都', '北海道', '冲绳', '名古屋'],
    }

    CONSULATE_KEYWORDS = {
        # 日本领区
        'JP-SH': {
            'name': '日本-上海领区',
            'keywords': ['上海', '江苏', '浙江', '安徽', '江西', '南京', '杭州', '苏州', '南昌', '合肥'],
        },
        'JP-BJ': {
            'name': '日本-北京领区',
            'keywords': ['北京', '天津', '河北', '山西', '河南', '湖北', '湖南', '陕西', '甘肃',
                         '青海', '内蒙古', '宁夏', '新疆', '西藏', '武汉', '长沙', '郑州', '西安'],
        },
        'JP-GZ': {
            'name': '日本-广州领区',
            'keywords': ['广州', '广东', '广西', '福建', '海南', '深圳', '南宁', '福州', '厦门', '海口', '珠海'],
        },
        'JP-CQ': {
            'name': '日本-重庆领区',
            'keywords': ['重庆', '四川', '贵州', '云南', '成都', '昆明', '贵阳'],
        },
        'JP-DL': {
            'name': '日本-大连领区',
            'keywords': ['大连'],
        },
        # 韩国领区
        'KR-SH': {
            'name': '韩国-上海领区',
            'keywords': ['上海', '江苏', '浙江', '安徽', '南京', '杭州', '苏州'],
        },
        'KR-BJ': {
            'name': '韩国-北京领区',
            'keywords': ['北京', '天津', '河北', '山西', '内蒙古', '新疆', '西藏', '青海'],
        },
        'KR-GZ': {
            'name': '韩国-广州领区',
            'keywords': ['广州', '广东', '广西', '福建', '海南', '深圳', '厦门'],
        },
        'KR-CD': {
            'name': '韩国-成都领区',
            'keywords': ['成都', '重庆', '四川', '贵州', '云南'],
        },
        'KR-XA': {
            'name': '韩国-西安领区',
            'keywords': ['西安', '陕西', '甘肃', '宁夏'],
        },
        'KR-WH': {
            'name': '韩国-武汉领区',
            'keywords': ['武汉', '河南', '湖北', '湖南', '江西', '长沙'],
        },
    }

    VISA_TYPE_KEYWORDS = {
        '个人签证': ['个签', '个人签证', '个人签'],
        '团签': ['团签', '团体签证', '团体签'],
        '单次签证': ['单次', '单次签', '单次签证'],
        '三年多次': ['三年', '三年多次', '3年多次'],
        '五年多次': ['五年', '五年多次', '5年多次'],
    }

    IDENTITY_KEYWORDS = [
        '在职', '上班族', '员工', '公司',
        '学生', '在读', '本科', '研究生',
        '退休', '退休人员', '老人',
        '家庭主妇', '主妇', '全职太太',
        '自由职业', '自由', '个体',
        '芝麻分', '芝麻信用',
    ]

    VISA_TRIGGER_KEYWORDS = [
        '签证', '签注', '办签', '送签', '领区', '送签领区',
        '护照', '身份证复印件', '户口本',
        '在职证明', '工资流水', '完税证明', '存款证明', '社保',
        '芝麻信用', '芝麻分', '出入境记录',
        '领事馆', '使馆', '签证申请表',
        '准签', '拒签', '出签', '面签',
        '材料清单', '签证材料', '办证',
    ]

    def __init__(self, md_text: str):
        self.md_text = md_text
        self.entries = self._parse_entries(md_text)
        self.id_index: Dict[str, Dict] = {e['id']: e for e in self.entries}

    def _parse_entries(self, md: str) -> List[Dict]:
        """按 # 标题切分签证条目"""
        entries = []
        chunks = re.split(r'\n(?=# )', md)
        for chunk in chunks:
            if "**签证编号**" not in chunk:
                continue

            id_m = re.search(r'\*\*签证编号\*\*:\s*([A-Z0-9\-]+)', chunk)
            if not id_m:
                continue
            vid = id_m.group(1)

            title_m = re.search(r'^# (.+)$', chunk, re.MULTILINE)
            title = title_m.group(1).strip() if title_m else vid

            type_m = re.search(r'\*\*签证类型\*\*:\s*(.+)', chunk)
            visa_type = type_m.group(1).strip() if type_m else ''

            country_m = re.search(r'\*\*适用国家\*\*:\s*(.+)', chunk)
            country = country_m.group(1).strip() if country_m else ''

            consulate_m = re.search(r'\*\*领区\*\*:\s*(.+)', chunk)
            consulate = consulate_m.group(1).strip() if consulate_m else ''

            area_m = re.search(r'\*\*覆盖地区\*\*:\s*(.+)', chunk)
            area = area_m.group(1).strip() if area_m else ''

            entries.append({
                'id': vid,
                'title': title,
                'visa_type': visa_type,
                'country': country,
                'consulate': consulate,
                'area': area,
                'full_text': chunk.strip(),
                'searchable': f"{title} {visa_type} {country} {consulate} {area} {chunk[:5000]}".lower(),
            })

        return entries

    def is_visa_question(self, query: str) -> bool:
        """判断是否签证类问题"""
        return any(kw in query for kw in self.VISA_TRIGGER_KEYWORDS)

    def _extract_country(self, query: str) -> List[str]:
        hits = []
        for country, kws in self.COUNTRY_KEYWORDS.items():
            if any(kw in query for kw in kws):
                hits.append(country)
        return hits

    def _extract_consulate(self, query: str, countries: List[str]) -> List[str]:
        hits = []
        for cid, info in self.CONSULATE_KEYWORDS.items():
            country_prefix = 'JP' if 'JP' in cid else 'KR'
            if '日本' in countries and country_prefix != 'JP':
                continue
            if '韩国' in countries and country_prefix != 'KR':
                continue

            if any(kw in query for kw in info['keywords']):
                hits.append(cid)
        return hits

    def _extract_visa_types(self, query: str) -> List[str]:
        hits = []
        for vtype, kws in self.VISA_TYPE_KEYWORDS.items():
            if any(kw in query for kw in kws):
                hits.append(vtype)
        return hits

    def _extract_identities(self, query: str) -> List[str]:
        return [kw for kw in self.IDENTITY_KEYWORDS if kw in query]

    def filter(self, query: str, top_k: int = 4) -> List[Tuple[int, Dict, List[str]]]:
        countries = self._extract_country(query)
        consulates = self._extract_consulate(query, countries)
        visa_types = self._extract_visa_types(query)
        identities = self._extract_identities(query)

        scored = []
        for e in self.entries:
            score = 0
            reasons = []

            # 国家匹配 (50分)
            for c in countries:
                if c in e['country']:
                    score += 50
                    reasons.append(f"国家:{c}")
                    break

            # 领区匹配 (40分)
            for cid in consulates:
                if cid in e['id']:
                    score += 40
                    reasons.append(f"领区:{cid}")
                    break

            # 签证类型匹配 (20分/个)
            for vt in visa_types:
                if vt in e['searchable']:
                    score += 20
                    reasons.append(f"类型:{vt}")

            # 身份匹配 (10分/个)
            for idn in identities:
                if idn in e['searchable']:
                    score += 10
                    reasons.append(f"身份:{idn}")

            # 中文模糊匹配
            for word in re.findall(r'[\u4e00-\u9fa5]{2,}', query):
                if word in e['searchable']:
                    score += 2

            if score > 0:
                scored.append((score, e, reasons))

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:top_k]

    def build_context(
        self,
        query: str,
        top_k: int = 4,
        max_chars: int = 60000,
    ) -> Tuple[str, List[str], List[Dict], str]:
        """构建签证问题的上下文"""
        results = self.filter(query, top_k)

        if not results:
            available = "\n".join([f"- {e['id']} | {e['title']}" for e in self.entries])
            context = (
                f"【上下文模式】visa_no_match\n"
                f"【客户问题】{query}\n"
                f"【分析】未能精确匹配签证条目。\n"
                f"【可用签证文档列表】\n{available}\n\n"
                f"【处理指示】请引导客户补充：办理什么国家签证？户籍所在省/市？签证类型（个人/团签/单次/多次）？\n"
            )
            return context, [], [], 'visa_no_match'

        id_list = [e['id'] for _, e, _ in results]
        header = (
            f"【上下文模式】visa_query\n"
            f"【客户原始问题】{query}\n"
            f"【匹配签证文档】{id_list}\n"
            f"【🔴 严格执行】下面是携程官方签证操作手册原文。\n"
            f"【🔴 铁律】所有回答必须严格引用文档原文，绝对禁止：\n"
            f"  - 编造文档中没有的材料/金额/天数\n"
            f"  - 凭经验或想象补充细节\n"
            f"  - 承诺出签率/出签时间/加急服务\n"
            f"【🔴 信息缺失处理】如果文档没有明确说明，必须回复：\n"
            f"  「该项信息暂未明确，建议转接签证专员核实」\n\n"
        )

        chunks = [header]
        total = len(header)
        hit_ids = []
        hit_details = []

        for i, (score, e, reasons) in enumerate(results, 1):
            block = (
                f"\n{'='*70}\n"
                f"📋 签证文档{i} ▍编号:【{e['id']}】 ▍匹配度:{score}分\n"
                f"▍{e['title']}\n"
                f"▍命中维度: {', '.join(reasons)}\n"
                f"{'='*70}\n"
                f"{e['full_text']}\n"
            )
            if total + len(block) > max_chars:
                break
            chunks.append(block)
            total += len(block)
            hit_ids.append(e['id'])
            hit_details.append({
                'id': e['id'],
                'title': e['title'],
                'score': score,
                'reasons': reasons,
                'country': e['country'],
                'consulate': e['consulate'],
            })

        return ''.join(chunks), hit_ids, hit_details, 'visa_query'

    def stats(self) -> Dict:
        return {
            'total': len(self.entries),
            'countries': len(set(e['country'] for e in self.entries if e['country'])),
        }
