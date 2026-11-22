# -*- coding: utf-8 -*-
"""
世界脉搏 · WorldPulse
每日国际态势简报：多源 RSS 采集 -> 事件聚类 -> 可解释指标 -> 移动端可视化报告 -> 推送

设计原则（对应"不能出现虚假信息"的要求）：
1. 只做"聚合 + 摘录"，不生成任何原文没有的表述；每条都带来源与直达链接。
2. 摘要一律标注「原文摘录」，指标一律标注「规则计算」。
3. 单一信源的事件明确标注「未获多源印证」。
4. 抓取失败的源会写进「数据审计」，不隐瞒数据缺口。
5. 只收录国际公共事务类话题（灾害/冲突/经济/气候/卫生/科技/事故/人道），不做政治敏感议题。

命令：
    python pulse.py collect            只采集
    python pulse.py build [--push]     生成报告（--push 同时推送）
    python pulse.py run                采集 + 报告 + 推送（给定时任务用）
    python pulse.py serve              起本地服务，手机同 WiFi 打开
    python pulse.py status             看数据概况
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import hmac
import json
import logging
import os
import re
import smtplib
import socket
import sqlite3
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr, parsedate_to_datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
DATA_DIR = BASE_DIR / "data"
LOG_DIR = DATA_DIR / "logs"
DB_PATH = DATA_DIR / "monitor.db"
SITE_DIR = BASE_DIR / "site"

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}
POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

# ------------------------------------------------------------------ 词典

REGIONS = [
    ("namerica", "北美", ["美国", "加拿大", "白宫", "华盛顿", "United States", "US ", "America", "Canada", "White House", "Washington"]),
    ("latam", "拉美", ["巴西", "阿根廷", "委内瑞拉", "哥伦比亚", "智利", "秘鲁", "玻利维亚", "厄瓜多尔", "古巴", "海地", "墨西哥", "乌拉圭", "巴拉圭", "Brazil", "Argentina", "Venezuela", "Colombia", "Chile", "Peru", "Bolivia", "Ecuador", "Cuba", "Haiti", "Mexico"]),
    ("europe", "欧洲", ["欧盟", "乌克兰", "俄罗斯", "德国", "法国", "英国", "意大利", "西班牙", "波兰", "荷兰", "比利时", "瑞典", "芬兰", "挪威", "丹麦", "瑞士", "奥地利", "希腊", "葡萄牙", "捷克", "匈牙利", "罗马尼亚", "塞尔维亚", "克罗地亚", "爱尔兰", "北约", "Europe", "Ukraine", "Russia", "Germany", "France", "Britain", "Italy", "Spain", "Poland", "Netherlands", "Sweden", "Finland", "Norway", "Denmark", "Switzerland", "Austria", "Greece", "Portugal", "NATO"]),
    ("meast", "中东", ["伊朗", "以色列", "加沙", "巴勒斯坦", "叙利亚", "黎巴嫩", "也门", "沙特", "阿联酋", "卡塔尔", "伊拉克", "土耳其", "约旦", "科威特", "阿曼", "Iran", "Israel", "Gaza", "Palestin", "Syria", "Lebanon", "Yemen", "Saudi", "Emirates", "Qatar", "Iraq", "Turkey", "Jordan"]),
    ("africa", "非洲", ["苏丹", "尼日利亚", "刚果", "埃塞俄比亚", "肯尼亚", "南非", "马里", "索马里", "利比亚", "乍得", "尼日尔", "布基纳法索", "加纳", "坦桑尼亚", "乌干达", "卢旺达", "津巴布韦", "莫桑比克", "Africa", "Sudan", "Nigeria", "Congo", "Ethiopia", "Kenya", "South Africa", "Mali", "Somalia", "Libya", "Chad", "Niger", "Ghana"]),
    ("apac", "亚太", ["日本", "韩国", "朝鲜", "越南", "菲律宾", "泰国", "印尼", "马来西亚", "新加坡", "缅甸", "柬埔寨", "老挝", "蒙古", "澳大利亚", "新西兰", "Japan", "Korea", "Vietnam", "Philippines", "Thailand", "Indonesia", "Malaysia", "Singapore", "Myanmar", "Cambodia", "Australia", "New Zealand"]),
    ("southasia", "南亚", ["印度", "巴基斯坦", "阿富汗", "孟加拉", "斯里兰卡", "尼泊尔", "不丹", "马尔代夫", "India", "Pakistan", "Afghanistan", "Bangladesh", "Sri Lanka", "Nepal", "Maldives"]),
    ("global", "全球", ["联合国", "世界银行", "国际货币基金组织", "世卫组织", "气候大会", "G20", "G7", "金砖", "United Nations", "World Bank", "IMF", "WHO", "G20", "G7", "BRICS"]),
]

# topic key, 中文名, 关键词, 严重度权重
TOPICS = [
    ("conflict", "武装冲突", ["空袭", "袭击", "导弹", "无人机", "交火", "战斗", "停火", "战争", "冲突", "入侵", "攻势", "枪击", "airstrike", "missile", "drone", "ceasefire", "war", "conflict", "invasion", "offensive", "attack", "troops", "clashes"], 25),
    ("disaster", "自然灾害", ["地震", "海啸", "洪水", "洪灾", "台风", "飓风", "龙卷风", "山火", "野火", "干旱", "滑坡", "泥石流", "火山", "暴雨", "earthquake", "tsunami", "flood", "typhoon", "hurricane", "tornado", "wildfire", "drought", "landslide", "volcano", "storm"], 25),
    ("accident", "事故灾难", ["坠机", "空难", "脱轨", "撞车", "翻船", "沉船", "坍塌", "火灾", "crash", "derail", "capsize", "collapse", "explosion"], 15),
    ("humanitarian", "人道状况", ["难民", "人道", "饥荒", "撤离", "粮食危机", "refugee", "humanitarian", "famine", "evacuat", "food crisis"], 10),
    ("diplomacy", "外交关系", ["峰会", "会谈", "访问", "条约", "联合声明", "谈判", "会晤", "summit", "talks", "visit", "treaty", "negotiat", "agreement"], 10),
    ("health", "公共卫生", ["疫情", "病毒", "疫苗", "感染", "霍乱", "麻疹", "流感", "outbreak", "virus", "vaccine", "infection", "cholera", "measles"], 10),
    ("climate", "气候能源", ["气候变化", "碳排放", "碳中和", "能源危机", "石油", "天然气", "核电", "可再生", "emissions", "carbon", "energy crisis", "oil", "nuclear plant", "renewable", "climate"], 10),
    ("economy", "经济金融", ["降息", "加息", "利率", "通胀", "通缩", "衰退", "关税", "制裁", "股市", "汇率", "债务", "违约", "失业", "rate cut", "rate hike", "inflation", "tariff", "sanction", "recession", "debt", "default", "unemployment"], 8),
    ("tech", "科技产业", ["芯片", "人工智能", "半导体", "数据中心", "自动驾驶", "航天", "卫星", "chip", "semiconductor", "data center", "autonomous", "spacecraft", "satellite"], 6),
]

SEVERITY_WORDS = ["死亡", "遇难", "伤亡", "受伤", "万人", "killed", "dead", "death", "wounded", "injured", "magnitude", "richter", "casualt"]
ESCALATION_WORDS = ["升级", "空袭", "袭击", "战斗", "进攻", "入侵", "伤亡", "escalat", "strike", "offensive", "invasion", "killed"]
DEESCALATION_WORDS = ["停火", "和谈", "谈判", "撤军", "缓和", "ceasefire", "truce", "talks", "withdraw", "peace"]

REGION_LABEL = {k: n for k, n, _ in REGIONS}
TOPIC_LABEL = {t[0]: t[1] for t in TOPICS}
TOPIC_WEIGHT = {t[0]: t[3] for t in TOPICS}
TOPIC_KEYS = {t[0]: t[2] for t in TOPICS}


def _build_matcher(terms):
    parts = []
    for term in terms:
        if re.search(r"[A-Za-z]", term):
            parts.append(r"\b" + re.escape(term.strip()) + r"\b")
        else:
            parts.append(re.escape(term))
    return re.compile("|".join(parts), re.IGNORECASE)


REGION_MATCHERS = [(k, n, _build_matcher(terms), terms) for k, n, terms in REGIONS]
TOPIC_MATCHERS = [(k, _build_matcher(terms)) for k, _, terms, _ in TOPICS]
SEV_RE = _build_matcher(SEVERITY_WORDS)
ESC_RE = _build_matcher(ESCALATION_WORDS)
DEESC_RE = _build_matcher(DEESCALATION_WORDS)

# 官方表态通稿：属于"官方口径"而非"媒体态势"，不进入日报
OFFICIAL_RE = re.compile(
    r"(外交部发言人|中国外交部发言|中国驻[^，,。]{0,10}使馆|国防部新闻发言"
    r"|国家主席|总书记|中共中央|中央政治局|国务院总理|全国人大|全国政协|中央军委)"
)

# ------------------------------------------------------------------ 基础


def emit(msg=""):
    try:
        if sys.stdout is not None:
            print(msg, flush=True)
    except Exception:
        pass


def now_cn() -> datetime:
    return datetime.now()


def parse_time(raw: str | None) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    try:
        dt = parsedate_to_datetime(raw)
        if dt is not None:
            return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def clean_text(raw: str) -> str:
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def load_config() -> dict:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    cfg.setdefault("window_hours", 24)
    cfg.setdefault("max_items_per_source", 60)
    cfg.setdefault("site_dir", "site")
    return cfg


def setup_logging(verbose=False):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("worldpulse")
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    fh = logging.FileHandler(LOG_DIR / "pulse.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(fh)
    if sys.stdout is not None:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
        logger.addHandler(sh)
    return logger

# ------------------------------------------------------------------ 采集


def strip_namespaces(data: bytes) -> bytes:
    data = re.sub(rb'\sxmlns(:[A-Za-z0-9]+)?="[^"]*"', b"", data)
    data = re.sub(rb"<(/?)([A-Za-z0-9]+):", rb"<\1", data)
    return data


def parse_feed(content: bytes) -> list[dict]:
    try:
        root = ET.fromstring(strip_namespaces(content))
    except ET.ParseError:
        return []
    items = root.findall(".//item") or root.findall(".//entry")
    out = []
    for node in items:
        title = clean_text(node.findtext("title") or "")
        link = (node.findtext("link") or "").strip()
        if not link:
            for child in node.findall("link"):
                if child.get("href"):
                    link = child.get("href")
                    break
        desc = (
            node.findtext("description")
            or node.findtext("summary")
            or node.findtext("encoded")
            or node.findtext("content")
            or ""
        )
        published = (
            node.findtext("pubDate")
            or node.findtext("published")
            or node.findtext("updated")
            or node.findtext("date")
            or ""
        )
        if title:
            out.append(
                {"title": title, "link": link, "summary": clean_text(desc)[:400], "published": published}
            )
    return out


def fetch_source(source: dict, cfg: dict, logger) -> tuple[list[dict], str, float]:
    started = time.time()
    proxy = cfg.get("proxy") if source.get("use_proxy") else None
    proxies = {"http": proxy, "https": proxy} if proxy else None
    last_status = ""
    for attempt in range(2):
        try:
            resp = requests.get(
                source["url"], headers=UA, timeout=15, proxies=proxies, allow_redirects=True
            )
            if resp.status_code != 200:
                last_status = f"HTTP {resp.status_code}"
                time.sleep(1.2)
                continue
            items = parse_feed(resp.content)
            if not items:
                last_status = f"解析不到条目（{len(resp.content)}B）"
                time.sleep(0.6)
                continue
            return items, "ok", time.time() - started
        except Exception as exc:
            last_status = f"{type(exc).__name__}: {str(exc)[:70]}"
            time.sleep(1.2)
    return [], last_status or "未知错误", time.time() - started

# ------------------------------------------------------------------ 数据库

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  hash TEXT UNIQUE,
  source_id TEXT,
  source_name TEXT,
  tier INTEGER,
  lang TEXT,
  title TEXT,
  summary TEXT,
  link TEXT,
  published_at TEXT,
  fetched_at TEXT,
  region TEXT,
  topics TEXT,
  in_scope INTEGER
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  report_date TEXT,
  key_hash TEXT,
  region TEXT,
  topic TEXT,
  title TEXT,
  risk INTEGER,
  sources_count INTEGER,
  articles_count INTEGER,
  payload TEXT
);
CREATE TABLE IF NOT EXISTS daily_stats (
  date TEXT, region TEXT, articles INTEGER, events INTEGER, avg_risk REAL,
  PRIMARY KEY (date, region)
);
CREATE TABLE IF NOT EXISTS source_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_at TEXT, source_id TEXT, name TEXT, status TEXT, items INTEGER, elapsed REAL
);
CREATE INDEX IF NOT EXISTS idx_art_pub ON articles(published_at);
CREATE INDEX IF NOT EXISTS idx_ev_date ON events(report_date);
"""


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn

# ------------------------------------------------------------------ 分类


def classify(title: str) -> tuple[str, set[str], list[str]]:
    """返回 (地区 key, 命中的具体实体集合, 命中的主题 key 列表)"""
    entities = set()
    region = "global"
    for key, label, matcher, terms in REGION_MATCHERS:
        hit = matcher.search(title)
        if hit:
            if region == "global" and key != "global":
                region = key
            for term in terms:
                if term.lower() in title.lower():
                    entities.add(term.strip())
    topics = [k for k, matcher in TOPIC_MATCHERS if matcher.search(title)]
    return region, entities, topics


def bigrams(text: str) -> set[str]:
    text = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]+", "", text)
    if re.search(r"[\u4e00-\u9fa5]", text):
        return {text[i : i + 2] for i in range(len(text) - 1)}
    words = set(re.findall(r"[a-z0-9]{3,}", text.lower()))
    return words


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)

# ------------------------------------------------------------------ 聚类与指标


def cluster_articles(articles: list[dict]) -> list[dict]:
    clusters = []
    for art in articles:
        topics = art["topics_list"]
        primary = max(topics, key=lambda t: TOPIC_WEIGHT.get(t, 0)) if topics else "other"
        key = (art["region"], primary, tuple(sorted(art["entities"])[:2]))
        grams = bigrams(art["title"])
        placed = None
        for cl in clusters:
            if cl["key"] == key:
                placed = cl
                break
            if cl["region"] == art["region"] and cl["topic"] == primary and jaccard(grams, cl["grams"]) >= 0.45:
                placed = cl
                break
        if placed:
            placed["articles"].append(art)
            placed["grams"] |= grams
        else:
            clusters.append(
                {
                    "key": key,
                    "region": art["region"],
                    "topic": primary,
                    "articles": [art],
                    "grams": set(grams),
                }
            )
    return clusters


