"""Photo enrichment for parsers: detail-page galleries + precache.

``PhotosMixin`` is mixed into ``BaseParser`` (see parsers/base.py). After a
run, the top listings get their detail-page galleries fetched and merged
into the card; images are pre-downloaded to the export_utils disk cache so
PDF export hits zero network.
"""
from __future__ import annotations

import logging
import threading
from typing import ClassVar

from parsers.models import Listing

log = logging.getLogger("parsers")


class PhotosMixin:
    """Detail-page photo enrichment + background precache."""

    # Number of top listings to enrich with detail-page photos.
    # Subclasses can override; 0 disables enrichment.
    enrich_photo_count: ClassVar[int] = 5
    # When True, _enrich_photos also downloads each photo to the disk cache
    # (cache/photos/) so export_pdf is instant (all cache hits).  Tests
    # that don't need real HTTP set this to False.
    precache_photos: ClassVar[bool] = True

    def _fetch_detail_photos(self, listing: Listing) -> list[str]:
        """Fetch the listing detail page and return all photo URLs.

        Override in subclasses that know how to find the photo gallery on
        the detail page. Returns an empty list if not implemented or on error.
        """
        return []

    def _enrich_photos(self, listings: list[Listing]) -> None:
        """Enrich *listings* (in place) with photos from detail pages.

        After extracting the photo **URLs** from the detail page, also
        **download each image** and write it to the on-disk photo cache
        (``cache/photos/``).  This way the export step later finds every
        photo already cached and hits zero network — the cache grows
        during parsing, not during export.
        """
        n = min(getattr(self, "enrich_photo_count", 0), len(listings))
        if n <= 0:
            return
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {
                pool.submit(self._fetch_detail_photos, item): item
                for item in listings[:n]
            }
            for fut in as_completed(futures):
                item = futures[fut]
                try:
                    photos = fut.result()
                    if photos:
                        # Merge instead of overwrite: the card photo is still a
                        # valid (and often the best) first frame.
                        existing = [item.photo] if item.photo else []
                        merged = list(dict.fromkeys(existing + photos))
                        item.photo = "|".join(merged)
                        # Pre-download all photos to the disk cache so the
                        # export step is instant (all cache hits).  Skip in
                        # tests (precache_photos=False) or when disabled.
                        if getattr(self, "precache_photos", True):
                            self._precache_photos(merged)
                except Exception as exc:
                    log.debug("[%s] photo enrichment failed for %s: %s",
                              self.name, item.url[:60], exc)

    def _precache_photos(self, urls: list[str]) -> None:
        """Download all photo URLs to the export_utils disk cache.

        Called after _fetch_detail_photos extracts URLs — the images
        themselves are fetched and cached so export_pdf doesn't hit
        the network later.  Runs in a **background daemon thread** so
        parsing returns results to the user immediately while photos
        trickle into the cache asynchronously.
        """
        import threading

        def _worker():
            try:
                from export_utils import _cache_get, _download_photo, _photo_urls
                to_download = [
                    u for u in _photo_urls("|".join(urls))
                    if _cache_get(u) is None
                ]
                if not to_download:
                    return
                from concurrent.futures import ThreadPoolExecutor, as_completed
                with ThreadPoolExecutor(max_workers=6) as pool:
                    futures = {pool.submit(_download_photo, u): u
                               for u in to_download}
                    done = 0
                    for fut in as_completed(futures):
                        try:
                            if fut.result():
                                done += 1
                        except Exception as exc:
                            log.debug("[%s] precache photo failed: %s",
                                      self.name, exc)
                if done:
                    log.debug("[%s] precached %d/%d photos",
                              self.name, done, len(to_download))
            except Exception as exc:
                log.debug("[%s] precache worker error: %s", self.name, exc)

        # Daemon thread: killed when the process exits, never blocks
        # the search response or the parser's run() return.
        t = threading.Thread(target=_worker, daemon=True)
        t.start()
