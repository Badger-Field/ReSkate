"""Downloads, cached on disk and polite to the servers they come from.

Everything fetched is kept under the cache folder (default ~/.cache/reskate-lidar, or
RESKATE_LIDAR_CACHE, or --cache), so running again reads nothing twice. Nothing here goes into
git: the cache is outside the repository by default, and contrib/lidar-parks/.gitignore keeps a
local one out. Each host gets at most one request per `min_interval` seconds where its usage
policy asks for it (Nominatim: one per second)."""
from __future__ import annotations

import hashlib
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

USER_AGENT = "ReSkate-lidar-parks/0.2 (+https://github.com/Badger-Field/ReSkate/tree/main/contrib/lidar-parks)"
# Hosts whose usage policies set a request rate (seconds between requests).
MIN_INTERVAL = {"nominatim.openstreetmap.org": 1.0, "overpass-api.de": 1.0, "graph.mapillary.com": 0.2,
                "api.openstreetcam.org": 1.0, "api.panoramax.xyz": 0.5}

_last_request: dict[str, float] = {}
_lock = threading.Lock()


def cache_dir(override: str | Path | None = None) -> Path:
    path = Path(override or os.environ.get("RESKATE_LIDAR_CACHE") or Path.home() / ".cache" / "reskate-lidar")
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_path(cache: Path, kind: str, key: str, suffix: str = "") -> Path:
    """A stable file under cache/kind/ for a key (a URL or a query)."""
    digest = hashlib.sha256(key.encode()).hexdigest()[:24]
    folder = cache / kind
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{digest}{suffix}"


def _wait_turn(host: str) -> None:
    interval = MIN_INTERVAL.get(host, 0.0)
    if not interval:
        return
    with _lock:
        wait = _last_request.get(host, 0.0) + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request[host] = time.monotonic()


def fetch(url: str, cache: Path, kind: str, data: bytes | None = None, suffix: str = "",
          retries: int = 4, timeout: float = 120.0, headers: dict[str, str] | None = None) -> bytes:
    """GET (or POST `data`) a URL, from the cache when it was fetched before. A local path or a
    file:// URL is read directly (tests, and mirrors of a dataset on disk). `headers` are sent but
    are not part of the cache key, so a token in them is never written to disk."""
    if "://" not in url or url.startswith("file://"):
        return Path(urllib.parse.urlparse(url).path if url.startswith("file://") else url).read_bytes()
    target = cache_path(cache, kind, url + ("\n" + data.decode("utf-8", "replace") if data else ""), suffix)
    if target.exists():
        return target.read_bytes()
    host = urllib.parse.urlparse(url).hostname or ""
    delay = 2.0
    for attempt in range(retries + 1):
        _wait_turn(host)
        request = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read()
            break
        except urllib.error.HTTPError as error:
            # 429 and 5xx are worth waiting out; anything else (404, 403) is an answer.
            if error.code not in (429, 500, 502, 503, 504) or attempt == retries:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == retries:
                raise
        time.sleep(delay)
        delay *= 2
    partial = target.with_suffix(target.suffix + ".part")
    partial.write_bytes(body)
    partial.replace(target)
    return body
