import os
import datetime as dt
import requests
import feedparser

FEISHU_WEBHOOK = (os.environ.get("FEISHU_WEBHOOK") or "").strip()
DEEPSEEK_API_KEY = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()

# ---------- Feishu ----------
def post_to_feishu(text: str):
    if not FEISHU_WEBHOOK:
        raise RuntimeError("Missing FEISHU_WEBHOOK secret.")
    payload = {"msg_type": "text", "content": {"text": text}}
    r = requests.post(FEISHU_WEBHOOK, json=payload, timeout=20)
    r.raise_for_status()

# ---------- RSS ----------
def read_feed(url: str, limit: int = 8):
    try:
        d = feedparser.parse(url)
        items = []
        for e in d.entries[:limit]:
            title = (e.get("title") or "").strip()
            link = (e.get("link") or "").strip()
            if title and link:
                items.append({"title": title, "link": link})
        return items
    except Exception as e:
        print("RSS error:", url, str(e))
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

def collect_ai_news():
    # 多源容灾：RSSHub 偶尔不稳，尽量多抓
    ai_feeds = [
        "https://rsshub.app/qbitai/category/资讯",
        "https://rsshub.app/36kr/newsflashes",
        "https://rsshub.app/36kr/news/latest",
        "https://rsshub.app/huxiu/article",
        "https://rsshub.app/juejin/category/ai",
        # 你也可以后续加：少数派/机器之心等（RSSHub 有路由就行）
    ]

    all_items = []
    for u in ai_feeds:
        all_items.extend(read_feed(u, limit=6))

    all_items = dedup_items(all_items)
    return all_items[:12]  # 最多取 12 条给模型

def collect_github_trending():
    gh_trending = "https://mshibanami.github.io/GitHubTrendingRSS/daily/all.xml"
    return read_feed(gh_trending, limit=12)

def format_items_block(title, items):
    lines = [f""]
    if not items:
        lines.append("（本次抓取为空/失败）")
        return "\n".join(lines)

    for i, it in enumerate(items, 1):
        lines.append(f"{i}. {it['title']}\n{it['link']}")
    return "\n".join(lines)

# ---------- DeepSeek ----------
def call_deepseek(material_text: str, today_str: str) -> str:
    if not DEEPSEEK_API_KEY:
        return "（未配置DEEPSEEK_API_KEY，已降级为原始情报）\n\n" + material_text[:1800]

    prompt = f"""
今天是：{today_str}（北京时间）。
你是我的创业情报秘书，请基于素材输出“可执行、可追踪”的日报。必须遵守格式：

【日期】必须输出：{today_str}
【Top 5（最重要）】5条：每条=一句话概括 + 链接（必须有链接）
【AI 创业圈（要点）】最多6条：每条=一句话概括 + 链接（必须有链接）
【GitHub Trending（要点）】最多6条：每条=一句话概括 + 链接（必须有链接）
【对我（一人公司）的机会点】3条：用“如果…那么我可以…”句式，具体到可做的微产品
【风险/注意】3条：license/可商用/维护活跃度/合规（没有信息就写“需核查”）
【24小时动作】3条：每条包含 产出物 + 截止时间 + 完成标准

写作要求：中文；秘书口吻；短句；高密度；不要空泛。
素材如下：
{material_text}
""".strip()

    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }

    r = requests.post(url, headers=headers, json=payload, timeout=60)
    if r.status_code != 200:
        print("DeepSeek status:", r.status_code)
        print("DeepSeek body:", r.text[:800])
        return "（DeepSeek调用失败，已降级为原始情报）\n\n" + material_text[:1800]

    data = r.json()
    return data["choices"][0]["message"]["content"].strip()

def main():
    # 北京时间
    beijing_now = dt.datetime.utcnow() + dt.timedelta(hours=8)
    today_str = beijing_now.strftime("%Y-%m-%d")
    title = f"AI创业日报（北京 {beijing_now.strftime('%Y-%m-%d %H:%M')}）"

    ai_items = collect_ai_news()
    gh_items = collect_github_trending()

    material = "\n\n".join([
        format_items_block("AI 创业圈（原始素材）", ai_items),
        format_items_block("GitHub Trending（原始素材）", gh_items),
    ])

    digest = call_deepseek(material, today_str)

    # 兜底：即使模型漏链接，也在末尾强制附原始清单
    raw_links = []
    for it in ai_items[:8]:
        raw_links.append(f"- {it['title']} | {it['link']}")
    for it in gh_items[:8]:
        raw_links.append(f"- {it['title']} | {it['link']}")
    raw_block = "【原始链接清单（兜底）】\n" + "\n".join(raw_links) if raw_links else ""

    text = f"{title}\n\n{digest}\n\n{raw_block}".strip()

    # 飞书单条文本长度控制
    if len(text) > 3800:
        text = text[:3800] + "\n\n（内容过长已截断）"

    post_to_feishu(text)

if __name__ == "__main__":
    main()
