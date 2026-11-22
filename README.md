# 世界脉搏 worldpulse

每天自动采集十余家通讯社/主流媒体的公开 RSS，聚合成「事件」，给出可解释的风险指数，生成一张手机上能直接看的简报页。

## 两种运行方式

| 方式 | 谁在跑 | 依赖 | 文件 |
|---|---|---|---|
| **云端（推荐，能脱离电脑）** | GitHub Actions，每天 08:00 北京时间自动跑并发布到 GitHub Pages | 只要一个 GitHub 账号 | `config.cloud.json` + `.github/workflows/daily.yml` + `上传到GitHub.ps1`，见 `云部署说明.md` |
| 本机 | 你的 Windows 电脑，计划任务 + 本地网页服务 | 电脑要开着 | `config.json` + `install_tasks.ps1` / `启动.ps1` |

云端模式下 GitHub 的机器在境外，BBC / Guardian / Al Jazeera 这些源可直连，**不需要代理**；代价是 `github.io` 在大陆访问时快时慢。

面向的三个目标：

| 目标 | 当前实现 | 说明 |
|---|---|---|
| 每天定时汇报监控情况 | 云端每天 08:00 自动跑 `run`（采集+生成+发布），本机用计划任务 | 云端模式不依赖你的电脑 |
| 每天推送各地区态势感知 | 报告按 8 个地区分区，逐条列事件，并给出与昨日的增减对比 | 地区由标题关键词词典归类 |
| 总结主流媒体对大事件的反馈 + 风险评估 | 同一事件聚合多源报道、并列各媒体标题、按独立信源数做可信度分级、给出 0–100 风险分 | **规则化汇总，不是大模型写的摘要**，见下方"边界" |


**边界（必须知道）**：本程序没有接大模型，所以"总结"是"事件聚类 + 多源标题并列 + 规则打分"，不做机器翻译、不生成自然语言综述。它保证的是**可溯源**（每条都指向原文链接），不是"读起来像人写的分析"。需要真正的摘要/翻译，得接一个 LLM API（预留了位置，见文末）。

## 目录

```
worldpulse/
├── pulse.py               主程序（采集→分类→聚类→打分→出报告→推送→起服务）
├── config.json            信源、时间窗口、代理、服务端口、推送渠道、话题白名单
├── site/
│   ├── index.html         最新报告（手机端样式，可直接双击打开看）
│   ├── archive/           以往每天的报告存档
│   ├── manifest.webmanifest / sw.js / icon.svg    PWA 资源（可"添加到主屏幕"）
├── data/
│   ├── monitor.db         SQLite：articles / events / daily_stats / source_audit
│   └── logs/pulse.log
├── tools/check_feeds.py   信源可用性探测（直连 + 代理双测）
├── 启动.ps1               起本地服务并打开浏览器
├── install_tasks.ps1      注册每天 08:00 自动简报 + 登录自启服务
└── uninstall_tasks.ps1    取消上面的自动化
```

## 常用命令

```powershell
$py = "C:\Users\jiang\AppData\Local\Programs\Python\Python311\python.exe"
cd C:\Users\jiang\Doubao\chats\2026-11-22\new-chat\worldpulse

& $py pulse.py run                 # 采集 + 生成 + 推送（每日任务跑的就是这个）
& $py pulse.py collect --verbose   # 只采集，看每个信源成功/失败原因
& $py pulse.py build               # 只用库里的数据重新生成报告
& $py pulse.py serve               # 起网页服务 http://127.0.0.1:8848/
& $py pulse.py status              # 看库里有多少文章/事件、各信源最近状态
```

立即手动触发一次定时任务：

```powershell
& "C:\Windows\System32\schtasks.exe" /run /tn "世界脉搏-每日简报"
```

## 手机上打开（像个小程序）

1. 确认服务在跑：`& $py pulse.py serve`，或登录后由启动快捷方式自动起。
2. 电脑和手机连同一个 WiFi，用电脑的局域网 IP 访问：`http://<电脑IP>:8848/`。
   IP 可用 `& $py pulse.py status` 或 `ipconfig` 查看；程序推送的桌面通知里也会带这个地址。
