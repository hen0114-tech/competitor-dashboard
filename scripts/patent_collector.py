#!/usr/bin/env python3
"""
竹材行业竞争对手专利数据采集器
每周自动从公开来源更新各公司专利数量
数据来源：爱企查(aiqicha.baidu.com)、PatentGuru、企查查搜索结果
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path

import requests

# ====== 配置 ======
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
COMPANIES_FILE = DATA_DIR / "companies.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def extract_patent_count(text: str) -> int | None:
    """从文本中提取专利数量"""
    # 匹配 "XX个专利" 或 "XX项专利" 或 "XX条专利信息" 等模式
    patterns = [
        r'(\d+)\s*个?\s*专利信息',
        r'(\d+)\s*个?\s*专利',
        r'(\d+)\s*项?\s*专利',
        r'(\d+)\s*条\s*专利',
        r'共.*?(\d+)\s*[项个条]\s*专利',
        r'拥有.*?(\d+)\s*[项个条]\s*专利',
        r'知识产权.*?(\d+)\s*[项个条]\s*专利',
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return int(m.group(1))
    return None


def search_aiqicha(company_name: str) -> dict | None:
    """通过百度搜索爱企查结果获取专利数"""
    try:
        url = f"https://www.baidu.com/s?wd=site%3Aaiqicha.baidu.com+{company_name}+%E4%B8%93%E5%88%A9%E6%95%B0%E9%87%8F"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = "utf-8"
        
        # 从搜索结果摘要中提取专利数
        count = extract_patent_count(resp.text)
        if count and count > 0:
            return {"source": "爱企查", "count": count}
    except Exception as e:
        print(f"    [爱企查] 搜索失败: {e}")
    
    # 备用方案：直接搜索公司名 + 专利数量
    try:
        url2 = f"https://www.baidu.com/s?wd={company_name}+%E4%B8%93%E5%88%A9%E6%95%B0%E9%87%8F"
        resp2 = requests.get(url2, headers=HEADERS, timeout=15)
        resp2.encoding = "utf-8"
        count = extract_patent_count(resp2.text)
        if count and count > 0:
            return {"source": "百度搜索", "count": count}
    except Exception as e:
        print(f"    [百度] 搜索失败: {e}")
    
    return None


def update_companies_patents():
    """主函数：更新所有公司的专利数据"""
    with open(COMPANIES_FILE, "r", encoding="utf-8") as f:
        companies = json.load(f)

    today = datetime.now().strftime("%Y-%m-%d")
    updated = []
    failed = []

    print(f"[{today}] 开始更新 {len(companies)} 家企业专利数据...\n")

    for c in companies:
        name = c["full_name"]
        old_patents = c.get("patents", 0)
        print(f"  [{c['id']:2d}] {name[:20]}... (当前: {old_patents})", end="", flush=True)

        result = search_aiqicha(name)
        
        if result and result["count"] != old_patents:
            c["patents"] = result["count"]
            c["patent_updated"] = today
            c["patent_source"] = result["source"]
            diff = result["count"] - old_patents
            arrow = "+" if diff > 0 else ""
            print(f" => {result['count']} ({arrow}{diff}) [{result['source']}] ✅")
            updated.append(c["full_name"])
        elif result:
            print(f" => {result['count']} (无变化) ✓")
        else:
            print(f" => 未找到新数据 ⚠️")
            failed.append(name)

        time.sleep(0.8)  # 避免请求过快

    # 保存更新后的数据
    with open(COMPANIES_FILE, "w", encoding="utf-8") as f:
        json.dump(companies, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*50}")
    print(f"✅ 完成！更新了 {len(updated)} 家企业的专利数据:")
    for u in updated:
        print(f"   • {u}")
    
    if failed:
        print(f"\n⚠️  未找到数据的 {len(failed)} 家:")
        for f_item in failed:
            print(f"   • {f_item}")
    
    return len(updated)


if __name__ == "__main__":
    n = update_companies_patents()
    exit(0 if n > 0 else 1)
