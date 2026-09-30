"""
Preloader: fetches names from 1C catalogs and builds DictionaryMatcher per channel.
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional

from ..command_queue import channel_command_queue
from ..config import settings
from .dictionary_matcher import DictionaryMatcher
from .anonymization_defaults import DEFAULT_DICTIONARY_SOURCES

logger = logging.getLogger(__name__)

_PRELOAD_LIMIT = 1_000_000  # НЕ 0! В 1С limit=0 означает "0 строк"


class DictionaryPreloader:
    """Manages per-channel DictionaryMatcher loading."""

    def __init__(self) -> None:
        self._matchers: Dict[str, DictionaryMatcher] = {}
        self._loading_locks: Dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    async def get_matcher(self, channel: str) -> Optional[DictionaryMatcher]:
        """Get matcher for channel, triggering preload on first call.

        Returns None if dictionary feature is disabled.
        """
        if not settings.anonymization_dictionary_enabled:
            return None

        # Fast path: already loaded
        if channel in self._matchers:
            return self._matchers[channel]

        # Get or create per-channel lock
        async with self._global_lock:
            if channel not in self._loading_locks:
                self._loading_locks[channel] = asyncio.Lock()
            lock = self._loading_locks[channel]

        async with lock:
            # Double-check after acquiring
            if channel in self._matchers:
                return self._matchers[channel]

            try:
                matcher = await self._preload(channel)
                self._matchers[channel] = matcher
                return matcher
            except Exception as e:
                logger.warning(
                    f"Dictionary preload failed for channel '{channel}': {e}. "
                    "Will retry on next request."
                )
                return None

    async def _preload(self, channel: str) -> DictionaryMatcher:
        """Execute 1C queries and build matcher for channel.

        Raises RuntimeError if all sources failed (so get_matcher won't cache
        and will retry on next request). Caches normally if at least one source
        responded — even if catalogs are empty.
        """
        sources = self._get_sources()
        terms: Dict[str, str] = {}  # canonical_term → category, first wins
        any_source_ok = False

        for source in sources:
            catalog = source.get("from", "")
            category = source.get("category", "ORG")
            extra_fields = source.get("extra_fields", [])
            fields = ["Наименование"] + [f for f in extra_fields if f != "Наименование"]

            try:
                names = await self._fetch_catalog_names(channel, catalog, fields)
                any_source_ok = True
                for name in names:
                    if name and name.strip() and name.strip() not in terms:
                        terms[name.strip()] = category
            except Exception as e:
                logger.warning(
                    f"Dictionary preload failed for '{catalog}' on channel '{channel}': {e}"
                )

        if not any_source_ok and sources:
            raise RuntimeError(
                f"All {len(sources)} dictionary sources failed for channel '{channel}'"
            )

        matcher = DictionaryMatcher()
        matcher.build(terms)
        logger.info(
            f"Dictionary loaded for channel '{channel}': "
            f"{matcher.term_count} terms from {len(sources)} sources"
        )
        return matcher

    async def _fetch_catalog_names(
        self, channel: str, catalog: str, fields: List[str]
    ) -> List[str]:
        """Execute 1C query to fetch names, with fallback to Наименование only."""
        field_sql = ", ".join(f"T.{f}" for f in fields)
        query = f"ВЫБРАТЬ РАЗЛИЧНЫЕ {field_sql} ИЗ {catalog} КАК T"
        params = {"query": query, "limit": _PRELOAD_LIMIT}

        try:
            result = await self._execute_raw(channel, params)
            return self._extract_names(result, fields)
        except Exception as e:
            if len(fields) > 1:
                logger.info(
                    f"Retrying '{catalog}' with Наименование only "
                    f"(original error: {e})"
                )
                query = f"ВЫБРАТЬ РАЗЛИЧНЫЕ T.Наименование ИЗ {catalog} КАК T"
                params = {"query": query, "limit": _PRELOAD_LIMIT}
                result = await self._execute_raw(channel, params)
                return self._extract_names(result, ["Наименование"])
            raise

    async def _execute_raw(
        self, channel: str, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute query via command queue, bypassing anonymization."""
        command_id = await channel_command_queue.add_command(
            channel, "execute_query", params
        )
        result = await channel_command_queue.wait_for_result(
            command_id, timeout=float(settings.timeout)
        )
        if isinstance(result, dict) and result.get("success") is False:
            raise RuntimeError(result.get("error", "unknown 1C error"))
        return result

    @staticmethod
    def _extract_names(result: Dict[str, Any], fields: List[str]) -> List[str]:
        """Extract name strings from query result rows."""
        names: List[str] = []
        data = result.get("data", [])
        if not isinstance(data, list):
            return names
        for row in data:
            if not isinstance(row, dict):
                continue
            for field in fields:
                val = row.get(field)
                if isinstance(val, str) and val.strip():
                    names.append(val.strip())
        return names

    def _get_sources(self) -> List[Dict]:
        """Return effective sources.

        If SOURCES_OVERRIDE is set — replaces defaults entirely.
        SOURCES_ADD always appended on top (to override or defaults).
        """
        override = settings.anonymization_dictionary_sources_override
        sources = list(override) if override is not None else list(DEFAULT_DICTIONARY_SOURCES)
        add = settings.anonymization_dictionary_sources_add
        if add:
            sources.extend(add)
        return sources


# Global singleton
dictionary_preloader = DictionaryPreloader()
