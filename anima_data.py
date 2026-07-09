"""
Anima 数据融合模块

数据目录结构 (data/)：
  anima/         - Anima 原始数据（画师、角色、服装）— 运行时从 Anima-Tools JS 读取
  scene/         - 场景/环境
  lighting/      - 光影/色调
  pose_action/   - 动作/姿势/表情
  shot/          - 镜头/构图
  custom/        - 用户自定义

每条数据格式：
  {"name": "显示名", "tags": "逗号分隔的提示词标签", "category": "分类名(可选)", "note": "备注(可选)"}
"""

import json
import os
import re
import logging
from pathlib import Path
from typing import Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)

# Anima-Tools JS 数据目录（插件自带，不依赖外部）
_ANIMA_TOOLS_JS_DIR = Path(__file__).resolve().parent / "data" / "anima_tools"

# 热门角色作品分类中文翻译（只覆盖前 50 热门，其余保持英文）
_CHARACTER_CATEGORY_CN = {
    "pokemon": "宝可梦",
    "kantai collection": "舰队Collection",
    "fate (series)": "Fate系列",
    "hololive": "ホロライブ",
    "idolmaster": "偶像大师",
    "touhou": "东方Project",
    "blue archive": "蔚蓝档案",
    "arknights": "明日方舟",
    "azur lane": "碧蓝航线",
    "fire emblem": "火焰纹章",
    "genshin impact": "原神",
    "umamusume": "赛马娘",
    "original": "原创",
    "precure": "光之美少女",
    "nijisanji": "彩虹社",
    "fate/grand order": "Fate/Grand Order",
    "honkai (series)": "崩坏系列",
    "final fantasy": "最终幻想",
    "girls' frontline": "少女前线",
    "granblue fantasy": "碧蓝幻想",
    "girls und panzer": "少女与战车",
    "kemono friends": "兽娘动物园",
    "jojo no kimyou na bouken": "JOJO的奇妙冒险",
    "gundam": "高达",
    "vocaloid": "VOCALOID",
    "league of legends": "英雄联盟",
    "love live!": "LoveLive!",
    "danganronpa (series)": "弹丸论破系列",
    "touken ranbu": "刀剑乱舞",
    "tales of (series)": "传说系列",
    "persona": "女神异闻录",
    "lyrical nanoha": "魔法少女奈叶",
    "yu-gi-oh!": "游戏王",
    "one piece": "海贼王",
    "dragon ball": "龙珠",
    "ragnarok online": "仙境传说",
    "bang dream!": "BanG Dream!",
    "umineko no naku koro ni": "海猫鸣泣之时",
    "boku no hero academia": "我的英雄学院",
    "princess connect!": "公主连结",
    "bishoujo senshi sailor moon": "美少女战士",
    "the legend of zelda": "塞尔达传说",
    "dragon quest": "勇者斗恶龙",
    "project moon": "Project Moon",
    "xenoblade chronicles (series)": "异度神剑系列",
    "zenless zone zero": "绝区零",
    "marvel": "漫威",
    "street fighter": "街头霸王",
    "goddess of victory: nikke": "胜利女神：NIKKE",
    "inazuma eleven (series)": "闪电十一人系列",
}

