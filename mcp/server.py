#!/usr/bin/env python3
"""MCP server for the Three Brained Searches tool (Open WebUI -> MCP port).

Exposes three tools:
  - three_smart_searches: 3 parallel searches, LLM 1 URL selection, scraping (Jina + BeautifulSoup), LLM 2 extraction
  - scrape_urls: scrape URLs with Jina, no LLM analysis
  - summarize_urls: scrape URLs with Jina then summarize with LLM 2

All "Valves" from the original Open WebUI tool are configurable via
environment variables: the valve name upper-cased, prefixed with TBS_.
Example: TBS_SEARXNG_URL, TBS_LLM_1_API_KEY, TBS_JINA_API_KEY.

Run: python server.py            (stdio transport)
"""

import json
import logging
import os
import sys

try:
    from mcp.server.fastmcp import FastMCP  # mcp 1.x
except ModuleNotFoundError:
    from mcp.server.mcpserver import MCPServer as FastMCP  # mcp 2.x

from tool_code import Tools

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("three-brained-searches-mcp")


def build_tools() -> Tools:
    tools = Tools()
    fields = type(tools.valves).model_fields
    overrides = {}
    for name in fields:
        env = "TBS_" + name.upper()
        if env in os.environ:
            raw = os.environ[env]
            ann = fields[name].annotation
            try:
                if ann is bool:
                    value = raw.strip().lower() in ("1", "true", "yes", "on")
                elif ann is int:
                    value = int(raw)
                elif ann is float:
                    value = float(raw)
                else:
                    value = raw
            except ValueError:
                logger.warning("Invalid value for %s: %r (kept default)", env, raw)
                continue
            overrides[name] = value
            logger.info("Override valve %s from %s", name, env)
    if overrides:
        tools.valves = tools.valves.model_copy(update=overrides)
    return tools


TOOLS_INSTANCE = build_tools()

mcp = FastMCP("three-brained-searches")


@mcp.tool()
async def three_smart_searches(
    query_1: str | None = None,
    query_2: str | None = None,
    query_3: str | None = None,
    long_query: str | None = None,
    max_urls_to_scrape_1: int | None = None,
    max_urls_to_scrape_2: int | None = None,
    max_urls_to_scrape_3: int | None = None,
    search_language_1: str | None = None,
    search_language_2: str | None = None,
    search_language_3: str | None = None,
    search_engines_1: str | None = None,
    search_engines_2: str | None = None,
    search_engines_3: str | None = None,
    search_categories_1: str | None = None,
    search_categories_2: str | None = None,
    search_categories_3: str | None = None,
    freshness_1: str | None = None,
    freshness_2: str | None = None,
    freshness_3: str | None = None,
) -> str:
    """Performs up to three intelligent web searches in parallel (SearXNG, with DDGS/LangSearch fallbacks), uses LLM 1 to select the most relevant URLs, scrapes them (Jina for query 1, BeautifulSoup for queries 2 and 3, Jina for PDFs), then combines everything and uses LLM 2 to extract and summarize only the most relevant information. At least one of query_1, query_2, query_3 must be provided. long_query is a detailed question used by both LLMs for URL selection and content extraction."""
    return await TOOLS_INSTANCE.three_smart_searches(
        query_1=query_1,
        query_2=query_2,
        query_3=query_3,
        long_query=long_query,
        max_urls_to_scrape_1=max_urls_to_scrape_1,
        max_urls_to_scrape_2=max_urls_to_scrape_2,
        max_urls_to_scrape_3=max_urls_to_scrape_3,
        search_language_1=search_language_1,
        search_language_2=search_language_2,
        search_language_3=search_language_3,
        search_engines_1=search_engines_1,
        search_engines_2=search_engines_2,
        search_engines_3=search_engines_3,
        search_categories_1=search_categories_1,
        search_categories_2=search_categories_2,
        search_categories_3=search_categories_3,
        freshness_1=freshness_1,
        freshness_2=freshness_2,
        freshness_3=freshness_3,
    )


@mcp.tool()
async def scrape_urls(urls: list[str]) -> str:
    """Scrape one or more URLs with the Jina scraper. Returns the raw content of each page without any LLM analysis."""
    return await TOOLS_INSTANCE.scrape_urls(urls=urls)


@mcp.tool()
async def summarize_urls(urls: list[str], long_query: str = "") -> str:
    """Scrape one or more URLs with the Jina scraper, then summarize the combined content with LLM 2 according to long_query (the question guiding the summarization; defaults to a generic summary)."""
    return await TOOLS_INSTANCE.summarize_urls(urls=urls, long_query=long_query)


if __name__ == "__main__":
    logger.info("Starting Three Brained Searches MCP server (stdio)")
    if not TOOLS_INSTANCE.valves.llm_1_api_key or not TOOLS_INSTANCE.valves.llm_2_api_key:
        logger.warning(
            "TBS_LLM_1_API_KEY and/or TBS_LLM_2_API_KEY are not set; "
            "the LLM-based tools will fail until configured."
        )
    mcp.run()
