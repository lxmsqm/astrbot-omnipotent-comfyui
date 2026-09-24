# -*- coding: utf-8 -*-
"""
data_paths.py — 插件数据分离：统一的大文件路径解析与迁移（v4.3.0）

背景：AstrBot 插件商店限制插件目录 <18MB，而本插件 data/ 下有 ~40MB 大文件：
  - data/user/ 下的角色/画师缓存 json（~33MB，用户使用后自动生成）
  - data/anima_tools/*.js 回退源（7.8MB）
  - data/user/config.json 用户配置
词库（词库分类目录/ + k2/）留在插件内（进 Git，也是云端同步的管理对象）。

新布局（重构后）：
  <AstrBot>/data/comfyui_allinone_data/          ← 外部数据根（AstrBot 数据目录下）
    ├── user/config.json / *_cache.json          ← 缓存+配置
    ├── anima_tools/*.js                         ← JS 回退源
    └── sync.lock / gitee_sync.json              ← 云端同步状态
  <插件>/data/词库分类目录/ k2/                   ← 保留在插件内

兼容规则（data_dir_resolver）：
  1) 外部目录存在历史数据 → 用外部（新部署升级后自然生效）
  2) 外部目录为空/不存在 → 首次启动由 migrate_out() 把插件内旧数据搬出去
  3) 任何一步失败 → 回退插件内 data/（绝对不能让插件起不来）

★ 历史坑（重构保持的行为）：
  - AnimaDataManager 的 user_data_dir 绝不能传空：缓存必须落在 user/ 下，
    传空会回退 data 顶层，load_all() 会把缓存 json 当成词库数据源加载。
  - load_all() / _webui_grimoire_sources() 扫描 data/*.json 时必须跳过
    anima_tools / cache / prompt_log.json / user（现在外部目录已不在扫描范围）。
"""

import json
import shutil
import time
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# AstrBot 数据目录定位标记：从插件目录逐级向上找 <root>/data/plugins/<本插件>
_PLUGIN_MARKER = ("data", "plugins")


def _plugin_root() -> Path:
    return Path(__file__).resolve().parent


def get_astrbot_data_dir() -> Path:
    """定位 AstrBot 数据目录 <root>/data/。
    从插件目录向上找 data/plugins/<本插件> 结构；找不到则兜底 /root/AstrBot/data
    （Termux proot 部署机的标准路径）。"""
    root = _plugin_root()
    # 结构：.../<root>/data/plugins/astrbot_plugin_comfyui_local/data_paths.py
    parts = root.parts
    for i in range(len(parts) - 2):
        if parts[i] == "data" and parts[i + 1] == "plugins":
            return Path(*parts[: i + 1])
    fallback = Path("/root/AstrBot/data")
    if fallback.exists():
        return fallback
    # 最终兜底：插件目录旁建一个（Windows 本地调试时也能跑）
    return root.parent / "AstrBot_data"


def external_data_root() -> Path:
    """外部数据根目录：<AstrBot>/data/comfyui_allinone_data/"""
    return get_astrbot_data_dir() / "comfyui_allinone_data"


def _dir_has_content(d: Path) -> bool:
    """目录是否存在且非空"""
    try:
        return d.is_dir() and any(d.iterdir())
    except Exception:
        return False


def data_dir_resolver() -> Path:
    """★ 核心入口：返回插件 data/ 目录（词库所在，供词库扫描/魔导书/随机图使用）。
    词库永远在插件内，直接返回插件 data/（保留函数是为了调用点语义清晰、
    未来若词库也外置只需改这里）。"""
    return _plugin_root() / "data"


def user_data_dir_resolver() -> Path:
    """★ 核心入口：返回用户数据目录（config.json / 缓存 / prompt_log 的真实位置）。
    优先外部 comfyui_allinone_data/user/；外部不存在时回退插件内 data/user/
    （保证任何异常情况下插件仍可用旧路径工作）。"""
    ext_user = external_data_root() / "user"
    if _dir_has_content(ext_user):
        return ext_user
    return data_dir_resolver() / "user"


