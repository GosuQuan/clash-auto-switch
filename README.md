<div align="center">

# 📡 Clash Auto Switch

**Clash 代理自动监听切换器 —— 节点劣化自动切换，本地看板实时可视**

*Auto-switch your Clash nodes when they degrade. Local dashboard included.*

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS-lightgrey)

<img src="docs/demo.gif" alt="看板演示" width="900">

*看板实时展示：当前节点体检指标 / 全节点吞吐实测 / 自动切换记录 / 运行日志*

</div>

---

还在手动切 Clash 节点？延迟看着不高却卡成 PPT？这个工具每 30 秒对策略组做一次「延迟 + **真实吞吐**」双重体检，节点劣化自动切换，**硬性排除香港节点，日本/新加坡等低延迟地区优先**，并内置本地 Web 看板。

## ✨ 功能特性

- 🔄 **自动巡检**：每 30s 通过 mihomo 外部控制器 API 组测速（内核并发，结果稳定）
- 📊 **真实吞吐验证**：不只看延迟，切到节点实测下载速度，"测速 76ms 却刷不开网页"的假活节点无所遁形
- 🌏 **地区策略**：地区黑名单（默认禁香港）+ 地区优先级（日本 > 新加坡 > 台湾 > 美国 > 英国）+ 线路类型偏好
- 🪄 **地区轮转抽样**：每轮按地区轮转实测 8 个节点，组测速丢包时高优先地区照样被验证
- 🛡 **防抖机制**：45% 评分领先阈值 + 120s 切换冷却 + 测速失败重试，不会在两个节点间反复横跳
- 📡 **本地看板**：浏览器实时查看体检结果/切换历史/日志，支持立即巡检、暂停、手动切换
- 🖥 **系统托盘**：托盘常驻 + 气泡通知，右键即可操作（Windows / macOS 菜单栏）
- 🔇 **零依赖运行**：打包后单文件免 Python 环境；不修改 Clash 配置文件，只调 API

## 🚀 快速开始