3. 首次访问 Windows 可能弹防火墙授权，选"允许专用网络"。
4. 在手机浏览器里"添加到主屏幕"，就是一个图标应用（PWA，已内置 manifest 和离线壳）。

## 信源与可靠性设计

信源分两档：

- **T1**（国家级通讯社/公共广播）：新华网·国际、人民网·国际、中新网·国际、BBC 中文、BBC World
- **T2**（主流市场化媒体）：界面新闻、纽约时报中文网、The Guardian、Al Jazeera、NPR

不出现虚假信息的四条硬规则：

1. **只认有明确发布时间的报道**。抓取时间不等于发布时间——新华网、人民网这类源常常没有 `pubDate`（或是旧档），若按"抓取时间"过滤，会把几个月前的旧闻当成"今日"。程序只让 `published_at != fetched_at` 且落在时间窗内的条目进入日报，宁可少报，不把旧闻充数。
2. **每条都可溯源**。报告里每个事件都列出原文标题 + 媒体 + 时间 + 链接，点开就能核对，不做无源的转述。
3. **不做二次加工成"事实"**。没有摘要模型，就不会生成看似权威却无出处的结论。
4. **失败透明**。某个信源抓不到（代理抖动、被墙、无 RSS）时，不会静默消失，`source_audit` 里记着原因，报告底部"信源健康"区也会显示当次可用数。

官方表态类通稿（外交部/使馆/国防部发言人、领导人活动等）默认过滤，因为它们不是"事件报道"，混进来会让报告变成通稿汇编。需要时可改 `config.json` 的 `scope`。

## 风险指数怎么算（报告里也有免责说明）

```
coverage  = min(30, 独立信源数 × 10)                        # 几家独立媒体报道
severity  = 话题权重（冲突25/灾害25/事故15/人道10/外交10/卫生10/气候10/经济8/科技6）
magnitude = min(15, 伤亡·规模词命中 × 3)
spread    = min(20, 相关报道条数 × 3)
divergence= 同时出现升级与缓和表述 +10
authority = ≥2 个 T1 源 +10，1 个 +5
总分封顶 100 → 高(≥70) / 中(≥45) / 低
```

它是**可解释的关注度排序**，不是预测，也不是情报评估。

## 代理（境外源必须）

`config.json` 里 `"proxy": "http://127.0.0.1:7897"`。境外 5 个源（BBC/纽约时报/Guardian/AlJazeera/BBC World）走它，境内源直连。

代理没开或节点抖动的表现：这几个源在 `source_audit` 里出现 `SSLError` / `HTTP 502`，报告只剩境内源。程序每个源自带 2 次重试，仍失败就跳过——**不会因此中断整份报告**。想确认当前状态就跑 `pulse.py collect --verbose` 或 `pulse.py status`。

换代理端口：改 `config.json` 的 `proxy`。新增境外源：在 `sources` 里加一条，`"use_proxy": true`。

## 调整

| 想改什么 | 改哪里 |
|---|---|
| 时间窗口（当前 36 小时） | `config.json` → `window_hours`。调大样本多，但会把更早的事拉进来 |
| 每日推送时间 | 重跑 `install_tasks.ps1 -Time "07:30"` |
| 每个源最多取几条 | `max_items_per_source` |
| 收录哪些话题 | `scope.topic_keys`（正向白名单，只收灾害/冲突/外交/经济/气候/卫生/科技/事故/人道） |
| 推送渠道 | `notify`：desktop / 邮件 / Server酱 / PushPlus / 飞书 / 通用 Webhook，默认只开 desktop |
| 服务端口 | `server.host` / `server.port`，或 `serve --port` |

## 接大模型做真摘要（未实现，需你提供 key）

在 `pulse.py` 的 `build_events()` 之后、`render_report()` 之前插一步：把每个事件的多条标题+摘要拼成 prompt，调用你选的模型，产出 2–3 句中文综述和一句风险判断，写进 `events.summary` 字段，模板里已有位置。需要：OpenAI/DeepSeek/豆包等任一家的 key，以及是否允许把新闻标题发给该服务。
