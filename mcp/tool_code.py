"""
title: Three Smart Searches Xng with Searxng search, Jina scraper and beautifulsoup scraper
author: Lug, scraper part from Web search v0.2.1 by siwadon
author_url: https://brainedai.netlify.app
description: An advanced search tool that do three searches in parallel and uses 2 LLMs to perform URL selection, and content extraction, returns only the most relevant information.
required_open_webui_version: 0.6.0
requirements: aiohttp, asyncio, requests, beautifulsoup4
version: 0.9.3
license: MIT
"""

import json
import time
import random
from urllib.parse import quote_plus, quote
from typing import Optional, List, Dict, Any, Callable
from pydantic import BaseModel, Field
from bs4 import BeautifulSoup
import unicodedata
import re
import asyncio
import aiohttp
import requests
import urllib.parse

try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None


# ============ AUXILIARY FUNCTIONS ============
def extract_title(text: str) -> Optional[str]:
    match = re.search(r"Title: (.*)\n", text)
    return match.group(1).strip() if match else None


def clean_urls(text: str) -> str:
    return re.sub(
        r"\((http[^)]+(?<!\.pdf)(?<!\.doc)(?<!\.docx)(?<!\.ppt)(?<!\.pptx))\)", "", text
    )


def normalize_url(u: str) -> str:
    return urllib.parse.urldefrag((u or "").strip())[0]


def truncate_to_n_words(text, token_limit):
    tokens = text.split()
    truncated_tokens = tokens[:token_limit]
    return " ".join(truncated_tokens)


def generate_excerpt(content, max_length=200):
    return content[:max_length] + "..." if len(content) > max_length else content


def format_text(original_text):
    """Format text from HTML"""
    soup = BeautifulSoup(original_text, "html.parser")
    formatted_text = soup.get_text(separator=" ", strip=True)
    formatted_text = unicodedata.normalize("NFKC", formatted_text)
    formatted_text = re.sub(r"\s+", " ", formatted_text)
    formatted_text = formatted_text.strip()
    return formatted_text


async def check_arxiv_html_exists(paper_id: str) -> Optional[str]:
    """Check if HTML version exists for arXiv paper, else return PDF"""
    versions_to_try = [
        f"https://arxiv.org/html/{paper_id}v2",
        f"https://arxiv.org/html/{paper_id}v1",
        f"https://arxiv.org/pdf/{paper_id}",
    ]
    async with aiohttp.ClientSession() as session:
        for url in versions_to_try:
            try:
                async with session.head(
                    url,
                    timeout=aiohttp.ClientTimeout(total=10),
                    allow_redirects=True,
                ) as response:
                    if response.status == 200:
                        return url
            except:
                continue
    return None


# ============ EVENT EMITTER ============
class EventEmitter:
    def __init__(self, event_emitter: Callable[[dict], Any] = None):
        self.event_emitter = event_emitter

    async def progress_update(self, description: str):
        await self.emit(description)

    async def error_update(self, description: str):
        await self.emit(description, "error", True)

    async def success_update(self, description: str):
        await self.emit(description, "success", True)

    async def emit(
        self,
        description: str = "Unknown State",
        status: str = "in_progress",
        done: bool = False,
    ):
        if self.event_emitter:
            await self.event_emitter(
                {
                    "type": "status",
                    "data": {
                        "status": status,
                        "description": description,
                        "done": done,
                    },
                }
            )


# ============ CONCURRENCY FUNCTIONS ============
def get_concurrency(jina_api_key: str) -> int:
    """Determines concurrency based on the presence of a Jina API key"""
    key_present = bool(jina_api_key)
    return 5 if key_present else 2


# ============ HTTP FUNCTIONS ============
async def http_get(
    url: str, headers: Dict[str, str], timeout=(5, 30), attempts: int = 3
) -> requests.Response:
    last_exc: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            resp: requests.Response = await asyncio.to_thread(
                requests.get, url, headers=headers, timeout=timeout
            )
            if resp.status_code in (429, 503):
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = float(retry_after)
                    except ValueError:
                        delay = 0.5 * (2 ** (attempt - 1)) + random.random() * 0.2
                else:
                    delay = 0.5 * (2 ** (attempt - 1)) + random.random() * 0.2
                await asyncio.sleep(delay)
                last_exc = requests.HTTPError(f"HTTP {resp.status_code} for {url}")
                continue
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last_exc = e
            if attempt == attempts:
                break
            delay = 0.5 * (2 ** (attempt - 1)) + random.random() * 0.2
            await asyncio.sleep(delay)
    raise last_exc


# ============ JINA FUNCTIONS ============
def build_jina_headers(jina_api_key: str, disable_caching: bool) -> Dict[str, str]:
    """Build headers for Jina."""
    headers = {}
    if jina_api_key:
        headers["Authorization"] = f"Bearer {jina_api_key}"
        headers["X-No-Cache"] = "true" if disable_caching else "false"
        headers["X-With-Generated-Alt"] = "true"
    return headers


async def scrape_one_url_jina(
    url: str,
    jina_api_key: str,
    disable_caching: bool,
    clean_content: bool,
    __event_emitter__: Optional[Callable[[dict], Any]] = None,
) -> Dict[str, Optional[str]]:
    """Scrape a single URL with Jina"""
    emitter = EventEmitter(__event_emitter__)
    url = normalize_url(url)
    await emitter.progress_update(f"Scraping {url}")
    jina_url = f"https://r.jina.ai/{url}"
    headers = build_jina_headers(jina_api_key, disable_caching)

    try:
        response = await http_get(jina_url, headers=headers)
        content = response.text
        if clean_content:
            await emitter.progress_update("Cleaning content...")
            content = clean_urls(content)

        title = extract_title(content)
        await emitter.success_update(f"Successfully scraped {title if title else url}")
        return {
            "url": url,
            "title": title or None,
            "content": content,
            "error": None,
        }
    except Exception as e:
        error_message = f"Error scraping {url}: {str(e)}"
        await emitter.error_update(error_message)
        return {
            "url": url,
            "title": None,
            "content": None,
            "error": error_message,
        }


async def web_scrape_multiple_jina(
    urls: List[str],
    jina_api_key: str,
    disable_caching: bool,
    clean_content: bool,
    __event_emitter__: Optional[Callable[[dict], Any]] = None,
) -> List[Dict[str, Optional[str]]]:
    """Scrape multiple URLs with Jina."""
    emitter = EventEmitter(__event_emitter__)
    limit = get_concurrency(jina_api_key)
    sem = asyncio.Semaphore(limit)
    results: List[Optional[Dict[str, Optional[str]]]] = [None] * len(urls)

    async def run_one(i: int, raw_url: str):
        async with sem:
            res = await scrape_one_url_jina(
                raw_url, jina_api_key, disable_caching, clean_content, __event_emitter__
            )
            results[i] = res

    await asyncio.gather(*(run_one(i, u) for i, u in enumerate(urls)))
    await emitter.success_update(f"Finished scraping {len(urls)} pages")
    return [r for r in results if r is not None]


# ============ BEAUTIFULSOUP SCRAPING FUNCTIONS ============
async def fetch_url_content_bs(
    headers, valves, session, url: str, word_limit: Optional[int] = None
) -> Dict:
    """Fetch content from URL using BeautifulSoup"""
    try:
        async with session.get(
            url, headers=headers, timeout=aiohttp.ClientTimeout(total=120)
        ) as response:
            if response.status == 200:
                html_content = await response.text()
                soup = BeautifulSoup(html_content, "html.parser")
                page_title = soup.title.string if soup.title else "No title found"
                page_title = unicodedata.normalize("NFKC", page_title.strip())

                content_site = format_text(soup.get_text(separator=" ", strip=True))
                actual_word_limit = (
                    word_limit
                    if word_limit is not None
                    else valves.search_scrape_and_summarize_words_limit_2
                )
                truncated_content = truncate_to_n_words(content_site, actual_word_limit)

                return {
                    "title": page_title,
                    "url": url,
                    "content": truncated_content,
                    "excerpt": generate_excerpt(content_site),
                }
            else:
                return {
                    "url": url,
                    "content": f"Failed to retrieve the page. HTTP {response.status}",
                }
    except Exception as e:
        return {
            "url": url,
            "content": f"Failed to retrieve the page. Error: {str(e)}",
        }


