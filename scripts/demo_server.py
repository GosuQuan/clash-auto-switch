# -*- coding: utf-8 -*-
"""演示看板用的模拟数据服务（仅生成 README 演示图/GIF，不含任何真实订阅信息）。"""
import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from clash_auto_switch import DASHBOARD_HTML  # noqa: E402

PORT = 38777

def node(name, region, lat, spd, score=None, reason="可用"):
    return {"name": name, "region": region, "latency": lat, "speed": spd,
            "score": score if score is not None else (round(region_base(region) + lat * 0.4 + 100 / max(spd, 0.1) * 3)),
            "reason": reason}

def region_base(region):
    return {"日本": 1000, "新加坡": 2000, "台湾": 3000, "美国": 4000, "英国": 5000}.get(region, 9000)

EVAL_A = [
    node("🇯🇵 日本高速 01", "日本", 76, 18.2),
    node("🇯🇵 日本高速 02", "日本", 98, 15.6),
    node("🇸🇬 新加坡高速 01", "新加坡", 104, 14.9),
    node("🇸🇬 新加坡专线 02", "新加坡", 118, 11.2),
    node("🇹🇼 台湾专线 01", "台湾", 44, 21.5),
    node("🇺🇸 美国高速 03", "美国", 182, 12.4),
    node("🇬🇧 英国伦敦 01", "英国", 231, 9.8),
    {"name": "🇭🇰 香港高速 01", "region": "香港", "latency": 31, "speed": None,
     "score": None, "reason": "已排除（地区黑名单）"},
]
EVAL_B = [
    node("🇯🇵 日本高速 01", "日本", 312, 2.1),
    node("🇯🇵 日本高速 02", "日本", 98, 15.6),
    node("🇸🇬 新加坡高速 01", "新加坡", 104, 14.9),
    node("🇸🇬 新加坡专线 02", "新加坡", 118, 11.2),
    node("🇹🇼 台湾专线 01", "台湾", 44, 21.5),
    node("🇺🇸 美国高速 03", "美国", 182, 12.4),
    node("🇬🇧 英国伦敦 01", "英国", 231, 9.8),
]
EVAL_C = [
    node("🇯🇵 日本高速 02", "日本", 97, 17.8),
    node("🇯🇵 日本高速 01", "日本", 302, 2.4),
    node("🇸🇬 新加坡高速 01", "新加坡", 102, 15.3),
    node("🇸🇬 新加坡专线 02", "新加坡", 117, 11.5),
    node("🇹🇼 台湾专线 01", "台湾", 45, 20.9),
    node("🇺🇸 美国高速 03", "美国", 179, 13.1),
    node("🇬🇧 英国伦敦 01", "英国", 228, 10.2),
]

CANDS = [
    "🇯🇵 日本高速 01", "🇯🇵 日本高速 02", "🇯🇵 日本高速 03", "🇯🇵 日本专线 01",
    "🇸🇬 新加坡高速 01", "🇸🇬 新加坡专线 02", "🇹🇼 台湾专线 01",
    "🇺🇸 美国高速 03", "🇺🇸 美国洛杉矶 01", "🇬🇧 英国伦敦 01",
    "🇭🇰 香港高速 01", "🇭🇰 香港专线 02",
]

LOG_A = """2026-10-26 21:04:11 INFO    一轮巡检开始：目标策略组=['节点选择']
2026-10-26 21:04:11 INFO    候选节点 44 个（已排除 香港/Hong Kong/HK）
2026-10-26 21:04:16 INFO    组测速完成：23 个节点返回延迟
2026-10-26 21:04:22 INFO    ✓ 🇯🇵 日本高速 01        地区=日本   延迟=76ms   吞吐=18.2Mbps  评分=1378
2026-10-26 21:04:29 INFO    ✓ 🇸🇬 新加坡高速 01      地区=新加坡  延迟=104ms  吞吐=14.9Mbps  评分=2252
2026-10-26 21:04:33 INFO    ✓ 🇹🇼 台湾专线 01        地区=台湾   延迟=44ms   吞吐=21.5Mbps  评分=3032
2026-10-26 21:04:37 INFO    保持当前节点 🇯🇵 日本高速 01（评分 1378，最优持平）
2026-10-26 21:04:37 INFO    下一次巡检：30 秒后
"""
LOG_B = """2026-10-26 21:04:41 INFO    一轮巡检开始：目标策略组=['节点选择']
2026-10-26 21:04:46 INFO    组测速完成：19 个节点返回延迟
2026-10-26 21:04:52 INFO    ✓ 🇯🇵 日本高速 01        地区=日本   延迟=312ms  吞吐=2.1Mbps   评分=1487
2026-10-26 21:04:58 INFO    ✓ 🇯🇵 日本高速 02        地区=日本   延迟=98ms   吞吐=15.6Mbps  评分=1451
2026-10-26 21:05:02 INFO    切换 → 🇯🇵 日本高速 02（当前节点已劣化）
2026-10-26 21:05:03 INFO    切换完成，当前节点：🇯🇵 日本高速 02 [日本]
2026-10-26 21:05:03 INFO    下一次巡检：30 秒后
"""
LOG_C = """2026-10-26 21:05:31 INFO    一轮巡检开始：目标策略组=['节点选择']
2026-10-26 21:05:36 INFO    组测速完成：26 个节点返回延迟
2026-10-26 21:05:42 INFO    ✓ 🇯🇵 日本高速 02        地区=日本   延迟=97ms   吞吐=17.8Mbps  评分=1443
2026-10-26 21:05:48 INFO    ✓ 🇸🇬 新加坡高速 01      地区=新加坡  延迟=102ms  吞吐=15.3Mbps  评分=2246
2026-10-26 21:05:52 INFO    保持当前节点 🇯🇵 日本高速 02（评分 1443，最优）
2026-10-26 21:05:52 INFO    下一次巡检：30 秒后
"""

