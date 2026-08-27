"""
每日AI行业动态跟踪脚本
通过RSS聚合中外AI科技媒体，分类整理后推送到Telegram
"""

import os
import sys
import json
import re
from datetime import datetime, timedelta, timezone
from collections import defaultdict

import feedparser
import requests

# ── 配置 ──────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

CATEGORIES = {
    "🆕 新产品/工具": [
        "launch", "release", "update", "feature", "tool", "product", "app", "plugin", "integrat",
        "roll out", "announce", "debut", "upgrade", "新功能", "发布", "上线", "推出", "产品",
        "工具", "应用", "插件", "更新", "升级", "开放", "公测", "内测", "上线了", "上新"
    ],
    "💰 投融资事件": [
        "funding", "investment", "raise", "VC", "acquisition", "merger", "IPO",
        "valuation", "series", "round", "investor", "startup funding",
        "融资", "投资", "收购", "上市", "估值", "轮融资", "募资", "战投", "入股", "注资"
    ],
    "🔬 技术突破/模型": [
        "model", "paper", "research", "breakthrough", "benchmark", "training",
        "parameter", "open source", "deep learning", "neural", "transformer",
        "LLM", "GPT", "Gemini", "Claude", "diffusion", "multimodal", "reasoning",
        "模型", "论文", "突破", "参数", "训练", "开源", "推理", "多模态",
        "大模型", "基座模型", "千亿", "万亿", "发布.*模型", "研究"
    ],
    "📋 政策与监管": [
        "regulation", "policy", "law", "government", "compliance", "ban",
        "legislation", "act", "executive order", "guideline", "framework",
        "政策", "监管", "法规", "政府", "合规", "立法", "暂行办法", "指导意见",
        "管理办法", "安全评估", "数据安全", "隐私"
    ],
}