async def web_scrape_bs(
    headers, valves, session, urls: List[str], word_limit: Optional[int] = None
) -> List[Dict]:
    """Scrape multiple URLs using BeautifulSoup"""
    tasks = [
        fetch_url_content_bs(headers, valves, session, url, word_limit) for url in urls
    ]
    scraped_data = await asyncio.gather(*tasks)
    return scraped_data


# ============ SEARCH FUNCTIONS ============
def search_searxng(
    query: str,
    base_url: str,
    engines: str = None,
    categories: str = None,
    count: int = 20,
    freshness: str = "",
    lang: str = "en",
    timeout: int = 27,
) -> str:
    """Internal method for performing a SearXNG search"""
    try:
        searxng_base_url = base_url.rstrip("/")
        search_path = "search"
        params = []
        params.append(f"q={quote_plus(query)}")
        params.append(f"engines={quote_plus(engines)}")
        if categories and categories.strip():
            params.append(f"category_{categories.strip()}=on")
        params.append(f"language={quote_plus(lang)}")
        params.append(f"time_range={quote_plus(freshness)}")
        params.append("format=json")

        search_url = f"{searxng_base_url}/{search_path}?{'&'.join(params)}"

        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; OpenWebUI-SecureSearch/1.0)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        }

        response = requests.get(
            search_url,
            headers=headers,
            timeout=timeout,
            verify=True,
        )

        if response.status_code != 200:
            return None

        json_data = response.json()
        results = []

        for i, result in enumerate(json_data.get("results", [])):
            if i >= count:
                break
            results.append(
                {
                    "title": result.get("title", "No title"),
                    "url": result.get("url", "#"),
                    "content": result.get("content", ""),
                    "engine": result.get("engine", "unknown"),
                }
            )

        if not results:
            return None

        return json.dumps(
            {
                "query": query,
                "engines": engines or "all",
                "results": results,
                "count": len(results),
                "status": "success",
                "server": base_url,
            }
        )
    except Exception as e:
        return None


def search_langsearch(
    query: str,
    api_key: str,
    count: int = 10,
    freshness: str = "noLimit",
    timeout: int = 27,
) -> str:
    """Internal method for performing a LangSearch search, fallback."""
    if not api_key:
        return None

    try:
        url = "https://api.langsearch.com/v1/web-search"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "query": query,
            "freshness": freshness,
            "summary": False,
            "count": count,
        }

        response = requests.post(url, headers=headers, json=payload, timeout=timeout)

        if response.status_code != 200:
            return None

        data = response.json()
        results = []

        for item in data.get("data", {}).get("webPages", {}).get("value", []):
            results.append(
                {
                    "title": item.get("name", "No title"),
                    "url": item.get("url", "#"),
                    "content": item.get("snippet", ""),
                    "engine": "langsearch",
                }
            )

        if not results:
            return None

        return json.dumps(
            {
                "query": query,
                "engines": "langsearch",
                "results": results,
                "count": len(results),
                "status": "success_fallback",
                "server": "LangSearch API",
            }
        )
    except Exception as e:
        return None


def search_ddgs_fallback(
    query: str,
    count: int = 10,
    freshness: str = "noLimit",
    timeout: int = 27,
) -> str:
    """Recherche DDGS comme fallback"""
    try:
        from duckduckgo_search import DDGS

        region = "wt-wt"  # recherche mondiale
        with DDGS(timeout=timeout) as ddgs:
            results = ddgs.text(
                keywords=query,
                region=region,
                safesearch="off",
                max_results=count,
            )

            if not results:
                return None

            formatted_results = []
            for res in results[:count]:
                title = res.get("title", "No title")
                url = res.get("href") or res.get("link", "#")
                snippet = res.get("body") or res.get("snippet", "")

                formatted_results.append(
                    {
                        "title": title,
                        "url": url,
                        "content": snippet,
                        "engine": "ddgs",
                    }
                )

            return json.dumps(
                {
                    "query": query,
                    "engines": "ddgs",
                    "results": formatted_results,
                    "count": len(formatted_results),
                    "status": "success_fallback",
                    "server": "DuckDuckGo",
                }
            )
    except Exception as e:
        print(f"Erreur DDGS fallback: {str(e)}")
        return None


async def search_xng_with_fallbacks(
    query: str,
    searxng_url: str,
    langsearch_api_key: str,
    engines: str = "brave,duckduckgo,google",
    categories: str = "general",
    count: int = 30,
    freshness: str = "",
    lang: str = "en",
    timeout: int = 27,
) -> str:
    """Recherche web principale avec SearXNG et fallbacks."""
    if not query or len(query.strip()) < 1:
        return json.dumps({"error": "Query must be non-empty"})

    sanitized_query = query.strip()[:500]

    # 1. SearXNG en premier
    result = search_searxng(
        sanitized_query,
        searxng_url,
        engines,
        categories,
        count,
        freshness,
        lang,
        timeout,
    )
    if result:
        return result

    # 2. Fallback direct à DDGS
    result = search_ddgs_fallback(
        sanitized_query,
        count=count,
        freshness="noLimit",
        timeout=timeout,
    )
    if result:
        return result

    # 3. Dernier fallback : LangSearch
    result = search_langsearch(
        sanitized_query,
        langsearch_api_key,
        count=count,
        freshness="noLimit",
        timeout=timeout,
    )
    if result:
        return result

    # 4. Tout a échoué
    return json.dumps(
        {
            "error": "All search services failed",
            "query": sanitized_query,
            "primary_server": searxng_url,
        }
    )


# ============ LLM FUNCTIONS ============
async def call_llm(
    messages: List[Dict],
    model: str,
    base_url: str,
    api_key: str,
    fallback_models_str: str,
    temperature: float,
    max_tokens: int,
    enable_reasoning: bool = False,
    provider_order: str = "",
    enable_throughput_sort: bool = False,
    ignore_provider: str = "",
) -> str:
    """Call LLM with the given messages"""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    if fallback_models_str and fallback_models_str.strip():
        fallback_list = [m.strip() for m in fallback_models_str.split(",") if m.strip()]
        models_array = [model] + fallback_list
        payload["models"] = models_array
        print(f"✓ Fallback models configured: {models_array}")

    if enable_reasoning:
        payload["reasoning"] = {"enabled": True}

    provider_config = {}
    if provider_order and provider_order.strip():
        providers_list = [p.strip() for p in provider_order.split(",")]
        provider_config["order"] = providers_list
        print(f"✓ Provider order configured: {providers_list}")

    if enable_throughput_sort:
        provider_config["sort"] = "throughput"
        if ignore_provider and ignore_provider.strip():
            provider_config["ignore"] = [
                p.strip() for p in ignore_provider.split(",") if p.strip()
            ]
            print(f"✓ Throughput sort enabled, ignoring: {ignore_provider}")
        else:
            print(f"✓ Throughput sort enabled, no provider ignored")

    if provider_config:
        payload["provider"] = provider_config

    print(f"URL: {base_url.rstrip('/')}/chat/completions")
    print(f"Model: {model}")
    if provider_config:
        print(f"Provider config: {provider_config}")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json=payload,
                timeout=260,
            ) as response:
                print(f"Status: {response.status}")
                response_text = await response.text()
                if response.status == 200:
                    result = await response.json()
                    return result["choices"][0]["message"]["content"]
                else:
                    return f"Error: HTTP {response.status} - {response_text}"
    except Exception as e:
        return f"Error calling LLM: {str(e)}"


