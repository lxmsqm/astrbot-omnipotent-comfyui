"""
Anima 数据融合模块

数据目录结构 (data/)：
  anima/         - Anima 原始数据（画师、角色、服装）
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

logger = logging.getLogger(__name__)

class AnimaDataManager:
    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self._datasets: dict[str, list[dict]] = {}
        """{分类名: [{name, tags, ...}]}"""
        self._name_index: dict[str, list[tuple[str, int]]] = {}
        """{关键词: [(分类名, 索引), ...]}"""
        self._loaded = False

    def load_all(self):
        """扫描 data/ 下所有 .json 文件并加载"""
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
            parts = cat.split("\\")
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