# ── RSS源列表 ─────────────────────────────────────────
RSS_FEEDS = [
    # 中文AI媒体
    {"url": "https://www.jiqizhixin.com/rss", "lang": "zh"},
    {"url": "https://www.qbitai.com/feed", "lang": "zh"},
    # 英文AI媒体
    {"url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "lang": "en"},
    {"url": "https://venturebeat.com/category/ai/feed/", "lang": "en"},
    {"url": "https://techcrunch.com/category/artificial-intelligence/feed/", "lang": "en"},
    {"url": "https://www.artificialintelligence-news.com/feed/", "lang": "en"},
    {"url": "https://news.mit.edu/rss/topic/artificial-intelligence2", "lang": "en"},
    {"url": "https://github.blog/category/ai-ml/feed/", "lang": "en"},
]

# UTC+8 时区
TZ = timezone(timedelta(hours=8))

# ── 工具函数 ──────────────────────────────────────────

def clean_text(text):
    """清理文本：去除HTML标签、转义字符，返回干净文本"""
    if not text:
        return ""
    import html as _html
    # 先转义HTML实体
    text = _html.unescape(text)
    # 去除HTML标签
    text = re.sub(r"<[^>]+>", "", text)
    # 合并多余空白
    text = re.sub(r"\s+", " ", text).strip()
    return text


def make_summary(text, max_len=200):
    """生成清晰摘要：优先保留完整句子，避免截断在句子中间"""
    text = clean_text(text)
    if not text:
        return ""
    
    # 尝试按句子分割（中文用。！？；，英文用. ! ?）
    sentences = re.split(r"([。！？；\n]|\.\s+|!\s+|\?\s+)", text)
    
    summary = ""
    for i in range(0, len(sentences)-1, 2):
        if i+1 < len(sentences):
            sentence = sentences[i] + sentences[i+1]
        else:
            sentence = sentences[i]
        sentence = sentence.strip()
        if not sentence:
            continue
        # 如果加这句会超长，且已有内容，则停止
        if len(summary) + len(sentence) > max_len:
            if summary:
                break
            else:
                # 实在没办法，截断
                return sentence[:max_len-1] + "…"
        summary += sentence
    
    # 如果没有按句子分割成功，直接截断
    if not summary:
        summary = text[:max_len]
        if len(text) > max_len:
            summary = summary.rstrip() + "…"
    
    return summary


# 常见英文媒体中文名称映射（避免频繁调用翻译API）
SOURCE_NAME_MAP = {
    "TechCrunch": "TechCrunch",
    "VentureBeat": "VentureBeat",
    "The Verge": "The Verge",
    "MIT News": "MIT新闻",
    "AI News": "AI新闻网",
    "Artificial Intelligence News": "人工智能新闻网",
    "GitHub Blog": "GitHub官方博客",
}

import urllib.parse
import time as _time

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


def _translate_via_mymemory(text):
    """使用 MyMemory API 翻译（每天免费 10000 字符，IP级别限速）"""
    url = "https://api.mymemory.translated.net/get"
    params = {"q": text, "langpair": "en|zh-CN", "de": "liwei@aivonwei.cloud"}
    headers = {"User-Agent": _UA}
    resp = requests.get(url, params=params, timeout=15, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    if data.get("responseStatus") != 200:
        raise RuntimeError(f"MyMemory status={data.get('responseStatus')} {data.get('responseDetails')}")
    return data["responseData"]["translatedText"].strip()


def _translate_via_google(text):
    """使用 Google Translate 免费 API 翻译（备用方案）"""
    url = "https://translate.googleapis.com/translate_a/single"
    params = {
        "client": "gtx", "sl": "en", "tl": "zh-CN", "dt": "t", "q": text,
    }
    headers = {
        "User-Agent": _UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    resp = requests.get(url, params=params, timeout=10, headers=headers)
    if resp.status_code != 200 or not resp.text.lstrip().startswith("["):
        raise RuntimeError(f"Google status={resp.status_code}")
    data = resp.json()
    translated = "".join(seg[0] for seg in data[0] if seg and seg[0])
    return translated.strip()


def _chunk_text(text, max_len=450):
    """按句子边界切分长文本，避免 API 单次长度限制"""
    text = text.strip()
    if len(text) <= max_len:
        return [text]
    parts = re.split(r"([.!?。！？;]+\s+|\n+)", text)
    chunks, buf = [], ""
    for i in range(0, len(parts), 2):
        s = parts[i] + (parts[i + 1] if i + 1 < len(parts) else "")
        if not s.strip():
            continue
        if len(buf) + len(s) > max_len and buf:
            chunks.append(buf.strip())
            buf = s
        else:
            buf += s
    if buf.strip():
        chunks.append(buf.strip())
    return chunks or [text]


def translate_to_chinese(text):
    """将英文翻译为中文。优先 MyMemory，失败回退 Google；最后兜底原文。"""
    if not text or not text.strip():
        return text
    chunks = _chunk_text(text)
    translated_parts = []
    for ch in chunks:
        # 优先 MyMemory
        try:
            translated_parts.append(_translate_via_mymemory(ch))
            _time.sleep(0.3)
            continue
        except Exception as e:
            print(f"[INFO] MyMemory 失败，回退 Google: {type(e).__name__}: {e}", file=sys.stderr)
        # 回退 Google
        try:
            translated_parts.append(_translate_via_google(ch))
            _time.sleep(0.5)
        except Exception as e:
            print(f"[WARN] Google 也失败，保留原文片段: {type(e).__name__}: {e}", file=sys.stderr)
            translated_parts.append(ch)
    result = "".join(translated_parts).strip()
    return result if result else text


def translate_article(article):
    """翻译英文文章的标题和摘要为中文，返回翻译后的文章"""
    if article.get("lang") != "en":
        return article

    # 翻译标题
    orig_title = article["title"]
    article["title"] = translate_to_chinese(orig_title)
    _time.sleep(0.4)

    # 翻译摘要
    if article.get("summary"):
        article["summary"] = translate_to_chinese(article["summary"])
        _time.sleep(0.4)

    # 翻译来源名称
    source = article.get("source", "")
    if source and source not in SOURCE_NAME_MAP:
        article["source"] = translate_to_chinese(source)
        _time.sleep(0.3)
    elif source in SOURCE_NAME_MAP:
        article["source"] = SOURCE_NAME_MAP[source]

    translated_ok = orig_title != article["title"]
    print(f"  {'✓' if translated_ok else '✗'} 翻译: {orig_title[:30]}... -> {article['title'][:30]}...")
    return article


def fetch_article_content(url):
    """简单抓取文章第一段作为补充摘要"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; AINewsTracker/1.0)"
        }
        resp = requests.get(url, headers=headers, timeout=10)
        resp.encoding = resp.apparent_encoding
        html = resp.text
        
        # 简单提取正文段落（去除HTML标签）
        # 尝试找到文章主体内容
        from html.parser import HTMLParser
        
        class ParagraphExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self.paragraphs = []
                self.current = ""
                self.in_p = False
            
            def handle_starttag(self, tag, attrs):
                if tag == "p":
                    self.in_p = True
                    self.current = ""
            
            def handle_endtag(self, tag):
                if tag == "p" and self.in_p:
                    self.in_p = False
                    text = clean_text(self.current)
                    if len(text) > 50:  # 只保留有意义的长段落
                        self.paragraphs.append(text)
            
            def handle_data(self, data):
                if self.in_p:
                    self.current += data
        
        parser = ParagraphExtractor()
        parser.feed(html)
        
        # 返回前两个段落的合并（最多300字）
        if parser.paragraphs:
            full_text = " ".join(parser.paragraphs[:2])
            return make_summary(full_text, max_len=250)
    except:
        pass
    return ""


def fetch_feeds():
    """拉取所有RSS源，返回文章列表"""
    articles = []
    for feed in RSS_FEEDS:
        try:
            parsed = feedparser.parse(feed["url"])
            for entry in parsed.entries:
                # 解析发布时间
                published = None
                for attr in ("published_parsed", "updated_parsed"):
                    t = getattr(entry, attr, None)
                    if t:
                        from time import mktime
                        published = datetime.fromtimestamp(mktime(t), tz=TZ)
                        break

                if published is None:
                    published = datetime.now(TZ)

                # 只保留24小时内的文章
                cutoff = datetime.now(TZ) - timedelta(hours=36)
                if published < cutoff:
                    continue

                title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()
                
                # 获取摘要，优先用 content，其次 summary
                summary = ""
                if hasattr(entry, "content") and entry.content:
                    summary = entry.content[0].value
                elif hasattr(entry, "summary") and entry.summary:
                    summary = entry.summary
                elif hasattr(entry, "description") and entry.description:
                    summary = entry.description
                
                summary = make_summary(summary, max_len=200)

                articles.append({
                    "title": title,
                    "link": link,
                    "summary": summary,
                    "published": published,
                    "lang": feed["lang"],
                    "source": parsed.feed.get("title", ""),
                })
        except Exception as e:
            print(f"[WARN] 获取RSS失败 {feed['url']}: {e}", file=sys.stderr)
    
    # 对摘要太短的文章，尝试抓取网页补充（限制数量，避免运行时间过长）
    fetch_count = 0
    max_fetch = 10  # 最多抓取10篇文章
    for a in articles:
        if fetch_count >= max_fetch:
            break
        if len(a["summary"]) < 30 and a["link"]:  # 摘要少于30字才补充
            print(f"  补充摘要: {a['title'][:40]}...")
            extra = fetch_article_content(a["link"])
            if extra and len(extra) > len(a["summary"]):
                a["summary"] = extra
                fetch_count += 1

    # 翻译英文文章
    en_count = sum(1 for a in articles if a.get("lang") == "en")
    if en_count > 0:
        print(f"  开始翻译 {en_count} 篇英文文章...")
        for a in articles:
            if a.get("lang") == "en":
                translate_article(a)
        print(f"  翻译完成")

    return articles


def categorize(article):
    """根据标题+摘要关键词分类"""
    text = (article["title"] + " " + article["summary"]).lower()
    for cat_name, keywords in CATEGORIES.items():
        for kw in keywords:
            if kw.lower() in text:
                return cat_name
    return None


def classify_all(articles):
    """分类所有文章"""
    grouped = defaultdict(list)
    seen = set()
    for a in articles:
        # 去重
        key = a["title"][:60]
        if key in seen:
            continue
        seen.add(key)

        cat = categorize(a)
        if cat:
            grouped[cat].append(a)
    return grouped


def htmlescape(text):
    """转义HTML特殊字符，防止Telegram HTML解析错误"""
    if not text:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_message_and_articles(grouped):
    """构建Telegram消息，同时返回带编号的文章列表（供用户按序号查询详情）"""
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    lines = [f"📡 <b>AI行业动态早报</b> | {today}", ""]
    numbered_articles = []  # 带全局编号的文章数据
    article_num = 0

    for cat_name, articles in [
        ("🆕 新产品/工具", grouped.get("🆕 新产品/工具", [])),
        ("💰 投融资事件", grouped.get("💰 投融资事件", [])),
        ("🔬 技术突破/模型", grouped.get("🔬 技术突破/模型", [])),
        ("📋 政策与监管", grouped.get("📋 政策与监管", [])),
    ]:
        if not articles:
            continue  # 无内容的分类直接跳过

        lines.append("━━━━━━━━━━━━━━━")
        lines.append(f"{cat_name}")
        lines.append("━━━━━━━━━━━━━━━")

        # 去重并排序，取前5条
        unique = []
        seen_titles = set()
        for a in sorted(articles, key=lambda x: x["published"], reverse=True):
            short = a["title"][:60]
            if short not in seen_titles:
                seen_titles.add(short)
                unique.append(a)
            if len(unique) >= 5:
                break

        for a in unique:
            article_num += 1
            title = a["title"]
            if len(title) > 80:
                title = title[:77] + "..."
            title_escaped = htmlescape(title)
            link = a["link"]
            source = htmlescape(a.get("source", ""))

            # 来源放在标题后面，带序号
            if source:
                lines.append(f"{article_num}. <a href='{link}'>{title_escaped}</a> [{source}]")
            else:
                lines.append(f"{article_num}. <a href='{link}'>{title_escaped}</a>")
            
            # 摘要单独一行
            summary = a.get("summary", "")
            if summary:
                summary_clean = htmlescape(clean_text(summary))
                if summary_clean:
                    lines.append(f"  📝 {summary_clean}")
            lines.append("")  # 每条新闻之间空行

            # 记录带编号的文章数据
            numbered_articles.append({
                "num": article_num,
                "title": a["title"],
                "link": a["link"],
                "summary": a.get("summary", ""),
                "source": a.get("source", ""),
                "lang": a.get("lang", "zh"),
                "category": cat_name,
            })

    now = datetime.now(TZ).strftime("%H:%M")
    lines.append("━━━━━━━━━━━━━━━")
    lines.append(f"📎 由 WorkBuddy 自动生成 | {now}")
    lines.append("")
    lines.append("💡 输入序号（如 3）即可查看新闻全文")

    return "\n".join(lines), numbered_articles


def save_articles_json(articles):
    """保存带编号的文章列表到 JSON 文件，供后续按序号查询"""
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    data = {
        "date": today,
        "generated_at": datetime.now(TZ).isoformat(),
        "total": len(articles),
        "articles": articles,
    }
    with open("today_articles.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  已保存 {len(articles)} 篇文章到 today_articles.json")


def send_telegram(msg):
    """发送消息到Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    # 如果消息过长，按行分段发送（避免截断HTML标签）
    max_len = 4000
    if len(msg) <= max_len:
        resp = requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "HTML",
        }, timeout=30)
        if not resp.json().get("ok"):
            print(f"[ERROR] Telegram发送失败: {resp.text}", file=sys.stderr)
        else:
            print("[OK] Telegram推送成功")
    else:
        # 按行分段，避免截断HTML标签
        lines = msg.split("\n")
        parts = []
        current = ""
        for line in lines:
            test = current + ("\n" if current else "") + line
            if len(test) > max_len:
                if current:
                    parts.append(current)
                    current = line
                else:
                    # 单行超过限制，强制截断
                    parts.append(line[:max_len])
                    current = ""
            else:
                current = test
        if current:
            parts.append(current)
        
        for i, part in enumerate(parts):
            if i > 0:
                part = "（续上）\n" + part
            resp = requests.post(url, data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": part,
                "parse_mode": "HTML",
            }, timeout=30)
            if resp.json().get("ok"):
                print(f"[OK] Telegram推送第{i+1}段成功")
            else:
                print(f"[ERROR] Telegram推送第{i+1}段失败: {resp.text}", file=sys.stderr)


# ── 主流程 ─────────────────────────────────────────────

def main():
    print(f"[{datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}] 开始抓取AI新闻...")
    
    articles = fetch_feeds()
    print(f"  拉取到 {len(articles)} 篇文章（24小时内）")

    grouped = classify_all(articles)
    total = sum(len(v) for v in grouped.values())
    print(f"  分类完成: {total} 篇有效文章")
    for cat, arts in grouped.items():
        print(f"    {cat}: {len(arts)} 篇")

    msg, all_articles = build_message_and_articles(grouped)
    send_telegram(msg)
    save_articles_json(all_articles)
    print("  任务完成")


if __name__ == "__main__":
    main()