def score_cluster(cl: dict) -> dict:
    arts = cl["articles"]
    sources = sorted({a["source_name"] for a in arts})
    tier1 = {a["source_name"] for a in arts if a["tier"] == 1}
    titles_blob = " ".join(a["title"] for a in arts)

    coverage = min(30, len(sources) * 10)
    severity = TOPIC_WEIGHT.get(cl["topic"], 5)
    magnitude = min(15, len(SEV_RE.findall(titles_blob)) * 3)
    spread = min(20, len(arts) * 3)
    diverged = bool(ESC_RE.search(titles_blob)) and bool(DEESC_RE.search(titles_blob))
    divergence = 10 if diverged else 0
    authority = 10 if len(tier1) >= 2 else (5 if tier1 else 0)

    risk = coverage + severity + magnitude + spread + divergence + authority
    risk = int(min(100, risk))

    reasons = [
        f"独立媒体 {len(sources)} 家（覆盖度 {coverage}）",
        f"话题类型「{TOPIC_LABEL.get(cl['topic'], '其他')}」（权重 {severity}）",
    ]
    if magnitude:
        reasons.append(f"标题含伤亡/规模类表述（{magnitude}）")
    reasons.append(f"报道量 {len(arts)} 条（{spread}）")
    if diverged:
        reasons.append("不同媒体同时出现「升级」与「缓和」类表述（分歧 +10）")
    if authority:
        reasons.append(f"权威源（Tier-1）{len(tier1)} 家（+{authority}）")

    # 代表标题：优先中文、Tier-1、信息量大的
    arts_sorted = sorted(
        arts, key=lambda a: (0 if a["lang"] == "zh" else 1, a["tier"], -len(a["title"]))
    )
    rep = arts_sorted[0]

    return {
        "region": cl["region"],
        "topic": cl["topic"],
        "title": rep["title"],
        "risk": risk,
        "level": "高" if risk >= 70 else ("中" if risk >= 45 else "低"),
        "sources": sources,
        "sources_count": len(sources),
        "articles_count": len(arts),
        "reasons": reasons,
        "diverged": diverged,
        "articles": arts_sorted,
        "first_published": min((a["published_at"] for a in arts if a["published_at"]), default=""),
        "latest_published": max((a["published_at"] for a in arts if a["published_at"]), default=""),
    }


def refresh_classification(conn, cfg, days: int = 7, logger=None) -> int:
    """词典调整后，回头重新标注近期文章，避免旧数据与旧规则不一致。"""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        "SELECT id, title, region, topics, in_scope FROM articles WHERE fetched_at >= ?", (since,)
    ).fetchall()
    changed = 0
    for r in rows:
        region, _, topics = classify(r["title"])
        in_scope = bool(set(topics) & set(cfg["scope"]["topic_keys"]))
        if OFFICIAL_RE.search(r["title"]):
            in_scope = False
        if (
            region != r["region"]
            or json.loads(r["topics"] or "[]") != topics
            or bool(r["in_scope"]) != in_scope
        ):
            conn.execute(
                "UPDATE articles SET region=?, topics=?, in_scope=? WHERE id=?",
                (region, json.dumps(topics, ensure_ascii=False), 1 if in_scope else 0, r["id"]),
            )
            changed += 1
    conn.commit()
    if logger and changed:
        logger.info(f"重新标注 {changed} 条文章")
    return changed


