import os
import time
import datetime as dt
import requests
import feedparser

FEISHU_WEBHOOK = (os.environ.get("FEISHU_WEBHOOK_WEEKLY_A") or "").strip()
DEEPSEEK_API_KEY = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()

# 发飞书：每段之间 sleep；失败自动重试（退避）
FEISHU_MAX_LEN = 2500          # 保守一点，减少被截断/拒收风险
FEISHU_SLEEP_SEC = 0.8         # 节流，降低频控概率
FEISHU_RETRY = 5               # 重试次数


def _post_to_feishu_once(text: str):
    payload = {"msg_type": "text", "content": {"text": text}}
    r = requests.post(FEISHU_WEBHOOK, json=payload, timeout=25)
    return r


def post_to_feishu(text: str):
    if not FEISHU_WEBHOOK:
        raise RuntimeError("Missing FEISHU_WEBHOOK_WEEKLY_A secret.")
    last_err = None
    for i in range(FEISHU_RETRY):
        r = _post_to_feishu_once(text)
        if 200 <= r.status_code < 300:
            return
        last_err = f"Feishu status={r.status_code}, body={r.text[:300]}"
        # 429/5xx 最常见，退避
        time.sleep((2 ** i) * 1.2)
    raise RuntimeError(last_err or "Feishu post failed.")


def split_into_chunks(text: str, max_len: int):
    # 按行分段，尽量不破坏结构
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

    # 极端情况下单行超长：再硬切
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
        seen.add(it["link"])
        out.append(it)
    return out


def filter_recent(items, max_age_seconds: int, now_ts: int):
    # 严格：无发布时间=丢弃
    out = []
    for it in items:
        ts = it.get("published_ts")
        if ts is None:
            continue
        age = now_ts - ts
        if 0 <= age <= max_age_seconds:
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
        return "（未配置DEEPSEEK_API_KEY，已降级为原始素材）\n\n" + material_text

    # 关键：约束输出长度 + 强制“写不下就压缩/续写”
    prompt = f"""
今天是：{today_str}（北京时间）。
你是“AI专家 + 产业预言家 + 政策解读官”，输出《周报A：经济&AI政策》。
要求：高密度、可执行、但不编造。

【硬约束】
- 只允许基于素材推理，不得编造不存在的政策/数据/日期
- 每条要点末尾必须带【链接】（从素材复制）
- 不输出“原始链接清单/兜底链接清单”
- 如果内容太长写不下：优先压缩表达，不要省略结构；必须覆盖到【4) 7天行动清单】

【结构（必须完整输出到第4部分）】
【0) 一句话总览】
【1) 关键变化 Top 6】
【2) AI政策解读 Top 6】
【3) 未来90天 3个剧本（含概率/窗口/领先指标）】
【4) 7天行动清单（5条，可验收：动作/产出/截止/验收标准）】

【素材】
{material_text}
""".strip()

    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        # 关键：给足输出预算（避免半截）
        "max_tokens": 2500,
    }

    r = requests.post(url, headers=headers, json=payload, timeout=100)
    print("DeepSeek status:", r.status_code)
    if r.status_code != 200:
        return "（DeepSeek调用失败，已降级为原始素材）\n\n" + material_text
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

    digest = call_deepseek(material, today_str)
    text = f"{title}\n\n{digest}".strip()
    post_to_feishu_in_chunks(text, max_len=FEISHU_MAX_LEN)


if __name__ == "__main__":
    main()
