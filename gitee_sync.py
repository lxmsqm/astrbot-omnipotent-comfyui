# -*- coding: utf-8 -*-
"""
gitee_sync.py — 从 Gitee 私有仓库拉取云端词库/缓存（v4.3.0「📥 同步云端数据库」后端）

仓库布局（由 PC 端 gitee_upload_v2.py 上传生成）：
  words/<大分类>/<子分类>.json   ← 39 个词库（含 k2/）
  words/k2/*.json               ← K2 词库
  anima_cache/*.json            ← 角色/画师大缓存（按需下载）
  manifest.json                 ← {updated_at, files:{path:size}, ...}

下载规则：
  - words/ 下的词库 → 覆盖到插件内 data/（词库是插件数据源，必须在插件内）
  - anima_cache/ 下的缓存 → 只下载 config 里勾选的（默认两个都下）→ 外部 user/ 目录
  - 单文件 GET raw 下载（私有仓库走 access_token 参数），失败重试 3 次

用法（WebUI API 调）：
  GiteeSync(plugin).sync(token, repo, include_cache) → dict 结果
"""

import base64
import json
import time
import urllib.request
import urllib.parse
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_REPO = "heigulin/astrbot-comfyui-data"
API_BASE = "https://gitee.com/api/v5/repos"


def _quote_path(p: str) -> str:
    """仓库内路径 URL 编码（保留 /）"""
    return urllib.parse.quote(p, safe="/")