def build_events(conn, cfg, report_date: str, logger) -> tuple[list[dict], dict]:
    window = cfg["window_hours"]
    since = (datetime.now() - timedelta(hours=window)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        "SELECT * FROM articles WHERE in_scope=1 "
        "AND published_at <> fetched_at "          # 只认有明确发布时间的报道，避免旧闻当新闻
        "AND published_at >= ? "
        "ORDER BY published_at DESC",
        (since,),
    ).fetchall()

    articles = []
    for r in rows:
        region = r["region"]
        topics = json.loads(r["topics"] or "[]")
        if region == "global" and r["summary"]:
            # 标题里没提到地区时，用首段摘录兜底判断
            fallback_region, _, fallback_topics = classify(r["summary"][:160])
            if fallback_region != "global":
                region = fallback_region
            if not topics and fallback_topics:
                topics = fallback_topics
                if not (set(topics) & set(cfg["scope"]["topic_keys"])):
                    continue
        articles.append(
            {
                "title": r["title"],
                "summary": r["summary"] or "",
                "link": r["link"] or "",
                "source_name": r["source_name"],
                "tier": r["tier"],
                "lang": r["lang"],
                "region": region,
                "topics_list": topics,
                "entities": set(),
                "published_at": r["published_at"] or "",
            }
        )

    clusters = cluster_articles(articles)
    events = [score_cluster(cl) for cl in clusters]
    events.sort(key=lambda e: (-e["risk"], -e["sources_count"], -e["articles_count"]))

    # 同一标题（不同源几乎同时转发）只保留一条事件，避免重复占位
    deduped = []
    seen_titles = set()
    for ev in events:
        norm = re.sub(r"[\s\W]+", "", ev["title"])[:24]
        if norm in seen_titles:
            continue
        seen_titles.add(norm)
        deduped.append(ev)
    events = deduped

    conn.execute("DELETE FROM events WHERE report_date=?", (report_date,))
    for ev in events:
        key_hash = hashlib.sha1(
            (ev["region"] + ev["topic"] + ev["title"]).encode("utf-8")
        ).hexdigest()[:16]
        conn.execute(
            "INSERT INTO events (report_date, key_hash, region, topic, title, risk, "
            "sources_count, articles_count, payload) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                report_date,
                key_hash,
                ev["region"],
                ev["topic"],
                ev["title"],
                ev["risk"],
                ev["sources_count"],
                ev["articles_count"],
                json.dumps(
                    {
                        "reasons": ev["reasons"],
                        "diverged": ev["diverged"],
                        "sources": ev["sources"],
                        "titles": [a["title"] for a in ev["articles"]],
                    },
                    ensure_ascii=False,
                ),
            ),
        )

    by_region = {}
    for ev in events:
        by_region.setdefault(ev["region"], []).append(ev)
    conn.execute("DELETE FROM daily_stats WHERE date=?", (report_date,))
    for region, evs in by_region.items():
        arts_count = sum(e["articles_count"] for e in evs)
        conn.execute(
            "INSERT INTO daily_stats (date, region, articles, events, avg_risk) VALUES (?,?,?,?,?)",
            (report_date, region, arts_count, len(evs), sum(e["risk"] for e in evs) / len(evs)),
        )
    conn.commit()

    total_articles = sum(len(e["articles"]) for e in events)

    # 与前一日的对比（云端运行靠它做趋势，不依赖本机缓存）
    prev_date = (datetime.strptime(report_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    prev_rows = conn.execute(
        "SELECT region, articles, events, avg_risk FROM daily_stats WHERE date=?", (prev_date,)
    ).fetchall()
    prev = {
        "date": prev_date,
        "ok": bool(prev_rows),
        "events": sum(r["events"] for r in prev_rows),
        "articles": sum(r["articles"] for r in prev_rows),
        "regions": {r["region"]: r["events"] for r in prev_rows},
    }

    meta = {
        "window_hours": window,
        "articles_in_scope": total_articles,
        "articles_fetched": len(rows),
        "events": len(events),
        "regions": len(by_region),
        "high_risk": sum(1 for e in events if e["risk"] >= 70),
        "prev": prev,
    }
    return events, meta

# ------------------------------------------------------------------ 报告渲染

CSS = """
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
:root{--bg:#0f1216;--panel:#171c23;--line:#262c36;--txt:#e8ecf2;--dim:#9aa4b2;
--acc:#4da3ff;--hi:#ff5c5c;--mid:#ffa93c;--lo:#3ddc97;--chip:#1f2630}
html,body{margin:0;padding:0;background:var(--bg);color:var(--txt);
font-family:-apple-system,"PingFang SC","Microsoft YaHei UI","Microsoft YaHei",system-ui,sans-serif;
font-size:15px;line-height:1.62;-webkit-font-smoothing:antialiased}
.wrap{max-width:760px;margin:0 auto;padding:0 14px 56px}
header{padding:26px 0 18px}
.brand{display:flex;align-items:center;gap:9px;font-size:13px;color:var(--dim);letter-spacing:2px}
.dot{width:8px;height:8px;border-radius:50%;background:var(--acc);box-shadow:0 0 10px var(--acc)}
h1{font-size:25px;margin:10px 0 4px;font-weight:650;letter-spacing:.5px}
.date{color:var(--dim);font-size:13px}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:9px;margin:16px 0 22px}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:11px 10px}
.kpi b{display:block;font-size:21px;font-weight:650;line-height:1.2}
.kpi span{font-size:11px;color:var(--dim)}
nav{position:sticky;top:0;z-index:9;background:rgba(15,18,22,.88);backdrop-filter:blur(10px);
display:flex;gap:8px;overflow-x:auto;padding:9px 0;margin:0 -14px 18px;padding-left:14px;scrollbar-width:none}
nav::-webkit-scrollbar{display:none}
nav a{flex:0 0 auto;font-size:12.5px;color:var(--dim);text-decoration:none;border:1px solid var(--line);
border-radius:20px;padding:5px 12px;background:var(--panel)}
h2{font-size:15px;margin:30px 0 12px;font-weight:600;color:var(--txt);
display:flex;align-items:center;gap:8px}
h2::before{content:"";width:3px;height:14px;background:var(--acc);border-radius:2px}
.lead{background:linear-gradient(165deg,#1b2635,#141a22);border:1px solid #2b3646;border-radius:14px;
padding:14px 16px;margin-bottom:11px}
.lead .t{font-size:15.5px;font-weight:600;margin-bottom:6px}
.lead .s{font-size:12.5px;color:var(--dim)}
.lead .q{font-size:13px;color:#c7d2e0;margin:8px 0 4px;padding-left:10px;border-left:2px solid #35506e}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.chip{font-size:11px;padding:2px 8px;border-radius:20px;background:var(--chip);color:#b9c4d2;border:1px solid var(--line)}
.chip.hi{background:rgba(255,92,92,.13);color:#ff8a8a;border-color:rgba(255,92,92,.32)}
.chip.mid{background:rgba(255,169,60,.13);color:#ffc070;border-color:rgba(255,169,60,.3)}
.chip.lo{background:rgba(61,220,151,.12);color:#7ff0bd;border-color:rgba(61,220,151,.28)}
.chip.acc{background:rgba(77,163,255,.13);color:#8cc4ff;border-color:rgba(77,163,255,.3)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px 15px;margin-bottom:11px}
.card h3{margin:0 0 6px;font-size:15px;font-weight:600;line-height:1.45}
.meta{font-size:12px;color:var(--dim);margin-bottom:2px}
.quote{font-size:13px;color:#cfd8e4;margin:9px 0 0;padding:9px 11px;background:#12171d;
border-radius:9px;border-left:2px solid #2f4a68}
.quote em{color:#7f8b9b;font-style:normal;font-size:11.5px}
.src{margin:10px 0 0;padding:0;list-style:none}
.src li{padding:7px 0;border-top:1px solid var(--line);font-size:12.5px}
.src li:first-child{border-top:0}
.src a{color:#9ecbff;text-decoration:none;word-break:break-all}
.src .m{color:var(--dim);font-size:11.5px}
details{margin-top:9px}
summary{cursor:pointer;font-size:12px;color:var(--acc);list-style:none}
summary::-webkit-details-marker{display:none}
details ul{margin:8px 0 0;padding-left:16px;color:var(--dim);font-size:12px}
.region{display:grid;grid-template-columns:repeat(2,1fr);gap:9px}
.rc{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px}
.rc .n{font-size:13px;font-weight:600;display:flex;justify-content:space-between;align-items:center}
.rc .v{font-size:12px;color:var(--dim);margin-top:5px}
.bar{height:4px;border-radius:3px;background:#232a34;margin-top:9px;overflow:hidden}
.bar i{display:block;height:100%;border-radius:3px}
table{width:100%;border-collapse:collapse;font-size:12px;background:var(--panel);
border:1px solid var(--line);border-radius:12px;overflow:hidden}
th,td{padding:8px 9px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--dim);font-weight:500;background:#141920}
.ok{color:var(--lo)}.bad{color:var(--hi)}
.note{font-size:12px;color:var(--dim);background:#12171d;border:1px dashed #2c3540;
border-radius:12px;padding:12px 14px;margin-top:10px}
.foot{margin-top:34px;font-size:11.5px;color:#78828f;line-height:1.7;border-top:1px solid var(--line);padding-top:16px}
a.plain{color:inherit;text-decoration:none}
@media (max-width:420px){.kpis{grid-template-columns:repeat(2,1fr)}h1{font-size:23px}}
"""


def esc(text) -> str:
    return html.escape(str(text or ""))


def excerpt(text: str, limit: int = 130) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    cut = re.split(r"(?<=[。！？])", text)
    out = ""
    for part in cut:
        if len(out) + len(part) > limit and out:
            break
        out += part
    return out.strip() or text[:limit]


def verification_level(ev: dict) -> tuple[str, str]:
    """按"独立信源数 + 是否有一级源"给出可信度分级（纯规则，不涉及内容判断）。"""
    n = ev["sources_count"]
    t1 = sum(1 for a in ev["articles"] if a.get("tier") == 1)
    if n >= 3 or (n >= 2 and t1 >= 1):
        return "三源以上印证", "lo"
    if n == 2:
        return "双源报道", "mid"
    return "单源待核实", "hi"


def render_report(report_date: str, events: list[dict], meta: dict, audit: list[dict], cfg: dict) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    total_art = meta["articles_in_scope"]
    src_ok = sum(1 for a in audit if a["status"] == "ok")
    src_total = len(audit)

    # KPI
    kpis = f"""
<div class="kpis">
  <div class="kpi"><b>{total_art}</b><span>收录报道</span></div>
  <div class="kpi"><b>{meta['events']}</b><span>识别事件</span></div>
  <div class="kpi"><b>{meta['high_risk']}</b><span>高关注度</span></div>
  <div class="kpi"><b>{src_ok}/{src_total}</b><span>信源可用</span></div>
</div>"""

    prev = meta.get("prev") or {}
    if prev.get("ok"):
        d_ev = meta["events"] - prev["events"]
        d_art = total_art - prev["articles"]
        arrow_ev = "▲" if d_ev > 0 else ("▼" if d_ev < 0 else "＝")
        trend_line = (
            f"较昨日：事件 {arrow_ev} {abs(d_ev)} · 报道 {abs(d_art)}"
            f"（昨日 {prev['date']} 为 {prev['events']} 起 / {prev['articles']} 条）"
        )
    else:
        trend_line = "较昨日：无对比基线（首次运行或昨日未生成）"

    # 今日要点
    leads = []
    for ev in events[:3]:
        lvl = "hi" if ev["risk"] >= 70 else ("mid" if ev["risk"] >= 45 else "lo")
        excerpt_html = ""
        for art in ev["articles"]:
            if art["summary"]:
                excerpt_html = (
                    f'<div class="q">“{esc(excerpt(art["summary"]))}”<br>'
                    f'<em>— {esc(art["source_name"])} 原文摘录</em></div>'
                )
                break
        leads.append(
            f"""<div class="lead">
  <div class="t">{esc(ev['title'])}</div>
  <div class="s">{esc(REGION_LABEL.get(ev['region'], '其他'))} · {esc(TOPIC_LABEL.get(ev['topic'], '其他'))} ·
    {ev['sources_count']} 家媒体 · {ev['articles_count']} 条报道 · 风险指数 {ev['risk']}</div>
  <div class="chips">
    <span class="chip {lvl}">关注度 {ev['level']}</span>
    <span class="chip">{esc(REGION_LABEL.get(ev['region'], '其他'))}</span>
    <span class="chip">{esc(TOPIC_LABEL.get(ev['topic'], '其他'))}</span>
  </div>{excerpt_html}
</div>"""
        )
    lead_html = "".join(leads) or '<div class="note">本次窗口内没有符合范围内的事件。</div>'

    # 地区态势
    region_cards = []
    region_events = {}
    for ev in events:
        region_events.setdefault(ev["region"], []).append(ev)
    order = ["apac", "southasia", "meast", "europe", "africa", "namerica", "latam", "global"]
    for rkey in order:
        evs = region_events.get(rkey)
        if not evs:
            continue
        top = evs[0]
        avg = sum(e["risk"] for e in evs) / len(evs)
        color = "var(--hi)" if avg >= 70 else ("var(--mid)" if avg >= 45 else "var(--lo)")
        pv = (prev.get("regions") or {}).get(rkey)
        dtxt = f" · 较昨日 {len(evs) - pv:+d}" if pv is not None else ""
        region_cards.append(
            f"""<div class="rc">
  <div class="n"><span>{esc(REGION_LABEL.get(rkey, '其他'))}</span><span style="color:{color};font-size:12px">{len(evs)} 起</span></div>
  <div class="v">{esc(excerpt(top['title'], 46))}</div>
  <div class="bar"><i style="width:{min(100, int(avg))}%;background:{color}"></i></div>
  <div class="v">平均风险 {int(avg)} · 最高 {max(e['risk'] for e in evs)}{dtxt}</div>
</div>"""
        )

    # 事件卡片
    event_cards = []
    for ev in events:
        lvl = "hi" if ev["risk"] >= 70 else ("mid" if ev["risk"] >= 45 else "lo")
        src_rows = []
        for art in ev["articles"][:8]:
            src_rows.append(
                f'<li><a href="{esc(art["link"])}" target="_blank" rel="noreferrer">{esc(art["title"])}</a>'
                f'<div class="m">{esc(art["source_name"])} · Tier-{art["tier"]} · '
                f'{esc((art["published_at"] or "")[:16])}</div></li>'
            )
        single = (
            '<div class="meta" style="color:#ffa93c;margin-top:8px">⚠ 仅单一信源，未获多源印证，请点原文核实</div>'
            if ev["sources_count"] == 1
            else ""
        )
        vlabel, vcls = verification_level(ev)
        diverge = (
            '<div class="meta" style="color:#ffa93c;margin-top:6px">⚠ 不同媒体在升级/缓和倾向上表述不一，已并列呈现各方标题</div>'
            if ev["diverged"]
            else ""
        )
        quote_html = ""
        for art in ev["articles"]:
            if art["summary"]:
                quote_html = (
                    f'<div class="quote">“{esc(excerpt(art["summary"], 150))}”<br>'
                    f'<em>— {esc(art["source_name"])} 原文摘录（点击下方链接看全文）</em></div>'
                )
                break
        reasons = "".join(f"<li>{esc(r)}</li>" for r in ev["reasons"])
        event_cards.append(
            f"""<article class="card">
  <div class="chips" style="margin:0 0 8px">
    <span class="chip {lvl}">{ev['level']} · {ev['risk']}</span>
    <span class="chip">{esc(REGION_LABEL.get(ev['region'], '其他'))}</span>
    <span class="chip">{esc(TOPIC_LABEL.get(ev['topic'], '其他'))}</span>
    <span class="chip acc">{ev['sources_count']} 源 / {ev['articles_count']} 条</span>
    <span class="chip {vcls}">{vlabel}</span>
  </div>
  <h3>{esc(ev['title'])}</h3>
  {quote_html}{single}{diverge}
  <details><summary>指标构成（规则计算，非结论）</summary><ul>{reasons}</ul></details>
  <details><summary>全部报道（{ev['articles_count']} 条，点开直达原文）</summary>
    <ul class="src">{''.join(src_rows)}</ul>
  </details>
</article>"""
        )

    # 媒体对照
    compare = []
    for ev in events:
        if ev["sources_count"] < 2:
            continue
        rows = []
        for art in ev["articles"][:6]:
            rows.append(
                f'<li><span class="m">{esc(art["source_name"])}（{art["lang"]}）</span><br>'
                f'<a href="{esc(art["link"])}" target="_blank" rel="noreferrer">{esc(art["title"])}</a></li>'
            )
        compare.append(
            f"""<article class="card">
  <div class="meta">{esc(REGION_LABEL.get(ev['region'], '其他'))} · {ev['sources_count']} 家媒体同题报道</div>
  <h3 style="font-size:14px">{esc(excerpt(ev['title'], 40))}…</h3>
  <ul class="src">{''.join(rows)}</ul>
</article>"""
        )
        if len(compare) >= 8:
            break
    compare_html = "".join(compare) or '<div class="note">今日没有出现被两家以上媒体同时报道的事件。</div>'

    # 话题热度：按类别汇总今日事件的条数与平均风险，看"今天世界在关心什么"
    topic_stat = {}
    for ev in events:
        topic_stat.setdefault(ev["topic"], []).append(ev)
    topic_rows = []
    for tkey, evs in sorted(topic_stat.items(), key=lambda kv: (-len(kv[1]), -max(e["risk"] for e in kv[1]))):
        avg = sum(e["risk"] for e in evs) / len(evs)
        lvl = "hi" if avg >= 70 else ("mid" if avg >= 45 else "lo")
        topic_rows.append(
            f"<tr><td>{esc(TOPIC_LABEL.get(tkey, tkey))}</td><td>{len(evs)}</td>"
            f"<td>{sum(e['articles_count'] for e in evs)}</td>"
            f"<td class='{lvl == 'hi' and 'bad' or 'ok'}'>{int(avg)}</td></tr>"
        )
    topic_html = (
        "<table><thead><tr><th>话题</th><th>事件</th><th>报道</th><th>平均风险</th></tr></thead>"
        f"<tbody>{''.join(topic_rows)}</tbody></table>"
        if topic_rows
        else '<div class="note">暂无</div>'
    )

    # 数据审计
    audit_rows = []
    for a in audit:
        cls = "ok" if a["status"] == "ok" else "bad"
        audit_rows.append(
            f"<tr><td>{esc(a['name'])}</td><td class='{cls}'>{esc(a['status'])}</td>"
            f"<td>{a['items']}</td><td>{a['elapsed']:.1f}s</td></tr>"
        )

    html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0f1216">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>世界脉搏 · {report_date}</title>
<link rel="manifest" href="manifest.webmanifest">
<link rel="icon" href="icon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="icon.svg">
<style>{CSS}</style>
</head><body><div class="wrap">
<header>
  <div class="brand"><span class="dot"></span>WORLD PULSE · 每日态势简报</div>
  <h1>{report_date} 国际态势</h1>
  <div class="date">生成于 {now} · 数据窗口过去 {meta['window_hours']} 小时 · 全部内容可溯源</div>
  <div class="date" style="color:#4da3ff">{trend_line}</div>
</header>
{kpis}
<nav>
  <a href="#lead">今日要点</a><a href="#watch">专项监测</a><a href="#regions">地区态势</a>
  <a href="#topics">话题热度</a>
  <a href="#events">事件全景</a><a href="#compare">媒体对照</a>
  <a href="#audit">数据审计</a><a href="#method">方法与免责</a>
</nav>

<h2 id="lead">今日要点</h2>
{lead_html}

<h2 id="watch">专项监测</h2>
<div class="note" style="margin-bottom:10px">对指定的公开募捐页面做独立监测，只记录项目进度与公开显示的捐款明细（新捐款按捐款 ID 判定），不含评论或推断。</div>
<a href="fund.html" style="display:block;background:#161b22;border:1px solid #253040;border-radius:10px;padding:12px 14px;margin:8px 0;color:#58a6ff;text-decoration:none;font-size:14px">募捐页面监测 · 5 个境外项目 →<div style="color:#8b98a9;font-size:12px;margin-top:4px">项目进度、新增捐款、捐款人明细（公开信息，仅作事实记录）</div></a>

<h2 id="regions">地区态势</h2>
<div class="region">{''.join(region_cards) or '<div class="note">暂无数据</div>'}</div>

<h2 id="topics">话题热度</h2>
<div class="note" style="margin-bottom:10px">按事件数与平均风险排序，反映当天国际报道的注意力分布，不代表事件重要性排序。</div>
{topic_html}

<h2 id="events">事件全景（{meta['events']}）</h2>
{''.join(event_cards) or '<div class="note">暂无事件</div>'}

<h2 id="compare">媒体对照</h2>
<div class="note" style="margin-bottom:10px">同一事件下并列各家原文标题，不做翻译改写、不做倾向评判，由你自己判断差异。</div>
{compare_html}

<h2 id="audit">数据审计</h2>
<table><thead><tr><th>信源</th><th>状态</th><th>条数</th><th>耗时</th></tr></thead>
<tbody>{''.join(audit_rows)}</tbody></table>

<h2 id="method">方法与免责</h2>
<div class="note">
<b>1. 内容怎么来的</b>：程序直接读取各媒体公开 RSS，只做「标题聚合 + 首段摘录」，不生成原文没有的说法，不做机器翻译，不做倾向改写。<br>
<b>2. 风险指数是什么</b>：由「独立媒体数、话题严重度、标题伤亡/规模词、报道量、是否出现相反叙事、权威源数量」六项按固定公式相加得出，满分 100。它是<b>报道强度指标</b>，不是事实判断，也不是预测。<br>
<b>3. 可信度怎么标</b>：只有一家媒体报道的事件标「未获多源印证」；Tier-1 指通讯社/公共广播等一级源，Tier-2 指主流市场化媒体。二者都可能是错的，请以原文为准。<br>
<b>4. 覆盖边界</b>：本报告只覆盖国际公共事务（灾害、冲突、经济、气候、卫生、科技、事故、人道），不覆盖政治敏感议题；抓取失败的源会在上表列出，不隐藏缺口。<br>
<b>5. 结论由谁下</b>：程序不下结论。任何判断请回到原始链接。<br>
<b>6. 可信度分级</b>：三源以上（或含 Tier-1）为「三源以上印证」，双源为「双源报道」，单源为「单源待核实」。分级只数"有几家独立媒体说了"，不代表内容为真。
</div>

<div class="foot">
世界脉搏 · {cfg.get('runtime_label', '本机运行')} · 报告为机器聚合产物<br>
{report_date} 生成 · 共 {src_total} 个信源 · {total_art} 条报道 · {meta['events']} 起事件
</div>
</div>
<script>
if ('serviceWorker' in navigator && location.protocol === 'https:' || location.hostname === 'localhost') {{
  navigator.serviceWorker.register('sw.js').catch(function(){{}});
}}
</script>
</body></html>
"""
    return html_doc


MANIFEST = {
    "name": "世界脉搏 · 每日态势简报",
    "short_name": "世界脉搏",
    "start_url": "./",
    "display": "standalone",
    "background_color": "#0f1216",
    "theme_color": "#0f1216",
    "lang": "zh-CN",
    "icons": [
        {"src": "icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"}
    ],
}

ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192">
<rect width="192" height="192" rx="38" fill="#0f1216"/>
<circle cx="96" cy="96" r="58" fill="none" stroke="#4da3ff" stroke-width="3"/>
<ellipse cx="96" cy="96" rx="26" ry="58" fill="none" stroke="#4da3ff" stroke-width="2" opacity=".8"/>
<path d="M38 96h116M44 72h104M44 120h104" stroke="#4da3ff" stroke-width="2" opacity=".55"/>
<circle cx="96" cy="96" r="7" fill="#4da3ff"/>
<path d="M96 30v14M96 148v14M30 96h14M148 96h14" stroke="#4da3ff" stroke-width="2.5" opacity=".7"/>
</svg>"""

SW_JS = """const CACHE = 'worldpulse-v1';
const ASSETS = ['./', './index.html', './manifest.webmanifest', './icon.svg'];
self.addEventListener('install', e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS))); });
self.addEventListener('fetch', e => {
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});
"""


def write_site(html_doc: str, report_date: str) -> Path:
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    (SITE_DIR / "archive").mkdir(exist_ok=True)
    (SITE_DIR / "index.html").write_text(html_doc, encoding="utf-8")
    (SITE_DIR / "archive" / f"{report_date}.html").write_text(html_doc, encoding="utf-8")
    (SITE_DIR / "manifest.webmanifest").write_text(
        json.dumps(MANIFEST, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (SITE_DIR / "sw.js").write_text(SW_JS, encoding="utf-8")
    icon = SITE_DIR / "icon.svg"
    if not icon.exists():
        icon.write_text(ICON_SVG, encoding="utf-8")
    return SITE_DIR / "index.html"

# ------------------------------------------------------------------ 推送


def notify_desktop(title: str, body: str) -> None:
    t = base64.b64encode(title.encode()).decode()
    b = base64.b64encode(body[:250].encode()).decode()
    script = f"""$ErrorActionPreference='Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$t=[System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{t}'))
$b=[System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{b}'))
$xmlText='<toast><visual><binding template="ToastText02"><text id="1">'+[System.Security.SecurityElement]::Escape($t)+'</text><text id="2">'+[System.Security.SecurityElement]::Escape($b)+'</text></binding></visual></toast>'
$x=New-Object Windows.Data.Xml.Dom.XmlDocument
$x.LoadXml($xmlText)
$n=New-Object Windows.UI.Notifications.ToastNotification $x
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}}\\WindowsPowerShell\\v1.0\\powershell.exe').Show($n)
"""
    p = DATA_DIR / "_toast.ps1"
    p.write_text(script, encoding="utf-8-sig")
    r = subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(p)],
        capture_output=True, timeout=40,
    )
    if r.returncode != 0:
        raise RuntimeError((r.stderr or b"").decode("utf-8", "ignore")[:200])


def notify_feishu(cfg, title, body):
    conf = cfg["notify"]["feishu"]
    payload = {"msg_type": "text", "content": {"text": f"{title}\n{body}"}}
    if conf.get("secret"):
        ts = str(int(time.time()))
        sign = base64.b64encode(
            hmac.new(conf["secret"].encode(), f"{ts}\n{conf['secret']}".encode(), hashlib.sha256).digest()
        ).decode()
        payload["timestamp"], payload["sign"] = ts, sign
    r = requests.post(conf["webhook"], json=payload, timeout=20)
    r.raise_for_status()


def _cred(cfg, name, field, env_name) -> str:
    """凭证优先取配置，其次取环境变量（云端用 GitHub Secrets 注入，避免明文入库）。"""
    return (str((cfg["notify"].get(name) or {}).get(field) or "").strip()
            or os.environ.get(env_name, "").strip())


def notify_serverchan(cfg, title, body):
    key = _cred(cfg, "serverchan", "sendkey", "SERVERCHAN_KEY")
    r = requests.post(f"https://sctapi.ftqq.com/{key}.send", data={"title": title, "desp": body}, timeout=20)
    r.raise_for_status()


def notify_pushplus(cfg, title, body):
    token = _cred(cfg, "pushplus", "token", "PUSHPLUS_TOKEN")
    r = requests.post(
        "https://www.pushplus.plus/send",
        json={"token": token, "title": title, "content": body, "template": "txt"}, timeout=20,
    )
    r.raise_for_status()


def notify_email(cfg, title, body):
    conf = cfg["notify"]["email"]
    sender = conf.get("from") or conf.get("user")
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(title, "utf-8")
    msg["From"] = formataddr(("世界脉搏", sender))
    msg["To"] = ", ".join(conf["to"])
    if conf.get("ssl"):
        s = smtplib.SMTP_SSL(conf["smtp_host"], conf["smtp_port"], timeout=30)
    else:
        s = smtplib.SMTP(conf["smtp_host"], conf["smtp_port"], timeout=30)
        s.starttls()
    try:
        s.login(conf["user"], conf["password"])
        s.sendmail(sender, conf["to"], msg.as_string())
    finally:
        s.quit()


CHANNELS = {
    "desktop": lambda cfg, t, b: notify_desktop(t, b),
    "feishu": notify_feishu,
    "serverchan": notify_serverchan,
    "pushplus": notify_pushplus,
    "email": notify_email,
}


def lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def push_brief(cfg, events, meta, report_date, logger) -> int:
    port = cfg.get("server", {}).get("port", 8848)
    url = cfg.get("public_url") or f"http://{lan_ip()}:{port}/"
    top = events[0]["title"] if events else "无符合范围内的事件"
    by_region = {}
    for ev in events[:12]:
        by_region[REGION_LABEL.get(ev["region"], "其他")] = by_region.get(
            REGION_LABEL.get(ev["region"], "其他"), 0
        ) + 1
    region_txt = "、".join(f"{k} {v}" for k, v in by_region.items()) or "无"

    focus = []
    for ev in events[:3]:
        vlabel, _ = verification_level(ev)
        focus.append(f"· {ev['title']}\n  {REGION_LABEL.get(ev['region'],'其他')} / "
                     f"{TOPIC_LABEL.get(ev['topic'],'其他')} / 风险 {ev['risk']} / {vlabel}")
    focus_txt = "\n".join(focus) if focus else "（今日无符合范围的事件）"

    prev_txt = ""
    prev = meta.get("prev") or {}
    if prev.get("ok"):
        d_ev = meta["events"] - prev["events"]
        d_ar = meta["articles_in_scope"] - prev["articles"]
        arrow = lambda v: "▲" if v > 0 else ("▼" if v < 0 else "－")
        prev_txt = f"较昨日：事件 {arrow(d_ev)}{abs(d_ev)} · 报道 {arrow(d_ar)}{abs(d_ar)}\n"

    body = (
        f"【今日概览】\n"
        f"{meta['articles_in_scope']} 条报道 · {meta['events']} 起事件 · {meta['high_risk']} 起高关注度\n"
        f"{prev_txt}"
        f"\n【焦点事件】\n{focus_txt}\n"
        f"\n【地区分布】\n{region_txt}\n"
        f"\n完整简报（含逐条信源与原文链接）：\n{url}\n"
        f"\n（风险分由公开报道的源数、伤亡规模、扩散度等规则计算，不代表事实认定；细节请回原文核实）"
    )
    title = f"世界脉搏 {report_date}：{meta['events']} 起事件，{meta['high_risk']} 起高关注"

    env_keys = {"pushplus": "PUSHPLUS_TOKEN", "serverchan": "SERVERCHAN_KEY"}
    sent = 0
    for name, fn in CHANNELS.items():
        conf = cfg["notify"].get(name) or {}
        if not conf.get("enabled"):
            continue
        if name == "desktop" and os.name != "nt":
            continue  # 云端（Linux）没有桌面通知，跳过不报错
        if name in env_keys and not str(conf.get("token") or conf.get("sendkey") or "").strip() \
                and not os.environ.get(env_keys[name], "").strip():
            logger.info(f"跳过推送：{name} 未配置凭证")
            continue
        try:
            fn(cfg, title, body)
            sent += 1
            logger.info(f"推送成功：{name}")
        except Exception as exc:
            logger.warning(f"推送失败：{name} → {exc}")
    return sent

# ------------------------------------------------------------------ 命令


def cmd_collect(args):
    cfg = load_config()
    logger = setup_logging(args.verbose)
    conn = connect()
    run_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_new = 0
    audit = []
    for src in cfg["sources"]:
        if not src.get("enabled", True):
            continue
        items, status, elapsed = fetch_source(src, cfg, logger)
        items = items[: cfg["max_items_per_source"]]
        new = 0
        for it in items:
            title = it["title"]
            if not title:
                continue
            # 同一信源同一标题只留一条（很多 RSS 有重复条目）
            if conn.execute(
                "SELECT 1 FROM articles WHERE source_id=? AND title=? LIMIT 1", (src["id"], title)
            ).fetchone():
                continue
            region, entities, topics = classify(title)
            in_scope = bool(set(topics) & set(cfg["scope"]["topic_keys"]))
            if OFFICIAL_RE.search(title):
                in_scope = False  # 官方表态通稿不计入"媒体态势"
            digest = hashlib.sha1((src["id"] + title + it["link"]).encode("utf-8")).hexdigest()
            pub = parse_time(it["published"])
            pub_str = pub.strftime("%Y-%m-%d %H:%M:%S") if pub else run_at
            try:
                conn.execute(
                    "INSERT INTO articles (hash, source_id, source_name, tier, lang, title, summary, "
                    "link, published_at, fetched_at, region, topics, in_scope) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        digest, src["id"], src["name"], src["tier"], src["lang"], title,
                        it["summary"], it["link"], pub_str, run_at, region,
                        json.dumps(topics, ensure_ascii=False), 1 if in_scope else 0,
                    ),
                )
                new += 1
            except sqlite3.IntegrityError:
                pass
        conn.execute(
            "INSERT INTO source_audit (run_at, source_id, name, status, items, elapsed) VALUES (?,?,?,?,?,?)",
            (run_at, src["id"], src["name"], status, len(items), round(elapsed, 2)),
        )
        audit.append({"name": src["name"], "status": status, "items": len(items), "elapsed": elapsed})
        total_new += new
        logger.info(f"{src['name']:<16} {status:<44} 新增 {new} 条")
    conn.commit()
    conn.close()
    logger.info(f"采集完成，新增 {total_new} 条")
    emit(f"采集完成：新增 {total_new} 条，信源 {sum(1 for a in audit if a['status']=='ok')}/{len(audit)} 可用")
    return 0


def cmd_build(args):
    cfg = load_config()
    logger = setup_logging(args.verbose)
    conn = connect()
    report_date = args.date or datetime.now().strftime("%Y-%m-%d")
    refresh_classification(conn, cfg, logger=logger)
    events, meta = build_events(conn, cfg, report_date, logger)
    last_run = conn.execute("SELECT run_at FROM source_audit ORDER BY id DESC LIMIT 1").fetchone()
    audit_rows = conn.execute(
        "SELECT name, status, items, elapsed FROM source_audit WHERE run_at=? ORDER BY id",
        (last_run["run_at"] if last_run else "",),
    ).fetchall()
    audit = [
        {"name": r["name"], "status": r["status"], "items": r["items"], "elapsed": r["elapsed"] or 0}
        for r in audit_rows
    ]
    doc = render_report(report_date, events, meta, audit, cfg)
    path = write_site(doc, report_date)
    conn.close()
    logger.info(f"报告已生成：{path}（{meta['events']} 起事件 / {meta['articles_in_scope']} 条报道）")
    emit(f"报告已生成：{path}")
    emit(f"  事件 {meta['events']} 起，报道 {meta['articles_in_scope']} 条，高关注度 {meta['high_risk']} 起")
    if args.push:
        conn = connect()
        sent = push_brief(cfg, events, meta, report_date, logger)
        conn.close()
        emit(f"  推送完成：{sent} 个渠道")
    return 0


def cmd_run(args):
    rc = cmd_collect(args)
    if rc != 0:
        return rc
    args.push = True
    if not hasattr(args, "date"):
        args.date = None
    return cmd_build(args)


def cmd_status(args):
    conn = connect()
    total = conn.execute("SELECT COUNT(*) c FROM articles").fetchone()["c"]
    scope = conn.execute("SELECT COUNT(*) c FROM articles WHERE in_scope=1").fetchone()["c"]
    latest = conn.execute("SELECT MAX(fetched_at) m FROM articles").fetchone()["m"]
    ev = conn.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
    emit(f"文章库：{total} 条（范围内 {scope} 条）")
    emit(f"最近采集：{latest or '—'}")
    emit(f"事件记录：{ev} 条")
    conn.close()
    return 0


def cmd_serve(args):
    cfg = load_config()
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    host = args.host or cfg.get("server", {}).get("host", "0.0.0.0")
    port = args.port or cfg.get("server", {}).get("port", 8848)

    class Quiet(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(SITE_DIR), **kw)

        def log_message(self, *a):
            pass

    ip = lan_ip()
    emit(f"世界脉搏已启动：")
    emit(f"  电脑访问 http://127.0.0.1:{port}/")
    emit(f"  手机（同一 WiFi）http://{ip}:{port}/")
    emit("  手机浏览器里「添加到主屏幕」即可当 App 用。Ctrl+C 停止。")
    srv = ThreadingHTTPServer((host, port), Quiet)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        emit("已停止")
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="pulse", description="世界脉搏 · 每日国际态势简报")
    sub = p.add_subparsers(dest="cmd")

    c = sub.add_parser("collect", help="采集各信源 RSS")
    c.add_argument("--verbose", action="store_true")
    c.set_defaults(func=cmd_collect)

    b = sub.add_parser("build", help="生成报告")
    b.add_argument("--date", help="报告日期 YYYY-MM-DD，默认今天")
    b.add_argument("--push", action="store_true", help="生成后推送")
    b.add_argument("--verbose", action="store_true")
    b.set_defaults(func=cmd_build)

    r = sub.add_parser("run", help="采集 + 生成 + 推送（定时任务用这个）")
    r.add_argument("--verbose", action="store_true")
    r.set_defaults(func=cmd_run, push=True)

    s = sub.add_parser("serve", help="本地服务，手机可访问")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.set_defaults(func=cmd_serve)

    st = sub.add_parser("status", help="看数据概况")
    st.set_defaults(func=cmd_status)
    return p


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
