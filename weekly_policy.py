import os
import time
import datetime as dt
import requests
import feedparser

FEISHU_WEBHOOK = (os.environ.get("FEISHU_WEBHOOK") or "").strip()
DEEPSEEK_API_KEY = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()

# =========================
# Feishu
# =========================
def post_to_feishu(text: str):
    if not FEISHU_WEBHOOK:
        raise RuntimeError("Missing FEISHU_WEBHOOK secret.")
    payload = {"msg_type": "text", "content": {"text": text}}
    r = requests.post(FEISHU_WEBHOOK, json=payload, timeout=20)
    r.raise_for_status()

def post_to_feishu_in_chunks(text: str, max_len: int = 3500):
    """自动分段多条发送，不截断、不丢内容。"""
    if len(text) <= max_len:
        post_to_feishu(text)
        return

    lines = text.splitlines()
    chunk, chunks = [], []
    cur = 0
    for line in lines:
        add = len(line) + 1
        if cur + add > max_len and chunk:
            chunks.append("\n".join(chunk))
            chunk, cur = [], 0
        chunk.append(line)
        cur += add
    if chunk:
        chunks.append("\n".join(chunk))

    for idx, c in enumerate(chunks, 1):
        if idx == 1:
            post_to_feishu(c)
        else:
            post_to_feishu(f"（续 {idx}）\n{c}")

# =========================
# RSS
# =========================
def read_feed(url: str, limit: int = 10):
    """
    读取 RSS 并解析发布时间。
    返回：title, link, published_ts（可能为 None）
    """
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
    """
    严格过滤：只保留 max_age_seconds 内发布的条目。
    没有发布时间的一律丢弃（严格满足你的要求）。
    """
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
        lines.append("（过去7天内无符合条件的条目/或源未提供发布时间）")
        return "\n".join(lines)
    for i, it in enumerate(items, 1):
        lines.append(f"{i}. {it['title']}\n{it['link']}")
    return "\n".join(lines)

# =========================
# DeepSeek
# =========================
def call_deepseek(material_text: str, today_str: str) -> str:
    if not DEEPSEEK_API_KEY:
        return "（未配置DEEPSEEK_API_KEY，已降级为原始情报）\n\n" + material_text

    prompt = f"""
今天是：{today_str}（北京时间）。
你是我的企业AI赋能助理，请输出“每周政策与成都AI简报”。

硬性规则：
1) 只允许基于素材总结，不得编造政策/链接/日期
2) 每一条要点必须带链接（从素材里取）
3) 日期必须输出为 {today_str}

输出结构（严格）：
【日期】{today_str}
【周期】近7天

【1) 经济政策 Top 5】
- 每条：一句话概括 + 影响对象 + 我该怎么用（1句话）+ 链接

【2) AI政策 Top 5】
- 每条：一句话概括 + 重点（数据/合规/备案/算力/产业扶持）+ 我该怎么用（1句话）+ 链接

【3) 成都AI政策 Top 5】
- 每条：一句话概括 + 机会点（申报/合作/市场）+ 链接

【4) 成都AI新闻动态 Top 8】
- 每条：一句话概括 + 机会点（合作/客户/渠道）+ 链接

【5) 本周可执行动作 3 条】
- 每条：产出物 + 截止时间 + 完成标准（可验收）

素材：
{material_text}
""".strip()

    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        print("DeepSeek status:", r.status_code)
        if r.status_code != 200:
            print("DeepSeek body:", r.text[:800])
            return "（DeepSeek调用失败，已降级为原始情报）\n\n" + material_text
        data = r.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as ex:
        print("DeepSeek exception:", str(ex))
        return "（DeepSeek异常，已降级为原始情报）\n\n" + material_text