> 前提：已安装并运行 [Clash Verge Rev](https://github.com/clash-verge-rev/clash-verge-rev)（mihomo 内核），且开启了「外部控制器」。
> 工具会自动从 Verge 配置读取控制器地址与 secret（默认 `127.0.0.1:9097`）。

### 🪟 Windows

**方式一：直接用打包好的 exe**

1. 从 [Releases](../../releases) 下载 `ClashAutoSwitch.exe`（或自行构建，见下文）
2. 双击运行 → 托盘出现图标 + 气泡提示 → 浏览器自动打开看板 `http://127.0.0.1:38766`
3. 完成，之后它每 30 秒自动巡检切换

**方式二：源码运行（需 Python 3.10+）**

```bat
pip install pystray pillow
python clash_auto_switch.py
```

**Windows 小贴士**

| 需求 | 操作 |
| --- | --- |
| 桌面快捷方式 | 直接给 exe 创建快捷方式即可 |
| 开机自启 | `Win+R` → `shell:startup` → 把快捷方式放进去 |
| 停止 | 托盘右键 → 退出 |
| 杀软误报 | 把程序所在文件夹加入信任区即可（exe 用了固定解压目录 `runtime-tmp/`，多数杀软不再拦截） |

### 🍎 macOS

```bash
git clone https://github.com/<you>/clash-auto-switch.git
cd clash-auto-switch
bash build_mac.sh        # 一键构建 dist/ClashAutoSwitch.app 并打出 DMG
open dist/ClashAutoSwitch.app
```

也可以不装任何东西，用 **GitHub Actions 云构建**：把本仓库推到 GitHub → Actions → *Build macOS App* → Run workflow → 下载产物里的 `ClashAutoSwitch-macOS.dmg`。

**macOS 小贴士**

| 需求 | 操作 |
| --- | --- |
| 首次打开被 Gatekeeper 拦 | **右键 app → 打开**；或 `xattr -cr /Applications/ClashAutoSwitch.app` |
| 配置 / 日志位置 | `~/Library/Application Support/clash-auto-switch/` |
| 托盘 | 顶部菜单栏图标；气泡通知需在系统设置中允许 |
| 分发 | `build_mac.sh` 会同时产出 `ClashAutoSwitch-macOS.dmg`，可直接发给朋友 |

### 🌐 使用看板（两个平台一致）

启动后浏览器打开 `http://127.0.0.1:38766`（不自动弹就手动开）：

- **当前节点卡片**：地区 / 延迟 / 吞吐 / 评分
- **本轮体检结果**：每个节点的实测数据与是否可用
- **切换记录 / 运行日志**：每次自动切换的原因都可追溯
- **手动切换**：下拉选节点一键切走，之后 10 分钟内自动切换只响应"彻底失效"，不抢你的手动选择

## 🧠 工作原理

```
每 30s 一轮巡检
 ├─ 1. 连通性守卫：当前节点 gstatic 204 检查
 ├─ 2. 组测速：一次 API 调用测全组延迟（内核并发）
 ├─ 3. 地区轮转抽样：日本→新加坡→台湾→美国→英国 各取最优，共 8 个
 ├─ 4. 逐个实测吞吐：切换 → 下载 Cloudflare 测速文件（失败重试 1 次）
 ├─ 5. 评分排序：
 │      score = 地区优先级×1000 + 线路类型×50 + 延迟/1000×400 + 100/吞吐×300
 │      （分数越低越好）
 └─ 6. 决策：
        当前节点失效/劣化        → 立即切换
        有节点评分领先 ≥45%       → 切换（120s 冷却防抖）
        手动保持期内             → 只响应硬故障
```

**地区识别**基于节点名称中的旗帜 emoji 与关键词（日本/JP/Tokyo…），**黑名单**同样按名称匹配（默认 `香港 / HK / Hong Kong`），换订阅无需改配置。

## ⚙️ 配置说明（config.json）

```jsonc
{
  "controller": "127.0.0.1:9097",   // 外部控制器
  "mixed_port": 7897,               // Verge 混合端口（吞吐实测走这里）
  "target_groups": [],              // 接管的策略组；留空自动挑选
  "blocked_regions": ["香港", "Hong Kong", "HK"],   // 硬排除
  "region_priority": { "日本": 1, "新加坡": 2, "台湾": 3, "美国": 4, "英国": 5 },
  "interval": 30,                   // 巡检周期（秒）
  "candidate_limit": 8,             // 每轮实测吞吐的节点数
  "max_latency_ms": 1500,           // 延迟超过即判不可用
  "min_speed_mbps": 0.8,            // 吞吐低于即判不可用
  "bad_latency_ms": 1200,           // 当前节点延迟劣化阈值
  "bad_speed_mbps": 3.0,            // 当前节点吞吐劣化阈值
  "switch_threshold": 0.45,         // 更优节点需领先 45% 才切换
  "dashboard_port": 38766,          // 看板端口
  "open_dashboard": true            // 启动时自动打开浏览器
}
```

## 📦 从源码构建

```bash
pip install pyinstaller pystray pillow

# Windows
pyinstaller --clean -y --noconsole --icon icon.ico \
  --hidden-import pystray --hidden-import pystray._win32 \
  --add-data "icon.ico;." \
  --runtime-tmpdir ./runtime-tmp \
  --name ClashAutoSwitch clash_auto_switch.py

# macOS
bash build_mac.sh
```

> 💡 Windows 建议保留 `--runtime-tmpdir`：部分杀软会拦截 exe 向 `%TEMP%` 解压 DLL。

## 🛠 开发

```bash
python clash_auto_switch.py --list     # 列出候选/被排除节点
python clash_auto_switch.py --once     # 只跑一轮巡检
python clash_auto_switch.py --status   # 查看状态
python clash_auto_switch.py --switch "节点名"

# 重新生成 README 的演示 GIF（模拟数据，不含真实订阅信息）
python scripts/demo_server.py          # 终端 1
python scripts/make_gif.py             # 终端 2
```

## ❓ FAQ

<details>
<summary>为什么延迟很低的节点被跳过了？</summary>

延迟只代表控制面探测可达，不代表隧道可用。本工具以实测吞吐为准——下载跑不起来就是不可用。
</details>

<details>
<summary>巡检期间网络会抖一下？</summary>

会。逐节点吞吐实测需要真的把策略组切到该节点下载几 MB，单节点约 3~5 秒。巡检结束后立即恢复最优节点。
</details>

<details>
<summary>支持 Clash Premium / 原版 Clash 吗？</summary>

理论上有外部控制器 API 即可，但仅在 Clash Verge Rev（mihomo 内核）下测试过。组测速接口 `/group/{name}/delay` 为 mihomo 特有。
</details>

<details>
<summary>切换会改动我的订阅/配置文件吗？</summary>

不会。只通过 REST API 切换策略组当前选中项，等价于你在面板里点了一下别的节点。
</details>

## ⚠️ 免责声明

本项目仅供个人学习与本地网络质量诊断使用，请遵守所在地区法律法规，仅用于你拥有合法使用权的订阅服务。项目不提供、不内置、不分发任何代理节点或订阅。

## 📄 License

[MIT](LICENSE) © 2026 clash-auto-switch contributors