class AnimaDataManager:
    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self._datasets: dict[str, list[dict]] = {}
        """{分类名: [{name, tags, ...}]}"""
        self._name_index: dict[str, list[tuple[str, int]]] = {}
        """{关键词: [(分类名, 索引), ...]}"""
        self._loaded = False

    # ------------------------------------------------------------------
    # Anima-Tools JS 直接引用（不存本地，运行时动态读取）
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_js_array(filepath: Path) -> list:
        """从 JS 文件中提取 JSON 数组（去掉 const xxx = 前缀）"""
        if not filepath.exists():
            logger.warning(f"[AnimaData] Anima-Tools JS 不存在: {filepath}")
            return []
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                text = f.read()
            start = text.find('[')
            end = text.rfind(']')
            if start == -1 or end == -1:
                logger.warning(f"[AnimaData] 无法找到数组: {filepath}")
                return []
            return json.loads(text[start:end+1])
        except Exception as e:
            logger.warning(f"[AnimaData] 解析 JS 失败 {filepath}: {e}")
            return []

    def _load_anima_tools_artists(self) -> list[dict]:
        """从 Anima-Tools data.js 加载画师数据（含 CDN 图片）"""
        raw = self._extract_js_array(_ANIMA_TOOLS_JS_DIR / "data.js")
        if not raw:
            return []
        items = []
        seen = set()
        for item in raw:
            name = (item.get("name") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            item_id = str(item.get("id", ""))
            partition = item.get("p", 1)
            image_url = (
                f"https://fastly.jsdelivr.net/gh/ThetaCursed/"
                f"Anima-Assets@main/images/{partition}/{item_id}.webp"
            )
            items.append({
                "name": name,
                "tags": f"@{name}",
                "category": "",
                "note": f"作品数: {item.get('post_count', 0)}, "
                        f"独特度: {item.get('uniqueness_score', 0)}",
                "source": "anima_tools",
                "post_count": item.get("post_count", 0),
                "uniqueness_score": item.get("uniqueness_score", 0),
                "image_url": image_url,
                "id": item_id,
                "p": partition,
                "style": "anime",
            })
        logger.info(f"[AnimaData] Anima-Tools 画师: {len(items)} 条 (JS)")
        return items

    def _load_anima_tools_characters(self) -> list[dict]:
        """从 Anima-Tools character_data.js 加载角色数据（含 CDN 图片）"""
        raw = self._extract_js_array(_ANIMA_TOOLS_JS_DIR / "character_data.js")
        if not raw:
            return []
        items = []
        for item in raw:
            name = (item.get("name") or "").strip()
            if not name:
                continue
            copyright_ = (item.get("copyright") or "").strip()
            gender = (item.get("gender") or "").strip()
            hair = (item.get("hair") or "").strip()
            eye = (item.get("eye") or "").strip()
            tags_parts = [name]
            if copyright_:
                tags_parts.append(copyright_)
            if gender:
                tags_parts.append(gender)
            if hair:
                tags_parts.append(f"{hair} hair")
            if eye:
                tags_parts.append(f"{eye} eyes")
            raw_name = f"{name}, {copyright_}" if copyright_ else name
            image_url = (
                f"https://blobs.animadex.net/Outputs/thumbs/"
                f"{quote(raw_name)}.webp"
            )
            items.append({
                "name": name,
                "tags": ", ".join(tags_parts),
                "category": copyright_,
                "note": (f"作品: {copyright_}, 发色: {hair}, 瞳色: {eye}"
                         if copyright_ else f"发色: {hair}, 瞳色: {eye}"),
                "source": "anima_tools",
                "post_count": item.get("post_count", 0),
                "gender": gender,
                "hair": hair,
                "eye": eye,
                "image_url": image_url,
                "name_cn": "",
                "category_cn": _CHARACTER_CATEGORY_CN.get(copyright_, copyright_),
                "style": "anime",
            })
        logger.info(f"[AnimaData] Anima-Tools 角色: {len(items)} 条 (JS)")
        return items

    def _load_anima_tools_clothing(self) -> list[dict]:
        """从 Anima-Tools clothing_data.js 加载服装数据"""
        raw = self._extract_js_array(_ANIMA_TOOLS_JS_DIR / "clothing_data.js")
        if not raw:
            return []
        items = []
        for item in raw:
            name_zh = (item.get("name_zh") or "").strip()
            name_en = (item.get("name") or "").strip()
            display_name = name_zh or name_en
            if not display_name:
                continue
            tags = (item.get("tags_zh") or item.get("tags") or "").strip()
            categories = item.get("categories") or []
            traits = item.get("traits") or []
            items.append({
                "name": display_name,
                "name_en": name_en,
                "tags": tags,
                "category": "; ".join(categories) if categories else "",
                "note": " | ".join(traits) if traits else "",
                "source": "anima_tools",
                "image_url": (item.get("preview") or "").strip(),
                "style": "general",
                "categories": categories,
                "traits": traits,
            })
        logger.info(f"[AnimaData] Anima-Tools 服装: {len(items)} 条 (JS)")
        return items

    def _check_anima_tools_fallback(self):
        """检查 anima 分类下是否有本地数据，没有则从 Anima-Tools JS 加载"""
        anima_dir = self.data_dir / "anima"
        anima_jsons = list(anima_dir.rglob("*.json")) if anima_dir.exists() else []

        # 哪些分类需要在 anima 下有数据
        expected = {
            "anima/artists": self._load_anima_tools_artists,
            "anima/characters": self._load_anima_tools_characters,
            "anima/clothing": self._load_anima_tools_clothing,
        }

        for cat_key, loader_fn in expected.items():
            # 本分类已有本地 JSON → 跳过
            has_local = any(str(fp.relative_to(self.data_dir)) == f"{cat_key}.json"
                            for fp in anima_jsons)
            if has_local:
                continue
            # 读取 Anima-Tools JS
            data = loader_fn()
            if data:
                self._datasets[cat_key] = data

    def load_all(self):
        """扫描 data/ 下所有 .json 文件并加载，缺失的 anima 数据从 Anima-Tools JS 补充"""
        self._datasets.clear()
        self._name_index.clear()
        if not self.data_dir.exists():
            logger.warning(f"[AnimaData] 数据目录不存在: {self.data_dir}")
            return

        for fpath in sorted(self.data_dir.rglob("*.json")):
            rel = fpath.relative_to(self.data_dir)
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if not isinstance(data, list):
                    logger.warning(f"[AnimaData] 跳过非数组文件: {rel}")
                    continue
                category = str(rel.parent / rel.stem)
                self._datasets[category] = data
                logger.info(f"[AnimaData] 加载 {category}: {len(data)} 条")
            except Exception as e:
                logger.warning(f"[AnimaData] 加载失败 {rel}: {e}")

        # 补充缺失的 anima 数据（从 Anima-Tools JS）
        self._check_anima_tools_fallback()

        self._build_index()
        self._loaded = True
        total = sum(len(v) for v in self._datasets.values())
        logger.info(f"[AnimaData] 共加载 {len(self._datasets)} 个分类, {total} 条数据")

    def _build_index(self):
        """构建名称全文索引"""
        for cat, items in self._datasets.items():
            for idx, item in enumerate(items):
                name = (item.get("name") or "").lower()
                tags = (item.get("tags") or "").lower()
                note = (item.get("note") or "").lower()
                full_text = f"{name} {tags} {note}"
                # 按空格/逗号/斜杠分词作为关键词
                words = set(re.split(r'[\s,/]+', full_text))
                for word in words:
                    word = word.strip()
                    if len(word) < 2:
                        continue
                    if word not in self._name_index:
                        self._name_index[word] = []
                    self._name_index[word].append((cat, idx))

    def search(self, keyword: str, top_k: int = 10) -> list[dict]:
        """搜索关键词，返回匹配结果"""
        if not self._loaded or not keyword:
            return []
        kw = keyword.lower().strip()
        if not kw:
            return []

        # 分词搜索
        words = re.split(r'[\s,/]+', kw)
        matched_scores: dict[tuple[str, int], float] = {}

        for word in words:
            word = word.strip()
            if len(word) < 2:
                continue
            # 精确匹配
            if word in self._name_index:
                for cat, idx in self._name_index[word]:
                    key = (cat, idx)
                    matched_scores[key] = matched_scores.get(key, 0) + 2.0
            # 前缀匹配
            for indexed_word, indices in self._name_index.items():
                if indexed_word.startswith(word) and indexed_word != word:
                    for cat, idx in indices:
                        key = (cat, idx)
                        matched_scores[key] = matched_scores.get(key, 0) + 1.0

        # 按评分排序取 top_k
        scored = []
        for (cat, idx), score in matched_scores.items():
            item = self._datasets[cat][idx]
            scored.append((score, cat, item))

        scored.sort(key=lambda x: -x[0])
        results = []
        seen = set()
        for score, cat, item in scored:
            dedup_key = (cat, item.get("name", ""))
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            results.append({
                "category": cat,
                "name": item.get("name", ""),
                "tags": item.get("tags", ""),
                "note": item.get("note", ""),
                "score": round(score, 1),
                "source": item.get("source", "anima"),
            })
            if len(results) >= top_k:
                break

        return results

    def get_statistics(self) -> dict:
        """获取数据统计信息"""
        stats = {}
        for cat, items in self._datasets.items():
            # 兼容 Windows \ 和 Unix / 两种路径分隔符
            parts = cat.split(os.sep)
            if len(parts) < 2:
                # 可能是 Unix 风格分隔符（如 anima/artists）
                parts = cat.split("/")
            if len(parts) < 2:
                continue
            domain = parts[0]
            if domain not in stats:
                stats[domain] = {"count": 0, "files": 0}
            stats[domain]["count"] += len(items)
            stats[domain]["files"] += 1
        return stats

    @property
    def is_loaded(self) -> bool:
        return self._loaded


# ====================================================================
# 模块级函数：供 main.py 的魔导书 API 直接调用（运行时加载 Anima-Tools JS）
# ====================================================================

_ANIMA_LOADER_CACHE: dict[str, list[dict]] = {}
"""{source_name: items} — 全局缓存，避免每次请求都解析 JS"""


def _loader_extract_js_array(filepath: Path) -> list:
    """从 JS 文件中提取 JSON 数组"""
    if not filepath.exists():
        return []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            text = f.read()
        start = text.find('[')
        end = text.rfind(']')
        if start == -1 or end == -1:
            return []
        return json.loads(text[start:end+1])
    except Exception:
        return []


def load_anima_tools_source(source_name: str) -> list[dict]:
    """从 Anima-Tools JS 加载指定源的数据（artists / characters / clothing）"""
    # 缓存命中
    if source_name in _ANIMA_LOADER_CACHE:
        return _ANIMA_LOADER_CACHE[source_name]

    js_dir = _ANIMA_TOOLS_JS_DIR
    items = []

    if source_name == "artists":
        raw = _loader_extract_js_array(js_dir / "data.js")
        seen = set()
        for item in raw:
            name = (item.get("name") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            item_id = str(item.get("id", ""))
            partition = item.get("p", 1)
            image_url = (
                f"https://fastly.jsdelivr.net/gh/ThetaCursed/"
                f"Anima-Assets@main/images/{partition}/{item_id}.webp"
            )
            items.append({
                "name": name,
                "tags": f"@{name}",
                "category": "",
                "note": f"作品数: {item.get('post_count', 0)}, "
                        f"独特度: {item.get('uniqueness_score', 0)}",
                "source": "anima_tools",
                "post_count": item.get("post_count", 0),
                "uniqueness_score": item.get("uniqueness_score", 0),
                "image_url": image_url,
                "id": item_id,
                "p": partition,
                "style": "anime",
            })

    elif source_name == "characters":
        raw = _loader_extract_js_array(js_dir / "character_data.js")
        for item in raw:
            name = (item.get("name") or "").strip()
            if not name:
                continue
            copyright_ = (item.get("copyright") or "").strip()
            gender = (item.get("gender") or "").strip()
            hair = (item.get("hair") or "").strip()
            eye = (item.get("eye") or "").strip()
            tags_parts = [name]
            if copyright_:
                tags_parts.append(copyright_)
            if gender:
                tags_parts.append(gender)
            if hair:
                tags_parts.append(f"{hair} hair")
            if eye:
                tags_parts.append(f"{eye} eyes")
            raw_name = f"{name}, {copyright_}" if copyright_ else name
            image_url = (
                f"https://blobs.animadex.net/Outputs/thumbs/"
                f"{quote(raw_name)}.webp"
            )
            items.append({
                "name": name,
                "tags": ", ".join(tags_parts),
                "category": copyright_,
                "note": (f"作品: {copyright_}, 发色: {hair}, 瞳色: {eye}"
                         if copyright_ else f"发色: {hair}, 瞳色: {eye}"),
                "source": "anima_tools",
                "post_count": item.get("post_count", 0),
                "gender": gender,
                "hair": hair,
                "eye": eye,
                "image_url": image_url,
                "name_cn": "",
                "category_cn": _CHARACTER_CATEGORY_CN.get(copyright_, copyright_),
                "style": "anime",
            })

    elif source_name == "clothing":
        raw = _loader_extract_js_array(js_dir / "clothing_data.js")
        for item in raw:
            name_zh = (item.get("name_zh") or "").strip()
            name_en = (item.get("name") or "").strip()
            display_name = name_zh or name_en
            if not display_name:
                continue
            tags = (item.get("tags_zh") or item.get("tags") or "").strip()
            categories = item.get("categories") or []
            traits = item.get("traits") or []
            items.append({
                "name": display_name,
                "name_en": name_en,
                "tags": tags,
                "category": "; ".join(categories) if categories else "",
                "note": " | ".join(traits) if traits else "",
                "source": "anima_tools",
                "image_url": (item.get("preview") or "").strip(),
                "style": "general",
                "categories": categories,
                "traits": traits,
            })

    _ANIMA_LOADER_CACHE[source_name] = items
    return items


def _is_anima_source(source_path: str) -> bool:
    """判断某个 source path 是否是 Anima-Tools 只读源"""
    normal = source_path.replace('\\', '/').lower()
    # 去掉 .json 后缀再比较
    if normal.endswith('.json'):
        normal = normal[:-5]
    for name in ("artists", "characters", "clothing"):
        if normal == name or normal.endswith(f"/{name}") or normal.endswith(f"/anima/{name}"):
            return True
    return False


_ANIMA_SOURCE_NAMES = [
    ("anima/artists", "artists", "画师"),
    ("anima/characters", "characters", "角色"),
    ("anima/clothing", "clothing", "服装"),
]
