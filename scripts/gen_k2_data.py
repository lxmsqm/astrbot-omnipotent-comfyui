# -*- coding: utf-8 -*-
"""把 K2 词库（K2提示词词库分类v12.json）转换成魔导书数据源格式，写入 data/k2/ 目录。

转换结果：data/k2/<分区>.json，每个文件是一个分区数据源（大分类=k2，子分类=分区）。
条目格式与现有魔导书数据源一致：{name: 中文名, tags: 中文描述(不翻译), category: 所属字段}。

运行： python scripts/gen_k2_data.py
输出： data/k2/*.json（会被魔导书数据源扫描自动发现）
"""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "K2提示词词库分类v12.json")
OUT_DIR = os.path.join(ROOT, "data", "k2")

# 清空旧输出目录，避免残留合并
if os.path.isdir(OUT_DIR):
    import shutil
    shutil.rmtree(OUT_DIR)
os.makedirs(OUT_DIR, exist_ok=True)

with open(SRC, "r", encoding="utf-8") as f:
    data = json.load(f)

# 分区序号前缀清理（①拍摄角度 → 拍摄角度）
def clean_section(s):
    return re.sub(r'^[①②③④⑤⑥⑦⑧⑨⑩]', '', s).strip()

# 字段名清理：去掉括号后缀（（必选）（可选）等）与空白
def clean_field(s):
    s = re.sub(r'（.*?）', '', s)   # 去全角括号内容
    s = re.sub(r'\(.*?\)', '', s)   # 去半角括号内容
    return s.strip()

count = 0
for section_name, section in data.items():
    if section_name == 'meta':
        continue
    sec = clean_section(section_name)
    fields = section.get('fields', []) if isinstance(section, dict) else []
    all_entries = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        fname = clean_field(field.get('name', '')) or '未命名'
        for it in (field.get('items') or []):
            if not isinstance(it, dict):
                continue
            _desc = (it.get('desc') or '').strip()
            _tags = _desc or (it.get('name') or '').strip()   # desc 为空则回退用 name（K2 词条名本身即中文提示词片段）
            all_entries.append({
                'name': it.get('name', ''),
                'tags': _tags,               # 不翻译：K2 描述文本/词条名直接作为提示词内容
                'category': fname,           # 所属字段名
            })
    if not all_entries:
        continue
    out = os.path.join(OUT_DIR, sec + '.json')   # 每分区一个文件（大分类=k2，子分类=分区）
    if os.path.exists(out):
        with open(out, 'r', encoding='utf-8') as f:
            try:
                exist = json.load(f)
            except Exception:
                exist = []
        if isinstance(exist, list):
            exist.extend(all_entries)
            all_entries = exist
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(all_entries, f, ensure_ascii=False, indent=2)
    count += len(all_entries)
print(f"已生成 data/k2/ 数据源，共写入 {count} 条词条")

# 汇总输出
summary = {}
for fn in sorted(os.listdir(OUT_DIR)):
    if fn.endswith('.json'):
        p = os.path.join(OUT_DIR, fn)
        with open(p, 'r', encoding='utf-8') as f:
            try:
                n = len(json.load(f))
            except Exception:
                n = 0
        summary[fn] = n
print(f"共 {len(summary)} 个分区文件")
for fn in sorted(summary):
    print(f"  {fn}: {summary[fn]} 条")