# ============ QUERY PROCESSING FUNCTIONS (MODIFIED) ============
async def process_query_1_scraping_only(
    valves,
    headers,
    query_1: str,
    long_query: str,
    max_urls_to_scrape_1: Optional[int],
    search_language_1: Optional[str],
    search_engines_1: Optional[str],
    search_categories_1: Optional[str],
    freshness_1: Optional[str],
    __event_emitter__: Optional[Callable[[dict], Any]],
) -> Dict:
    """Process query 1 using Jina scraper - SCRAPING ONLY (no LLM 2)"""
    if not query_1 or len(query_1.strip()) < 1:
        return {"error": "Query 1 must be non-empty", "query_num": 1}

    urls_limit = (
        max_urls_to_scrape_1
        if max_urls_to_scrape_1 is not None
        else valves.max_urls_to_scrape_1
    )
    lang = (
        search_language_1
        if search_language_1 is not None
        else valves.default_search_language_1
    )
    engines = (
        search_engines_1
        if search_engines_1 is not None
        else valves.default_search_engines_1
    )
    categories = (
        search_categories_1
        if search_categories_1 is not None
        else valves.default_search_categories_1
    )
    time_range = freshness_1 if freshness_1 is not None else ""
    llm_query = long_query if long_query and long_query.strip() else query_1

    # Step 1: Perform initial web search
    print(
        f"[Query 1] Step 1: Perform initial web search for '{query_1}' (engines: {engines}, category: {categories}, language: {lang}, freshness: {time_range or 'all time'})..."
    )
    initial_search_results_str = await search_xng_with_fallbacks(
        query_1,
        valves.searxng_url,
        valves.langsearch_api_key,
        engines=engines,
        categories=categories,
        count=valves.max_search_results_1,
        freshness=time_range,
        lang=lang,
        timeout=valves.request_timeout,
    )

    initial_search_results = json.loads(initial_search_results_str)
    if initial_search_results.get("error"):
        return {
            "error": f"Search failed: {initial_search_results['error']}",
            "query_num": 1,
        }

    # Format results for LLM 1
    search_results_for_llm = []
    all_search_urls = []
    for i, res in enumerate(initial_search_results.get("results", [])):
        search_results_for_llm.append(
            f"{i+1}. Title: {res['title']}\n   URL: {res['url']}\n   Content Snippet: {res['content']}\n"
        )
        all_search_urls.append(res["url"])

    if not search_results_for_llm:
        return {"message": "No relevant search results found.", "query_num": 1}

    # Step 2: Use LLM 1 to select relevant URLs
    print(f"[Query 1] Step 2: Use LLM 1 to select relevant URLs (max: {urls_limit})...")
    selected_urls = []
    llm1_success = False

    llm1_prompt = [
        {
            "role": "system",
            "content": f"You are an intelligent web search assistant. Given a user query and a list of search results, select the {urls_limit} most relevant URLs that are likely to contain the answer to the query. **Provide ONLY the URLs, one per line, nothing else. No explanations, no numbering, just the raw URLs**. If fewer than {urls_limit} relevant URLs are found, return all relevant ones.",
        },
        {
            "role": "user",
            "content": f"Query: {llm_query}\n\nSearch Results:\n"
            + "\n".join(search_results_for_llm)
            + f"\n\nList ONLY the {urls_limit} most relevant URLs (one per line):",
        },
    ]

    try:
        selected_urls_str = await call_llm(
            llm1_prompt,
            valves.llm_1_model,
            valves.llm_1_base_url,
            valves.llm_1_api_key,
            valves.llm_1_fallback_models,
            temperature=0.3,
            max_tokens=5000,
        )
        if "Error" not in selected_urls_str:
            url_pattern = r'https?://[^\s<>"{}|\^`\[\]]+'
            potential_urls = re.findall(url_pattern, selected_urls_str)
            for url in potential_urls:
                url_clean = url.strip().rstrip(",").rstrip("/")
                normalized_search_urls = [u.rstrip("/") for u in all_search_urls]
                if url_clean in normalized_search_urls:
                    selected_urls.append(url_clean)
                    if len(selected_urls) >= urls_limit:
                        break
            if selected_urls:
                llm1_success = True
                print(f"[Query 1] ✅ LLM1 selected {len(selected_urls)} valid URLs")
    except Exception as e:
        print(f"[Query 1] ⚠️ Error during call to LLM1: {str(e)}")

    # Fallback - Use first search URLs if LLM1 fails
    if not llm1_success or not selected_urls:
        print(f"[Query 1] ⚠️ Fallback enabled: use of first {urls_limit} search URLs")
        selected_urls = all_search_urls[:urls_limit]

    if not selected_urls:
        return {"message": "No URLs available for scraping.", "query_num": 1}

    print(f"[Query 1] URLs to scrape: {selected_urls}")

    # Step 2.5: Convert arXiv URLs to HTML format if available
    print("[Query 1] Step 2.5: Converting arXiv URLs to HTML format if available...")
    converted_urls = []
    arxiv_conversions = 0
    for url in selected_urls:
        arxiv_pattern = r"https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/(\d+\.\d+)(?:v\d+)?"
        match = re.match(arxiv_pattern, url)
        if match:
            paper_id = match.group(1)
            print(
                f"[Query 1]   🔍 Checking HTML or PDF version for arXiv {paper_id}..."
            )
            html_url = await check_arxiv_html_exists(paper_id)
            if html_url:
                print(f"[Query 1]   ✅ HTML or PDF available: {html_url}")
                converted_urls.append(html_url)
                arxiv_conversions += 1
            else:
                print(
                    f"[Query 1]   ⚠️ No HTML or PDF version for {paper_id}, keeping original URL"
                )
                converted_urls.append(url)
        else:
            converted_urls.append(url)

    if arxiv_conversions > 0:
        print(f"[Query 1] 📊 {arxiv_conversions} arXiv URL(s) converted to HTML format")
    selected_urls = converted_urls

    # Step 3: Scrape the selected URLs with Jina
    print("[Query 1] Step 3: Scrape the selected URLs with Jina...")
    scraped_content = await web_scrape_multiple_jina(
        selected_urls,
        valves.JINA_API_KEY,
        valves.DISABLE_JINA_CACHING,
        valves.CLEAN_JINA_CONTENT,
        __event_emitter__,
    )

    if not scraped_content:
        return {
            "message": "No content could be scraped from the selected URLs.",
            "query_num": 1,
        }

    # Format scraped content
    content_for_llm = []
    failed_scrapes = 0
    for content_item in scraped_content:
        if content_item.get("error") is None and content_item.get("content"):
            content_text = content_item.get("content", "")
            if len(content_text.strip()) > 100:
                truncated_content = truncate_to_n_words(
                    content_text,
                    valves.search_scrape_and_summarize_words_limit_1,
                )
                content_for_llm.append(
                    {
                        "source": f"{content_item.get('title', 'Unknown')} ({content_item['url']})",
                        "content": truncated_content,
                        "url": content_item["url"],
                        "scraper": "Jina",
                    }
                )
            else:
                failed_scrapes += 1
                print(f"[Query 1] ⚠️ Content too short for {content_item['url']}")
        else:
            failed_scrapes += 1
            error_msg = content_item.get("error", "Unknown error")
            print(
                f"[Query 1] ❌ Scraping failed for {content_item['url']}: {error_msg}"
            )

    print(
        f"[Query 1] 📊 Scraping statistics: {len(content_for_llm)} successes, {failed_scrapes} failures"
    )

    # Return scraping results (without LLM 2 processing)
    return {
        "query": query_1,
        "llm_query_used": llm_query,
        "search_language": lang,
        "search_engines": engines,
        "search_categories": categories,
        "freshness": time_range,
        "max_urls_scraped": urls_limit,
        "scraped_content": content_for_llm,
        "sources_found": len(content_for_llm),
        "urls_scraped": selected_urls,
        "arxiv_html_conversions": arxiv_conversions,
        "llm1_used": llm1_success,
        "fallback_used": not llm1_success,
        "scraping_stats": {
            "successful": len(content_for_llm),
            "failed": failed_scrapes,
        },
        "search_results_count": len(initial_search_results.get("results", [])),
        "status": "success",
        "query_num": 1,
        "scraper_used": "Jina",
    }


