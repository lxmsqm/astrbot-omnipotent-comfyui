# -*- coding: utf-8 -*-
"""
gitee_sync.py — 从 Gitee 仓库拉取云端词库/缓存（v4.3.2「📥 同步云端数据库」后端）

仓库布局（由 PC 端 gitee_upload_v2.py 上传生成）：
  words/<大分类>/<子分类>.json   ← 39 个词库（含 k2/）
  words/k2/*.json               ← K2 词库
  anima_cache/*.json            ← 角色/画师大缓存（按需下载）
  manifest.json                 ← {updated_at, files:{path:size}, ...}

两种模式（v4.3.2 起）：
  ★ 匿名模式（无 token，公开仓库）：
    - 下载/清单全部走 raw 直链（raw/master/...），不占 API 限额
    - 文件清单优先读 manifest.json；manifest 缺失时回退 contents API 匿名列举
    - 仓库若仍是私有 → raw 返回 403，报错提示「设为开源或填令牌」
  ★ 令牌模式（有 token，私有/公开均可）：
    - 走 api/v5/contents 接口（base64 content），大文件截断时自动改 git/blobs

实测坑（勿回退）：
  - 私有仓库 raw 地址带 token 也 403（Access denied）→ 私有必须走 contents API
  - contents 接口对大文件静默截断 base64 content 到恰好 10MB → 必须核对 size，
    不一致走 git/blobs；大小校验失败绝不落盘
"""

import base64
import json
import time
import urllib.request
import urllib.parse
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_REPO = "lxmsqm/astrbot-comfyui-data"
DEFAULT_PROVIDER = "github"
"""v4.3.4 起默认走 GitHub 公开镜像（lxmsqm/astrbot-comfyui-data，匿名 raw）。
Gitee 通道废弃原因：原仓库被 Gitee 打「涉嫌外链滥用(RAW)」标记禁止转公开，
新建公开仓库也被帐号级限制拦截（要求绑手机+复审），公开路线在 Gitee 走不通。
GitHub raw 对国内直连不稳定，下载失败会自动重试，必要时用户可自行挂代理；
令牌模式保留（gitee 私有仓库个人备份仍可用）。"""
GITEE_API = "https://gitee.com/api/v5/repos"
GH_RAW = "https://raw.githubusercontent.com"


def _quote_path(p: str) -> str:
    """仓库内路径 URL 编码（保留 /）"""
    return urllib.parse.quote(p, safe="/")


