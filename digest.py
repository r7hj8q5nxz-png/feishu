import os
import time
import datetime as dt
import requests
import feedparser

FEISHU_WEBHOOK = (os.environ.get("FEISHU_WEBHOOK") or "").strip()
DEEPSEEK_API_KEY = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()


# -------------------------
# Feishu
# -------------------------
def post_to_feishu(text: str):
    if not FEISHU_WEBHOOK:
        raise RuntimeError("Missing FEISHU_WEBHOOK secret.")
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


# -------------------------
# RSS helpers
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
    严格：无发布时间 -> 丢弃
    """
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
        lines.append("（过去24小时内无符合条件的条目）")
        return "\n".join(lines)
    for i, it in enumerate(items, 1):
        lines.append(f"{i}. {it['title']}\n{it['link']}")
    return "\n".join(lines)


# -------------------------
# DeepSeek
# -------------------------
def call_deepseek(material_text: str, date_str: str) -> str:
    if not DEEPSEEK_API_KEY:
        return "（未配置DEEPSEEK_API_KEY，已降级为原始素材）\n\n" + material_text

    prompt = f"""
今天是：{date_str}（北京时间）。
你是“AI创业情报官 + 产业预言家 + ToB落地顾问”，为一人公司（企业AI赋能/Agent工作流/AI应用落地）输出《AI创业日报》。

【硬约束（必须遵守）】
1) 只允许基于素材推理，不得编造不存在的公司/融资/产品/项目/日期/数据
2) 每条要点末尾必须带【链接】（从素材原文链接复制）
3) 禁止空话：每条必须落到“对我意味着什么/我接下来做什么”
4) 预测必须写成：领先指标 → 推论 → 概率（高/中/低）→ 时间窗口（1-2周/1-3月/3-12月）
5) 不要输出“原始链接清单/兜底链接清单”或类似栏目（不需要）
6) 如果素材不足（例如有效条目<5），也不要编造；只输出“渠道健康诊断+补救动作”

【输出结构（严格）】
【0) 一句话风向】
- 1句总结今天AI创业圈最强信号（资金/产品/落地/开源/政策选其一）

【1) 今日 Top 5（最重要）】
每条格式固定：
- 要点：xxx（≤16字）
  事件概括：1句（发生了什么）
  影响：1句（对行业/客户/竞品的影响）
  对我意味着：1句（我能怎么借势获客/做产品）
  领先指标：1个（我能监控）
  概率&窗口：高/中/低 + 时间窗口
  【链接】xxx

【2) 机会清单（最多6条，偏可卖的ToB）】
每条格式固定：
- 机会：xxx（≤16字）
  目标客户：1类（制造/零售/政务/教育/园区/电商等）
  我能卖的交付：1句（Agent/自动化/知识库/客服/数据治理/POC）
  成交路径：1句（怎么找到人、怎么开口）
  客单价&周期：区间（如3k-1w/3-7天）
  【链接】xxx

【3) 开源/工具信号（最多6条）】
每条格式固定：
- 项目：xxx
  能解决：1句
  我怎么用：1句（落到工作流/产品）
  【链接】xxx

【4) 预测：未来30天 3条确定性趋势】
每条包含：
- 趋势短句
- 领先指标（可监控）
- 触发阈值（出现什么算确认）
- 概率&窗口

【5) 24小时行动（可验收）】
每条必须含：
- 动作
- 产出物（文档/脚本/报价/演示）
- 截止时间（今天/明天具体时间）
- 验收标准（可检查）

【6) 渠道健康诊断（只在素材不足时输出）】
- 为什么少：可能原因（2条）
- 我该怎么修：补救动作（3条）

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


# -------------------------
# Main
# -------------------------
def main():
    now_ts = int(time.time())
    DAY = 24 * 3600

    beijing_now = dt.datetime.utcnow() + dt.timedelta(hours=8)
    date_str = beijing_now.strftime("%Y-%m-%d")
    title = f"AI创业日报（北京 {beijing_now.strftime('%Y-%m-%d %H:%M')}）"

    ai_feeds = [
        "https://news.google.com/rss/search?q=site:36kr.com%20AI%20%E5%88%9B%E4%B8%9A%20OR%20%E8%9E%8D%E8%B5%84%20OR%20%E5%A4%A7%E6%A8%A1%E5%9E%8B%20OR%20ToB%20when:1d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=site:huxiu.com%20AI%20OR%20%E5%A4%A7%E6%A8%A1%E5%9E%8B%20OR%20Agent%20OR%20%E5%88%9B%E4%B8%9A%20when:1d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=site:qbitai.com%20AI%20OR%20%E5%A4%A7%E6%A8%A1%E5%9E%8B%20OR%20Agent%20OR%20%E7%AE%97%E5%8A%9B%20when:1d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=site:jiqizhixin.com%20AI%20OR%20%E5%A4%A7%E6%A8%A1%E5%9E%8B%20OR%20Agent%20OR%20%E6%8A%80%E6%9C%AF%20when:1d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=site:geekpark.net%20AI%20OR%20%E5%88%9B%E4%B8%9A%20OR%20%E4%BA%A7%E5%93%81%20when:1d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=site:infoq.cn%20AI%20OR%20Agent%20OR%20RAG%20OR%20%E4%BC%81%E4%B8%9AAI%20when:1d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=site:sspai.com%20AI%20OR%20Agent%20OR%20%E5%B7%A5%E4%BD%9C%E6%B5%81%20when:1d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
    ]

    items = []
    for u in ai_feeds:
        items.extend(read_feed(u, limit=12))

    items = filter_recent(dedup(items), DAY, now_ts)[:25]
    material = block("AI创业圈素材（中国媒体｜过去24小时）", items)

    digest = call_deepseek(material, date_str)

    # 关键：这里不再拼接任何 raw_block/兜底链接
    text = f"{title}\n\n{digest}".strip()
    post_to_feishu_in_chunks(text, max_len=3500)


if __name__ == "__main__":
    main()
# trigger scheduler update
