#!/usr/bin/env python3
"""
竹材行业竞争对手新闻采集器 (GitHub Actions 版本)
每天自动运行，从公开来源采集行业动态，不依赖 AI/API。

采集管道：
  1. 官网文章采集 — 直接抓取竞品官网新闻/动态页
  2. 百度/搜狐新闻搜索 — 11家重点公司
  3. 行业政策新闻 — 国家林业局
  4. 官网可达性抽查 — 每天5家
"""

import json
import os
import re
import time
import socket
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ====== 配置 ======
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
COLLECTED_DIR = DATA_DIR / "collected"
COMPANIES_FILE = DATA_DIR / "companies.json"
TODAY = datetime.now().strftime("%Y-%m-%d")
MAX_ARTICLE_AGE_DAYS = 30  # 只保留一个月内的文章
MAX_ARTICLES_PER_SITE = 3  # 每家官网最多保留3篇最新文章

# 行业RSS/新闻源
INDUSTRY_SOURCES = [
    {
        "name": "林业局竹产业",
        "url": "https://www.forestry.gov.cn/lyj/1/gglcswcy/index.html",
        "type": "gov"
    },
]

# 重点公司搜索关键词（只用公司名，不用AI扩展）
FOCUS_COMPANIES = [
    "浙江大庄实业集团有限公司",
    "浙江永裕家居股份有限公司",
    "福建省庄禾竹业科技有限公司",
    "福建金竹竹业有限公司",
    "洪雅竹元科技有限公司",
    "江西南丰振宇实业集团有限公司",
    "福建华宇集团有限公司",
    "安徽鸿叶集团有限公司",
    "湖南桃花江竹材科技股份有限公司",
    "双枪科技股份有限公司",
    "龙竹科技集团股份有限公司",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; CompetitorMonitor/1.0; +https://github.com/hen0114-tech/competitor-dashboard)"
}

# 全局超时
socket.setdefaulttimeout(12)

# ============================================================
# 官网文章采集
# ============================================================

# 常见新闻页探测路径
NEWS_PATHS = [
    "/news", "/xinwen", "/article", "/articles",
    "/newslist", "/xinwenlist", "/dongtai",
    "/gsxw", "/qyxw", "/xwzx", "/xwdt",
    "/news.aspx", "/news.php", "/xinwen.aspx",
    "/about.asp", "/about.aspx",
]


def safe_url(base: str, href: str) -> str:
    """拼接绝对URL"""
    if href.startswith("http"):
        return href
    if href.startswith("//"):
        return "https:" + href
    parsed = urllib.parse.urlparse(base)
    if href.startswith("/"):
        return f"{parsed.scheme}://{parsed.netloc}{href}"
    if href.startswith("?"):
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}{href}"
    return f"{parsed.scheme}://{parsed.netloc}/{href}"