# =========================
# Main
# =========================
def main():
    now_ts = int(time.time())
    WEEK_SECONDS = 7 * 24 * 3600

    beijing_now = dt.datetime.utcnow() + dt.timedelta(hours=8)
    today_str = beijing_now.strftime("%Y-%m-%d")
    title = f"每周政策&成都AI简报（北京 {beijing_now.strftime('%Y-%m-%d %H:%M')}）"

    # 进一步减少旧闻：Google News RSS query 加 when:7d（但仍以“代码过滤发布时间”为最终准则）
    # 经济政策（全国）
    econ_feeds = [
        "https://news.google.com/rss/search?q=%E5%9B%BD%E5%8A%A1%E9%99%A2%20%E7%BB%8F%E6%B5%8E%20%E6%94%BF%E7%AD%96%20when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=%E5%9B%BD%E5%AE%B6%E5%8F%91%E6%94%B9%E5%A7%94%20%E6%94%BF%E7%AD%96%20%E9%80%9A%E7%9F%A5%20when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=%E8%B4%A2%E6%94%BF%E9%83%A8%20%E6%94%BF%E7%AD%96%20%E9%80%9A%E7%9F%A5%20when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=%E7%A8%8E%E6%94%B6%20%E4%BC%98%E6%83%A0%20%E6%94%BF%E7%AD%96%20when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
    ]

    # AI政策（全国）
    ai_policy_feeds = [
        "https://news.google.com/rss/search?q=%E7%94%9F%E6%88%90%E5%BC%8FAI%20%E7%AE%A1%E7%90%86%20%E9%80%9A%E7%9F%A5%20when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=%E7%BD%91%E4%BF%A1%E5%8A%9E%20%E7%AE%97%E6%B3%95%20%E5%A4%87%E6%A1%88%20when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=%E5%B7%A5%E4%BF%A1%E9%83%A8%20%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD%20%E6%94%BF%E7%AD%96%20when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=%E6%95%B0%E6%8D%AE%E5%87%BA%E5%A2%83%20%E5%AE%89%E5%85%A8%20%E8%AF%84%E4%BC%B0%20when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
    ]

    # 成都AI政策（更偏“通知/扶持/政策”）
    chengdu_ai_policy_feeds = [
        "https://news.google.com/rss/search?q=%E6%88%90%E9%83%BD%20AI%20%E6%94%BF%E7%AD%96%20%E9%80%9A%E7%9F%A5%20when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=%E6%88%90%E9%83%BD%20%E9%AB%98%E6%96%B0%E5%8C%BA%20AI%20%E6%89%B6%E6%8C%81%20when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=%E5%A4%A9%E5%BA%9C%E6%96%B0%E5%8C%BA%20AI%20%E6%89%B6%E6%8C%81%20when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
    ]

    # 成都AI新闻动态（更偏“项目/企业/活动/算力/大模型”）
    chengdu_ai_news_feeds = [
        "https://news.google.com/rss/search?q=%E6%88%90%E9%83%BD%20AI%20%E4%BA%A7%E4%B8%9A%20%E9%A1%B9%E7%9B%AE%20when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=%E6%88%90%E9%83%BD%20%E7%AE%97%E5%8A%9B%20%E5%A4%A7%E6%A8%A1%E5%9E%8B%20when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=%E6%88%90%E9%83%BD%20AI%20%E6%B4%BB%E5%8A%A8%20%E5%A4%A7%E4%BC%9A%20when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=%E6%88%90%E9%83%BD%20%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD%20%E4%BC%81%E4%B8%9A%20%E8%9E%8D%E8%B5%84%20when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
    ]

    econ_items, ai_items = [], []
    cd_policy_items, cd_news_items = [], []

    for u in econ_feeds:
        econ_items.extend(read_feed(u, limit=12))
    for u in ai_policy_feeds:
        ai_items.extend(read_feed(u, limit=12))
    for u in chengdu_ai_policy_feeds:
        cd_policy_items.extend(read_feed(u, limit=12))
    for u in chengdu_ai_news_feeds:
        cd_news_items.extend(read_feed(u, limit=12))

    # 去重 + 严格时间过滤（核心）
    econ_items = filter_recent(dedup(econ_items), WEEK_SECONDS, now_ts)[:18]
    ai_items = filter_recent(dedup(ai_items), WEEK_SECONDS, now_ts)[:18]
    cd_policy_items = filter_recent(dedup(cd_policy_items), WEEK_SECONDS, now_ts)[:18]
    cd_news_items = filter_recent(dedup(cd_news_items), WEEK_SECONDS, now_ts)[:24]

    material = "\n\n".join([
        block("经济政策素材（全国｜近7天）", econ_items),
        block("AI政策素材（全国｜近7天）", ai_items),
        block("成都AI政策素材（近7天）", cd_policy_items),
        block("成都AI新闻动态素材（近7天）", cd_news_items),
    ])

    digest = call_deepseek(material, today_str)

    # 兜底：原始链接清单（防止模型漏链接）
    raw = []
    for it in econ_items[:6]:
        raw.append(f"- {it['title']} | {it['link']}")
    for it in ai_items[:6]:
        raw.append(f"- {it['title']} | {it['link']}")
    for it in cd_policy_items[:6]:
        raw.append(f"- {it['title']} | {it['link']}")
    for it in cd_news_items[:8]:
        raw.append(f"- {it['title']} | {it['link']}")
    raw_block = "【原始链接清单（兜底）】\n" + "\n".join(raw) if raw else "【原始链接清单（兜底）】\n（无）"

    text = f"{title}\n\n{digest}\n\n{raw_block}".strip()
    post_to_feishu_in_chunks(text, max_len=3500)

if __name__ == "__main__":
    main()
