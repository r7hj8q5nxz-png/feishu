import os
import datetime as dt
import requests
import feedparser

FEISHU_WEBHOOK = (os.environ.get("FEISHU_WEBHOOK") or "").strip()
DEEPSEEK_API_KEY = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()

def post_to_feishu_in_chunks(text: str, max_len: int = 3500):
    # 自动拆分发送，避免单条消息过长导致发送失败
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

    # 第一条原样发，后续条带“续n”
    for idx, c in enumerate(chunks, 1):
        if idx == 1:
            post_to_feishu(c)
        else:
            post_to_feishu(f"（续 {idx}）\n{c}")


def read_feed(url: str, limit: int = 10):
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

def dedup(items):
    seen, out = set(), []
    for it in items:
        if it["link"] in seen:
            continue
        seen.add(it["link"])
        out.append(it)
    return out

def block(title, items):
    lines = [f""]
    if not items:
        lines.append("（本周抓取为空/失败）")
        return "\n".join(lines)
    for i, it in enumerate(items, 1):
        lines.append(f"{i}. {it['title']}\n{it['link']}")
    return "\n".join(lines)

def call_deepseek(material_text: str, today_str: str) -> str:
    if not DEEPSEEK_API_KEY:
        return "（未配置DEEPSEEK_API_KEY，已降级为原始情报）\n\n" + material_text[:1800]

    prompt = f"""
今天是：{today_str}（北京时间）。
你是我的企业AI赋能助理，请输出“每周政策与成都AI简报”。必须遵守格式（每条都要带链接）：

【周期】写：近7天
【1) 经济政策Top 5】5条：一句话概括 + 影响对象（企业/创业者/高校等）+ 我该怎么用（行动建议）+ 链接
【2) AI政策Top 5】5条：同上（重点：数据/合规/备案/模型/算力/产业扶持）
【3) 成都AI动态Top 8】8条：一句话概括 + 机会点（合作/申报/市场）+ 链接
【4) 本周可执行动作】3条：产出物 + 截止时间 + 完成标准

写作要求：中文；秘书口吻；短句；高密度；不空泛；不要编造日期/政策。
素材如下（只允许基于素材总结）：
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
            return "（DeepSeek调用失败，已降级为原始情报）\n\n" + material_text[:1800]
        data = r.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print("DeepSeek exception:", str(e))
        return "（DeepSeek异常，已降级为原始情报）\n\n" + material_text[:1800]

def main():
    beijing_now = dt.datetime.utcnow() + dt.timedelta(hours=8)
    today_str = beijing_now.strftime("%Y-%m-%d")
    title = f"每周政策&成都AI简报（北京 {beijing_now.strftime('%Y-%m-%d %H:%M')}）"

    # ===== RSS源（稳定优先：用 Google News RSS 关键词检索）=====
    # 经济政策（宏观/发改/财政/国务院）
    econ_feeds = [
        "https://news.google.com/rss/search?q=%E5%9B%BD%E5%8A%A1%E9%99%A2%20%E7%BB%8F%E6%B5%8E%20%E6%94%BF%E7%AD%96&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=%E5%9B%BD%E5%AE%B6%E5%8F%91%E6%94%B9%E5%A7%94%20%E6%94%BF%E7%AD%96%20%E9%80%9A%E7%9F%A5&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=%E8%B4%A2%E6%94%BF%E9%83%A8%20%E6%94%BF%E7%AD%96%20%E9%80%9A%E7%9F%A5&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=%E6%95%B0%E5%AD%97%E7%BB%8F%E6%B5%8E%20%E6%94%BF%E7%AD%96%20%E6%96%87%E4%BB%B6&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
    ]

    # AI政策（网信办/工信部/生成式AI/备案/数据合规）
    ai_policy_feeds = [
        "https://news.google.com/rss/search?q=%E7%94%9F%E6%88%90%E5%BC%8FAI%20%E7%AE%A1%E7%90%86%20%E5%8A%9E%E6%B3%95%20%E9%80%9A%E7%9F%A5&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=%E7%BD%91%E4%BF%A1%E5%8A%9E%20%E7%AE%97%E6%B3%95%20%E5%A4%87%E6%A1%88%20%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=%E5%B7%A5%E4%BF%A1%E9%83%A8%20%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD%20%E6%94%BF%E7%AD%96&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=%E6%95%B0%E6%8D%AE%E5%87%BA%E5%A2%83%20%E5%AE%89%E5%85%A8%20%E8%AF%84%E4%BC%B0%20%E6%94%BF%E7%AD%96&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
    ]

    # 成都AI新闻/政策/活动（成都、高新区、天府新区、AI、算力、模型、产业）
    chengdu_ai_feeds = [
        "https://news.google.com/rss/search?q=%E6%88%90%E9%83%BD%20%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD%20%E6%94%BF%E7%AD%96&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=%E6%88%90%E9%83%BD%20%E9%AB%98%E6%96%B0%E5%8C%BA%20%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=%E5%A4%A9%E5%BA%9C%E6%96%B0%E5%8C%BA%20AI%20%E4%BA%A7%E4%B8%9A&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "https://news.google.com/rss/search?q=%E6%88%90%E9%83%BD%20%E7%AE%97%E5%8A%9B%20%E5%A4%A7%E6%A8%A1%E5%9E%8B&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
    ]

    econ_items, ai_items, cd_items = [], [], []
    for u in econ_feeds:
        econ_items.extend(read_feed(u, limit=8))
    for u in ai_policy_feeds:
        ai_items.extend(read_feed(u, limit=8))
    for u in chengdu_ai_feeds:
        cd_items.extend(read_feed(u, limit=10))

    econ_items = dedup(econ_items)[:15]
    ai_items = dedup(ai_items)[:15]
    cd_items = dedup(cd_items)[:20]

    material = "\n\n".join([
        block("经济政策素材", econ_items),
        block("AI政策素材", ai_items),
        block("成都AI动态素材", cd_items),
    ])

    digest = call_deepseek(material, today_str)

    # 兜底：附原始链接清单
    raw = []
    for it in econ_items[:8]:
        raw.append(f"- {it['title']} | {it['link']}")
    for it in ai_items[:8]:
        raw.append(f"- {it['title']} | {it['link']}")
    for it in cd_items[:10]:
        raw.append(f"- {it['title']} | {it['link']}")
    raw_block = "【原始链接清单（兜底）】\n" + "\n".join(raw) if raw else ""

    text = f"{title}\n\n{digest}\n\n{raw_block}".strip()
    post_to_feishu_in_chunks(text, max_len=3500)


if __name__ == "__main__":
    main()
