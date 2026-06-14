#!/usr/bin/env python3
"""
竹材行业竞争对手新闻采集器 (GitHub Actions 版本)
每天自动运行，从公开来源采集行业动态，不依赖 AI/API。
"""

import json
import os
import re
import time
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
            # 百度搜索结果通常包含日期
            date_match = re.search(r"\d{4}[年-]\d{1,2}[月-]\d{1,2}", item.get_text())
            date = (
                date_match.group().replace("年", "-").replace("月", "-").replace("日", "")
                if date_match
                else ""
            )
            results.append({"title": title, "url": link, "date": date, "source": "百度新闻"})
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
            link = title_el.get("href", "")
            date_el = item.select_one(".time")
            date = date_el.get_text(strip=True) if date_el else ""
            results.append({"title": title, "url": link, "date": date, "source": "搜狐新闻"})
    except Exception:
        pass
    return results


def fetch_government_news() -> list[dict]:
    """采集政府/行业官方新闻"""
    results = []
    # 国家林业和草原局 - 竹产业
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
                results.append(
                    {"title": title, "url": link, "date": date, "source": "国家林业局"}
                )
    except Exception:
        pass
    return results


def check_website(web_url: str) -> dict:
    """检查公司官网是否可访问"""
    result = {"url": web_url, "accessible": False, "status": "unknown"}
    try:
        resp = requests.get(web_url, headers=HEADERS, timeout=10, allow_redirects=True)
        result["accessible"] = True
        result["status"] = resp.status_code
        if resp.status_code == 200:
            # 尝试提取标题
            soup = BeautifulSoup(resp.text, "html.parser")
            result["title"] = soup.title.string.strip() if soup.title else ""
    except Exception as e:
        result["error"] = str(e)[:100]
    return result


def main():
    # 加载公司列表
    with open(COMPANIES_FILE, "r", encoding="utf-8") as f:
        companies = json.load(f)

    collected = {}
    total_updates = 0

    print(f"[{TODAY}] 开始采集 {len(companies)} 家竹材企业动态...")
    print(f"重点搜索: {len(FOCUS_COMPANIES)} 家\n")

    # 1. 对重点公司进行百度/搜狐新闻搜索
    for company_name in FOCUS_COMPANIES:
        # 找到匹配的公司
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
        print(f"  搜索: {company_name[:20]}...")

        updates = search_baidu_news(company_name)
        if len(updates) < 3:
            updates += search_sohu_news(company_name)

        if updates:
            collected[str(company_id)] = {
                "company_id": company_id,
                "company_name": company_name,
                "collected_at": datetime.now().isoformat(),
                "updates": updates,
                "total": len(updates),
            }
            total_updates += len(updates)
            print(f"    -> {len(updates)} 条")

        time.sleep(1)  # 避免请求过快被限制

    # 2. 行业政策新闻
    print("\n  采集行业新闻...")
    gov_news = fetch_government_news()
    if gov_news:
        collected["industry_news"] = gov_news
        print(f"    -> {len(gov_news)} 条竹产业相关")

    # 3. 官网可达性抽查 (每天抽查5家，循环)
    print("\n  抽查官网可达性...")
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
            print(f"    {icon} {c['full_name'][:20]}: {status.get('status', 'error')}")

    # 4. 保存每日数据
    collected["collected_at"] = datetime.now().isoformat()
    collected["total_updates"] = total_updates

    output_file = COLLECTED_DIR / f"{TODAY}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(collected, f, ensure_ascii=False, indent=2)

    # 5. 更新 latest.json（看板直接读取这个文件）
    latest_file = DATA_DIR / "latest.json"
    with open(latest_file, "w", encoding="utf-8") as f:
        json.dump(collected, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 采集完成：{total_updates} 条新闻动态")
    print(f"   每日数据: {output_file}")
    print(f"   看板数据: {latest_file}")


if __name__ == "__main__":
    main()