# ============ NOUVEAU: FONCTION POUR SÉLECTIONNER LES URLs (sans scraping) ============
async def select_urls_with_llm1(
    valves,
    query: str,
    long_query: str,
    max_urls_to_scrape: int,
    search_language: str,
    search_engines: str,
    search_categories: str,
    freshness: str,
    query_num: int,
    __event_emitter__: Optional[Callable[[dict], Any]],
) -> Dict:
    """Sélectionne les URLs avec LLM 1 sans faire le scraping"""
    if not query or len(query.strip()) < 1:
        return {
            "error": f"Query {query_num} must be non-empty",
            "query_num": query_num,
            "selected_urls": [],
        }

    llm_query = long_query if long_query and long_query.strip() else query

    # Step 1: Perform initial web search
    print(
        f"[Query {query_num}] Step 1: Perform initial web search for '{query}' (engines: {search_engines}, category: {search_categories}, language: {search_language}, freshness: {freshness or 'all time'})..."
    )
    initial_search_results_str = await search_xng_with_fallbacks(
        query,
        valves.searxng_url,
        valves.langsearch_api_key,
        engines=search_engines,
        categories=search_categories,
        count=(
            valves.max_search_results_2
            if query_num in [2, 3]
            else valves.max_search_results_1
        ),
        freshness=freshness,
        lang=search_language,
        timeout=valves.request_timeout,
    )

    initial_search_results = json.loads(initial_search_results_str)
    if initial_search_results.get("error"):
        return {
            "error": f"Search failed: {initial_search_results['error']}",
            "query_num": query_num,
            "selected_urls": [],
        }

    # Format results for LLM 1
    search_results_for_llm = []
    all_search_urls = []
    for i, res in enumerate(initial_search_results.get("results", [])):
        search_results_for_llm.append(
            f"{i+1}. Title: {res['title']}\n   URL: {res['url']}\n   Content Snippet: {res['content']}\n"
        )
        all_search_urls.append(res["url"])

    if not search_results_for_llm:
        return {
            "message": "No relevant search results found.",
            "query_num": query_num,
            "selected_urls": [],
        }

    # Step 2: Use LLM 1 to select relevant URLs
    print(
        f"[Query {query_num}] Step 2: Use LLM 1 to select relevant URLs (max: {max_urls_to_scrape})..."
    )
    selected_urls = []
    llm1_success = False

    arxiv_priority = "choose arxiv URLs as a priority" if query_num in [2, 3] else ""

    llm1_prompt = [
        {
            "role": "system",
            "content": f"You are an intelligent web search assistant. Given a user query and a list of search results, select the {max_urls_to_scrape} most relevant URLs that are likely to contain the answer to the query{', ' + arxiv_priority if arxiv_priority else ''}. **Provide ONLY the URLs, one per line, nothing else. No explanations, no numbering, just the raw URLs**. If fewer than {max_urls_to_scrape} relevant URLs are found, return all relevant ones.",
        },
        {
            "role": "user",
            "content": f"Query: {llm_query}\n\nSearch Results:\n"
            + "\n".join(search_results_for_llm)
            + f"\n\nList ONLY the {max_urls_to_scrape} most relevant URLs (one per line):",
        },
    ]

    try:
        selected_urls_str = await call_llm(
            llm1_prompt,
            valves.llm_1_model,
            valves.llm_1_base_url,
            valves.llm_1_api_key,
            valves.llm_1_fallback_models,
            temperature=0.3,
            max_tokens=5000,
        )
        if "Error" not in selected_urls_str:
            url_pattern = r'https?://[^\s<>"{}|\^`\[\]]+'
            potential_urls = re.findall(url_pattern, selected_urls_str)
            for url in potential_urls:
                url_clean = url.strip().rstrip(",").rstrip("/")
                normalized_search_urls = [u.rstrip("/") for u in all_search_urls]
                if url_clean in normalized_search_urls:
                    selected_urls.append(url_clean)
                    if len(selected_urls) >= max_urls_to_scrape:
                        break
            if selected_urls:
                llm1_success = True
                print(
                    f"[Query {query_num}] ✅ LLM1 selected {len(selected_urls)} valid URLs"
                )
    except Exception as e:
        print(f"[Query {query_num}] ⚠️ Error during call to LLM1: {str(e)}")

    # Fallback - Use first search URLs if LLM1 fails
    if not llm1_success or not selected_urls:
        print(
            f"[Query {query_num}] ⚠️ Fallback enabled: use of first {max_urls_to_scrape} search URLs"
        )
        selected_urls = all_search_urls[:max_urls_to_scrape]

    if not selected_urls:
        return {
            "message": "No URLs available for scraping.",
            "query_num": query_num,
            "selected_urls": [],
        }

    print(f"[Query {query_num}] URLs selected: {selected_urls}")

    # Convert arXiv URLs
    print(f"[Query {query_num}] Converting arXiv URLs to HTML format if available...")
    converted_urls = []
    arxiv_conversions = 0
    for url in selected_urls:
        arxiv_pattern = r"https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/(\d+\.\d+)(?:v\d+)?"
        match = re.match(arxiv_pattern, url)
        if match:
            paper_id = match.group(1)
            print(
                f"[Query {query_num}]   🔍 Checking HTML version for arXiv {paper_id}..."
            )
            html_url = await check_arxiv_html_exists(paper_id)
            if html_url:
                print(f"[Query {query_num}]   ✅ HTML available: {html_url}")
                converted_urls.append(html_url)
                arxiv_conversions += 1
            else:
                print(
                    f"[Query {query_num}]   ⚠️ No HTML version for {paper_id}, keeping original URL"
                )
                converted_urls.append(url)
        else:
            converted_urls.append(url)

    if arxiv_conversions > 0:
        print(
            f"[Query {query_num}] 📊 Converted {arxiv_conversions} arXiv URL(s) to HTML format"
        )

    return {
        "query": query,
        "llm_query_used": llm_query,
        "search_language": search_language,
        "search_engines": search_engines,
        "search_categories": search_categories,
        "freshness": freshness,
        "selected_urls": converted_urls,
        "arxiv_html_conversions": arxiv_conversions,
        "llm1_used": llm1_success,
        "fallback_used": not llm1_success,
        "search_results_count": len(initial_search_results.get("results", [])),
        "status": "success",
        "query_num": query_num,
    }


