import os
import datetime as dt
import requests
import feedparser

FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "").strip()
LLM_API_KEY = os.environ.get("LLM_API_KEY", "").strip()

# ===== 1) 飞书群机器人发送 =====
def post_to_feishu(text: str):
    if not FEISHU_WEBHOOK:
        raise RuntimeError("Missing FEISHU_WEBHOOK secret.")
    payload = {"msg_type": "text", "content": {"text": text}}
    r = requests.post(FEISHU_WEBHOOK, json=payload, timeout=20)
    r.raise_for_status()

# ===== 2) 读取 RSS =====
def read_feed(url: str, limit: int = 8):
    d = feedparser.parse(url)
    items = []
    for e in d.entries[:limit]:
        title = (e.get("title") or "").strip()
        link = (e.get("link") or "").strip()
        if title and link:
            items.append({"title": title, "link": link})
    return items

def build_material(ai_items, gh_items):
    lines = []
    lines.append("【AI 创业圈】")
    for i, it in enumerate(ai_items, 1):
        lines.append(f"{i}. {it['title']} | {it['link']}")
    lines.append("\n【GitHub Trending】")
    for i, it in enumerate(gh_items, 1):
        lines.append(f"{i}. {it['title']} | {it['link']}")
    return "\n".join(lines)

# ===== 3) 大模型总结（默认 OpenAI 兼容接口）=====
def call_llm(material_text: str) -> str:
    # 只打印长度/前缀，不泄露真实key
    print("LLM_API_KEY len:", len(LLM_API_KEY))
    print("LLM_API_KEY prefix:", LLM_API_KEY[:7])

    # 没有key：直接降级
    if not LLM_API_KEY:
        return "（未配置LLM_API_KEY，已降级为原始情报）\n\n" + material_text[:1800]

    prompt = f"""
你是我的创业情报秘书。基于素材输出中文日报，要求：
1) 趋势判断（3-5条，强判断短句）
2) 未来方向（3条：未来1-3个月值得跟进）
3) 今日要点（5-10条，每条<=30字，带#标签：#融资 #产品 #开源 #监管 #增长 等）
4) 24小时动作（3条，可执行）
素材：
{material_text}
""".strip()

    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        print("OpenAI status:", r.status_code)

        if r.status_code != 200:
            # 打印错误体，方便定位
            print("OpenAI body:", r.text[:800])
            return "（LLM调用失败/401，已降级为原始情报）\n\n" + material_text[:1800]

        data = r.json()
        return data["choices"][0]["message"]["content"].strip()

    except Exception as e:
        print("LLM exception:", str(e))
        return "（LLM异常，已降级为原始情报）\n\n" + material_text[:1800]


def main():
    # ===== 你只用群机器人，所以只做“群消息推送” =====
    # AI 创业圈（示例源：RSSHub - 量子位资讯）
    ai_feed = "https://rsshub.app/qbitai/category/资讯"
    # GitHub Trending（第三方聚合 RSS）
    gh_trending = "https://mshibanami.github.io/GitHubTrendingRSS/daily/all.xml"

    ai_items = read_feed(ai_feed, limit=8)
    gh_items = read_feed(gh_trending, limit=8)

    material = build_material(ai_items, gh_items)
    digest = call_llm(material)

    # 北京时间标题（UTC+8）
    beijing_now = dt.datetime.utcnow() + dt.timedelta(hours=8)
    title = f"AI创业日报（北京 {beijing_now.strftime('%Y-%m-%d %H:%M')}）"

    # 控制飞书单条消息长度（保守截断，避免过长失败）
    max_len = 3500
    text = f"{title}\n\n{digest}"
    if len(text) > max_len:
        text = text[:max_len] + "\n\n（内容过长已截断）"

    post_to_feishu(text)

if __name__ == "__main__":
    main()