class GiteeSync:
    """云端数据库同步器（状态存用户数据目录 gitee_sync.json）"""

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
    def _raw_url(repo: str, path: str, provider: str = "gitee") -> str:
        """raw 直链（公开仓库免认证；gitee 私有仓库 403）"""
        if provider == "github":
            return f"{GH_RAW}/{repo}/master/{_quote_path(path)}"
        return f"https://gitee.com/{repo}/raw/master/{_quote_path(path)}"

    @staticmethod
    def _api_url(repo: str, path: str, token: str) -> str:
        """contents API（gitee 专用；token 可为空 → 匿名，公开仓库可用）"""
        q = "ref=master"
        if token:
            q = f"access_token={token}&ref=master"
        return f"{GITEE_API}/{repo}/contents/{_quote_path(path)}?{q}"

    def _fetch(self, url: str, binary: bool = False, retries: int = 3,
               timeout: int = 300):
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
        """contents API 列目录 → [{name,type,size}]；失败返回 []"""
        try:
            data = json.loads(self._fetch(self._api_url(repo, path, token)))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    # ------------------------------------------------------------------
    def _download_token_mode(self, repo: str, repo_path: str, token: str,
                             dest: Path, expect_size: int = 0) -> bool:
        """令牌模式下载：contents API → 截断时 blobs 兜底 → 严格大小校验"""
        try:
            meta = json.loads(self._fetch(self._api_url(repo, repo_path, token)))
            size = int(meta.get("size") or 0)
            raw = base64.b64decode((meta.get("content") or "").replace("\n", ""))
            if len(raw) != size:
                sha = meta.get("sha")
                if not sha:
                    raise RuntimeError(f"内容不完整且无 sha（无法走 blobs 兜底）")
                logger.info(f"[GiteeSync] {repo_path} contents 截断({len(raw)}/{size})，改走 blobs API")
                blob = json.loads(self._fetch(
                    f"{API_BASE}/{repo}/git/blobs/{sha}?access_token={token}", timeout=600))
                raw = base64.b64decode((blob.get("content") or "").replace("\n", ""))
            if expect_size and len(raw) != expect_size:
                raise RuntimeError(f"大小校验失败(得到 {len(raw)}B, 期望 {expect_size}B)")
            self._atomic_write(dest, raw)
            return True
        except Exception as e:
            logger.warning(f"[GiteeSync] 下载 {repo_path} 失败: {e}")
            return False

    def _download_anon_mode(self, repo: str, repo_path: str, dest: Path,
                            expect_size: int = 0, provider: str = "gitee") -> bool:
        """匿名模式下载：raw 直链（公开仓库）→ 大小校验（manifest 提供期望值）"""
        try:
            raw = self._fetch(self._raw_url(repo, repo_path, provider), binary=True, timeout=600)
            if expect_size and len(raw) != expect_size:
                raise RuntimeError(f"大小校验失败(得到 {len(raw)}B, 期望 {expect_size}B)")
            self._atomic_write(dest, raw)
            return True
        except Exception as e:
            logger.warning(f"[GiteeSync] 下载 {repo_path} 失败: {e}")
            return False

    @staticmethod
    def _atomic_write(dest: Path, raw: bytes):
        """先写 .tmp 再原子替换（防中断损坏）；写入前确保目录存在"""
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_bytes(raw)
        tmp.replace(dest)

    # ------------------------------------------------------------------
    def sync(self, token: str, repo: str = DEFAULT_REPO,
             include_artists: bool = True, include_characters: bool = True,
             provider: str = DEFAULT_PROVIDER) -> dict:
        """执行同步。token 为空 → 匿名模式（要求仓库已公开）。
        provider: "github"（默认，匿名 raw）/ "gitee"（匿名 raw 或令牌 contents）。
        返回 {ok, words_ok, words_fail, cache_ok, cache_fail, updated_at, errors[], mode}"""
        token = (token or "").strip()
        repo = (repo or "").strip() or DEFAULT_REPO
        provider = (provider or DEFAULT_PROVIDER).strip().lower()
        anon = not token
        res = {"ok": False, "mode": f"anon@{provider}" if anon else f"token@{provider}",
               "words_ok": 0, "words_fail": 0, "cache_ok": 0,
               "cache_fail": 0, "updated_at": "", "repo": repo,
               "errors": [], "started_at": time.strftime("%Y-%m-%d %H:%M:%S")}

        try:
            from .data_paths import data_dir_resolver, user_data_dir_resolver
        except ImportError:
            from data_paths import data_dir_resolver, user_data_dir_resolver

        # ① 匿名模式可达性探测（github raw 国内直连可能被重置，重试已内置）
        if anon:
            try:
                self._fetch(self._raw_url(repo, "manifest.json", provider), timeout=60)
            except Exception as e:
                hint = ("GitHub raw 拉取失败（国内网络对 raw.githubusercontent.com 不稳定，"
                        "可稍后重试或配置系统代理）；仓库 " + repo)
                if provider == "gitee":
                    hint = ("仓库无法匿名访问（可能未公开或不存在）。请在 Gitee 仓库"
                            "「管理→基本信息→开源」设为公开；或回 WebUI 填写私人令牌。")
                return {"ok": False, "mode": res["mode"], "repo": repo,
                        "error": f"{hint}｜最后错误: {e}"}

        # ② manifest（匿名模式已探测过一次，这里带清单解析）
        manifest_files = {}
        try:
            if anon:
                manifest = json.loads(self._fetch(self._raw_url(repo, "manifest.json", provider), timeout=60))
            elif provider == "gitee":
                _m = json.loads(self._fetch(self._api_url(repo, "manifest.json", token)))
                manifest = json.loads(base64.b64decode(
                    (_m.get("content") or "").replace("\n", "")).decode("utf-8"))
            else:
                manifest = json.loads(self._fetch(
                    self._raw_url(repo, "manifest.json", "github"), timeout=60))
            res["updated_at"] = manifest.get("updated_at", "")
            manifest_files = manifest.get("files") or {}
        except Exception as e:
            res["errors"].append(f"manifest.json 读取失败(继续用目录列举): {e}")

        want_cache = set()
        if include_artists:
            want_cache.add("anima_artists_cache.json")
        if include_characters:
            want_cache.add("anima_characters_cache.json")

        data_dir = Path(data_dir_resolver())
        user_dir = Path(user_data_dir_resolver())

        # ③ 组任务清单：优先 manifest（匿名零列举成本），否则 contents 列举
        words_tasks = []   # (repo_path, dest, expect_size)
        cache_tasks = []   # (repo_path, dest, expect_size)
        if manifest_files:
            for rp, sz in manifest_files.items():
                sz = int(sz or 0)
                if rp.startswith("words/"):
                    # words/ 下的相对结构即插件 data/ 下的结构（含 k2/）
                    rel = rp[len("words/"):]
                    words_tasks.append((rp, data_dir / rel, sz))
                elif rp.startswith("anima_cache/"):
                    fname = rp[len("anima_cache/"):]
                    if fname in want_cache:
                        cache_tasks.append((rp, user_dir / fname, sz))
        else:
            # 回退：contents 列举（匿名模式下此调用不占多少限额：13 个目录）
            for entry in self._list_dir(repo, "words", token):
                if entry.get("type") != "dir":
                    continue
                dname = entry.get("name", "")
                for f in self._list_dir(repo, f"words/{dname}", token):
                    if f.get("type") != "file" or not str(f.get("name", "")).endswith(".json"):
                        continue
                    rel = f"{dname}/{f['name']}"
                    words_tasks.append((f"words/{rel}", data_dir / rel, f.get("size") or 0))
            for f in self._list_dir(repo, "anima_cache", token):
                fname = f.get("name", "")
                if f.get("type") != "file" or fname not in want_cache:
                    continue
                cache_tasks.append((f"anima_cache/{fname}", user_dir / fname, f.get("size") or 0))

        # ④ 下载词库 → 插件内 data/
        for rp, dest, size in words_tasks:
            ok = (self._download_anon_mode(repo, rp, dest, size, provider) if anon
                  else self._download_token_mode(repo, rp, token, dest, size))
            if ok:
                res["words_ok"] += 1
            else:
                res["words_fail"] += 1
                res["errors"].append(f"词库下载失败: {rp}")

        # ⑤ 缓存 → 外部 user/ 目录（本地同大小自动跳过）
        for rp, dest, size in cache_tasks:
            if dest.exists() and size and dest.stat().st_size == size:
                logger.info(f"[GiteeSync] {dest.name} 本地已是最新，跳过")
                res["cache_ok"] += 1
                continue
            ok = (self._download_anon_mode(repo, rp, dest, size, provider) if anon
                  else self._download_token_mode(repo, rp, token, dest, size))
            if ok:
                res["cache_ok"] += 1
            else:
                res["cache_fail"] += 1
                res["errors"].append(f"缓存下载失败: {dest.name}")

        # ⑥ 记录状态 + 刷新内存数据
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