def anima_tools_dir_resolver() -> Path:
    """★ 核心入口：返回 Anima-Tools JS 回退源目录。
    优先外部 anima_tools/；不存在则回退插件内 data/anima_tools/。"""
    ext = external_data_root() / "anima_tools"
    if _dir_has_content(ext):
        return ext
    return data_dir_resolver() / "anima_tools"


def resolve_all() -> dict:
    """一次性解析三个目录（供 __init__ 打日志/调试）。"""
    return {
        "data_dir": data_dir_resolver(),
        "user_data_dir": user_data_dir_resolver(),
        "anima_tools_dir": anima_tools_dir_resolver(),
        "external_root": external_data_root(),
    }


# ----------------------------------------------------------------------
# 迁移：插件内旧数据 → 外部目录（只搬一次；幂等、失败不影响启动）
# ----------------------------------------------------------------------

# 要搬到外部目录的顶层条目（位于插件 data/ 下）
_MIGRATE_TOP = ["user", "anima_tools"]


def migrate_out() -> dict:
    """把插件内的大文件搬到外部数据目录（幂等）。
    返回 {"moved": [...], "skipped": [...], "errors": [...]}。
    ⚠️ 绝不抛异常：迁移失败只记日志，插件回退旧路径照常运行。
    """
    res = {"moved": [], "skipped": [], "errors": []}
    try:
        src_root = data_dir_resolver()
        ext_root = external_data_root()
        ext_root.mkdir(parents=True, exist_ok=True)
        for name in _MIGRATE_TOP:
            src = src_root / name
            dst = ext_root / name
            if not src.exists():
                res["skipped"].append(f"{name} (插件内不存在)")
                continue
            if _dir_has_content(dst):
                # 外部已有数据：只把插件内"外部没有"的文件补过去（如新增的 js）
                copied = 0
                for f in src.rglob("*"):
                    if not f.is_file():
                        continue
                    rel = f.relative_to(src)
                    target = dst / rel
                    if not target.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(f), str(target))
                        copied += 1
                if copied:
                    res["moved"].append(f"{name} (补齐 {copied} 个新文件)")
                else:
                    res["skipped"].append(f"{name} (外部已有)")
                # 插件内副本改名为 _migrated_backup 留观（不删，用户可手动清）
                backup = src_root / f"{name}_migrated_backup"
                if not backup.exists():
                    try:
                        src.rename(backup)
                        logger.info(f"[DataPaths] 插件内 {name} 已改名 {backup.name}（外部目录生效）")
                    except Exception as e:
                        logger.warning(f"[DataPaths] 改名 {name} 失败（不影响运行）: {e}")
                continue
            # 外部没有：整体搬过去（copy 后将源改名留观，重启后外部目录已有内容 → 走上面的分支）
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copytree(str(src), str(dst), dirs_exist_ok=True)
                backup = src_root / f"{name}_migrated_backup"
                if backup.exists():
                    shutil.rmtree(str(backup), ignore_errors=True)
                src.rename(backup)
                res["moved"].append(f"{name} ({_dir_size_mb(dst):.1f}MB)")
                logger.info(f"[DataPaths] 已迁移 {name} → {dst}")
            except Exception as e:
                res["errors"].append(f"{name}: {e}")
                logger.warning(f"[DataPaths] 迁移 {name} 失败（插件回退旧路径）: {e}")
        # 迁移标记（供 WebUI 显示状态）
        try:
            (ext_root / ".migrated").write_text(
                json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "result": res},
                           ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    except Exception as e:
        res["errors"].append(str(e))
        logger.warning(f"[DataPaths] 迁移初始化失败（插件回退旧路径）: {e}")
    return res


def _dir_size_mb(d: Path) -> float:
    total = 0
    try:
        for f in d.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    except Exception:
        pass
    return total / 1048576


def migration_status() -> dict:
    """当前迁移/布局状态（WebUI 用）。"""
    ext = external_data_root()
    return {
        "external_root": str(ext),
        "external_active": _dir_has_content(ext / "user") or _dir_has_content(ext / "anima_tools"),
        "user_dir": str(user_data_dir_resolver()),
        "anima_tools_dir": str(anima_tools_dir_resolver()),
        "plugin_data_mb": _dir_size_mb(data_dir_resolver()),
        "external_mb": _dir_size_mb(ext),
    }
