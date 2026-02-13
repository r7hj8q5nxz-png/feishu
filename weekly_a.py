import os
import time
import datetime as dt
import requests
import feedparser
from urllib.parse import urlparse

FEISHU_WEBHOOK = (os.environ.get("FEISHU_WEBHOOK_WEEKLY_A") or "").strip()
DEEPSEEK_API_KEY = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()

FEISHU_MAX_LEN = 1800
FEISHU_SLEEP_SEC = 1.2
FEISHU_RETRY = 6

DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"

# 续写轮数上限（你要求“允许多轮续写”）
CONTINUE_MAX_ROUNDS = 6


# -------------------------
# Feishu
# -------------------------
def _post_to_feishu_once(text: str):
    payload = {"msg_type": "text", "content": {"text": text}}
    return requests.post(FEISHU_WEBHOOK, json=payload, timeout=25)

def post_to_feishu(text: str):
    if not FEISHU_WEBHOOK:
        raise RuntimeError("Missing FEISHU_WEBHOOK_WEEKLY_A secret.")
    last = None
    for i in range(FEISHU_RETRY):
        r = _post_to_feishu_once(text)
        if 200 <= r.status_code < 300:
            return
        last = f"Feishu status={r.status_code}, body={r.text[:300]}"
        time.sleep((2 ** i) * 1.1)
    raise RuntimeError(last or "Feishu post failed.")

def split_into_chunks(text: str, max_len: int):
    lines = text.splitlines()
    chunks, buf, cur = [], [], 0
    for line in lines:
        add = len(line) + 1
        if cur + add > max_len and buf:
            chunks.append("\n".join(buf))
            buf, cur = [], 0
        buf.append(line)
        cur += add
    if buf:
        chunks.append("\n".join(buf))
    fixed = []
    for c in chunks:
        if len(c) <= max_len:
            fixed.append(c)
        else:
            for j in range(0, len(c), max_len):
                fixed.append(c[j:j + max_len])
    return fixed

def post_to_feishu_in_chunks(text: str, max_len: int = FEISHU_MAX_LEN):
    chunks = split_into_chunks(text, max_len)
    total = len(chunks)
    for idx, c in enumerate(chunks, 1):
        header = "" if total == 1 else f"（第 {idx}/{total} 段）\n"
        post_to_feishu(header + c)
        time.sleep(FEISHU_SLEEP_SEC)


# -------------------------
# RSS
# -------------------------
def read_feed(url: str, limit: int = 12):
    try:
        d = feedparser.parse(url)
        items = []
        for e in d.entries[:limit]:
            title = (e.get("title") or "").strip()
            link = (e.get("link") or "").strip()
            t = e.get("published_parsed") or e.get("updated_parsed")
            published_ts = int(time.mktime(t)) if t else None
            if title:
                items.append({"title": title, "link": link, "published_ts": published_ts})
        return items
    except Exception as ex:
        print("RSS error:", url, str(ex))
        return []