# ============ SCRAPING BeautifulSoup pour query 2 et 3 combinées ============
async def scrape_combined_bs_queries(
    valves,
    headers,
    result_2: Dict,
    result_3: Dict,
    __event_emitter__: Optional[Callable[[dict], Any]],
) -> Dict:
    """Scrappe les URLs de query_2 et query_3 combinées - PDFs avec Jina, autres avec BeautifulSoup"""
    # Combiner les URLs des deux requêtes
    all_urls = []
    query_2_urls = result_2.get("selected_urls", [])
    query_3_urls = result_3.get("selected_urls", [])
    all_urls.extend(query_2_urls)
    all_urls.extend(query_3_urls)

    if not all_urls:
        return {
            "message": "No URLs to scrape from queries 2 and 3.",
            "scraped_content": [],
        }

    # ========== Séparer les URLs PDF des URLs normales ==========
    pdf_urls = []
    normal_urls = []

    for url in all_urls:
        # Vérifier si l'URL est un PDF
        url_lower = url.lower()
        if url_lower.endswith(".pdf") or "arxiv.org/pdf/" in url_lower:
            pdf_urls.append(url)
        else:
            normal_urls.append(url)

    print(f"[Query 2+3] Scraping {len(all_urls)} combined URLs...")
    print(f"[Query 2+3]   - PDF URLs (→ Jina): {len(pdf_urls)}")
    print(f"[Query 2+3]   - Normal URLs (→ BeautifulSoup): {len(normal_urls)}")
    print(f"[Query 2+3]   - From Query 2: {len(query_2_urls)} URLs")
    print(f"[Query 2+3]   - From Query 3: {len(query_3_urls)} URLs")

    # Afficher les URLs PDF détectées
    if pdf_urls:
        print(f"[Query 2+3] 📄 PDF URLs detected:")
        for pdf_url in pdf_urls:
            print(f"[Query 2+3]   - {pdf_url}")

    content_for_llm = []
    failed_scrapes = 0

    # ========== SCRAPING DES PDFs AVEC JINA ==========
    if pdf_urls:
        print(f"[Query 2+3] 🔧 Scraping {len(pdf_urls)} PDF URL(s) with Jina...")
        try:
            jina_results = await web_scrape_multiple_jina(
                pdf_urls,
                valves.JINA_API_KEY,
                valves.DISABLE_JINA_CACHING,
                valves.CLEAN_JINA_CONTENT,
                __event_emitter__,
            )

            # Traiter les résultats Jina
            for content_item in jina_results:
                url = content_item.get("url", "")
                source_query = "query_2" if url in query_2_urls else "query_3"

                if content_item.get("error") is None and content_item.get("content"):
                    content_text = content_item.get("content", "")
                    if len(content_text.strip()) > 100:
                        truncated_content = truncate_to_n_words(
                            content_text,
                            valves.search_scrape_and_summarize_words_limit_2,
                        )
                        content_for_llm.append(
                            {
                                "source": f"{content_item.get('title', 'Unknown')} ({url})",
                                "content": truncated_content,
                                "url": url,
                                "scraper": "Jina (PDF)",
                                "source_query": source_query,
                            }
                        )
                        print(
                            f"[Query 2+3] ✅ Successfully scraped PDF with Jina: {url}"
                        )
                    else:
                        failed_scrapes += 1
                        print(f"[Query 2+3] ⚠️ Content too short for PDF: {url}")
                else:
                    failed_scrapes += 1
                    error_msg = content_item.get("error", "Unknown error")
                    print(
                        f"[Query 2+3] ❌ Jina scraping failed for PDF {url}: {error_msg}"
                    )
        except Exception as e:
            print(f"[Query 2+3] ❌ Error during Jina PDF scraping: {str(e)}")
            failed_scrapes += len(pdf_urls)

    # ========== SCRAPING DES URLs NORMALES AVEC BEAUTIFULSOUP ==========
    if normal_urls:
        print(
            f"[Query 2+3] 🔧 Scraping {len(normal_urls)} normal URL(s) with BeautifulSoup..."
        )
        try:
            async with aiohttp.ClientSession() as session:
                bs_results = await web_scrape_bs(headers, valves, session, normal_urls)

            # Traiter les résultats BeautifulSoup
            for content_item in bs_results:
                content_text = content_item.get("content", "")
                url = content_item.get("url", "")
                source_query = "query_2" if url in query_2_urls else "query_3"

                if (
                    "Failed to retrieve" not in content_text
                    and len(content_text.strip()) > 50
                ):
                    content_for_llm.append(
                        {
                            "source": f"{content_item.get('title', 'Unknown')} ({url})",
                            "content": content_text,
                            "url": url,
                            "scraper": "BeautifulSoup",
                            "source_query": source_query,
                        }
                    )
                    print(
                        f"[Query 2+3] ✅ Successfully scraped with BeautifulSoup: {url}"
                    )
                else:
                    failed_scrapes += 1
                    print(f"[Query 2+3] ⚠️ Failed to scrape with BeautifulSoup: {url}")
        except Exception as e:
            print(f"[Query 2+3] ❌ Error during BeautifulSoup scraping: {str(e)}")
            failed_scrapes += len(normal_urls)

    print(
        f"[Query 2+3] 📊 Scraping statistics: {len(content_for_llm)} successes, {failed_scrapes} failures"
    )
    print(f"[Query 2+3]   - PDFs scraped with Jina: {len(pdf_urls)}")
    print(
        f"[Query 2+3]   - Normal pages scraped with BeautifulSoup: {len(normal_urls)}"
    )

    return {
        "scraped_content": content_for_llm,
        "sources_found": len(content_for_llm),
        "urls_scraped": all_urls,
        "query_2_urls_count": len(query_2_urls),
        "query_3_urls_count": len(query_3_urls),
        "pdf_urls_count": len(pdf_urls),
        "normal_urls_count": len(normal_urls),
        "scraping_stats": {
            "successful": len(content_for_llm),
            "failed": failed_scrapes,
            "pdf_scraped_with_jina": len(pdf_urls),
            "normal_scraped_with_bs": len(normal_urls),
        },
        "status": "success",
        "scraper_used": "BeautifulSoup + Jina (for PDFs)",
    }