STATES = {
    "1": {
        "time": "2026-10-26 21:04:37", "uptime_sec": 3817, "paused": False,
        "groups": ["节点选择"], "current": "🇯🇵 日本高速 01",
        "current_info": EVAL_A[0], "current_since": "20:51:02",
        "note": "保持当前节点（评分持平）", "next_scan_in": 24, "manual_hold": 0,
        "evaluated": EVAL_A, "candidates": CANDS,
        "blocked": ["香港", "Hong Kong", "HK"],
        "region_priority": {"日本": 1, "新加坡": 2, "台湾": 3, "美国": 4, "英国": 5},
        "switches": [
            {"time": "10-26 20:48:31", "from": "🇺🇸 美国高速 03", "to": "🇯🇵 日本高速 01", "why": "更优节点领先 52%"},
            {"time": "10-26 20:51:02", "from": "🇸🇬 新加坡高速 01", "to": "🇯🇵 日本高速 01", "why": "当前节点已劣化"},
        ],
        "fail_streak": 0, "log_tail": LOG_A.splitlines(True),
    },
    "2": {
        "time": "2026-10-26 21:04:55", "uptime_sec": 3835, "paused": False,
        "groups": ["节点选择"], "current": "🇯🇵 日本高速 01",
        "current_info": EVAL_B[0], "current_since": "20:51:02",
        "note": "当前节点已劣化", "next_scan_in": 18, "manual_hold": 0,
        "evaluated": EVAL_B, "candidates": CANDS,
        "blocked": ["香港", "Hong Kong", "HK"],
        "region_priority": {"日本": 1, "新加坡": 2, "台湾": 3, "美国": 4, "英国": 5},
        "switches": [
            {"time": "10-26 20:48:31", "from": "🇺🇸 美国高速 03", "to": "🇯🇵 日本高速 01", "why": "更优节点领先 52%"},
            {"time": "10-26 20:51:02", "from": "🇸🇬 新加坡高速 01", "to": "🇯🇵 日本高速 01", "why": "当前节点已劣化"},
        ],
        "fail_streak": 0, "log_tail": LOG_B.splitlines(True),
    },
    "3": {
        "time": "2026-10-26 21:05:03", "uptime_sec": 3843, "paused": False,
        "groups": ["节点选择"], "current": "🇯🇵 日本高速 02",
        "current_info": EVAL_C[0], "current_since": "21:05:03",
        "note": "已自动切换（当前节点已劣化）", "next_scan_in": 30, "manual_hold": 0,
        "evaluated": EVAL_C, "candidates": CANDS,
        "blocked": ["香港", "Hong Kong", "HK"],
        "region_priority": {"日本": 1, "新加坡": 2, "台湾": 3, "美国": 4, "英国": 5},
        "switches": [
            {"time": "10-26 20:48:31", "from": "🇺🇸 美国高速 03", "to": "🇯🇵 日本高速 01", "why": "更优节点领先 52%"},
            {"time": "10-26 20:51:02", "from": "🇸🇬 新加坡高速 01", "to": "🇯🇵 日本高速 01", "why": "当前节点已劣化"},
            {"time": "10-26 21:05:03", "from": "🇯🇵 日本高速 01", "to": "🇯🇵 日本高速 02", "why": "当前节点已劣化"},
        ],
        "fail_streak": 0, "log_tail": LOG_C.splitlines(True),
    },
}

current = {"n": "1"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if path == "/":
            self._send(200, DASHBOARD_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/status":
            if q.get("frame"):
                current["n"] = q["frame"][0]
            self._send(200, json.dumps(STATES[current["n"]], ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
        else:
            self._send(404, b"nf", "text/plain")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        self._send(200, b'{"ok":true}', "application/json")


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"demo dashboard: http://127.0.0.1:{PORT}/?frame=1")
    srv.serve_forever()
