import os
import datetime as dt
import requests
import feedparser

FEISHU_WEBHOOK = (os.environ.get("FEISHU_WEBHOOK") or "").strip()
DEEPSEEK_API_KEY = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()

# ===== 1) 飞书推送 =====
def post_to_feishu(text: str):
    if not FEISHU_WEBHOOK:
        raise RuntimeError("Missing FEISHU_WEBHOOK secret.")
    payload = {"msg_type": "text", "content": {"text": text}}
    r = requests.post(FEISHU_WEBHOOK, json=payload, timeout=20)
    r.raise_for_status()

# ===== 2) RSS 读取 =====
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

def build_material(ai_items, gh_items):
    lines = []
    lines.append("【AI 创业圈】")
    if ai_items:
        for i, it in enumerate(ai_items, 1):
            lines.append(f"{i}. {it['title']} | {it['link']}")
    else:
        lines.append("（本次抓取为空/失败）")

    lines.append("\n【GitHub Trending】")
    if gh_items:
        for i, it in enumerate(gh_items, 1):
            lines.append(f"{i}. {it['title']} | {it['link']}")
    else:
        lines.append("（本次抓取为空/失败）")

    return "\n".join(lines)

# ===== 3) DeepSeek 总结（失败自动降级，不让 workflow 红）=====
def call_deepseek_or_fallback(material_text: str) -> str:
    # 打印长度帮助你排查是否传入（不泄露 key）
    print("DEEPSEEK_API_KEY len:", len(DEEPSEEK_API_KEY))
    print("DEEPSEEK_API_KEY prefix:", DEEPSEEK_API_KEY[:6])

    if not DEEPSEEK_API_KEY:
        return "（未配置DEEPSEEK_API_KEY，已降级为原始情报）\n\n" + material_text[:1800]

    prompt = f"""
你是我的创业情报秘书。基于素材输出中文日报，要求：
1) 趋势判断（3-5条，强判断短句）
2) 未来方向（3条：未来1-3个月值得跟进）
3) 今日要点（5-10条，每条<=30字，带#标签：#融资 #产品 #开源 #监管 #增长 等）
4) 24小时动作（3条，可执行）
口吻：秘书式、冷静、言简意赅、信息密度高
素材：
{material_text}
""".strip()

    # DeepSeek 通常兼容 OpenAI Chat Completions 风格
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        print("DeepSeek status:", r.status_code)
        if r.status_code != 200:
            print("DeepSeek body:", r.text[:800])
            return "（DeepSeek调用失败，已降级为原始情报）\n\n" + material_text[:1800]

        data = r.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print("DeepSeek exception:", str(e))
        return "（DeepSeek异常，已降级为原始情报）\n\n" + material_text[:1800]

def main():
    # 你可以后续换源；先保证跑通
    ai_feed = "https://rsshub.app/qbitai/category/资讯"
    gh_trending = "https://mshibanami.github.io/GitHubTrendingRSS/daily/all.xml"

    ai_items = read_feed(ai_feed, limit=8)
    gh_items = read_feed(gh_trending, limit=8)

    material = build_material(ai_items, gh_items)
    digest = call_deepseek_or_fallback(material)

    beijing_now = dt.datetime.utcnow() + dt.timedelta(hours=8)
    title = f"AI创业日报（北京 {beijing_now.strftime('%Y-%m-%d %H:%M')}）"

    # 飞书单条文本别太长，保守截断
    max_len = 3500
    text = f"{title}\n\n{digest}"
    if len(text) > max_len:
        text = text[:max_len] + "\n\n（内容过长已截断）"

    post_to_feishu(text)

if __name__ == "__main__":
    main()
