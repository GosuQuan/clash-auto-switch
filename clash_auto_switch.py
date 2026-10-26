#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Clash 代理自动监听切换器
========================

适用于 Clash Verge Rev (verge-mihomo 内核)。

工作方式
--------
1. 通过 Clash 外部控制器 REST API（默认 127.0.0.1:9097）读取策略组与节点。
2. 周期性对「候选节点」做健康检查：
   - 先用组测速 API 批量拿延迟（一次请求，并发由内核完成，结果稳定）；
   - 再按延迟从低到高，用真实请求验证可用性 + 实测下载速度（吞吐）。
3. 依据「地区偏好 + 延迟 + 吞吐」综合打分，选出最优节点。
4. 若当前节点明显劣于最优节点，则通过 API 切换策略组到新节点。

核心规则（可由 config.json 调整）
--------------------------------
- 默认禁止香港（HK）节点；
- 日本、新加坡等低延迟地区优先；
- 香港节点即使延迟为 0 也不会被选中。

退出码：0 正常；1 启动失败。
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import re
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass, field, asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

APP_NAME = "clash-auto-switch"
if getattr(sys, "frozen", False):          # PyInstaller 打包后
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

if sys.platform == "darwin":
    # macOS：.app 包内不可写，数据放到用户应用支持目录
    APP_DIR = os.path.join(os.path.expanduser("~"), "Library",
                           "Application Support", APP_NAME)
    try:
        os.makedirs(APP_DIR, exist_ok=True)
    except Exception:
        pass
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
LOG_PATH = os.path.join(APP_DIR, "auto-switch.log")
STATE_PATH = os.path.join(APP_DIR, "state.json")

# ---------------------------------------------------------------- 默认配置

DEFAULT_CONFIG: Dict[str, Any] = {
    "controller": "127.0.0.1:9097",
    "secret": "",
    "mixed_port": 7897,

    # 需要接管切换的策略组；留空则自动挑选：
    # 优先「良心云」一类包含多个地区的组，其次第一个 selector。
    "target_groups": [],
    # 自动挑选策略组时，优先匹配这些名字（按顺序）
    "preferred_group_names": ["良心云", "节点选择", "Proxy", "PROXY", "GLOBAL"],

    # ---------------- 地区规则 ----------------
    # 硬性排除（永不选用）。默认禁止香港。
    "blocked_regions": ["香港", "Hong Kong", "HK"],
    # 地区优先级：数字越小越优先。未列出的地区排最后。
    "region_priority": {"日本": 1, "新加坡": 2, "台湾": 3, "美国": 4, "英国": 5},
    # 节点名前缀的附加偏好（例如高速线路优于专线/流媒体线路）
    "tag_priority": [("高速", 1), ("专线", 2), ("流媒体", 3)],
    # 只要出现这些关键词就跳过（信息节点、体验节点等）
    "skip_keywords": [
        "剩余流量", "套餐到期", "距离下次重置", "官网", "过期", "重置",
        "套餐", "流量", "如用不了", "到期", "刷新", "更新", "客服",
    ],

    # ---------------- 评分权重 ----------------
    # score = region_priority * region_weight
    #       + tag_priority   * tag_weight
    #       + latency/1000   * latency_weight
    #       + 100/throughput * speed_weight   (吞吐越高越优)
    "region_weight": 1000.0,
    "tag_weight": 50.0,
    "latency_weight": 400.0,
    "speed_weight": 300.0,

    # ---------------- 周期与阈值 ----------------
    "interval": 30,                 # 常规巡检间隔（秒）
    "verify_interval": 15,          # 代理不可用时的快速重试间隔（秒）
    "candidate_limit": 8,           # 每轮实际做吞吐验证的节点数量上限
    "delay_timeout_ms": 5000,       # 延迟测速超时
    "speed_timeout": 12,            # 吞吐测试超时（秒）
    "speed_url": "https://speed.cloudflare.com/__down?bytes=4000000",
    "speed_bytes": 3000000,         # 吞吐测试最多读取字节数
    "max_latency_ms": 1500,         # 延迟超过该值直接视为不可用
    "min_speed_mbps": 0.8,          # 吞吐低于该值视为不可用
    "switch_threshold": 0.45,       # 新节点分数需比当前好这么多才切换（相对值）
    "bad_latency_ms": 1200,         # 当前延迟高于此值 → 认为劣化
    "bad_speed_mbps": 3.0,          # 当前吞吐低于此值 → 认为劣化
    "fail_limit": 3,                # 连续失败次数达到后强制换节点
    "cooldown": 120,                # 切到某节点后，该节点冷却时间（秒），避免来回跳
    "restore_on_exit": True,        # 退出时恢复最初选择的节点

    # ---------------- 连通性守卫 ----------------
    "guard_enabled": True,
    "guard_url": "http://www.gstatic.com/generate_204",
    "guard_timeout": 8,

    # ---------------- 本地看板 ----------------
    "dashboard_port": 38766,
    "open_dashboard": True,        # 启动时自动打开浏览器看板

    "log_level": "INFO",
    "log_max_bytes": 2_000_000,
    "log_backups": 3,
}

# ---------------------------------------------------------------- 地区识别

# 用 emoji 旗帜 / 文字关键词识别地区，避免依赖节点命名细节
_REGION_RULES: List[Tuple[str, List[str]]] = [
    ("香港", ["🇭🇰", "香港", "hongkong", "hong kong", "hkg", "hk-", "hk|", "香港岛"]),
    ("日本", ["🇯🇵", "日本", "japan", "tokyo", "osaka", "jp-", "jp|", "jp1", "jp2", "东京", "大阪"]),
    ("新加坡", ["🇸🇬", "新加坡", "singapore", "sgp", "sg-", "sg|", "狮城"]),
    ("台湾", ["🇹🇼", "台湾", "taiwan", "tpe", "tw-", "tw|"]),
    ("美国", ["🇺🇸", "美国", "united states", "los angeles", "洛杉矶", "us-", "us|", "usa"]),
    ("英国", ["🇬🇧", "英国", "伦敦", "london", "uk-", "uk|"]),
    ("韩国", ["🇰🇷", "韩国", "korea", "首尔", "seoul", "kr-", "kr|"]),
    ("德国", ["🇩🇪", "德国", "germany", "法兰克福", "de-", "de|"]),
]

