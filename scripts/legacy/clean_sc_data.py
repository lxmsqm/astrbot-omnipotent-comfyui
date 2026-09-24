"""
SmartComfy → 魔导书 重新整理
合并小分类、去掉无用字段、文件名统一 sc_分类.json
"""
import json
import os
from pathlib import Path

SMARTCOMFY_DATA = r"D:\win下载\SmartComfy-v1.0.9\Vue\dist\random-prompt"
PLUGIN_DATA = Path(r"C:\Users\HeiGuLin\.astrbot\data\plugins\astrbot_plugin_comfyui_local\data")

# 合并规则：哪些原始分类合并到一个文件
MERGE_RULES = [
    ("sc_画质.json", ["画质与渲染", "粒子光效"]),
    ("sc_风格.json", ["艺术风格", "3D渲染"]),
    ("sc_人物.json", ["人物主体", "发型与发色", "五官与表情"]),
    ("sc_服饰.json", ["服装与配饰"]),
    ("sc_材质.json", ["材质质感"]),
    ("sc_动作.json", ["动作姿态"]),
    ("sc_场景.json", ["场景与背景", "色彩与氛围", "构图与视角"]),
    ("sc_NSFW.json", ["NSFW"]),
]

def load_tags(source="anima"):
    """加载 SmartComfy 原始标签并按分类名索引"""
    path = os.path.join(SMARTCOMFY_DATA, f"tags-{source}.json")
    if not os.path.exists(path):
        print(f"❌ 找不到: {path}")
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    cats = {}
    for cat in data.get("categories", []):
        cat_name = cat["name"]
        entries = []
        for sub in cat.get("subcategories", []):
            for tag in sub.get("tags", []):
                en = tag["en"]
                entries.append({
                    "name": en,
                    "tags": en,
                    "style": cat_name,
                })
        cats[cat_name] = entries
    return cats

def main():
    cats = load_tags("anima")
    if not cats:
        return

    # 反查所有分类名
    all_cat_names = set()
    for _, names in MERGE_RULES:
        for n in names:
            all_cat_names.add(n)
    missing = all_cat_names - set(cats.keys())
    if missing:
        print(f"⚠️ 以下分类在源数据中未找到: {missing}")

    total = 0
    for fname, cat_names in MERGE_RULES:
        entries = []
        for cn in cat_names:
            if cn in cats:
                entries.extend(cats[cn])
        if not entries:
            continue
        # 去重（同名字的只留一条）
        seen = set()
        unique = []
        for e in entries:
            if e["name"].lower() not in seen:
                seen.add(e["name"].lower())
                unique.append(e)
        out_path = PLUGIN_DATA / fname
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(unique, f, ensure_ascii=False, indent=2)
        print(f"  {fname}  →  {len(unique)} 条 (去重前{len(entries)})")
        total += len(unique)

    print(f"\n✅ 共 {total} 条, 写入 {PLUGIN_DATA}/")

if __name__ == "__main__":
    print("整理 SmartComfy 标签...")
    main()
