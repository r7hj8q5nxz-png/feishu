import os
import time
import datetime as dt
import requests
import feedparser

FEISHU_WEBHOOK = (os.environ.get("FEISHU_WEBHOOK_WEEKLY_A") or "").strip()
DEEPSEEK_API_KEY = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()

def post_to_feishu(text: str):
    if not FEISHU_WEBHOOK:
        raise RuntimeError("Missing FEISHU_WEBHOOK_WEEKLY_A secret.")
    payload = {"msg_type": "text", "content": {"text": text}}
    r = requests.post(FEISHU_WEBHOOK, json=payload, timeout=20)
    r.raise_for_status()

def post_to_feishu_in_chunks(text: str, max_len: int = 3500):
    if len(text) <= max_len:
        post_to_feishu(text)
        return
    lines = text.splitlines()
    chunk, chunks, cur = [], [], 0
    for line in lines:
        add = len(line) + 1
        if cur + add > max_len and chunk:
            chunks.append("\n".join(chunk))
            chunk, cur = [], 0
        chunk.append(line); cur += add
    if chunk:
        chunks.append("\n".join(chunk))
    for idx, c in enumerate(chunks, 1):
        post_to_feishu(c if idx == 1 else f"（续 {idx}）\n{c}")

def read_feed(url: str, limit: int = 12):
    try:
        d = feedparser.parse(url)
        items = []
        for e in d.entries[:limit]:
            title = (e.get("title") or "").strip()
            link = (e.get("link") or "").strip()
            t = e.get("published_parsed") or e.get("updated_parsed")
            published_ts = int(time.mktime(t)) if t else None
            if title and link:
                items.append({"title": title, "link": link, "published_ts": published_ts})
        return items
    except Exception as ex:
        print("RSS error:", url, str(ex))
        return []

def dedup(items):
    seen, out = set(), []
    for it in items:
        if it["link"] in seen:
            continue
        seen.add(it["link"]); out.append(it)
    return out

def filter_recent(items, max_age_seconds: int, now_ts: int):
    out = []
    for it in items:
        ts = it.get("published_ts")
        if ts is None:
            continue
        if 0 <= (now_ts - ts) <= max_age_seconds:
            out.append(it)
    return out

def block(title: str, items):
    lines = [f""]
    if not items:
        lines.append("（近7天内无符合条件的条目）")
        return "\n".join(lines)
    for i, it in enumerate(items, 1):
        lines.append(f"{i}. {it['title']}\n{it['link']}")
    return "\n".join(lines)

def call_deepseek(material_text: str, today_str: str) -> str:
    if not DEEPSEEK_API_KEY:
        return "（未配置DEEPSEEK_API_KEY，已降级为原始情报）\n\n" + material_text

    prompt = f"""
今天是：{today_str}（北京时间）。
你是我的“周报A（经济+AI政策）”秘书。只基于素材总结，不得编造。每条必须带链接。

输出结构（严格）：
【日期】{today_str}
【周期】近7天

【1) 经济政策 Top 6】
- 一句话概括 + 影响对象 + 我该怎么用（1句）+ 链接

【2) AI政策 Top 6】
- 一句话概括 + 重点（合规/备案/数据/算力/扶持）+ 我该怎么用（1句）+ 链接

【3) 本周结论】
- 3条：强判断短句

【4) 下周动作】
- 3条：产出物 + 截止时间 + 完成标准

素材：
{material_text}
""".strip()

    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}], "temperature": 0.2}

    r = requests.post(url, headers=headers, json=payload, timeout=60)
    print("DeepSeek status:", r.status_code)
    if r.status_code != 200:
        return "（DeepSeek调用失败，已降级为原始情报）\n\n" + material_text
    return r.json()["choices"][0]["message"]["content"].strip()

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
    ]
    ai_policy_feeds = [
        "https://news.google.com/rss/search?q=%E7%BD%91%E4%BF%A1%E5%8A%9E%20%E7%AE%97%E6%B3%95%20%E5%A4%87%E6%A1%88%20when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=%E7%94%9F%E6%88%90%E5%BC%8FAI%20%E7%AE%A1%E7%90%86%20%E9%80%9A%E7%9F%A5%20when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=%E5%B7%A5%E4%BF%A1%E9%83%A8%20%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD%20%E6%94%BF%E7%AD%96%20when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
    ]

    econ_items, ai_items = [], []
    for u in econ_feeds:
        econ_items.extend(read_feed(u, limit=12))
    for u in ai_policy_feeds:
        ai_items.extend(read_feed(u, limit=12))

    econ_items = filter_recent(dedup(econ_items), WEEK, now_ts)[:18]
    ai_items = filter_recent(dedup(ai_items), WEEK, now_ts)[:18]

    material = "\n\n".join([
        block("经济政策素材（全国｜近7天）", econ_items),
        block("AI政策素材（全国｜近7天）", ai_items),
    ])

    try:
        digest = call_deepseek(material, today_str)
    except Exception:
        digest = "（DeepSeek异常，已降级为原始情报）\n\n" + material

    raw = []
    for it in econ_items[:8]:
        raw.append(f"- {it['title']} | {it['link']}")
    for it in ai_items[:8]:
        raw.append(f"- {it['title']} | {it['link']}")
    raw_block = "【原始链接清单（兜底）】\n" + ("\n".join(raw) if raw else "（无）")

    text = f"{title}\n\n{digest}\n\n{raw_block}".strip()
    post_to_feishu_in_chunks(text, max_len=3500)

if __name__ == "__main__":
    main()