async def process_combined_results(
    valves,
    result_1: Dict,
    result_2_3_combined: Dict,
    long_query: str,
    __event_emitter__: Optional[Callable[[dict], Any]],
) -> Dict:
    """Process combined results from all scrapers with single LLM 2 call"""
    print("[Combined] Step 4: Processing combined results from all scrapers...")

    # Combine all scraped content
    all_content = []
    all_urls = []

    # Add content from query 1 (Jina)
    if result_1.get("scraped_content"):
        all_content.extend(result_1["scraped_content"])
        all_urls.extend(result_1.get("urls_scraped", []))

    # Add content from query 2+3 (BeautifulSoup combined)
    if result_2_3_combined.get("scraped_content"):
        all_content.extend(result_2_3_combined["scraped_content"])
        all_urls.extend(result_2_3_combined.get("urls_scraped", []))

    if not all_content:
        return {"error": "No valid content from any scraper", "status": "failed"}

    # Format combined content for LLM 2
    content_for_llm = []
    for item in all_content:
        source_query_info = (
            f" [from {item.get('source_query', 'unknown')}]"
            if item.get("source_query")
            else ""
        )
        content_for_llm.append(
            f"[Scraper: {item['scraper']}{source_query_info}]\nSource: {item['source']}\nContent: {item['content']}\n"
        )

    print(f"[Combined] Total sources to analyze: {len(all_content)}")
    print(
        f"[Combined] Scrapers used: Jina ({len(result_1.get('scraped_content', []))}) + BeautifulSoup ({len(result_2_3_combined.get('scraped_content', []))})"
    )

    # Use LLM 2 to extract and summarize + DETECT PDFs
    print("[Combined] Calling LLM 2 for combined analysis and PDF detection...")
    llm_query = (
        long_query if long_query and long_query.strip() else "Combined search query"
    )

    llm2_prompt = [
        {
            "role": "system",
            "content": """            

[ROLE AND CONTEXT]
You are a Precision Information Extractor and Fidelity Curator. Your expertise lies in identifying and isolating relevant information with absolute fidelity to the source material. You function as a high-precision filter.

[EXECUTION METHODOLOGY]
Your mission comprises three distinct phases that must be executed sequentially and presented separately:

**PHASE 1: EXTRACTION AND ORGANIZATION BY SOURCE**

Identify and copy only the specific segments that serve as direct evidence or direct answers to the user's query. Do not include surrounding text unless it changes the meaning. If the source is not in English, provide an accurate English translation preserving original meaning. Synthesis is not allowed; extract specific excerpts only.

**CRITICAL ORGANIZATION REQUIREMENT:** Structure your entire response by source. For each URL, create a self-contained section with ALL information from that source before moving to the next. Do not merge, intermix, or synthesize across sources in this phase.

For each source, include these headers:
- **URL**: Web address
- **Author(s)**: Author name(s) if available
- **Type**: (Website / Academic paper / Blog post / Wikipedia article / etc.)
- **Publisher/Journal**: For academic papers, books, or articles
- **Date**: Publication or last update date if available

---

**PHASE 2: CHAIN OF THOUGHT ANALYSIS**

After presenting all source material, execute the following analytical sequence:

**A. Contextualization**
- Situate findings within relevant historical, cultural, technical, or theoretical frameworks
- Map relationships and connections between elements

**B. Critical Analysis**
- Examine underlying meanings, implications, and assumptions
- Identify biases and evaluate methodological strengths/weaknesses

**C. Deepening**
- Develop nuanced understanding beyond surface-level interpretations
- Analyze mechanisms, causal relationships, and system dynamics (transcend authors' stated conclusions)

---

**PHASE 3: PDF DETECTION**

Detect if any page contains ONLY a summary/abstract with a link to download a full PDF.

⚠️ **IMPORTANT DISTINCTION:**
- ✅ EXTRACT: URLs to PDFs that you have NOT yet read (found in abstracts/summaries)
- ❌ DO NOT EXTRACT: URLs of content you have already received and analyzed

**Example of what TO extract:**
- Page shows: "Read full paper: https://example.com/newpaper.pdf" → EXTRACT THIS

**Example of what NOT to extract:**
- You received content from "https://arxiv.org/pdf/1234.56789" → DO NOT extract this URL

---

**OUTPUT FORMAT**

Your response must be a JSON object with exactly two fields:

{
  "answer": "# PHASE 1: SOURCE MATERIAL\n\n## Source 1\n[URL: ...]\n[Scraper Used: ...]\n...\n[Full extracted content]\n\n## Source 2\n[URL: ...]\n...\n[Full extracted content]\n\n# PHASE 2: ANALYSIS\n\n## A. Contextualization\n[Your analysis]\n\n## B. Critical Analysis\n[Your analysis]\n\n## C. Deepening\n[Your analysis]\n\n# PHASE 3: PDF URLs\n[List extracted PDF URLs or state \"No PDF URLs found\"]",
  "pdf_urls_found": ["https://example.com/paper.pdf", "https://arxiv.org/pdf/1234.5678.pdf"]
}

If no PDFs are detected, set `"pdf_urls_found": []` and include "No PDF URLs found" in the Phase 3 section of the answer field.""",
        },
        {
            "role": "user",
            "content": f"Query: {llm_query}\n\nURLs already scraped:\n"
            + "\n".join([f"- {url}" for url in all_urls])
            + "\n\nCombined Scraped Content from Jina and BeautifulSoup:\n"
            + "\n".join(content_for_llm)
            + "\n\nProvide your JSON response:",
        },
    ]

    llm2_response = await call_llm(
        llm2_prompt,
        valves.llm_2_model,
        valves.llm_2_base_url,
        valves.llm_2_api_key,
        valves.llm_2_fallback_models,
        temperature=valves.llm_2_temperature,
        max_tokens=valves.llm_2_max_tokens,
        enable_reasoning=valves.llm_2_enable_reasoning,
        provider_order=valves.llm_2_provider_order,
        enable_throughput_sort=valves.llm_2_enable_throughput_sort,
        ignore_provider=valves.llm_2_ignore_provider,
    )

    if "Error" in llm2_response:
        return {
            "error": f"LLM 2 failed to process combined content: {llm2_response}",
            "status": "failed",
        }

    # Parse LLM 2 JSON response
    try:
        json_match = re.search(r'\{[\s\S]*"answer"[\s\S]*\}', llm2_response)
        if json_match:
            llm2_json = json.loads(json_match.group(0))
        else:
            llm2_json = {
                "answer": llm2_response,
                "pdf_urls_found": [],
            }
    except json.JSONDecodeError:
        llm2_json = {
            "answer": llm2_response,
            "pdf_urls_found": [],
        }

    final_answer = llm2_json.get("answer", llm2_response)
    pdf_urls_found = llm2_json.get("pdf_urls_found", [])

    # Filter already scraped URLs
    already_scraped = set(all_urls)
    new_pdf_urls = [url for url in pdf_urls_found if url not in already_scraped]

    if new_pdf_urls and len(new_pdf_urls) != len(pdf_urls_found):
        filtered_count = len(pdf_urls_found) - len(new_pdf_urls)
        print(f"[Combined] 🔍 Filtered out {filtered_count} already-scraped URL(s)")

    # Return combined analysis
    return {
        "answer": final_answer,
        "combined_sources": len(all_content),
        "sources_breakdown": {
            "jina": len(result_1.get("scraped_content", [])),
            "beautifulsoup_combined": len(
                result_2_3_combined.get("scraped_content", [])
            ),
        },
        "pdf_urls_detected": new_pdf_urls,
        "pdf_detection_info": {
            "total_detected": len(pdf_urls_found),
            "filtered_out": len(pdf_urls_found) - len(new_pdf_urls),
            "message": (
                "These PDFs can be scraped later"
                if new_pdf_urls
                else "No additional PDFs detected"
            ),
        },
        "status": "success",
    }