_NON_NODE_TYPES = {"Selector", "URLTest", "Fallback", "LoadBalance", "Relay",
                   "Direct", "Reject", "Compatible", "Pass", "Dns",
                   "RejectDrop", "Direct", "Block"}

# 内核内置策略名，绝不切换
_BUILTIN_NAMES = {"DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE", "GLOBAL"}


def is_real_node(name: str, node_type: str) -> bool:
    if name.upper() in _BUILTIN_NAMES:
        return False
    return node_type not in _NON_NODE_TYPES


def detect_region(name: str) -> str:
    """返回节点所属地区；无法识别返回空串。"""
    low = name.lower()
    for region, keys in _REGION_RULES:
        for k in keys:
            if k.lower() in low:
                return region
    return ""


# ---------------------------------------------------------------- 数据结构

@dataclass
class NodeInfo:
    name: str
    region: str = ""
    latency: Optional[float] = None          # ms
    speed: Optional[float] = None            # Mbps
    score: Optional[float] = None
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Candidate:
    """一次巡检的结果汇总。"""
    best: Optional[NodeInfo] = None
    current: str = ""
    evaluated: List[NodeInfo] = field(default_factory=list)
    note: str = ""


# ---------------------------------------------------------------- Clash API

class ClashAPI:
    def __init__(self, controller: str, secret: str, mixed_port: int, timeout: float = 15.0):
        self.base = f"http://{controller}"
        self.secret = secret
        self.mixed_port = mixed_port
        self.timeout = timeout

    # -- 底层请求 ----------------------------------------------------------
    def _request(self, path: str, method: str = "GET", body: Optional[dict] = None,
                 timeout: Optional[float] = None) -> str:
        url = self.base + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"}
        if self.secret:
            headers["Authorization"] = f"Bearer {self.secret}"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
            return resp.read().decode("utf-8", "replace")

    def get_json(self, path: str, timeout: Optional[float] = None) -> Any:
        txt = self._request(path, timeout=timeout)
        return json.loads(txt) if txt.strip() else None

    # -- 具体接口 ----------------------------------------------------------
    def version(self) -> dict:
        return self.get_json("/version") or {}

    def proxies(self) -> Dict[str, dict]:
        return (self.get_json("/proxies") or {}).get("proxies", {})

    def group_now(self, group: str) -> str:
        obj = self.get_json("/proxies/" + urllib.parse.quote(group, safe="")) or {}
        return obj.get("now", "") or ""

    def select(self, group: str, node: str) -> None:
        self._request("/proxies/" + urllib.parse.quote(group, safe=""),
                      method="PUT", body={"name": node})

    def group_delay(self, group: str, timeout_ms: int, url: str) -> Dict[str, int]:
        """对整个策略组做一次延迟测试，返回 {节点名: 延迟ms}（不可达通常为 0 或不出现）。"""
        q = urllib.parse.urlencode({"url": url, "timeout": timeout_ms})
        path = "/group/" + urllib.parse.quote(group, safe="") + "/delay?" + q
        # 组测速在内核侧并发进行，给足时间
        try:
            return self.get_json(path, timeout=(timeout_ms / 1000.0) * 3 + 10) or {}
        except Exception:
            return {}

    def node_delay(self, node: str, timeout_ms: int, url: str) -> Optional[float]:
        q = urllib.parse.urlencode({"url": url, "timeout": timeout_ms})
        path = "/proxies/" + urllib.parse.quote(node, safe="") + "/delay?" + q
        try:
            obj = self.get_json(path, timeout=timeout_ms / 1000.0 + 5)
            d = (obj or {}).get("delay")
            return float(d) if d else None
        except Exception:
            return None

    # -- 经代理真实访问 ----------------------------------------------------
    def _opener(self) -> urllib.request.OpenerDirector:
        proxy = f"http://127.0.0.1:{self.mixed_port}"
        op = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
        op.addheaders = [("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")]
        return op

    def measure_speed(self, url: str, max_bytes: int, timeout: float) -> Optional[float]:
        """经当前代理下载，返回 Mbps；失败返回 None。"""
        op = self._opener()
        got = 0
        start = time.perf_counter()
        try:
            with op.open(url, timeout=timeout) as resp:
                while got < max_bytes:
                    chunk = resp.read(min(65536, max_bytes - got))
                    if not chunk:
                        break
                    got += len(chunk)
        except Exception:
            if got < 200_000:      # 数据太少不足以判定
                return None
        elapsed = max(time.perf_counter() - start, 1e-3)
        if got < 200_000:
            return None
        return got * 8 / elapsed / 1e6

    def guard_ok(self, url: str, timeout: float) -> bool:
        op = self._opener()
        try:
            with op.open(url, timeout=timeout) as resp:
                return 200 <= resp.status < 400
        except Exception:
            return False


# ---------------------------------------------------------------- 配置加载

def deep_merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str = CONFIG_PATH) -> Dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                user = json.load(f)
            cfg = deep_merge(cfg, user)
        except Exception as exc:
            print(f"[warn] 配置文件解析失败，使用默认配置: {exc}", file=sys.stderr)
    return cfg


def setup_logging(cfg: Dict[str, Any], verbose: bool = False) -> logging.Logger:
    logger = logging.getLogger(APP_NAME)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")

    fh = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=int(cfg["log_max_bytes"]), backupCount=int(cfg["log_backups"]),
        encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)
    logger.addHandler(fh)

    if sys.stdout is not None:      # PyInstaller --noconsole 下 stdout 为 None
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(fmt)
        ch.setLevel(logging.DEBUG if verbose else getattr(logging, str(cfg["log_level"]).upper(), logging.INFO))
        logger.addHandler(ch)
    return logger


# ---------------------------------------------------------------- 核心引擎

