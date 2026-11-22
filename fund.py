#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""世界脉搏 · 募捐页面监测（fund.py）

只做两件事：
  1. 这些项目有没有新捐款（按捐款 ID 判定：新 ID = 新捐款）
  2. 捐款人信息（页面公开显示的显示名、金额、时间、是否匿名/定期）

数据来源：页面内嵌的 __NEXT_DATA__ → __APOLLO_STATE__（结构化 JSON，比解析 HTML 稳定）
输出：site/fund.html + data/monitor.db 里的两张表

用法：
    python fund.py run              # 采集 + 生成页面
    python fund.py run --proxy http://127.0.0.1:7897   # 本机走代理
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "monitor.db"
SITE_DIR = ROOT / "site"
FUND_CFG = ROOT / "fund.json"
CONFIG = ROOT / "config.json"

LOCAL_TZ = timezone(timedelta(hours=8))
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS fund_campaigns (
  slug TEXT PRIMARY KEY,
  url TEXT,
  title TEXT,
  currency TEXT,
  current REAL,
  goal REAL,
  donors INTEGER,
  created_at TEXT,
  prev_current REAL,
  prev_donors INTEGER,
  status TEXT,
  first_seen TEXT,
  last_run TEXT
);
CREATE TABLE IF NOT EXISTS fund_donations (
  donation_id TEXT PRIMARY KEY,
  slug TEXT,
  name TEXT,
  amount REAL,
  currency TEXT,
  created_at TEXT,
  is_anonymous INTEGER,
  is_recurring INTEGER,
  first_seen TEXT
);
CREATE INDEX IF NOT EXISTS idx_don_slug ON fund_donations(slug);
CREATE INDEX IF NOT EXISTS idx_don_seen ON fund_donations(first_seen);
"""


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def load_proxy(argv_proxy):
    if argv_proxy is not None:
        return argv_proxy or None
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
        return cfg.get("proxy") or None
    except Exception:
        return None


def fetch_page(url, proxy):
    proxies = {"http": proxy, "https": proxy} if proxy else None
    last = ""
    for attempt in range(2):
        try:
            r = requests.get(url, headers=UA, timeout=25, proxies=proxies, allow_redirects=True)
            if r.status_code != 200:
                last = f"HTTP {r.status_code}"
            elif "__NEXT_DATA__" not in r.text:
                last = f"页面结构异常（{len(r.text)}B）"
            else:
                return r.text, "ok"
        except Exception as exc:
            last = f"{type(exc).__name__}: {str(exc)[:60]}"
        time.sleep(1.5)
    return None, last


MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"], 1)}


def parse_launch_date(html):
    """页面公开展示的发起日期（如 August 20th, 2026）。
    注意：JSON 里的 createdAt 是草稿建档时间，常与展示日期不一致，不能拿去当发起日。"""
    m = re.search(r'data-testid="created-date">\s*([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,\s*(20\d\d)', html)
    if not m:
        return ""
    mon, day, year = m.group(1), int(m.group(2)), m.group(3)
    if mon not in MONTHS:
        return ""
    return f"{year}-{MONTHS[mon]:02d}-{day:02d}"


def parse_page(html):
    """从 __NEXT_DATA__ 里取出项目节点与该项目的捐款节点"""
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        raise ValueError("找不到 __NEXT_DATA__")
    state = json.loads(m.group(1))["props"]["pageProps"]["__APOLLO_STATE__"]

    fkeys = [k for k in state if str(k).startswith("Fundraiser:")]
    if not fkeys:
        raise ValueError("找不到 Fundraiser 节点")
    fkey = fkeys[0]
    node = state[fkey]

    def money(obj):
        return (obj or {}).get("amount"), (obj or {}).get("currencyCode")

    cur_amt, cur_ccy = money(node.get("currentAmount"))
    goal_amt, goal_ccy = money(node.get("goalAmount"))

    donations = []
    for k, v in state.items():
        if not str(k).startswith("Donation:"):
            continue
        if ((v.get("fundraiser") or {}).get("__ref")) != fkey:
            continue
        amt, ccy = money(v.get("amount"))
        donations.append({
            "donation_id": str(v.get("id") or str(k).split(":", 1)[1]),
            "name": v.get("name") or ("匿名" if v.get("isAnonymous") else "未署名"),
            "amount": amt,
            "currency": ccy,
            "created_at": v.get("createdAt") or "",
            "is_anonymous": 1 if v.get("isAnonymous") else 0,
            "is_recurring": 1 if v.get("isRecurring") else 0,
        })
    donations.sort(key=lambda d: d["created_at"], reverse=True)

    return {
        "title": node.get("title") or "",
        "currency": cur_ccy or goal_ccy or "",
        "current": cur_amt,
        "goal": goal_amt,
        "donors": node.get("donationCount"),
        "created_at": parse_launch_date(html) or (node.get("createdAt") or "")[:10],
        "state": node.get("state") or "",
        "last24": node.get("donationsInLast24HoursCount"),
        "donations": donations,
    }


def collect(campaigns, proxy, logger=print):
    conn = connect()
    run_at = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
    results = []

    for item in campaigns:
        slug, url = item["slug"], item["url"]
        logger(f"  抓取 {slug} …", end="")
        html, status = fetch_page(url, proxy)
        row = conn.execute("SELECT * FROM fund_campaigns WHERE slug=?", (slug,)).fetchone()
        prev = dict(row) if row else None

        if html is None:
            logger(f" 失败（{status}）")
            conn.execute(
                "INSERT INTO fund_campaigns (slug,url,title,status,last_run,first_seen) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(slug) DO UPDATE SET status=excluded.status, last_run=excluded.last_run",
                (slug, url, (prev or {}).get("title") or item.get("label", slug), f"抓取失败：{status}", run_at, run_at),
            )
            results.append({"slug": slug, "url": url, "ok": False, "error": status,
                            "title": (prev or {}).get("title") or item.get("label", slug)})
            conn.commit()
            continue

        try:
            info = parse_page(html)
        except Exception as exc:
            logger(f" 解析失败（{exc}）")
            conn.execute(
                "INSERT INTO fund_campaigns (slug,url,title,status,last_run,first_seen) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(slug) DO UPDATE SET status=excluded.status, last_run=excluded.last_run",
                (slug, url, (prev or {}).get("title") or item.get("label", slug), f"解析失败：{exc}", run_at, run_at),
            )
            results.append({"slug": slug, "url": url, "ok": False, "error": f"解析失败：{exc}",
                            "title": (prev or {}).get("title") or item.get("label", slug)})
            conn.commit()
            continue

        # 已见过的捐款 ID
        seen = {r["donation_id"] for r in
                conn.execute("SELECT donation_id FROM fund_donations WHERE slug=?", (slug,))}
        new_list = [d for d in info["donations"] if d["donation_id"] not in seen]
        for d in info["donations"]:
            conn.execute(
                "INSERT OR IGNORE INTO fund_donations (donation_id,slug,name,amount,currency,created_at,"
                "is_anonymous,is_recurring,first_seen) VALUES (?,?,?,?,?,?,?,?,?)",
                (d["donation_id"], slug, d["name"], d["amount"], d["currency"], d["created_at"],
                 d["is_anonymous"], d["is_recurring"], run_at),
            )

        baseline = prev is None
        new_amount = sum((d["amount"] or 0) for d in new_list)
        donor_delta = None
        if prev and prev["donors"] is not None and info["donors"] is not None:
            donor_delta = info["donors"] - prev["donors"]
        baseline_count = len(new_list) if baseline else 0
        if baseline:
            new_list = []  # 首次只建基线，不把历史记录算作"新增"

        conn.execute(
            "INSERT INTO fund_campaigns (slug,url,title,currency,current,goal,donors,created_at,"
            "prev_current,prev_donors,status,first_seen,last_run) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(slug) DO UPDATE SET title=excluded.title, currency=excluded.currency, "
            "current=excluded.current, goal=excluded.goal, donors=excluded.donors, created_at=excluded.created_at, "
            "prev_current=excluded.prev_current, prev_donors=excluded.prev_donors, status=excluded.status, last_run=excluded.last_run",
            (slug, url, info["title"], info["currency"], info["current"], info["goal"], info["donors"],
             info["created_at"], (prev or {}).get("current"), (prev or {}).get("donors"), "ok", run_at, run_at),
        )
        conn.commit()

        results.append({
            "slug": slug, "url": url, "ok": True, "title": info["title"],
            "currency": info["currency"], "current": info["current"], "goal": info["goal"],
            "donors": info["donors"], "created_at": info["created_at"], "last24": info["last24"],
            "new": new_list, "new_amount": new_amount, "baseline": baseline,
            "baseline_count": baseline_count,
            "donor_delta": donor_delta, "embedded": len(info["donations"]),
        })
        logger(f" 成功：新增 {len(new_list)} 笔 / 累计 {info['current']} {info['currency']} / "
               f"{info['donors']} 位捐款人")

    conn.close()
    return results, run_at


def money_text(amount, currency):
    sym = {"USD": "$", "EUR": "€", "GBP": "£", "CAD": "C$", "AUD": "A$"}.get(currency or "", "")
    if amount is None:
        return "—"
    return f"{sym}{amount:,.0f}" if sym else f"{amount:,.0f} {currency}"


def time_text(iso):
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(LOCAL_TZ)
        return dt.strftime("%m-%d %H:%M")
    except Exception:
        return iso[:16]


def esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"));


def render(results, run_at, cfg):
    total_new = sum(len(r["new"]) for r in results if r["ok"])
    total_amount = {}
    for r in results:
        if r["ok"] and r["new"]:
            total_amount[r["currency"]] = total_amount.get(r["currency"], 0) + (r["new_amount"] or 0)
    amount_txt = "、".join(money_text(v, k) for k, v in total_amount.items()) or "—"
    baseline_all = all(r["baseline"] for r in results if r["ok"]) and results
    top_note = ('<div class="meta">首次运行：已建立基线（共收录 '
                f'{sum(r.get("baseline_count", 0) for r in results if r["ok"])} 笔历史明细），下次起统计新增。</div>'
                if baseline_all else "")

    cards = []
    for r in results:
        if not r["ok"]:
            cards.append(f"""
  <section class="card bad">
    <div class="t">{esc(r['title'])}</div>
    <div class="warn">本次抓取失败：{esc(r['error'])}（页面保留上次数据，稍后自动重试）</div>
    <div class="meta"><a href="{esc(r['url'])}" target="_blank" rel="noopener">页面 ↗</a></div>
  </section>""")
            continue

        pct = 0
        if r["goal"]:
            pct = min(100, round(100 * (r["current"] or 0) / r["goal"]))
        delta_txt = ""
        if r["baseline"]:
            delta_txt = (f'<span class="chip base">首次建立基线 · 已收录 {r["baseline_count"]} 笔历史明细'
                         f'（下次起显示新增）</span>')
        elif r["new"]:
            delta_txt = (f'<span class="chip new">新增 {len(r["new"])} 笔 · '
                         f'{money_text(r["new_amount"], r["currency"])}</span>')
        else:
            delta_txt = '<span class="chip zero">本次无新捐款</span>'
        if r.get("donor_delta"):
            delta_txt += (f'<span class="chip {"new" if r["donor_delta"] > 0 else "zero"}">'
                          f'捐款人数 较上次 {r["donor_delta"]:+d}</span>')
        elif r.get("donor_delta") == 0:
            delta_txt += '<span class="chip zero">捐款人数不变</span>'

        rows = []
        for d in r["new"][:10]:
            flags = []
            if d["is_recurring"]:
                flags.append("定期")
            if d["is_anonymous"]:
                flags.append("匿名")
            flag_txt = f'<em>{"/".join(flags)}</em>' if flags else ""
            rows.append(f'<li><span class="n">{esc(d["name"])}</span>'
                        f'<span class="a">{money_text(d["amount"], d["currency"])}</span>'
                        f'<span class="tm">{time_text(d["created_at"])}{flag_txt}</span></li>')
        new_block = ""
        if rows:
            more = f'<p class="more">另有 {len(r["new"]) - 10} 笔未显示</p>' if len(r["new"]) > 10 else ""
            new_block = f'<div class="sub">新增捐款明细</div><ul class="dl">{"".join(rows)}</ul>{more}'

        cards.append(f"""
  <section class="card">
    <div class="t">{esc(r['title'])}</div>
    <div class="bar"><i style="width:{pct}%"></i></div>
    <div class="nums">
      <b>{money_text(r['current'], r['currency'])}</b>
      <span>目标 {money_text(r['goal'], r['currency'])} · {pct}%</span>
    </div>
    <div class="meta">捐款人 {r['donors'] if r['donors'] is not None else '—'} 位
      · 页面内可见明细 {r['embedded']} 笔
      · 发起 {time_text(r['created_at'])}</div>
    <div class="delta">{delta_txt}</div>
    {new_block}
    <div class="meta"><a href="{esc(r['url'])}" target="_blank" rel="noopener">打开原页面 ↗</a></div>
  </section>""")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>募捐页面监测 · 世界脉搏</title>
<style>
:root{{--bg:#0e1116;--card:#161b22;--line:#253040;--fg:#e6edf3;--dim:#8b98a9;--hi:#ff7b72;--ok:#3fb950;--warn:#d29922;--link:#58a6ff}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;padding:16px 14px 40px}}
h1{{font-size:20px;margin:0 0 4px}}
.sub1{{color:var(--dim);font-size:13px;margin-bottom:16px}}
.top{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin-bottom:16px}}
.top b{{font-size:22px;color:var(--hi)}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin-bottom:12px}}
.card .t{{font-weight:600;font-size:15px;margin-bottom:8px}}
.bar{{height:6px;background:#0b0f14;border-radius:3px;overflow:hidden;margin:8px 0}}
.bar i{{display:block;height:100%;background:linear-gradient(90deg,#2f81f7,#3fb950)}}
.nums{{display:flex;justify-content:space-between;align-items:baseline}}
.nums b{{font-size:18px}}
.nums span{{color:var(--dim);font-size:12px}}
.meta{{color:var(--dim);font-size:12px;margin-top:6px}}
a{{color:var(--link);text-decoration:none}}
.chip{{display:inline-block;font-size:12px;padding:2px 8px;border-radius:999px;margin-top:8px}}
.chip.new{{background:#1f3d2b;color:#3fb950;border:1px solid #2ea043}}
.chip.zero{{background:#1c2530;color:var(--dim);border:1px solid var(--line)}}
.chip.base{{background:#2a2416;color:var(--warn);border:1px solid #6b5420}}
.sub{{color:var(--dim);font-size:12px;margin:12px 0 6px}}
.dl{{list-style:none;margin:0;padding:0}}
.dl li{{display:flex;gap:8px;align-items:baseline;padding:5px 0;border-bottom:1px dashed #1e2732}}
.dl .n{{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.dl .a{{color:var(--hi);font-variant-numeric:tabular-nums}}
.dl .tm{{color:var(--dim);font-size:12px;white-space:nowrap}}
.dl em{{font-style:normal;color:var(--warn);font-size:11px;margin-left:4px}}
.more{{color:var(--dim);font-size:12px;margin:6px 0 0}}
.warn{{color:var(--warn);font-size:13px;margin:8px 0}}
.foot{{color:var(--dim);font-size:12px;margin-top:20px;line-height:1.7}}
</style></head><body>
<h1>募捐页面监测</h1>
<div class="sub1">监测 {len(results)} 个公开募捐页面 · 更新于 {run_at}（{esc(cfg.get('runtime_label',''))}）</div>
<div class="top">本次新增捐款 <b>{total_new}</b> 笔 · 合计 {amount_txt}{top_note}<div class="meta">判定方式：捐款 ID 未出现过即为新捐款，并与页面显示的“捐款人数”变化交叉核对。</div></div>
{''.join(cards)}
<div class="foot">
  · 数据全部取自页面公开显示的项目进度与捐款明细，仅作事实记录，不含评论或推断。<br>
  · 捐款人显示名按页面原文照录，匿名捐款记为“匿名”，未做任何身份补充或关联。<br>
  · 页面只内嵌最近一段捐款明细（项目显示的总捐款人数可能大于可见条数），因此新增笔数以可见明细为准；<br>
    若两次检查之间新增过多，可能与“捐款人数”变化不一致，此时以人数变化为准。
</div>
</body></html>"""
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    path = SITE_DIR / "fund.html"
    path.write_text(html, encoding="utf-8")
    return path


def cmd_run(args):
    cfg = json.loads(FUND_CFG.read_text(encoding="utf-8-sig"))
    campaigns = cfg["campaigns"]
    proxy = load_proxy(args.proxy)
    print(f"读取配置：{len(campaigns)} 个页面；代理={'是' if proxy else '否'}")
    results, run_at = collect(campaigns, proxy)
    path = render(results, run_at, cfg)
    ok = sum(1 for r in results if r["ok"])
    total_new = sum(len(r["new"]) for r in results if r["ok"])
    print(f"完成：{ok}/{len(results)} 个页面抓取成功，新增捐款 {total_new} 笔")
    print(f"页面：{path}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run"], nargs="?", default="run")
    ap.add_argument("--proxy", default=None)
    a = ap.parse_args()
    return cmd_run(a)


if __name__ == "__main__":
    sys.exit(main())