# ============ MAIN TOOLS CLASS ============
class Tools:
    def __init__(self):
        self.valves = self.Valves()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 15; SM-S931U Build/AP3A.240905.015.A2; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/132.0.6834.163 Mobile Safari/537.36"
        }

    class Valves(BaseModel):
        # ========== SearXNG Configuration ==========
        searxng_url: str = Field(
            default="http://host.docker.internal:8080",
            description="SearXNG base URL",
        )
        request_timeout: int = Field(default=27, description="Request timeout")

        # ========== LangSearch API Configuration ==========
        langsearch_api_key: str = Field(
            default="",
            description="Your LangSearch API key (used as fallback for query 1)",
        )

        # ========== Query 1 Configuration (Jina) ==========
        max_search_results_1: int = Field(
            default=37, description="Maximum number of search results for query 1"
        )
        max_urls_to_scrape_1: int = Field(
            default=5,
            description="Default maximum number of URLs to scrape for query 1",
        )
        default_search_language_1: str = Field(
            default="en",
            description="Default language for SearXNG searches (query 1)",
        )
        default_search_engines_1: str = Field(
            default="",
            description="Default search engines for SearXNG searches (query 1)",
        )
        default_search_categories_1: str = Field(
            default="general",
            description="Default search category for SearXNG searches (general, news or science) (query 1)",
        )

        # ========== Query 2 Configuration (BeautifulSoup) ==========
        max_search_results_2: int = Field(
            default=30, description="Maximum number of search results for query 2"
        )
        max_urls_to_scrape_2: int = Field(
            default=4,
            description="Default maximum number of URLs to scrape for query 2",
        )
        default_search_language_2: str = Field(
            default="en",
            description="Default language for SearXNG search (query 2)",
        )
        default_search_engines_2: str = Field(
            default="",
            description="Default search engines for SearXNG searches (query 2)",
        )
        default_search_categories_2: str = Field(
            default="general",
            description="Default search category for SearXNG searches (general, news or science) (query 2)",
        )

        # ========== NOUVEAU: Query 3 Configuration (BeautifulSoup) ==========
        max_urls_to_scrape_3: int = Field(
            default=4,
            description="Default maximum number of URLs to scrape for query 3",
        )
        default_search_language_3: str = Field(
            default="en",
            description="Default language for SearXNG search (query 3)",
        )
        default_search_engines_3: str = Field(
            default="",
            description="Default search engines for SearXNG searches (query 3)",
        )
        default_search_categories_3: str = Field(
            default="general",
            description="Default search category for SearXNG searches (general, news or science) (query 3)",
        )

        # ========== Jina API Configuration ==========
        JINA_API_KEY: str = Field(
            default="",
            description="Optional Jina API key for query 1",
        )
        DISABLE_JINA_CACHING: bool = Field(
            default=False,
            description="Bypass Jina Cache when scraping (requires API key)",
        )
        CLEAN_JINA_CONTENT: bool = Field(
            default=True,
            description="Remove links and image urls from scraped content (reduces tokens)",
        )

        # ========== Scraping Configuration ==========
        search_scrape_and_summarize_words_limit_1: int = Field(
            default=7500, description="Word limit for smart search function (query 1)"
        )
        search_scrape_and_summarize_words_limit_2: int = Field(
            default=7500,
            description="Word limit for smart search function (query 2 and 3)",
        )

        # ========== Configuration LLM 1 ==========
        llm_1_model: str = Field(
            default="mistralai/ministral-8b-2512",
            description="LLM 1 Model for URL selection",
        )
        llm_1_base_url: str = Field(
            default="https://openrouter.ai/api/v1",
            description="Base URL for LLM 1",
        )
        llm_1_api_key: str = Field(
            default="",
            description="API key for LLM 1",
        )
        llm_1_fallback_models: str = Field(
            default="deepseek/deepseek-r1-0528-qwen3-8b,meta-llama/llama-3.1-8b-instruct",
            description="Comma-separated list of fallback models for LLM 1",
        )

        # ========== Configuration LLM 2 ==========
        llm_2_model: str = Field(
            default="amazon/nova-2-lite-v1:free",
            description="Model for content summarization",
        )
        llm_2_base_url: str = Field(
            default="https://openrouter.ai/api/v1",
            description="Base URL for LLM 2",
        )
        llm_2_api_key: str = Field(
            default="",
            description="API key for LLM 2",
        )
        llm_2_fallback_models: str = Field(
            default="x-ai/grok-4.1-fast,minimax/minimax-m2",
            description="Comma-separated list of fallback models for LLM 2",
        )

        # ========== Paramètres LLM 2 ==========
        llm_2_temperature: float = Field(
            default=0.35,
            description="Temperature for LLM 2",
        )
        llm_2_max_tokens: int = Field(
            default=11000,
            description="Max tokens for LLM 2",
        )
        llm_2_enable_reasoning: bool = Field(
            default=False,
            description="Enable reasoning mode for LLM 2",
        )
        llm_2_provider_order: str = Field(
            default="",
            description="List of providers in order of preference",
        )
        llm_2_enable_throughput_sort: bool = Field(
            default=False,
            description="Enable sorting by throughput for LLM 2",
        )
        llm_2_ignore_provider: str = Field(
            default="",
            description="Provider to ignore for LLM 2",
        )

    async def three_smart_searches(
        self,
        query_1: Optional[str] = None,
        query_2: Optional[str] = None,
        query_3: Optional[str] = None,
        long_query: Optional[str] = None,
        max_urls_to_scrape_1: Optional[int] = None,
        max_urls_to_scrape_2: Optional[int] = None,
        max_urls_to_scrape_3: Optional[int] = None,
        search_language_1: Optional[str] = None,
        search_language_2: Optional[str] = None,
        search_language_3: Optional[str] = None,
        search_engines_1: Optional[str] = None,
        search_engines_2: Optional[str] = None,
        search_engines_3: Optional[str] = None,
        search_categories_1: Optional[str] = None,
        search_categories_2: Optional[str] = None,
        search_categories_3: Optional[str] = None,
        freshness_1: Optional[str] = None,
        freshness_2: Optional[str] = None,
        freshness_3: Optional[str] = None,
        __event_emitter__: Optional[Callable[[dict], Any]] = None,
    ) -> str:
        """
        Performs three intelligent web searches in parallel and combines results.

        Process:
        1. Performs initial web
        2. Uses LLM 1 to select the most relevant URLs
        3. Scrapes the content:
        4. COMBINES all scraped content and uses single LLM to extract and summarize relevant information from ALL sources

        :param query_1: First search query
        :param query_2: Second search query
        :param query_3: Third search query
        :param long_query: Detailed query used by the 2 LLMs
        :param max_urls_to_scrape_1: Max URLs for query 1
        :param max_urls_to_scrape_2: Max URLs for query 2
        :param max_urls_to_scrape_3: Max URLs for query 3
        :param search_language_1/2/3: Language for each query (default 'en')
        :param search_engines_1/2/3: Engines for each query
        :param search_categories_1/2/3: Category for each query (default 'general')
        :param freshness_1/2/3: Time range for each query
        """
        # Validate that at least one query is provided
        if not query_1 and not query_2 and not query_3:
            return json.dumps(
                {
                    "error": "At least one of query_1, query_2, or query_3 must be provided"
                }
            )

        # Phase 1: URL Selection with LLM 1 (parallel for all queries)
        tasks = []
        has_query_1 = False
        has_query_2 = False
        has_query_3 = False

        if query_1:
            has_query_1 = True
            tasks.append(
                process_query_1_scraping_only(
                    self.valves,
                    self.headers,
                    query_1,
                    long_query or "",
                    max_urls_to_scrape_1,
                    search_language_1,
                    search_engines_1,
                    search_categories_1,
                    freshness_1,
                    __event_emitter__,
                )
            )

        if query_2:
            has_query_2 = True
            urls_limit_2 = (
                max_urls_to_scrape_2
                if max_urls_to_scrape_2 is not None
                else self.valves.max_urls_to_scrape_2
            )
            lang_2 = (
                search_language_2
                if search_language_2 is not None
                else self.valves.default_search_language_2
            )
            engines_2 = (
                search_engines_2
                if search_engines_2 is not None
                else self.valves.default_search_engines_2
            )
            categories_2 = (
                search_categories_2
                if search_categories_2 is not None
                else self.valves.default_search_categories_2
            )
            freshness_2_val = freshness_2 if freshness_2 is not None else ""

            tasks.append(
                select_urls_with_llm1(
                    self.valves,
                    query_2,
                    long_query or "",
                    urls_limit_2,
                    lang_2,
                    engines_2,
                    categories_2,
                    freshness_2_val,
                    2,
                    __event_emitter__,
                )
            )

        if query_3:
            has_query_3 = True
            urls_limit_3 = (
                max_urls_to_scrape_3
                if max_urls_to_scrape_3 is not None
                else self.valves.max_urls_to_scrape_3
            )
            lang_3 = (
                search_language_3
                if search_language_3 is not None
                else self.valves.default_search_language_3
            )
            engines_3 = (
                search_engines_3
                if search_engines_3 is not None
                else self.valves.default_search_engines_3
            )
            categories_3 = (
                search_categories_3
                if search_categories_3 is not None
                else self.valves.default_search_categories_3
            )
            freshness_3_val = freshness_3 if freshness_3 is not None else ""

            tasks.append(
                select_urls_with_llm1(
                    self.valves,
                    query_3,
                    long_query or "",
                    urls_limit_3,
                    lang_3,
                    engines_3,
                    categories_3,
                    freshness_3_val,
                    3,
                    __event_emitter__,
                )
            )

        # Execute URL selection in parallel
        print("=" * 80)
        print("PHASE 1: PARALLEL URL SELECTION WITH LLM 1...")
        print("=" * 80)
        url_selection_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Separate URL selection results
        result_1 = None
        result_2_urls = None
        result_3_urls = None

        result_index = 0
        if has_query_1:
            result_1 = (
                url_selection_results[result_index]
                if not isinstance(url_selection_results[result_index], Exception)
                else {
                    "error": str(url_selection_results[result_index]),
                    "scraped_content": [],
                    "urls_scraped": [],
                }
            )
            result_index += 1

        if has_query_2:
            result_2_urls = (
                url_selection_results[result_index]
                if not isinstance(url_selection_results[result_index], Exception)
                else {
                    "error": str(url_selection_results[result_index]),
                    "selected_urls": [],
                }
            )
            result_index += 1

        if has_query_3:
            result_3_urls = (
                url_selection_results[result_index]
                if not isinstance(url_selection_results[result_index], Exception)
                else {
                    "error": str(url_selection_results[result_index]),
                    "selected_urls": [],
                }
            )

        # Set empty results for queries that weren't provided
        if not has_query_1:
            result_1 = {"scraped_content": [], "urls_scraped": []}
        if not has_query_2:
            result_2_urls = {"selected_urls": []}
        if not has_query_3:
            result_3_urls = {"selected_urls": []}

        # Check for errors in URL selection
        if (
            result_1.get("error")
            and result_2_urls.get("error")
            and result_3_urls.get("error")
        ):
            return json.dumps(
                {
                    "error": "All query URL selections failed",
                    "query_1_error": result_1.get("error"),
                    "query_2_error": result_2_urls.get("error"),
                    "query_3_error": result_3_urls.get("error"),
                }
            )

        # Phase 2: Scraping (Query 2 + Query 3 combined with BeautifulSoup)
        print("=" * 80)
        print("PHASE 2: SCRAPING COMBINED QUERY 2+3 WITH BEAUTIFULSOUP...")
        print("=" * 80)

        result_2_3_combined = await scrape_combined_bs_queries(
            self.valves,
            self.headers,
            result_2_urls,
            result_3_urls,
            __event_emitter__,
        )

        # Phase 3: Process combined results with LLM 2
        print("=" * 80)
        print("PHASE 3: PROCESSING COMBINED RESULTS WITH LLM 2...")
        print("=" * 80)
        combined_result = await process_combined_results(
            self.valves,
            result_1,
            result_2_3_combined,
            long_query or "",
            __event_emitter__,
        )

        # Prepare final response
        response = {
            "scraping_details": {
                "query_1": (
                    {
                        "scraper": "Jina",
                        "sources_found": len(result_1.get("scraped_content", [])),
                        "urls_scraped": result_1.get("urls_scraped", []),
                        "stats": result_1.get("scraping_stats", {}),
                    }
                    if has_query_1
                    else None
                ),
                "query_2_3_combined": (
                    {
                        "scraper": "BeautifulSoup",
                        "sources_found": len(
                            result_2_3_combined.get("scraped_content", [])
                        ),
                        "urls_scraped": result_2_3_combined.get("urls_scraped", []),
                        "query_2_urls_count": result_2_3_combined.get(
                            "query_2_urls_count", 0
                        ),
                        "query_3_urls_count": result_2_3_combined.get(
                            "query_3_urls_count", 0
                        ),
                        "stats": result_2_3_combined.get("scraping_stats", {}),
                    }
                    if (has_query_2 or has_query_3)
                    else None
                ),
            },
            "combined_analysis": combined_result,
            "status": "success",
        }

        return json.dumps(response, ensure_ascii=False)

    async def scrape_urls(
        self,
        urls: List[str],
        __event_emitter__: Optional[Callable[[dict], Any]] = None,
    ) -> str:
        """
        Scrape one or more URLs with Jina (step 3 only).
        Returns raw content without LLM analysis.
        """
        if not urls:
            return json.dumps({"error": "Aucune URL fournie."})

        # Filtrer les URLs valides
        valid_urls = [u for u in urls if u and u.startswith(("http://", "https://"))]
        if not valid_urls:
            return json.dumps({"error": "Aucune URL valide fournie."})

        print(f"Scraping de {len(valid_urls)} URL(s) avec Jina...")

        # Utiliser la fonction existante pour scraper en parallèle
        scraped_data = await web_scrape_multiple_jina(
            valid_urls,
            self.valves.JINA_API_KEY,
            self.valves.DISABLE_JINA_CACHING,
            self.valves.CLEAN_JINA_CONTENT,
            __event_emitter__,
        )

        # Formater les résultats
        results = []
        for item in scraped_data:
            content = item.get("content", "")
            results.append(
                {
                    "title": item.get("title", "Sans titre"),
                    "url": item["url"],
                    "content": truncate_to_n_words(
                        content, self.valves.search_scrape_and_summarize_words_limit_1
                    ),
                    "excerpt": generate_excerpt(content),
                    "error": item.get("error"),
                }
            )

        return json.dumps(
            {"urls_scraped": len(valid_urls), "results": results, "status": "success"},
            ensure_ascii=False,
        )

    async def summarize_urls(
        self,
        urls: List[str],
        long_query: str = "",
        __event_emitter__: Optional[Callable[[dict], Any]] = None,
    ) -> str:
        """
        Scrape one or more URLs with Jina, then summarize the content with LLM 2 (steps 3 + 4).
        """
        if not urls:
            return json.dumps({"error": "Aucune URL fournie."})

        # Filtrer les URLs valides
        valid_urls = [u for u in urls if u and u.startswith(("http://", "https://"))]
        if not valid_urls:
            return json.dumps({"error": "Aucune URL valide fournie."})

        print(f"Scraping et résumé de {len(valid_urls)} URL(s)...")

        # Étape 3: Scraper avec Jina
        scraped_content = await web_scrape_multiple_jina(
            valid_urls,
            self.valves.JINA_API_KEY,
            self.valves.DISABLE_JINA_CACHING,
            self.valves.CLEAN_JINA_CONTENT,
            __event_emitter__,
        )

        if not scraped_content:
            return json.dumps({"error": "Aucun contenu n'a pu être extrait."})

        # Formater le contenu pour LLM 2 (même format que process_combined_results)
        content_for_llm = []
        for item in scraped_content:
            if item.get("content") and not item.get("error"):
                content_for_llm.append(
                    {
                        "source": f"{item.get('title', 'Inconnu')} ({item['url']})",
                        "content": truncate_to_n_words(
                            item["content"],
                            self.valves.search_scrape_and_summarize_words_limit_1,
                        ),
                        "url": item["url"],
                        "scraper": "Jina",
                    }
                )

        if not content_for_llm:
            return json.dumps({"error": "Aucun contenu valide extrait."})

        # Étape 4: Utiliser LLM 2 pour résumer (réutilisation du prompt de process_combined_results)
        llm_query = long_query if long_query.strip() else "Résumé du contenu"

        # Créer un résultat factice compatible avec process_combined_results
        fake_result_1 = {"scraped_content": content_for_llm, "urls_scraped": valid_urls}
        fake_result_2_3 = {"scraped_content": []}

        combined_analysis = await process_combined_results(
            self.valves,
            fake_result_1,
            fake_result_2_3,
            llm_query,
            __event_emitter__,
        )

        return json.dumps(
            {
                "urls_analyzed": len(valid_urls),
                "llm_query_used": llm_query,
                "combined_analysis": combined_analysis,
                "status": "success",
            },
            ensure_ascii=False,
        )