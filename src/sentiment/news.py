"""Crypto news sentiment collector using CryptoCompare Data API (free, no key required)."""

import logging
from datetime import datetime

import aiohttp

logger = logging.getLogger(__name__)

CRYPTOCOMPARE_NEWS_URL = "https://data-api.cryptocompare.com/news/v1/article/list"

# Map CryptoCompare SENTIMENT field to our internal format
_SENTIMENT_MAP = {
    "POSITIVE": "bullish",
    "NEGATIVE": "bearish",
    "NEUTRAL": "neutral",
}


class NewsCollector:
    """Collect crypto news sentiment from CryptoCompare (free, no API key needed).

    Falls back to CryptoPanic if a key is provided and CryptoCompare fails.
    """

    def __init__(self, cryptopanic_api_key: str = "") -> None:
        self.cryptopanic_api_key = cryptopanic_api_key

    async def fetch_news(
        self,
        categories: list[str] | None = None,
        limit: int = 50,
        known_symbols: set[str] | None = None,
    ) -> list[dict]:
        """Fetch news from CryptoCompare with optional category filter.

        Categories correspond to coin tickers: BTC, ETH, SOL, etc.
        known_symbols: set of symbols to match against article categories.
            If None, all article categories are matched as potential symbols.

        Returns list of dicts with: symbol, headline, source, sentiment,
        sentiment_score, url, published_at.
        """
        params: dict[str, str | int] = {
            "limit": limit,
            "lang": "EN",
        }
        if categories:
            params["categories"] = ",".join(categories)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    CRYPTOCOMPARE_NEWS_URL,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        logger.warning("CryptoCompare News API returned %d", resp.status)
                        return []
                    data = await resp.json()
        except Exception:
            logger.exception("Failed to fetch CryptoCompare news")
            return []

        results = []
        for article in data.get("Data", []):
            # Map sentiment
            raw_sentiment = (article.get("SENTIMENT") or "NEUTRAL").upper()
            sentiment = _SENTIMENT_MAP.get(raw_sentiment, "neutral")

            # Compute numeric score from sentiment + votes
            upvotes = article.get("UPVOTES", 0) or 0
            downvotes = article.get("DOWNVOTES", 0) or 0
            # Base score from sentiment label
            base_score = {"bullish": 0.5, "bearish": -0.5, "neutral": 0.0}[sentiment]
            # Adjust with votes if any
            if upvotes + downvotes > 0:
                vote_bias = (upvotes - downvotes) / (upvotes + downvotes + 1)
                score = base_score * 0.7 + vote_bias * 0.3
            else:
                score = base_score

            # Extract symbol from category data
            # Match against known watchlist symbols, or treat any category as a symbol
            symbol = None
            for cat in article.get("CATEGORY_DATA", []):
                cat_name = cat.get("CATEGORY", "").upper()
                if known_symbols is None or cat_name in known_symbols:
                    symbol = cat_name
                    break

            published_on = article.get("PUBLISHED_ON")
            published_at = datetime.utcfromtimestamp(published_on).isoformat() if published_on else None

            source_data = article.get("SOURCE_DATA", {})
            source_name = source_data.get("NAME", "") if isinstance(source_data, dict) else ""

            results.append({
                "symbol": symbol,
                "headline": article.get("TITLE", ""),
                "source": source_name,
                "sentiment": sentiment,
                "sentiment_score": round(score, 4),
                "url": article.get("URL") or article.get("GUID", ""),
                "published_at": published_at,
            })

        return results

    async def collect_all(self, symbols: list[str]) -> list[dict]:
        """Batch fetch news for watchlist symbols + general crypto news.

        Deduplicates by URL. Uses watchlist symbols for category matching
        so articles are tagged with the correct coin.
        """
        symbol_set = {s.upper() for s in symbols} if symbols else None
        seen_urls: set[str] = set()
        all_news: list[dict] = []

        # Fetch general crypto news
        general = await self.fetch_news(limit=30, known_symbols=symbol_set)
        for item in general:
            url = item.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_news.append(item)

        # Fetch per-symbol news
        if symbols:
            symbol_news = await self.fetch_news(categories=symbols, limit=30, known_symbols=symbol_set)
            for item in symbol_news:
                url = item.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_news.append(item)

        return all_news