def fetch_html(url: str, timeout: int = 10) -> tuple:
    """
    抓取页面 HTML，返回 (html, is_js_rendered)
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        html = resp.text
        if len(html) < 300:
            return None, False

        # JS 渲染检测
        js_rendered = False
        if len(html) < 1500:
            js_rendered = True
        spa_markers = ["<div id=\"app\"", "<div id=\"root\"",
                       "window.__INITIAL_STATE__", "webpack", "/static/js/"]
        match_count = sum(1 for m in spa_markers if m.lower() in html.lower())
        if match_count >= 2:
            js_rendered = True

        return html, js_rendered
    except Exception:
        return None, False


def find_date_near(html: str, href: str) -> str | None:
    """在链接附近查找发表日期，超过30天的返回None"""
    pos = html.find(href)
    if pos < 0:
        pos = 0
    start = max(0, pos - 600)
    end = min(len(html), pos + 600)
    context = html[start:end]

    patterns = [
        r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})',
        r'(\d{4})年(\d{1,2})月(\d{1,2})日',
        r'(\d{4})\.(\d{1,2})\.(\d{1,2})',
    ]
    for p in patterns:
        m = re.search(p, context)
        if m:
            try:
                y, mon, d = int(m[1]), int(m[2]), int(m[3])
                dt = datetime(y, mon, d)
                cutoff = datetime.now() - timedelta(days=MAX_ARTICLE_AGE_DAYS)
                if dt < cutoff:
                    return None  # 超过30天，丢弃
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                pass
    return None  # 找不到日期也丢弃，避免保留无日期旧文


def extract_articles(html: str, base_url: str, max_articles: int = MAX_ARTICLES_PER_SITE) -> list[dict]:
    """
    从 HTML 中提取文章列表。
    策略：
      1. 找 news/article 相关容器区块
      2. 提取其中的链接
      3. 过滤：标题≥7字、URL包含文章特征
      4. 提取日期
    """
    articles = []
    seen_urls = set()

    # 先找文章列表容器
    list_pattern = r'(<(?:div|ul|section)[^>]*?(?:news|article|list|xinwen|dongtai)[^>]*>.*?</(?:div|ul|section)>)'
    list_blocks = re.findall(list_pattern, html, re.IGNORECASE | re.DOTALL)

    if not list_blocks:
        list_blocks = [html]
    else:
        list_blocks.sort(key=len, reverse=True)
        list_blocks = list_blocks[:3]

    for block in list_blocks:
        links = re.findall(
            r'<a\s+[^>]*href\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            block, re.IGNORECASE | re.DOTALL
        )

        for href, inner in links:
            text = re.sub(r'<[^>]+>', '', inner).strip()
            text = re.sub(r'\s+', ' ', text)
            text = text.replace("&nbsp;", " ").replace("&amp;", "&")

            if len(text) < 7 or len(text) > 300:
                continue

            full_url = safe_url(base_url, href)
            if full_url in seen_urls:
                continue

            # 判断是否像文章链接
            url_lower = full_url.lower()
            is_likely_article = any(kw in url_lower for kw in [
                "news", "article", "xinwen", "dongtai",
                "info", "show", "detail", "view", "content",
            ])
            is_likely_article = is_likely_article or bool(re.search(r'[?&]id=\d+', url_lower))
            is_likely_article = is_likely_article or bool(re.search(r'/\d+\.html?', url_lower))

            if not is_likely_article and len(text) < 15:
                continue

            pub_date = find_date_near(block, href)
            if pub_date is None:
                continue  # 超过30天或找不到日期，跳过
            seen_urls.add(full_url)
            articles.append({
                "title": text,
                "url": full_url,
                "date": pub_date,
                "source": "官网",
                "source_label": "官网",
            })

            if len(articles) >= max_articles:
                return articles

    return articles


def scan_company_website(company: dict) -> list[dict]:
    """
    扫描单个公司官网，提取新闻文章。
    返回格式与现有 updates 数组一致。
    """
    website = (company.get("website") or "").strip().rstrip("/")
    if not website:
        return []

    articles = []

    # 1. 抓首页
    html, js = fetch_html(website, timeout=10)
    if html is None:
        # 首页不可达：用备注记录
        return [{
            "title": f"官网 {website} 当前无法访问",
            "url": website,
            "date": TODAY,
            "source": "官网",
            "source_label": "官网",
            "summary": "网站不可达或超时"
        }]

    if js:
        # JS 渲染：首页拿不到内容，尝试通用新闻页
        for path in ["/news", "/xinwen", "/article", "/dongtai"]:
            u = website + path
            h, js2 = fetch_html(u, timeout=12)
            if h and not js2 and len(h) > 2000:
                articles = extract_articles(h, u)
                break
        if not articles:
            # 标记为 JS 渲染，需手动采集
            articles = [{
                "title": f"官网 {website} 为JS动态渲染，需人工查看",
                "url": website,
                "date": TODAY,
                "source": "官网",
                "source_label": "官网",
                "summary": "JS动态渲染网站，无法自动提取文章列表"
            }]
        return articles

    # 2. 提取首页文章
    articles = extract_articles(html, website)

    # 3. 探测新闻列表页
    for path in NEWS_PATHS[:7]:  # 只试前7个，节省时间
        u = website + path
        h, _ = fetch_html(u, timeout=7)
        if h and len(h) > 2000:
            news_articles = extract_articles(h, u, max_articles=5)
            if news_articles:
                existing_urls = {a["url"] for a in articles}
                for a in news_articles:
                    if a["url"] not in existing_urls:
                        articles.append(a)
                break

    return articles


# ============================================================
# 百度/搜狐搜索（原有逻辑）
# ============================================================

def search_baidu_news(query: str, max_results: int = 5) -> list[dict]:
    """从百度新闻搜索，返回结果列表"""
    results = []
    try:
        url = f"https://www.baidu.com/s?tn=news&rtt=1&bsst=1&cl=2&wd={query}"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        for item in soup.select(".result")[:max_results]:
            title_el = item.select_one("h3 a")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            link = title_el.get("href", "")
            # 检查是否与竹材相关
            if not any(kw in title for kw in ["竹", "木", "地板", "建材", "户外"]):
                continue
            date_match = re.search(r"\d{4}[年-]\d{1,2}[月-]\d{1,2}", item.get_text())
            date = (
                date_match.group().replace("年", "-").replace("月", "-").replace("日", "")
                if date_match
                else ""
            )
            results.append({
                "title": title, "url": link, "date": date,
                "source": "百度新闻", "source_label": "百度新闻"
            })
    except Exception:
        pass
    return results


def search_sohu_news(query: str) -> list[dict]:
    """从搜狐新闻搜索"""
    results = []
    try:
        url = f"https://news.sohu.com/search?q={query}"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        for item in soup.select(".news-list .item")[:5]:
            title_el = item.select_one("h4 a")
            if not title_el:
                title_el = item.select_one("a")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            if not any(kw in title for kw in ["竹", "木", "地板", "建材", "户外"]):
                continue
            link = title_el.get("href", "")
            date_el = item.select_one(".time")
            date = date_el.get_text(strip=True) if date_el else ""
            results.append({
                "title": title, "url": link, "date": date,
                "source": "搜狐新闻", "source_label": "搜狐新闻"
            })
    except Exception:
        pass
    return results


# ============================================================
# 行业新闻（原有逻辑）
# ============================================================

def fetch_government_news() -> list[dict]:
    """采集政府/行业官方新闻"""
    results = []
    try:
        url = "https://www.forestry.gov.cn/lyj/1/gglcswcy/index.html"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        for item in soup.select(".news_list li, .list li")[:8]:
            a = item.select_one("a")
            if not a:
                continue
            title = a.get_text(strip=True)
            link = a.get("href", "")
            if "竹" in title:
                date_el = item.select_one("span")
                date = date_el.get_text(strip=True) if date_el else ""
                results.append({
                    "title": title, "url": link, "date": date,
                    "source": "国家林业局", "source_label": "国家林业局"
                })
    except Exception:
        pass
    return results


# ============================================================
# 官网可达性检查（原有逻辑）
# ============================================================

def check_website(web_url: str) -> dict:
    """检查公司官网是否可访问"""
    result = {"url": web_url, "accessible": False, "status": "unknown"}
    try:
        resp = requests.get(web_url, headers=HEADERS, timeout=10, allow_redirects=True)
        result["accessible"] = True
        result["status"] = resp.status_code
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            result["title"] = soup.title.string.strip() if soup.title else ""
    except Exception as e:
        result["error"] = str(e)[:100]
    return result


# ============================================================
# 主逻辑
# ============================================================

def main():
    try:
        _main()
    except Exception as e:
        print(f"\n⚠️ 采集异常: {e}")
        import traceback
        traceback.print_exc()
        _save_minimal()


def _main():
    # 加载公司列表
    with open(COMPANIES_FILE, "r", encoding="utf-8") as f:
        companies = json.load(f)

    collected = {}
    total_updates = 0

    print(f"[{TODAY}] 开始采集 {len(companies)} 家竹材企业动态...")
    print(f"重点搜索: {len(FOCUS_COMPANIES)} 家\n")

    # ================================================================
    # 管道1: 官网文章采集（所有有官网的公司）
    # ================================================================
    print("┌─ 管道1: 官网文章采集 ─────────────────────")
    companies_with_web = [c for c in companies if c.get("website", "").startswith("http")]
    print(f"│  共 {len(companies_with_web)} 家有待扫描官网")

    site_ok = 0
    site_js = 0
    site_dead = 0
    site_articles = 0

    for i, c in enumerate(companies_with_web, 1):
        print(f"│  [{i}/{len(companies_with_web)}] {c['full_name'][:16]}... ", end="", flush=True)

        articles = scan_company_website(c)
        if not articles:
            print("❌ 不可达")
            site_dead += 1
            time.sleep(0.3)
            continue

        # 判断是否为JS渲染标记
        is_js_hint = any("JS" in a.get("summary", "") for a in articles) or \
                     any("无法访问" in a.get("summary", "") for a in articles) or \
                     any("不可达" in a.get("summary", "") for a in articles)

        if is_js_hint:
            print(f"🟡 JS渲染")
            site_js += 1
        else:
            real_articles = [a for a in articles if "JS" not in a.get("summary", "")
                             and "无法访问" not in a.get("summary", "")]
            n = len(real_articles)
            print(f"✅ {n}篇")
            site_ok += 1
            site_articles += n

        # 入库
        company_id = c["id"]
        collected[str(company_id)] = {
            "company_id": company_id,
            "company_name": c["full_name"],
            "collected_at": datetime.now().isoformat(),
            "updates": articles,
            "total": len(articles),
        }
        total_updates += len(articles)

        time.sleep(0.5)

    print(f"│  结果: ✅{site_ok} 🟡{site_js} ❌{site_dead} | 共{site_articles}篇官网文章")
    print("└───────────────────────────────────────────\n")

    # ================================================================
    # 管道2: 百度/搜狐新闻搜索（11家重点公司）
    # ================================================================
    print("┌─ 管道2: 百度/搜狐新闻搜索 ───────────────────")

    for company_name in FOCUS_COMPANIES:
        matched = next(
            (c for c in companies if c["full_name"] == company_name), None
        )
        if not matched:
            matched = next(
                (c for c in companies if company_name[:4] in c["full_name"]),
                None,
            )
        if not matched:
            continue

        company_id = matched["id"]
        print(f"│  搜索: {company_name[:20]}...")

        updates = search_baidu_news(company_name)
        if len(updates) < 3:
            updates += search_sohu_news(company_name)

        if updates:
            key = str(company_id)
            if key in collected and collected[key].get("updates"):
                # 合并到已有updates（去重）
                existing_urls = {u["url"] for u in collected[key]["updates"]}
                for u in updates:
                    if u["url"] not in existing_urls:
                        collected[key]["updates"].append(u)
                collected[key]["total"] = len(collected[key]["updates"])
            else:
                collected[key] = {
                    "company_id": company_id,
                    "company_name": company_name,
                    "collected_at": datetime.now().isoformat(),
                    "updates": updates,
                    "total": len(updates),
                }
            total_updates += len(updates)
            print(f"│    -> {len(updates)} 条新闻")
        else:
            print(f"│    -> 无结果")

        time.sleep(1)

    print("└───────────────────────────────────────────\n")

    # ================================================================
    # 管道3: 行业政策新闻
    # ================================================================
    print("┌─ 管道3: 行业政策新闻 ───────────────────────")
    gov_news = fetch_government_news()
    if gov_news:
        collected["industry_news"] = gov_news
        print(f"│  -> {len(gov_news)} 条竹产业相关")
    else:
        print(f"│  -> 无新政策")
    print("└───────────────────────────────────────────\n")

    # ================================================================
    # 管道4: 官网可达性抽查（每天5家）
    # ================================================================
    print("┌─ 管道4: 官网可达性抽查 ─────────────────────")
    day_of_year = datetime.now().timetuple().tm_yday
    start_idx = (day_of_year * 5) % len(companies)
    for i in range(start_idx, start_idx + 5):
        idx = i % len(companies)
        c = companies[idx]
        web = c.get("website", "")
        if web and web.startswith("http"):
            status = check_website(web)
            status_key = f"site_{c['id']}"
            collected[status_key] = {
                "company_id": c["id"],
                "company_name": c["full_name"],
                "website_status": status,
            }
            icon = "✅" if status["accessible"] else "❌"
            print(f"│  {icon} {c['full_name'][:20]}: {status.get('status', 'error')}")
    print("└───────────────────────────────────────────\n")

    # ================================================================
    # 保存前过滤：只保留一个月内的文章，每家最多3篇
    # ================================================================
    cutoff_date = datetime.now() - timedelta(days=MAX_ARTICLE_AGE_DAYS)

    filtered_total = 0
    keys_to_remove = []

    for key, val in collected.items():
        if key in ("collected_at", "total_updates", "industry_news"):
            continue
        if "updates" not in val:
            continue

        articles = val.get("updates", [])
        fresh_articles = []
        seen = set()

        for a in articles:
            date_str = a.get("date", "")
            if not date_str:
                continue
            try:
                dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
            except ValueError:
                try:
                    dt = datetime.strptime(date_str, "%Y年%m月%d日")
                except ValueError:
                    continue
            if dt >= cutoff_date:
                unique_key = a.get("url", a.get("title", ""))
                if unique_key not in seen:
                    seen.add(unique_key)
                    fresh_articles.append(a)

        if fresh_articles:
            fresh_articles.sort(key=lambda x: x.get("date", ""), reverse=True)
            fresh_articles = fresh_articles[:MAX_ARTICLES_PER_SITE]
            val["updates"] = fresh_articles
            val["total"] = len(fresh_articles)
            filtered_total += len(fresh_articles)
        else:
            keys_to_remove.append(key)

    for key in keys_to_remove:
        del collected[key]

    collected["total_updates"] = filtered_total

    # ================================================================
    # 保存
    # ================================================================
    collected["collected_at"] = datetime.now().isoformat()

    # 每日数据
    COLLECTED_DIR.mkdir(parents=True, exist_ok=True)
    output_file = COLLECTED_DIR / f"{TODAY}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(collected, f, ensure_ascii=False, indent=2)

    # 看板数据
    latest_file = DATA_DIR / "latest.json"
    with open(latest_file, "w", encoding="utf-8") as f:
        json.dump(collected, f, ensure_ascii=False, indent=2)

    print(f"✅ 采集完成：{filtered_total} 条新动态（30天内）")
    print(f"   每日数据: {output_file}")
    print(f"   看板数据: {latest_file}")


def _save_minimal():
    """即使采集完全失败，也保留旧数据但更新时间戳"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    COLLECTED_DIR.mkdir(parents=True, exist_ok=True)

    latest_file = DATA_DIR / "latest.json"
    if latest_file.exists():
        with open(latest_file, "r", encoding="utf-8") as f:
            preserved = json.load(f)
    else:
        preserved = {}

    preserved["collected_at"] = datetime.now().isoformat()
    if "note" not in preserved:
        preserved["note"] = "本次自动采集未能获取新数据（搜索源可能限制了GitHub服务器IP）"

    with open(COLLECTED_DIR / f"{TODAY}.json", "w", encoding="utf-8") as f:
        json.dump(preserved, f, ensure_ascii=False, indent=2)
    with open(latest_file, "w", encoding="utf-8") as f:
        json.dump(preserved, f, ensure_ascii=False, indent=2)
    print(f"\n⚠️ 采集未获取新数据，保留旧数据，仅更新时间戳: {preserved['collected_at']}")


if __name__ == "__main__":
    main()
