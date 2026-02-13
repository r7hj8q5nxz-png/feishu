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
        chunk.append(line)
        cur += add
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
        seen.add(it["link"])
        out.append(it)
    return out


def filter_recent(items, max_age_seconds: int, now_ts: int):
    out = []
    for it in items:
        ts = it.get("published_ts")
        if ts is None:
            continue  # 严格：没发布时间=丢弃
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

    prompt = f"""
今天是：{today_str}（北京时间）。
你是“AI专家 + 产业预言家 + 政策解读官”，为一人公司（主营：企业AI赋能/Agent工作流/AI应用落地）输出《周报A：经济&AI政策》。

【铁律（必须遵守）】
- 只允许基于素材推理，不得编造不存在的政策、机构、数据、日期、项目
- 每一条要点末尾必须带【链接】（从素材原文链接复制）
- 禁止空话：每条都要落到“对我有什么用/下一步怎么做”
- 预测必须写成：领先指标 → 推论 → 概率（高/中/低）→ 时间窗口（1-4周/1-3月/3-12月）
- 不要输出“原始链接清单/兜底链接清单”单独栏目（不需要）

【输出结构（严格按此）】
【0) 一句话总览】
- 1句总结本周政策环境的总风向

【1) 关键变化 Top 6（按重要性排序）】
- 变化点：xxx（≤16字）
  影响链：A → B → C（≤20字）
  对我意味着：1句话（可执行）
  领先指标：1个（我能监控）
  概率&窗口：高/中/低 + 时间窗口
  【链接】xxx

【2) AI政策解读 Top 6（合规/备案/数据/算力/产业扶持）】
- 政策信号：xxx（≤16字）
  风险：1句（合规红线）
  机会：1句（市场/申报/合作/产品）
  我该怎么用：1句（立刻可做）
  【链接】xxx

【3) 预测：未来 90 天的 3 个剧本（情景推演）】
每个剧本包含：
- 剧本名（≤10字）+ 概率（高/中/低）
- 驱动因素（3条）
- 谁受益/谁受损（各2条）
- 我的一人公司应对策略（3条行动）

【4) 7天行动清单（可验收）】
每条必须含：
- 动作
- 产出物（文档/脚本/报价/演示/对接清单）
- 截止（具体日期）
- 验收标准（可检查：数量/完成定义）

【素材】
{material_text}
""".strip()

    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}], "temperature": 0.2}

    r = requests.post(url, headers=headers, json=payload, timeout=80)
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
    post_to_feishu_in_chunks(text, max_len=3500)


if __name__ == "__main__":
    main()
