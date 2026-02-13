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
def read_feed(url: str, limit: int = 12, assume_now_if_missing_ts: bool = False, now_ts: int = None):
    """
    读取 RSS 并解析发布时间（published/updated）。
    - 正常：没有发布时间 -> published_ts=None
    - 白名单：assume_now_if_missing_ts=True 时，没有发布时间 -> 用 now_ts 作为发布时间（用于 GitHub Trending 这种“当天榜单”）
    """
    try:
        d = feedparser.parse(url)
        items = []
        for e in d.entries[:limit]:
            title = (e.get("title") or "").strip()
            link = (e.get("link") or "").strip()

            t = e.get("published_parsed") or e.get("updated_parsed")
            published_ts = int(time.mktime(t)) if t else None

            if published_ts is None and assume_now_if_missing_ts and now_ts is not None:
                published_ts = now_ts

            if title and link:
                items.append({"title": title, "link": link, "published_ts": published_ts})
        return items
    except Exception as ex:
        print("RSS error:", url, str(ex))
        return []

def dedup_items(items):
    seen = set()
    out = []
    for it in items:
        if it["link"] in seen:
            continue
        seen.add(it["link"])
        out.append(it)
    return out

def filter_recent(items, max_age_seconds: int, now_ts: int, drop_if_no_ts: bool = True):
    """
    严格时间过滤：
    - drop_if_no_ts=True：无发布时间 -> 丢弃（你的硬要求）
    - 但对 GitHub Trending 我们在 read_feed 里已经把缺失时间“视为 now_ts”，因此仍能通过过滤
    """
    out = []
    for it in items:
        ts = it.get("published_ts")
        if ts is None:
            if drop_if_no_ts:
                continue
            else:
                out.append(it)
                continue
        age = now_ts - ts
        if 0 <= age <= max_age_seconds:
            out.append(it)
    return out

def format_items_block(title, items):
    lines = [f""]
    if not items:
        lines.append("（过去24小时内无符合条件的条目）")
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
你是我的AI创业情报秘书，请基于素材输出中文日报，必须遵守：

硬性规则：
1) 只允许基于素材总结，不得编造项目/链接/日期
2) 每一条要点必须带链接（从素材里取）
3) 日期必须输出为 {today_str}

输出格式：
【日期】{today_str}
【Top 5（最重要）】5条：一句话概括 + 链接
【AI 创业圈（要点）】最多6条：一句话概括 + 链接
【GitHub Trending（要点）】最多6条：一句话概括 + 链接
【趋势判断】3条：强判断短句
【24小时动作】3条：产出物 + 截止时间 + 完成标准

写作要求：秘书口吻；短句；高密度；不空泛。
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
    DAY_SECONDS = 24 * 3600

    beijing_now = dt.datetime.utcnow() + dt.timedelta(hours=8)
    today_str = beijing_now.strftime("%Y-%m-%d")
    title = f"AI创业日报（北京 {beijing_now.strftime('%Y-%m-%d %H:%M')}）"

    # -------------------------
    # AI 创业圈：换成 Google News RSS + when:1d（发布时间稳定）
    # -------------------------
    ai_feeds = [
        # AI创业总体
        "https://news.google.com/rss/search?q=AI%20%E5%88%9B%E4%B8%9A%20when:1d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        # 企业AI赋能 / ToB
        "https://news.google.com/rss/search?q=%E4%BC%81%E4%B8%9AAI%20%E8%B5%8B%E8%83%BD%20when:1d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        # Agent / 工作流
        "https://news.google.com/rss/search?q=AI%20Agent%20%E5%B7%A5%E4%BD%9C%E6%B5%81%20when:1d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        # 融资/并购（信号强）
        "https://news.google.com/rss/search?q=AI%20%E8%9E%8D%E8%B5%84%20when:1d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
    ]

    ai_items = []
    for u in ai_feeds:
        ai_items.extend(read_feed(u, limit=12))
    ai_items = dedup_items(ai_items)
    ai_items = filter_recent(ai_items, DAY_SECONDS, now_ts, drop_if_no_ts=True)[:15]

    # -------------------------
    # GitHub Trending：白名单例外（很多RSS没发布时间）
    # 解释：Trending 本质是“当天榜单”，没有 pubDate 也应视为今日有效
    # -------------------------
    gh_feeds = [
        "https://mshibanami.github.io/GitHubTrendingRSS/daily/all.xml",
    ]

    gh_items = []
    for u in gh_feeds:
        gh_items.extend(read_feed(u, limit=20, assume_now_if_missing_ts=True, now_ts=now_ts))
    gh_items = dedup_items(gh_items)
    gh_items = filter_recent(gh_items, DAY_SECONDS, now_ts, drop_if_no_ts=True)[:15]

    material = "\n\n".join([
        format_items_block("AI 创业圈（过去24小时素材）", ai_items),
        format_items_block("GitHub Trending（今日榜单视为24小时内）", gh_items),
    ])

    digest = call_deepseek(material, today_str)

    # 兜底：原始链接清单（防止模型漏链接）
    raw_links = []
    for it in ai_items[:10]:
        raw_links.append(f"- {it['title']} | {it['link']}")
    for it in gh_items[:10]:
        raw_links.append(f"- {it['title']} | {it['link']}")
    raw_block = "【原始链接清单（兜底）】\n" + "\n".join(raw_links) if raw_links else "【原始链接清单（兜底）】\n（无）"

    text = f"{title}\n\n{digest}\n\n{raw_block}".strip()
    post_to_feishu_in_chunks(text, max_len=3500)

if __name__ == "__main__":
    main()