class AutoSwitcher:
    def __init__(self, cfg: Dict[str, Any], logger: logging.Logger):
        self.cfg = cfg
        self.log = logger
        self.api = ClashAPI(cfg["controller"], cfg["secret"], int(cfg["mixed_port"]))
        self.groups: List[str] = []
        self.original: Dict[str, str] = {}
        self.cooldown_until: Dict[str, float] = {}
        self.fail_streak = 0
        self.current: str = ""
        self.stopped = threading.Event()
        self.scan_event = threading.Event()          # 看板"立即巡检"
        self.paused = False                          # 看板暂停/恢复
        self.manual_hold_until = 0.0                 # 手动切换后的保持期
        self.started_at = time.time()
        self.current_since = ""
        self.next_scan_at = 0.0
        self.switch_history: List[Dict[str, Any]] = []
        self.last_report: Optional[Dict[str, Any]] = None
        self._load_state()

    # -- 节点过滤 ----------------------------------------------------------
    def blocked(self, name: str) -> bool:
        low = name.lower()
        for kw in self.cfg["skip_keywords"]:
            if kw.lower() in low:
                return True
        region = detect_region(name)
        if region:
            for bad in self.cfg["blocked_regions"]:
                if bad.lower() == region.lower() or bad.lower() in name.lower():
                    return True
        else:
            # 无法识别地区时，按原始名字做黑名单兜底
            for bad in self.cfg["blocked_regions"]:
                if bad.lower() in low:
                    return True
        return False

    def region_rank(self, region: str) -> int:
        prio = self.cfg["region_priority"]
        if region in prio:
            try:
                return int(prio[region])
            except Exception:
                pass
        return 99

    def tag_rank(self, name: str) -> int:
        best = 99
        for kw, rank in self.cfg["tag_priority"]:
            if kw in name:
                best = min(best, int(rank))
        return best

    def node_names(self, proxies: Dict[str, dict]) -> List[str]:
        return [n for n, v in proxies.items() if is_real_node(n, v.get("type", ""))]

    def candidates(self, proxies: Dict[str, dict]) -> List[str]:
        out = []
        for n in self.node_names(proxies):
            if self.blocked(n):
                continue
            # 只考虑目标组可见的节点，避免动到无关节点
            if self.groups:
                visible = any(n in (proxies.get(g, {}) or {}).get("all", []) for g in self.groups)
                if not visible:
                    continue
            out.append(n)
        return out

    # -- 策略组自动挑选 ----------------------------------------------------
    def resolve_groups(self, proxies: Dict[str, dict]) -> List[str]:
        explicit = list(self.cfg.get("target_groups") or [])
        if explicit:
            self.groups = [g for g in explicit if g in proxies]
            return self.groups

        def node_members(g: str) -> List[str]:
            return [n for n in (proxies.get(g, {}) or {}).get("all", [])
                    if is_real_node(n, proxies.get(n, {}).get("type", ""))]

        async_groups = {"Selectable", "URLTest", "Fallback", "LoadBalance", "Relay"}
        selectors = [g for g, v in proxies.items()
                     if v.get("type") == "Selector" and g != "GLOBAL" and node_members(g)]
        # 1) 名字命中偏好
        for pref in self.cfg["preferred_group_names"]:
            for g in selectors:
                if pref.lower() in g.lower() and node_members(g):
                    self.groups = [g]
                    return self.groups
        # 2) 覆盖地区最多的 selector
        scored = []
        for g in selectors:
            regions = {detect_region(n) for n in node_members(g)}
            regions.discard("")
            scored.append((len(regions), g))
        scored.sort(key=lambda x: -x[0])
        self.groups = [scored[0][1]] if scored else []
        return self.groups

    # -- 评分 --------------------------------------------------------------
    def score(self, node: NodeInfo) -> float:
        c = self.cfg
        r_rank = self.region_rank(node.region)
        t_rank = self.tag_rank(node.name)

        # 地区未知时用最低优先级，但不至于完全排除
        s = r_rank * float(c["region_weight"]) + t_rank * float(c["tag_weight"])
        s += (float(node.latency or 9999) / 1000.0) * float(c["latency_weight"])
        sp = node.speed or 0.01
        s += (100.0 / sp) * float(c["speed_weight"])
        return s

    # -- 单节点验证 --------------------------------------------------------
    def verify(self, node: str, proxies: Dict[str, dict]) -> Tuple[Optional[float], Optional[float]]:
        """切到该节点后实测吞吐与延迟。返回 (latency_ms, speed_mbps)。"""
        cached = getattr(self, "latency_cache", {}).get(node)
        latency = cached
        # 应用切换
        switched = self.apply(node)
        if not switched:
            return latency, None
        speed = self.speed_with_retry()
        # 用连通性守卫复核
        ok = self.api.guard_ok(str(self.cfg["guard_url"]), float(self.cfg["guard_timeout"]))
        if not ok and (speed is None):
            return latency, None
        return latency, speed

    def apply(self, node: str) -> bool:
        ok = False
        for g in self.groups:
            try:
                self.api.select(g, node)
                ok = True
            except Exception as exc:
                self.log.warning("切换 %s -> %s 失败: %s", g, node, exc)
        if ok:
            time.sleep(0.5)     # 等内核生效
        return ok

    def speed_with_retry(self) -> Optional[float]:
        """测速失败重试一次，避免瞬时抖动造成误判。"""
        c = self.cfg
        sp = self.api.measure_speed(str(c["speed_url"]), int(c["speed_bytes"]),
                                    float(c["speed_timeout"]))
        if sp is None:
            time.sleep(2.0)
            sp = self.api.measure_speed(str(c["speed_url"]), int(c["speed_bytes"]),
                                        float(c["speed_timeout"]))
        return sp

    # -- 一轮巡检 ----------------------------------------------------------
    def once(self) -> Candidate:
        c, api = self.cfg, self.api
        proxies = api.proxies()
        self.log.info("一轮巡检开始：目标策略组=%s", self.groups or "(待解析)")

        cur_now = ""
        if self.groups:
            cur_now = api.group_now(self.groups[0])
        self.current = cur_now

        # 0) 连通性守卫：完全不通报则直接强制换
        healthy = True
        if c["guard_enabled"] and cur_now:
            healthy = api.guard_ok(str(c["guard_url"]), float(c["guard_timeout"]))
            if not healthy:
                self.log.warning("当前节点 %s 连通性检查失败", cur_now)

        cands = self.candidates(proxies)
        self.log.info("候选节点 %d 个（已排除 %s）", len(cands),
                      "/".join(c["blocked_regions"]) or "无")
        if not cands:
            self.log.error("没有可用候选节点，请检查 blocked_regions 配置")
            return Candidate(current=cur_now, note="无候选节点")

        # 1) 组测速拿延迟
        delays: Dict[str, int] = {}
        tgt_group = None
        for g in self.groups:
            if g in proxies:
                tgt_group = g
                break
        if tgt_group:
            delays = api.group_delay(tgt_group, int(c["delay_timeout_ms"]),
                                     str(c["guard_url"]))
            self.log.info("组测速完成：%d 个节点返回延迟", len(delays))
        self.latency_cache = {k: float(v) for k, v in delays.items() if v and v > 0}

        # 2) 按「地区 + 延迟」初筛，取前 N 个做吞吐验证
        def prelim(n: str) -> float:
            reg = detect_region(n)
            lat = self.latency_cache.get(n, 9999.0)
            return self.region_rank(reg) * 1000 + self.tag_rank(n) * 50 + lat

        pool = sorted(cands, key=prelim)
        now_t = time.time()
        pool = [n for n in pool if self.cooldown_until.get(n, 0) <= now_t or n == cur_now]
        # 按地区轮转取样：每个地区先取最优的一个，再取次优……
        # 保证组测速丢包时（延迟缺失），高优先地区仍会被实测验证
        by_region: Dict[str, List[str]] = {}
        for n in pool:
            by_region.setdefault(detect_region(n) or "未知", []).append(n)
        ordered_regions = sorted(by_region.keys(), key=lambda r: (self.region_rank(r), r))
        shortlist: List[str] = []
        idx = 0
        while len(shortlist) < int(c["candidate_limit"]):
            added = False
            for reg in ordered_regions:
                if idx < len(by_region[reg]):
                    shortlist.append(by_region[reg][idx])
                    added = True
                    if len(shortlist) >= int(c["candidate_limit"]):
                        break
            if not added:
                break
            idx += 1
        if cur_now and cur_now in pool and cur_now not in shortlist:
            shortlist.append(cur_now)       # 当前节点总要复核

        # 3) 逐个实测吞吐
        results: List[NodeInfo] = []
        max_lat = float(c["max_latency_ms"])
        min_spd = float(c["min_speed_mbps"])
        for n in shortlist:
            lat = self.latency_cache.get(n)
            if lat is not None and lat > max_lat:
                results.append(NodeInfo(n, detect_region(n), lat, None, None, "延迟过高"))
                continue
            was_current = (n == cur_now) and healthy
            if was_current:
                spd = self.speed_with_retry()
                lat2 = lat
            else:
                lat2, spd = self.verify(n, proxies)
            info = NodeInfo(n, detect_region(n), lat2, spd)
            if spd is None or spd < min_spd:
                info.reason = f"吞吐不足({spd})"
                results.append(info)
                self.log.info("✗ %-30s 延迟=%-6s 吞吐=%-6s 跳过",
                              n, _fmt(lat2, "ms"), _fmt(spd, ""))
                continue
            info.score = self.score(info)
            info.reason = "可用"
            results.append(info)
            self.log.info("✓ %-30s 地区=%-4s 延迟=%-6s 吞吐=%-8s 评分=%.0f",
                          n, info.region or "?", _fmt(lat2, "ms"),
                          _fmt(spd, "Mbps"), info.score)

        usable = [r for r in results if r.score is not None]
        if not usable:
            self.log.warning("本轮没有可用节点（候选 %d 个全部失败）", len(shortlist))
            self.fail_streak += 1
            # 测试期间组可能已被切走，尽量切回原节点
            if cur_now and self.groups and self.api.group_now(self.groups[0]) != cur_now:
                self.apply(cur_now)
                self.log.info("已切回原节点 %s", cur_now)
            return Candidate(current=cur_now, evaluated=results, note="无可用节点")

        usable.sort(key=lambda x: x.score)      # 分越低越好
        best = usable[0]

        # 4) 决定是否切换
        cur_info = next((r for r in results if r.name == cur_now and r.score is not None), None)
        need_switch = False
        why = ""

        if not cur_now:
            need_switch, why = True, "当前未选择节点"
        elif cur_info is None:
            if best.name != cur_now:
                need_switch, why = True, "当前节点不可用或已被排除"
        elif not healthy:
            self.fail_streak += 1
            if best.name != cur_now:
                need_switch, why = True, "连通性检查连续失败"
        else:
            self.fail_streak = 0
            curb = cur_info.score
            improvement = (curb - best.score) / max(curb, 1e-9)
            degraded = (
                (cur_info.latency or 0) > float(c["bad_latency_ms"]) or
                (cur_info.speed or 0) < float(c["bad_speed_mbps"])
            )
            manual_hold = time.time() < self.manual_hold_until
            if manual_hold:
                self.log.info("手动选择保持中（剩余 %d 秒），跳过自动优化",
                              int(self.manual_hold_until - time.time()))
            elif best.name != cur_now and improvement > float(c["switch_threshold"]):
                need_switch = True
                why = f"更优节点领先 {improvement*100:.0f}%"
            elif degraded and best.name != cur_now:
                need_switch = True
                why = "当前节点已劣化"
            else:
                self.log.info("保持当前节点 %s（评分 %.0f，最优 %s 评分 %.0f）",
                              cur_now, curb, best.name, best.score)

        if need_switch:
            self.log.info("切换 → %s（%s）", best.name, why)
            if self.apply(best.name):
                self.switch_history.append({
                    "time": time.strftime("%m-%d %H:%M:%S"),
                    "from": cur_now, "to": best.name, "why": why,
                })
                self.current = best.name
                self.current_since = time.strftime("%H:%M:%S")
                self.cooldown_until[best.name] = time.time() + float(c["cooldown"])
                self.fail_streak = 0
                self.log.info("切换完成，当前节点：%s [%s]", best.name, best.region or "?")
            else:
                self.log.error("切换失败：%s", best.name)
        elif self.fail_streak >= int(c["fail_limit"]) and best.name != cur_now:
            self.log.warning("连续失败 %d 次，强制切换 → %s", self.fail_streak, best.name)
            self.apply(best.name)
            self.current = best.name
            self.fail_streak = 0
        else:
            # 验证过程把组切到了最后一个被测节点；决定保持时必须切回当前节点
            if cur_now and self.groups and self.api.group_now(self.groups[0]) != cur_now:
                self.apply(cur_now)
                self.log.info("已切回当前节点 %s", cur_now)

        cand = Candidate(best=best, current=self.current, evaluated=results,
                         note=why or "保持")
        self.last_report = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "current": self.current,
            "groups": self.groups,
            "note": cand.note,
            "evaluated": [asdict(r) for r in results],
        }
        self.save_state()
        return cand

    def save_state(self) -> None:
        try:
            data = dict(self.last_report or {})
            data["switches"] = self.switch_history[-30:]
            data["current_since"] = self.current_since
            data["paused"] = self.paused
            with open(STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _load_state(self) -> None:
        try:
            if os.path.exists(STATE_PATH):
                with open(STATE_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.switch_history = list(data.get("switches") or [])
        except Exception:
            self.switch_history = []

    # -- 看板数据与操作 ----------------------------------------------------
    def status_payload(self) -> Dict[str, Any]:
        cur_info = None
        for r in (self.last_report or {}).get("evaluated", []):
            if r.get("name") == self.current:
                cur_info = r
                break
        try:
            candidates = self.candidates(self.api.proxies())
        except Exception:
            candidates = []
        return {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "uptime_sec": int(time.time() - self.started_at),
            "paused": self.paused,
            "groups": self.groups,
            "current": self.current,
            "current_info": cur_info,
            "current_since": self.current_since,
            "note": (self.last_report or {}).get("note", ""),
            "next_scan_in": max(0, int(self.next_scan_at - time.time())) if self.next_scan_at else None,
            "manual_hold": max(0, int(self.manual_hold_until - time.time())),
            "evaluated": (self.last_report or {}).get("evaluated", []),
            "candidates": candidates,
            "blocked": self.cfg["blocked_regions"],
            "region_priority": self.cfg["region_priority"],
            "switches": self.switch_history[-30:],
            "fail_streak": self.fail_streak,
            "log_tail": self._log_tail(60),
        }

    @staticmethod
    def _log_tail(n: int) -> List[str]:
        try:
            with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
                return f.readlines()[-n:]
        except Exception:
            return []

    def manual_switch(self, node: str) -> Tuple[bool, str]:
        """看板手动切换；之后 10 分钟内自动切换只在节点彻底失效时介入。"""
        try:
            proxies = self.api.proxies()
        except Exception as exc:
            return False, f"读取节点失败: {exc}"
        if node not in proxies:
            return False, "节点不存在"
        in_group = any(node in (proxies.get(g) or {}).get("all", []) for g in self.groups)
        if not in_group:
            return False, "该节点不在接管策略组内"
        if not self.apply(node):
            return False, "切换失败"
        prev = self.current
        self.current = node
        self.current_since = time.strftime("%H:%M:%S")
        self.manual_hold_until = time.time() + 600
        self.switch_history.append({
            "time": time.strftime("%m-%d %H:%M:%S"), "from": prev, "to": node, "why": "手动切换",
        })
        self.save_state()
        self.log.info("手动切换 → %s", node)
        return True, ""

    # -- 主循环 ------------------------------------------------------------
    def run(self, once_only: bool = False) -> None:
        c = self.cfg
        proxies = self.api.proxies()
        self.resolve_groups(proxies)
        if not self.groups:
            self.log.error("未找到可接管的策略组，请手动设置 target_groups")
            return
        self.log.info("接管策略组：%s", ", ".join(self.groups))
        for g in self.groups:
            self.original[g] = self.api.group_now(g)
        self.log.info("初始选择：%s", self.original)

        if once_only:
            self.once()
            return

        while not self.stopped.is_set():
            if self.paused:
                self.next_scan_at = 0.0
                time.sleep(0.5)
                continue
            try:
                cand = self.once()
                bad = cand.best is None
            except Exception as exc:
                self.log.exception("巡检异常: %s", exc)
                bad = True
            wait = float(c["verify_interval"] if bad else c["interval"])
            self.next_scan_at = time.time() + wait
            self.log.info("下一次巡检：%d 秒后\n", int(wait))
            self.save_state()
            # 分段等待，可被"立即巡检"/暂停/退出打断
            end = time.time() + wait
            while time.time() < end and not self.stopped.is_set() and not self.paused:
                if self.scan_event.is_set():
                    self.scan_event.clear()
                    self.log.info("收到看板请求，立即巡检")
                    break
                time.sleep(min(0.5, max(end - time.time(), 0.05)))

        if c["restore_on_exit"]:
            for g, node in self.original.items():
                if node:
                    try:
                        self.api.select(g, node)
                        self.log.info("已恢复 %s 的初始选择：%s", g, node)
                    except Exception as exc:
                        self.log.warning("恢复 %s 失败: %s", g, exc)


def _fmt(v: Optional[float], unit: str) -> str:
    if v is None:
        return "-"
    return f"{v:.0f}{unit}" if unit else f"{v:.1f}"


def _pp(v: Optional[float], unit: str) -> str:
    if v is None:
        return "-"
    return f"{v:.0f} {unit}".strip()


# ---------------------------------------------------------------- 本地看板页面

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Clash 自动切换器 · 看板</title>
<style>
  :root{--bg:#0d1117;--card:#161b22;--line:#30363d;--txt:#e6edf3;--sub:#8b949e;
        --ok:#3fb950;--bad:#f85149;--warn:#d29922;--acc:#58a6ff;}
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--txt);font:14px/1.6 "Segoe UI","Microsoft YaHei",sans-serif;padding:20px}
  .wrap{max-width:1080px;margin:0 auto}
  .row{display:flex;gap:14px;flex-wrap:wrap}
  h1{font-size:19px;font-weight:600}
  .muted{color:var(--sub);font-size:12px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}
  .head{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-bottom:14px}
  .badge{padding:2px 12px;border-radius:999px;font-size:12px;border:1px solid var(--line)}
  .badge.run{color:var(--ok);border-color:var(--ok)}
  .badge.pause{color:var(--warn);border-color:var(--warn)}
  .current{flex:1.4;min-width:300px}
  .current .name{font-size:20px;font-weight:600;margin:4px 0 10px;word-break:break-all}
  .chips{display:flex;gap:8px;flex-wrap:wrap}
  .chip{background:#21262d;border:1px solid var(--line);border-radius:8px;padding:3px 10px;font-size:12.5px}
  .chip b{color:var(--acc)}
  .side{flex:1;min-width:230px;display:flex;flex-direction:column;gap:10px}
  .btns{display:flex;gap:8px;flex-wrap:wrap}
  button{background:#21262d;color:var(--txt);border:1px solid var(--line);border-radius:8px;
         padding:7px 14px;cursor:pointer;font-size:13px}
  button:hover{border-color:var(--acc);color:var(--acc)}
  button.primary{background:#1f3a5f;border-color:#2f5d8f}
  select{background:#0d1117;color:var(--txt);border:1px solid var(--line);border-radius:8px;
         padding:7px 8px;font-size:13px;max-width:100%}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #21262d;white-space:nowrap}
  th{color:var(--sub);font-weight:500;font-size:12px}
  td.name{white-space:normal;word-break:break-all;max-width:340px}
  .ok{color:var(--ok)} .bad{color:var(--bad)} .warn{color:var(--warn)}
  .num{text-align:right;font-variant-numeric:tabular-nums}
  pre{background:#0d1117;border:1px solid var(--line);border-radius:10px;padding:12px;
      font:12px/1.5 Consolas,monospace;overflow:auto;max-height:260px;color:#c9d1d9}
  .sec{margin-top:14px}
  .toast{position:fixed;right:20px;bottom:20px;background:#1f6feb;color:#fff;border-radius:10px;
         padding:10px 16px;opacity:0;transition:opacity .25s;pointer-events:none;max-width:70vw}
  .toast.show{opacity:.95}
  .switch-item{padding:5px 0;border-bottom:1px dashed #21262d;font-size:12.5px}
  .switch-item:last-child{border-bottom:none}
  .grid2{display:grid;grid-template-columns:2fr 1fr;gap:14px}
  @media(max-width:760px){.grid2{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="wrap">
  <div class="head">
    <div>
      <h1>📡 Clash 自动切换器</h1>
      <div class="muted" id="meta">加载中…</div>
    </div>
    <span class="badge run" id="badge">…</span>
  </div>

  <div class="row">
    <div class="card current">
      <div class="muted">当前节点</div>
      <div class="name" id="curName">—</div>
      <div class="chips" id="curChips"></div>
      <div class="muted" id="curNote" style="margin-top:8px"></div>
    </div>
    <div class="side">
      <div class="card">
        <div class="btns">
          <button class="primary" onclick="doScan()">⚡ 立即巡检</button>
          <button id="btnPause" onclick="doPause()">⏸ 暂停</button>
        </div>
        <div class="muted" id="holdHint" style="margin-top:8px"></div>
      </div>
      <div class="card">
        <div class="muted" style="margin-bottom:6px">手动切换（保持 10 分钟）</div>
        <div style="display:flex;gap:8px">
          <select id="selNode" style="flex:1"></select>
          <button onclick="doSwitch()">切换</button>
        </div>
      </div>
    </div>
  </div>

  <div class="card sec">
    <div class="muted" style="margin-bottom:6px">本轮节点体检结果 <span id="evalMeta"></span></div>
    <table>
      <thead><tr><th>状态</th><th>节点</th><th>地区</th><th class="num">延迟</th>
      <th class="num">吞吐</th><th class="num">评分</th><th>说明</th></tr></thead>
      <tbody id="evalBody"></tbody>
    </table>
  </div>

  <div class="grid2 sec">
    <div class="card">
      <div class="muted" style="margin-bottom:6px">运行日志（最近 60 行）</div>
      <pre id="logBox">—</pre>
    </div>
    <div class="card">
      <div class="muted" style="margin-bottom:6px">切换记录</div>
      <div id="hisBox"><div class="muted">暂无</div></div>
    </div>
  </div>
</div>
<div class="toast" id="toast"></div>
<script>
let lastData=null;
const $=id=>document.getElementById(id);
function toast(m){const t=$('toast');t.textContent=m;t.classList.add('show');
  clearTimeout(t._h);t._h=setTimeout(()=>t.classList.remove('show'),2600);}
function fmtMs(v){return v==null?'—':Math.round(v)+' ms'}
function fmtSp(v){return v==null?'—':(+v).toFixed(1)+' Mbps'}
function fmtUp(s){const h=Math.floor(s/3600),m=Math.floor(s%3600/60);
  return h>0?h+' 小时 '+m+' 分':(m>0?m+' 分 '+s%60+' 秒':s+' 秒')}
async function refresh(){
  try{
    const r=await fetch('/api/status');const d=await r.json();lastData=d;
    $('badge').textContent=d.paused?'⏸ 已暂停':(d.current?'● 自动运行中':'○ 启动中');
    $('badge').className='badge '+(d.paused?'pause':'run');
    $('meta').textContent=`策略组 ${d.groups.join('、')||'—'} ｜ 已运行 ${fmtUp(d.uptime_sec)}`+
      (d.next_scan_in!=null&&!d.paused?` ｜ 下次巡检 ${d.next_scan_in} 秒后`:'')+
      (d.manual_hold>0?` ｜ 手动保持 ${d.manual_hold} 秒`:'');
    const ci=d.current_info||{};
    $('curName').textContent=d.current||'—';
    $('curChips').innerHTML=[
      `地区 <b>${ci.region||'?'}</b>`,`延迟 <b>${fmtMs(ci.latency)}</b>`,
      `吞吐 <b>${fmtSp(ci.speed)}</b>`,`评分 <b>${ci.score!=null?Math.round(ci.score):'—'}</b>`]
      .map(c=>`<span class="chip">${c}</span>`).join('');
    $('curNote').textContent=(d.current_since?('自 '+d.current_since+' 起'):'')+
      (d.note?(' ｜ '+d.note):'');
    $('holdHint').textContent=d.manual_hold>0?('手动选择保持中，'+d.manual_hold+' 秒内自动切换仅响应硬故障'):'';
    $('btnPause').textContent=d.paused?'▶ 恢复':'⏸ 暂停';
    // 体检表
    const ev=(d.evaluated||[]).slice().sort((a,b)=>
      (a.score==null?1e9:a.score)-(b.score==null?1e9:b.score));
    $('evalMeta').textContent=`（${ev.filter(x=>x.score!=null).length}/${ev.length} 可用）`;
    const best=ev.find(x=>x.score!=null);
    $('evalBody').innerHTML=ev.map(x=>{
      const ok=x.score!=null;
      const isCur=x.name===d.current, isBest=best&&x.name===best.name;
      return `<tr><td class="${ok?'ok':'bad'}">${ok?(isBest&&isCur?'★ 最优':(isBest?'✓ 最优':(isCur?'◆ 当前':'✓'))):'✗'}</td>`+
        `<td class="name">${x.name}</td><td>${x.region||'?'}</td>`+
        `<td class="num">${fmtMs(x.latency)}</td><td class="num">${fmtSp(x.speed)}</td>`+
        `<td class="num">${x.score!=null?Math.round(x.score):'—'}</td>`+
        `<td class="muted">${x.reason||''}</td></tr>`;}).join('')||'<tr><td colspan="7" class="muted">等待首轮巡检…</td></tr>';
    // 手动切换下拉
    const sel=$('selNode');
    if(sel.dataset.n!==String((d.candidates||[]).length)+d.current){
      sel.innerHTML=(d.candidates||[]).map(n=>
        `<option ${n===d.current?'selected':''}>${n}</option>`).join('');
      sel.dataset.n=String((d.candidates||[]).length)+d.current;
    }
    // 历史
    $('hisBox').innerHTML=(d.switches||[]).slice().reverse().map(s=>
      `<div class="switch-item"><span class="muted">${s.time}</span><br>`+
      `${s.from||'—'} → <b>${s.to}</b><span class="muted">（${s.why||''}）</span></div>`).join('')
      ||'<div class="muted">暂无</div>';
    $('logBox').textContent=(d.log_tail||[]).join('').trimEnd()||'—';
  }catch(e){$('meta').textContent='看板数据获取失败：'+e}
}
async function post(path,body){
  const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body||{})});return r.json();
}
async function doScan(){toast(await post('/api/scan').then(x=>x.msg||'已请求'));setTimeout(refresh,1500);}
async function doPause(){const x=await post('/api/pause');toast(x.paused?'已暂停自动切换':'已恢复自动切换');refresh();}
async function doSwitch(){const n=$('selNode').value;
  if(!n)return toast('请选择节点');
  const x=await post('/api/switch',{node:n});toast(x.ok?('已切换 → '+n):('失败：'+x.msg));refresh();}
refresh();setInterval(refresh,5000);
</script>
</body>
</html>
"""


# ---------------------------------------------------------------- 单实例锁

class DashboardServer:
    """本地看板：127.0.0.1 上的轻量 HTTP 服务。"""

    def __init__(self, engine: "AutoSwitcher", port: int):
        self.engine = engine
        self.port = port
        self.httpd: Optional[ThreadingHTTPServer] = None

    def start(self) -> bool:
        engine = self.engine

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):      # 静默访问日志
                pass

            def _send(self, code: int, body: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    self.wfile.write(body)
                except Exception:
                    pass

            def _json(self, obj: Any, code: int = 200) -> None:
                self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")

            def do_GET(self):
                path = urllib.parse.urlparse(self.path).path
                if path in ("/", "/index.html"):
                    self._send(200, DASHBOARD_HTML.encode("utf-8"),
                               "text/html; charset=utf-8")
                elif path == "/api/status":
                    try:
                        self._json(engine.status_payload())
                    except Exception as exc:
                        self._json({"error": str(exc)}, 500)
                else:
                    self._send(404, b"not found", "text/plain")

            def do_POST(self):
                path = urllib.parse.urlparse(self.path).path
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    raw = self.rfile.read(length) if length else b"{}"
                    body = json.loads(raw.decode("utf-8") or "{}")
                except Exception:
                    body = {}
                if path == "/api/scan":
                    engine.scan_event.set()
                    self._json({"ok": True, "msg": "已请求立即巡检"})
                elif path == "/api/pause":
                    engine.paused = not engine.paused
                    engine.save_state()
                    self._json({"ok": True, "paused": engine.paused})
                elif path == "/api/switch":
                    node = str(body.get("node") or "")
                    ok, msg = engine.manual_switch(node) if node else (False, "缺少节点名")
                    self._json({"ok": ok, "msg": msg})
                else:
                    self._json({"ok": False, "msg": "未知接口"}, 404)

        try:
            self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
            self.httpd.daemon_threads = True
            threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
            return True
        except OSError as exc:
            print(f"[warn] 看板端口 {self.port} 启动失败: {exc}", file=sys.stderr)
            return False


class SingleInstance:
    """用本地 socket 占端口实现单实例，避免重复启动多个巡检进程。"""

    def __init__(self, port: int = 38765):
        self.port = port
        self.sock: Optional[socket.socket] = None

    def acquire(self) -> bool:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", self.port))
            s.listen(1)
        except OSError:
            try:
                s.close()
            except Exception:
                pass
            return False
        self.sock = s
        return True


# ---------------------------------------------------------------- 托盘图标

def start_tray(engine: "AutoSwitcher", port: int, logger: logging.Logger):
    """系统托盘图标：气泡提示 + 右键菜单（打开看板/立即巡检/暂停/退出）。"""
    try:
        import pystray
        from PIL import Image as PILImage
    except Exception:
        logger.info("pystray 不可用，跳过托盘图标")
        return None

    icon_candidates = [os.path.join(APP_DIR, "icon.ico")]
    if getattr(sys, "frozen", False):      # PyInstaller --add-data 打进包里的资源
        icon_candidates.insert(0, os.path.join(getattr(sys, "_MEIPASS", ""), "icon.ico"))
    img = None
    for p in icon_candidates:
        try:
            if os.path.exists(p):
                img = PILImage.open(p)
                break
        except Exception:
            continue
    if img is None:
        img = PILImage.new("RGB", (64, 64), (79, 70, 229))

    def open_board(icon=None, item=None):
        webbrowser.open(f"http://127.0.0.1:{port}")

    def scan_now(icon=None, item=None):
        engine.scan_event.set()
        try:
            icon.notify("已请求立即巡检", "Clash 自动切换器")
        except Exception:
            pass

    def toggle_pause(icon=None, item=None):
        engine.paused = not engine.paused
        engine.save_state()
        try:
            icon.notify("已暂停自动切换" if engine.paused else "已恢复自动切换",
                        "Clash 自动切换器")
        except Exception:
            pass

    def quit_app(icon=None, item=None):
        logger.info("托盘请求退出")
        engine.stopped.set()
        try:
            icon.stop()
        except Exception:
            pass
        threading.Timer(1.0, lambda: os._exit(0)).start()

    menu = pystray.Menu(
        pystray.MenuItem("打开看板", open_board, default=True),
        pystray.MenuItem("立即巡检", scan_now),
        pystray.MenuItem(
            lambda item: "▶ 恢复自动切换" if engine.paused else "⏸ 暂停自动切换",
            toggle_pause),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", quit_app),
    )
    ic = pystray.Icon("clash_auto_switch", img,
                      "Clash 自动切换器 - 运行中", menu)
    ic.run_detached()
    try:
        ic.notify("已启动！看板即将在浏览器打开：http://127.0.0.1:%d" % port,
                  "Clash 自动切换器")
    except Exception:
        pass
    logger.info("托盘图标已启动")
    return ic


# ---------------------------------------------------------------- CLI

def read_secret_from_verge() -> str:
    """从 Clash Verge 生成的 config.yaml 中读取外部控制器 secret（跨平台路径）。"""
    home = os.path.expanduser("~")
    verge_dir = "io.github.clash-verge-rev.clash-verge-rev"
    candidates = [
        os.path.join(APP_DIR, "verge-config.yaml"),
        os.path.join(os.environ.get("APPDATA", ""), verge_dir, "config.yaml"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), verge_dir, "config.yaml"),
        # macOS / Linux
        os.path.join(home, "Library", "Application Support", verge_dir, "config.yaml"),
        os.path.join(home, ".config", verge_dir, "config.yaml"),
    ]
    for path in candidates:
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
                for line in f:
                    s = line.strip()
                    if s.startswith("secret:"):
                        val = s.split(":", 1)[1].strip().strip("'\"")
                        if val:
                            return val
        except Exception:
            continue
    return ""


def cmd_status(cfg: Dict[str, Any]) -> int:
    api = ClashAPI(cfg["controller"], cfg["secret"], int(cfg["mixed_port"]))
    info = api.version()
    print(f"内核: {info.get('version')}  meta={info.get('meta')}")
    proxies = api.proxies()
    for g, v in proxies.items():
        if v.get("type") in ("Selector", "URLTest", "Fallback", "LoadBalance"):
            cnt = len([n for n in v.get("all", []) if is_real_node(n, proxies.get(n, {}).get("type", ""))])
            print(f"  [{v['type']:<9}] {g:<12} 当前={v.get('now')}  节点数={cnt}")
    if os.path.exists(STATE_PATH):
        print("\n最近一轮巡检:")
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            st = json.load(f)
        print(f"  时间={st.get('time')}  当前={st.get('current')}  说明={st.get('note')}")
        for r in (st.get("evaluated") or [])[:8]:
            print("    {:<30} {:<5} {:>6} {:>8} {:>7}".format(
                r.get("name", "")[:30], r.get("region") or "?",
                _pp(r.get("latency"), "ms"), _pp(r.get("speed"), "Mbps"),
                _pp(r.get("score"), "")))
    return 0


def cmd_switch(cfg: Dict[str, Any], node: str) -> int:
    api = ClashAPI(cfg["controller"], cfg["secret"], int(cfg["mixed_port"]))
    proxies = api.proxies()
    for g, v in proxies.items():
        if v.get("type") == "Selector" and g != "GLOBAL" and node in v.get("all", []):
            api.select(g, node)
            print(f"{g} -> {node}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Clash 代理自动监听切换器")
    ap.add_argument("--config", default=CONFIG_PATH, help="配置文件路径")
    ap.add_argument("--once", action="store_true", help="只巡检一轮后退出")
    ap.add_argument("--status", action="store_true", help="查看当前状态")
    ap.add_argument("--switch", metavar="NODE", help="手动切换到指定节点")
    ap.add_argument("--list", action="store_true", help="列出候选节点（含被排除的）")
    ap.add_argument("--verbose", action="store_true", help="输出调试日志")
    ap.add_argument("--no-guard", action="store_true", help="禁用连通性守卫")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    if args.no_guard:
        cfg["guard_enabled"] = False
    if not cfg["secret"]:
        cfg["secret"] = read_secret_from_verge()

    if args.status:
        return cmd_status(cfg)
    if args.switch:
        return cmd_switch(cfg, args.switch)

    logger = setup_logging(cfg, args.verbose)

    if args.list:
        api = ClashAPI(cfg["controller"], cfg["secret"], int(cfg["mixed_port"]))
        sw = AutoSwitcher(cfg, logger)
        proxies = api.proxies()
        sw.resolve_groups(proxies)
        print(f"接管策略组: {sw.groups}")
        for n in sw.node_names(proxies):
            flag = "✗排除" if sw.blocked(n) else "✓候选"
            print(f"  {flag}  {detect_region(n) or '?':<5} {n}")
        return 0

    lock = SingleInstance()
    if not lock.acquire():
        # 已有实例在跑：把看板唤到前台，给用户可见反馈
        try:
            port = int(cfg.get("dashboard_port") or 0)
            if port:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2)
                webbrowser.open(f"http://127.0.0.1:{port}")
        except Exception:
            pass
        return 0

    # 读取 secret（Clash Verge 会写入 config.yaml）
    if not cfg["secret"]:
        cfg["secret"] = read_secret_from_verge()
    if not cfg["secret"]:
        logger.warning("未找到 API secret，若内核要求鉴权将无法连接")

    engine = AutoSwitcher(cfg, logger)

    def _stop(signum, frame):
        logger.info("收到退出信号 %s，准备停止…", signum)
        engine.stopped.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _stop)
        except Exception:
            pass

    logger.info("=" * 60)
    logger.info("Clash 自动切换器启动 | 控制器=%s | 禁止地区=%s | 偏好=%s",
                cfg["controller"], cfg["blocked_regions"],
                list(cfg["region_priority"].keys()))
    if not args.once:
        port = int(cfg.get("dashboard_port") or 0)
        if port:
            if DashboardServer(engine, port).start():
                logger.info("看板已启动: http://127.0.0.1:%d", port)
                if cfg.get("open_dashboard"):
                    threading.Timer(1.5, lambda: webbrowser.open(
                        f"http://127.0.0.1:{port}")).start()
            start_tray(engine, port or 38766, logger)
    try:
        engine.run(once_only=args.once)
    except Exception as exc:
        logger.exception("运行出错: %s", exc)
        return 1
    logger.info("已停止")
    try:
        sys.stdout and sys.stdout.flush()
    except Exception:
        pass
    os._exit(0)          # 确保托盘守护线程不阻塞退出


if __name__ == "__main__":
    sys.exit(main())
