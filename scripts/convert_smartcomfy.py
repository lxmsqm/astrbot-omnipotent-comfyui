"""
SmartComfy 标签数据 → 魔导书格式 转换脚本
把 tags-anima.json 的层级标签转成魔导书能读的 flat JSON 文件
每个 SmartComfy 分类输出一个文件到 data/sc/ 目录
"""

import json
import os
from pathlib import Path

# 路径
SMARTCOMFY_DATA = r"D:\win下载\SmartComfy-v1.0.9\Vue\dist\random-prompt"
PLUGIN_DATA = Path(r"C:\Users\HeiGuLin\.astrbot\data\plugins\astrbot_plugin_comfyui_local\data")
OUT_DIR = PLUGIN_DATA / "sc"

def convert(source_name: str):
    """把 SmartComfy 的 tags JSON 转成魔导书格式"""
    src_path = os.path.join(SMARTCOMFY_DATA, f"tags-{source_name}.json")
    if not os.path.exists(src_path):
        print(f"❌ 找不到: {src_path}")
        return

    with open(src_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    categories = data.get("categories", [])
    total = 0
    out_dir = OUT_DIR / source_name
    out_dir.mkdir(parents=True, exist_ok=True)

    for cat in categories:
        cat_name = cat["name"]
        entries = []

        for sub in cat.get("subcategories", []):
            sub_name = sub["name"]
            sub_type = sub.get("type", "single")
            pool_group = sub.get("poolGroup", "")

            for tag in sub.get("tags", []):
                en = tag["en"]
                zh = tag.get("zh", "")
                # 用英文作为 name 和 tags（SD 标准标签）
                entries.append({
                    "name": en,
                    "tags": en,
                    "zh": zh,  # 保留中文翻译供参考
                    "style": "general",
                    "_subcategory": sub_name,
                    "_type": sub_type,
                    "_pool_group": pool_group,
                })

        if entries:
            # 用分类名做文件名
            safe_name = f"{cat.get('id', cat_name)}_{cat_name}"
            # 去掉不安全字符
            safe_name = "".join(c if c.isalnum() or c in '_ -' else '_' for c in safe_name)
            out_path = out_dir / f"{safe_name}.json"
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(entries, f, ensure_ascii=False, indent=2)
            total += len(entries)
            print(f"  📄 {out_path.name}  →  {len(entries)} 条")

    print(f"\n✅ {source_name}: 共 {total} 条, 写入 {out_dir}")


def convert_flat(source_name: str):
    """全部丢到一个大文件里（另一种组织方式）"""
    src_path = os.path.join(SMARTCOMFY_DATA, f"tags-{source_name}.json")
    if not os.path.exists(src_path):
        return

    with open(src_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    categories = data.get("categories", [])
    all_entries = []

    for cat in categories:
        for sub in cat.get("subcategories", []):
            for tag in sub.get("tags", []):
                all_entries.append({
                    "name": tag["en"],
                    "tags": tag["en"],
                    "zh": tag.get("zh", ""),
                    "style": cat["name"],
                    "_subcategory": sub["name"],
                })

    out_path = Path(PLUGIN_DATA) / f"sc_{source_name}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_entries, f, ensure_ascii=False, indent=2)

    print(f"\n📦 {out_path.name}  →  共 {len(all_entries)} 条（合并到一个文件）")


if __name__ == "__main__":
    print("=" * 50)
    print("SmartComfy → 魔导书 格式转换")
    print("=" * 50)

    # 按分类拆分（推荐：方便选择特定分类）
    print("\n--- 按分类拆分 ---")
    convert("anima")
    # convert("flux")  # flux 标签偏中文描述，暂不转

    # 也生成一个全集（可选）
    print("\n--- 合并文件 ---")
    convert_flat("anima")

    print("\n✅ 全部完成！魔导书数据源在:")
    print(f"   {OUT_DIR}")