def dedup(items):
    seen, out = set(), []
    for it in items:
        key = (it.get("title") or "")[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out

def filter_recent(items, max_age_seconds: int, now_ts: int):
    out = []
    for it in items:
        ts = it.get("published_ts")
        if ts is None:
            continue
        age = now_ts - ts
        if 0 <= age <= max_age_seconds:
            out.append(it)
    return out

def fmt_ts(ts: int):
    return dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")

def domain_of(url: str):
    try:
        if not url:
            return "unknown"
        return urlparse(url).netloc or "unknown"
    except:
        return "unknown"

def material_block(title: str, items, cap: int):
    lines = [f""]
    if not items:
        lines.append("（近7天内无符合条件条目）")
        return "\n".join(lines)

    items = items[:cap]
    for i, it in enumerate(items, 1):
        pub = fmt_ts(it["published_ts"])
        dom = domain_of(it.get("link", ""))
        # 关键：不提供URL，避免模型产出链接占位符
        lines.append(f"{i}. {it['title']} ｜来源:{dom}｜日期:{pub}")
    return "\n".join(lines)


# -------------------------
# DeepSeek
# -------------------------
def deepseek_chat(messages, max_tokens: int = 4200) -> str:
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("Missing DEEPSEEK_API_KEY secret.")
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    r = requests.post(DEEPSEEK_URL, headers=headers, json=payload, timeout=140)
    print("DeepSeek status:", r.status_code)
    if r.status_code != 200:
        raise RuntimeError(f"DeepSeek failed: {r.status_code}, {r.text[:300]}")
    return r.json()["choices"][0]["message"]["content"].strip()

def is_complete_weekly_a(text: str) -> bool:
    t = text.replace(" ", "")
    return ("【4)" in t) or ("【4）" in t)

def call_deepseek_weekly_a(material_text: str, today_str: str) -> str:
    system_rules = f"""
今天是：{today_str}（北京时间）。
你是“宏观/政策分析官 + AI产业预言家 + ToB落地顾问”，输出《周报A：经济&AI政策》。

【强约束】
1) 禁止输出任何链接、URL、或“【链接1】”占位符；只允许基于标题做摘要与推理
2) 不得编造不存在的政策/项目/日期/数据；不知道就写“证据不足”
3) 必须完整输出到【4) 7天行动清单】；写不下就压缩，不许半截停
4) 信息密度高：每条都要落到“对我意味着什么/我下一步做什么”

【结构（必须完整）】
【0) 一句话总览】
【1) 关键变化 Top 6（标题摘要/影响/对我意味着）】
【2) AI政策解读 Top 6（合规/算力/数据/招投标等角度）】
【3) 未来90天 3个剧本（驱动因素/受益方/受损方/我该怎么做/领先指标/触发阈值/概率&窗口）】
【4) 7天行动清单（5条：动作/产出物/截止时间/验收标准）】
""".strip()

    messages = [
        {"role": "system", "content": system_rules},
        {"role": "user", "content": f"【素材（仅标题/来源/日期）】\n{material_text}"},
    ]

    out = deepseek_chat(messages, max_tokens=4200)

    # 多轮续写：直到完整或达到轮数上限
    rounds = 0
    while (not is_complete_weekly_a(out)) and rounds < CONTINUE_MAX_ROUNDS:
        rounds += 1
        cont_messages = messages + [
            {"role": "assistant", "content": out},
            {"role": "user", "content": "你输出仍不完整：请从中断处继续，保持原结构与编号，必须补齐【4) 7天行动清单】直至结束。仍禁止任何链接/URL/占位符。"},
        ]
        more = deepseek_chat(cont_messages, max_tokens=2600)
        out = (out.rstrip() + "\n\n" + more.lstrip()).strip()

    if not is_complete_weekly_a(out):
        out += "\n\n【4) 7天行动清单】\n（模型多轮续写后仍未补齐：请进一步压缩【1)】【2)】条目数量，或提高max_tokens。）"
    return out


# -------------------------
# Main
# -------------------------
def main():
    now_ts = int(time.time())
    WEEK = 7 * 24 * 3600

    beijing_now = dt.datetime.utcnow() + dt.timedelta(hours=8)
    today_str = beijing_now.strftime("%Y-%m-%d")
    title = f"周报A｜经济&AI政策（北京 {beijing_now.strftime('%Y-%m-%d %H:%M')}）"

    econ_feeds = [
        "https://news.google.com/rss/search?q=%E5%9B%BD%E5%8A%A1%E9%99%A2%20%E7%BB%8F%E6%B5%8E%20%E6%94%BF%E7%AD%96%20when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=%E5%9B%BD%E5%AE%B6%E5%8F%91%E6%94%B9%E5%A7%94%20%E6%94%BF%E7%AD%96%20%E9%80%9A%E7%9F%A5%20when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=%E8%B4%A2%E6%94%BF%E9%83%A8%20%E6%94%BF%E7%AD%96%20%E9%80%9A%E7%9F%A5%20when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=%E5%A4%AE%E8%A1%8C%20%E9%87%91%E8%9E%8D%20%E6%94%BF%E7%AD%96%20when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
    ]
    ai_policy_feeds = [
        "https://news.google.com/rss/search?q=%E7%BD%91%E4%BF%A1%E5%8A%9E%20%E7%AE%97%E6%B3%95%20%E5%A4%87%E6%A1%88%20when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=%E7%94%9F%E6%88%90%E5%BC%8FAI%20%E7%AE%A1%E7%90%86%20%E9%80%9A%E7%9F%A5%20when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=%E5%B7%A5%E4%BF%A1%E9%83%A8%20%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD%20%E6%94%BF%E7%AD%96%20when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=%E6%95%B0%E6%8D%AE%20%E8%A6%81%E7%B4%A0%20%E6%B5%81%E9%80%9A%20%E6%94%BF%E7%AD%96%20when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
    ]

    econ_items, ai_items = [], []
    for u in econ_feeds:
        econ_items.extend(read_feed(u, limit=14))
    for u in ai_policy_feeds:
        ai_items.extend(read_feed(u, limit=14))

    econ_items = filter_recent(dedup(econ_items), WEEK, now_ts)
    ai_items = filter_recent(dedup(ai_items), WEEK, now_ts)

    # 关键：为避免内容过长导致续写压力，先在素材层面限量
    material = "\n\n".join([
        material_block("经济政策标题（全国｜近7天）", econ_items, cap=18),
        material_block("AI政策标题（全国｜近7天）", ai_items, cap=18),
    ])

    try:
        digest = call_deepseek_weekly_a(material, today_str)
    except Exception as e:
        digest = f"（DeepSeek调用失败：{e}。已降级为标题素材）\n\n{material}"

    text = f"{title}\n\n{digest}".strip()
    post_to_feishu_in_chunks(text, max_len=FEISHU_MAX_LEN)


if __name__ == "__main__":
    main()