class GiteeSync:
    """云端数据库同步器（一次实例只跑一次 sync；状态存外部数据目录）"""

    def __init__(self, plugin):
        self.plugin = plugin  # ComfyUILocalPlugin 实例（取 user_data_dir / 刷数据）

    # ------------------------------------------------------------------
    def _state_path(self) -> Path:
        return Path(self.plugin._user_data_dir) / "gitee_sync.json"

    def _read_state(self) -> dict:
        try:
            return json.loads(self._state_path().read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_state(self, st: dict):
        try:
            self._state_path().write_text(
                json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"[GiteeSync] 写状态失败: {e}")

    def last_state(self) -> dict:
        """上次同步结果（WebUI 显示用；从未同步过返回 {}）"""
        return self._read_state()

    # ------------------------------------------------------------------
    @staticmethod
    def _api_url(repo: str, path: str, token: str) -> str:
        return f"{API_BASE}/{repo}/contents/{_quote_path(path)}?access_token={token}&ref=master"

    def _fetch(self, url: str, binary: bool = False, retries: int = 3, timeout: int = 300):
        """GET 下载（重试 3 次，退避 5/10/15s）"""
        last_err = None
        for attempt in range(1, retries + 1):
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": "AstrBot-ComfyUI-Plugin-Sync",
                    "Accept": "*/*",
                })
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = resp.read()
                return data if binary else data.decode("utf-8")
            except Exception as e:
                last_err = e
                logger.warning(f"[GiteeSync] 下载失败(第{attempt}次): {e}")
                if attempt < retries:
                    time.sleep(5 * attempt)
        raise last_err

    def _list_dir(self, repo: str, path: str, token: str) -> list:
        """列目录 → [{name,type,size}]；目录不存在返回 []"""
        try:
            data = json.loads(self._fetch(self._api_url(repo, path, token)))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _get_file_bytes(self, repo: str, repo_path: str, token: str):
        """下载文件原始字节。
        ★ 实测坑：contents 接口对大文件会静默截断 base64 content（28MB 文件只回
          恰好 10MB 解码字节，且不报错）——所以先取元数据（size/sha），解码后
          长度对不上就走 git/blobs 接口（无截断，实测可取完整 28MB）。"""
        meta = json.loads(self._fetch(self._api_url(repo, repo_path, token)))
        size = int(meta.get("size") or 0)
        raw = base64.b64decode((meta.get("content") or "").replace("\n", ""))
        if len(raw) != size:
            sha = meta.get("sha")
            if not sha:
                raise RuntimeError(f"{repo_path}: 内容不完整且无 sha（无法走 blobs 兜底）")
            logger.info(f"[GiteeSync] {repo_path} contents 截断({len(raw)}/{size})，改走 blobs API")
            blob = json.loads(self._fetch(
                f"{API_BASE}/{repo}/git/blobs/{sha}?access_token={token}", timeout=600))
            raw = base64.b64decode((blob.get("content") or "").replace("\n", ""))
        return raw, size

    def _download_file(self, repo: str, repo_path: str, token: str, dest: Path,
                       expect_size: int = 0) -> bool:
        """下载单个文件到 dest（先写 .tmp 再原子替换，防中断损坏）。
        ★ 大小校验失败绝不落盘：旧版"仍写入"曾把 contents 截断的 10MB 坏缓存
          存进用户目录，导致下次启动 JSON 解析失败回退 API 重拉（10-20 分钟）。"""
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            raw, size = self._get_file_bytes(repo, repo_path, token)
            if expect_size and len(raw) != expect_size:
                raise RuntimeError(f"大小校验失败(得到 {len(raw)}B, 期望 {expect_size}B)")
            tmp.write_bytes(raw)
            tmp.replace(dest)
            return True
        except Exception as e:
            logger.warning(f"[GiteeSync] 下载 {repo_path} 失败: {e}")
            return False

    # ------------------------------------------------------------------
    def sync(self, token: str, repo: str = DEFAULT_REPO,
             include_artists: bool = True, include_characters: bool = True) -> dict:
        """执行同步。返回 {ok, words_ok, words_fail, cache_ok, cache_fail, updated_at, errors[]}"""
        token = (token or "").strip()
        if not token:
            return {"ok": False, "error": "未填写 Gitee 私人令牌（gitee_token）"}
        repo = (repo or "").strip() or DEFAULT_REPO
        res = {"ok": False, "words_ok": 0, "words_fail": 0, "cache_ok": 0,
               "cache_fail": 0, "updated_at": "", "repo": repo,
               "errors": [], "started_at": time.strftime("%Y-%m-%d %H:%M:%S")}

        try:
            from .data_paths import data_dir_resolver, user_data_dir_resolver
        except ImportError:
            # 独立脚本/导入校验场景（非包上下文）回退直接导入
            from data_paths import data_dir_resolver, user_data_dir_resolver

        # 1) manifest（拿文件清单与更新时间；没有也能继续）
        manifest = {}
        try:
            _m = json.loads(self._fetch(self._api_url(repo, "manifest.json", token)))
            manifest = json.loads(base64.b64decode(
                (_m.get("content") or "").replace("\n", "")).decode("utf-8"))
            res["updated_at"] = manifest.get("updated_at", "")
        except Exception as e:
            res["errors"].append(f"manifest.json 读取失败(继续用目录列举): {e}")

        want_cache = set()
        if include_artists:
            want_cache.add("anima_artists_cache.json")
        if include_characters:
            want_cache.add("anima_characters_cache.json")

        # 2) 遍历 words/ 下载词库 → 插件内 data/
        data_dir = Path(data_dir_resolver())
        top_dirs = self._list_dir(repo, "words", token)
        words_tasks = []  # (repo_path, dest, expect_size)
        for entry in top_dirs:
            if entry.get("type") != "dir":
                continue
            dname = entry.get("name", "")
            for f in self._list_dir(repo, f"words/{dname}", token):
                if f.get("type") != "file" or not str(f.get("name", "")).endswith(".json"):
                    continue
                dest = (data_dir / "k2" if dname == "k2" else data_dir / dname) / f["name"]
                words_tasks.append((f"words/{dname}/{f['name']}", dest, f.get("size") or 0))
        for rp, dest, size in words_tasks:
            if self._download_file(repo, rp, token, dest, size):
                res["words_ok"] += 1
            else:
                res["words_fail"] += 1
                res["errors"].append(f"词库下载失败: {rp}")

        # 3) 缓存下载 → 外部 user/ 目录（大小检查，28MB 级别）
        user_dir = Path(user_data_dir_resolver())
        cache_entries = self._list_dir(repo, "anima_cache", token)
        for f in cache_entries:
            fname = f.get("name", "")
            if f.get("type") != "file" or fname not in want_cache:
                continue
            size = f.get("size") or 0
            local = user_dir / fname
            # 本地已有同大小文件 → 跳过（28MB 重复下载没必要）
            if local.exists() and size and local.stat().st_size == size:
                logger.info(f"[GiteeSync] {fname} 本地已是最新，跳过")
                res["cache_ok"] += 1
                continue
            if self._download_file(repo, f"anima_cache/{fname}", token, local, size):
                res["cache_ok"] += 1
            else:
                res["cache_fail"] += 1
                res["errors"].append(f"缓存下载失败: {fname}")

        # 4) 记录状态 + 刷新内存数据
        res["ok"] = (res["words_fail"] == 0 and res["cache_fail"] == 0
                     and (res["words_ok"] + res["cache_ok"]) > 0)
        res["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._write_state(res)

        if res["words_ok"]:
            self._reload_after_sync()
        return res

    def _reload_after_sync(self):
        """同步词库后刷新内存里的魔导书数据（不重启也能用上新城库）"""
        try:
            self.plugin.anima_data.load_all()
            logger.info("[GiteeSync] 已重新加载词库数据")
        except Exception as e:
            logger.warning(f"[GiteeSync] 重新加载数据失败（重启后生效）: {e}")
