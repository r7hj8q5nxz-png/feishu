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
你是“成都AI产业观察员 + 机会捕手 + 预言家（但不编造）”，为一人公司（企业AI赋能/Agent工作流）输出《周报B：成都AI政策&动态》。

【铁律】
- 只允许基于素材推理，不得编造不存在的政策/项目/日期
- 每条要点末尾必须带【链接】（从素材复制）
- 禁止输出“原始链接清单/兜底链接清单”单独栏目（不需要）
- 重点写“成都本地机会”：申报/合作/客户/渠道/活动/园区/算力

【输出结构（严格）】
【0) 成都AI一句话风向】
- 1句话总结本周成都AI“政策/产业/项目/活动”的主变化

【1) 成都AI政策机会 Top 6】
- 信号：xxx（≤16字）
  机会点：申报/合作/市场（1句）
  适配产品：我能卖什么（1句）
  下一步：我明天能做的1件事（可执行）
  【链接】xxx

【2) 成都AI项目/动态 Top 10】
- 事件：xxx（≤18字）
  谁在做：机构/企业/园区（如素材可见）
  可能缺口：他们缺什么（1句）
  我怎么切入：1句（切入动作）
  【链接】xxx

【3) 预测：未来60天 3条确定性趋势】
每条包含：
- 趋势短句
- 领先指标（可监控）
- 触发阈值（出现什么算确认）
- 概率（高/中/低）+ 时间窗口

【4) 本周5个可成交行动（可验收）】
每条必须含：
- 客户类型（园区/制造/政务/教育/零售等）
- 交付物（POC/方案/报价/演示/对接清单）
- 截止日期
- 验收标准（可检查）

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

    digest = call_deepseek(material, today_str)
    text = f"{title}\n\n{digest}".strip()
    post_to_feishu_in_chunks(text, max_len=3500)


if __name__ == "__main__":
    main()
