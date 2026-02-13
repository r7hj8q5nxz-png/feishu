import os
import time
import datetime as dt
import requests
import feedparser

FEISHU_WEBHOOK = (os.environ.get("FEISHU_WEBHOOK_WEEKLY_B") or "").strip()
DEEPSEEK_API_KEY = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()

def post_to_feishu(text: str):
    if not FEISHU_WEBHOOK:
        raise RuntimeError("Missing FEISHU_WEBHOOK_WEEKLY_B secret.")
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
你是我的“周报B（成都AI）”秘书。只基于素材总结，不得编造。每条必须带链接。

输出结构（严格）：
【日期】{today_str}
【周期】近7天

【1) 成都AI政策 Top 6】
- 一句话概括 + 机会点（申报/合作/市场）+ 链接

【2) 成都AI新闻动态 Top 10】
- 一句话概括 + 机会点（客户/渠道/合作）+ 链接

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
    title = f"周报B｜成都AI（北京 {beijing_now.strftime('%Y-%m-%d %H:%M')}）"

    cd_policy_feeds = [
        "https://news.google.com/rss/search?q=%E6%88%90%E9%83%BD%20AI%20%E6%94%BF%E7%AD%96%20%E9%80%9A%E7%9F%A5%20when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=%E6%88%90%E9%83%BD%20%E9%AB%98%E6%96%B0%E5%8C%BA%20AI%20%E6%89%B6%E6%8C%81%20when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=%E5%A4%A9%E5%BA%9C%E6%96%B0%E5%8C%BA%20AI%20%E6%89%B6%E6%8C%81%20when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
    ]
    cd_news_feeds = [
        "https://news.google.com/rss/search?q=%E6%88%90%E9%83%BD%20AI%20%E4%BA%A7%E4%B8%9A%20%E9%A1%B9%E7%9B%AE%20when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=%E6%88%90%E9%83%BD%20%E7%AE%97%E5%8A%9B%20%E5%A4%A7%E6%A8%A1%E5%9E%8B%20when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=%E6%88%90%E9%83%BD%20AI%20%E6%B4%BB%E5%8A%A8%20%E5%A4%A7%E4%BC%9A%20when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
    ]

    policy_items, news_items = [], []
    for u in cd_policy_feeds:
        policy_items.extend(read_feed(u, limit=12))
    for u in cd_news_feeds:
        news_items.extend(read_feed(u, limit=12))

    policy_items = filter_recent(dedup(policy_items), WEEK, now_ts)[:18]
    news_items = filter_recent(dedup(news_items), WEEK, now_ts)[:24]

    material = "\n\n".join([
        block("成都AI政策素材（近7天）", policy_items),
        block("成都AI新闻动态素材（近7天）", news_items),
    ])

    try:
        digest = call_deepseek(material, today_str)
    except Exception:
        digest = "（DeepSeek异常，已降级为原始情报）\n\n" + material

    raw = []
    for it in policy_items[:8]:
        raw.append(f"- {it['title']} | {it['link']}")
    for it in news_items[:10]:
        raw.append(f"- {it['title']} | {it['link']}")
    raw_block = "【原始链接清单（兜底）】\n" + ("\n".join(raw) if raw else "（无）")

    text = f"{title}\n\n{digest}\n\n{raw_block}".strip()
    post_to_feishu_in_chunks(text, max_len=3500)

if __name__ == "__main__":
    main()
