"""
scraper/proxy.py
================
Advanced Feature — Free Proxy Rotation

Prevents IP bans when scraping many pages.

Sources of free proxies:
  1. free-proxy-list.net  (scraped fresh every session)
  2. proxyscrape.com API  (no key needed)
  3. Manual list          (paste your own)

Usage:
    from scraper.proxy import ProxyPool
    pool = ProxyPool()
    pool.refresh()                          # fetch fresh proxies
    proxies = pool.get()                    # {'http': ..., 'https': ...}
    html = requests.get(url, proxies=proxies, timeout=10)

    # Or use the patched session:
    session = pool.make_session()
    html = session.get(url)
"""

import logging
import random
import time
import requests
from typing import Optional
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class Proxy:
    ip:       str
    port:     str
    protocol: str = "http"
    latency:  float = 0.0
    failures: int = 0

    @property
    def url(self) -> str:
        return f"{self.protocol}://{self.ip}:{self.port}"

    @property
    def dict(self) -> dict:
        return {"http": self.url, "https": self.url}


class ProxyPool:
    """
    Manages a rotating pool of free proxies.
    Automatically retires proxies that fail too many times.
    """

    MAX_FAILURES   = 3       # retire a proxy after this many consecutive failures
    TEST_URL       = "https://httpbin.org/ip"
    TEST_TIMEOUT   = 6       # seconds

    def __init__(self, manual_proxies: list[str] = None):
        """
        Args:
            manual_proxies: Optional list of "ip:port" strings.
                            If provided, skips auto-fetch.
        """
        self._pool: list[Proxy] = []
        if manual_proxies:
            for p in manual_proxies:
                ip, port = p.strip().split(":")
                self._pool.append(Proxy(ip=ip, port=port))

    # ── Fetching ───────────────────────────────────────────────────────────────

    def _fetch_from_proxyscrape(self) -> list[Proxy]:
        """Fetch HTTPS proxies from proxyscrape.com (free, no key needed)."""
        try:
            resp = requests.get(
                "https://api.proxyscrape.com/v3/free-proxy-list/get",
                params={
                    "request":  "displayproxies",
                    "protocol": "http",
                    "timeout":  5000,
                    "country":  "all",
                    "ssl":      "yes",
                    "anonymity": "elite,anonymous",
                },
                timeout=15,
            )
            proxies = []
            for line in resp.text.strip().splitlines():
                line = line.strip()
                if ":" in line:
                    ip, port = line.split(":")
                    proxies.append(Proxy(ip=ip, port=port, protocol="http"))
            log.info("proxyscrape.com returned %d proxies", len(proxies))
            return proxies
        except Exception as exc:
            log.warning("proxyscrape fetch failed: %s", exc)
            return []

    def _fetch_from_free_proxy_list(self) -> list[Proxy]:
        """Scrape free-proxy-list.net for fresh proxies."""
        try:
            from bs4 import BeautifulSoup
            resp = requests.get(
                "https://free-proxy-list.net/",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15,
            )
            soup  = BeautifulSoup(resp.text, "html.parser")
            rows  = soup.select("table tbody tr")
            proxies = []
            for row in rows:
                cols = row.find_all("td")
                if len(cols) < 7:
                    continue
                ip       = cols[0].text.strip()
                port     = cols[1].text.strip()
                is_https = cols[6].text.strip().lower() == "yes"
                protocol = "https" if is_https else "http"
                proxies.append(Proxy(ip=ip, port=port, protocol=protocol))
            log.info("free-proxy-list.net returned %d proxies", len(proxies))
            return proxies
        except Exception as exc:
            log.warning("free-proxy-list.net fetch failed: %s", exc)
            return []

    def refresh(self, test: bool = False) -> int:
        """
        Fetch a fresh proxy list from all sources and merge.

        Args:
            test: If True, test each proxy before adding (slow but reliable).
        Returns:
            Number of proxies in the pool.
        """
        fresh: list[Proxy] = []
        fresh += self._fetch_from_proxyscrape()
        fresh += self._fetch_from_free_proxy_list()

        # Deduplicate by ip:port
        seen = set()
        unique = []
        for p in fresh:
            key = f"{p.ip}:{p.port}"
            if key not in seen:
                seen.add(key)
                unique.append(p)

        if test:
            log.info("Testing %d proxies (this may take a while)…", len(unique))
            unique = [p for p in unique if self._test_proxy(p)]
            log.info("%d proxies passed testing", len(unique))

        self._pool = unique
        random.shuffle(self._pool)
        log.info("Proxy pool refreshed: %d proxies available", len(self._pool))
        return len(self._pool)

    # ── Testing ────────────────────────────────────────────────────────────────

    def _test_proxy(self, proxy: Proxy) -> bool:
        """Return True if proxy responds within TEST_TIMEOUT seconds."""
        try:
            start = time.time()
            resp  = requests.get(
                self.TEST_URL,
                proxies=proxy.dict,
                timeout=self.TEST_TIMEOUT,
            )
            resp.raise_for_status()
            proxy.latency = round(time.time() - start, 2)
            return True
        except Exception:
            return False

    # ── Getting a proxy ────────────────────────────────────────────────────────

    def get(self) -> Optional[dict]:
        """
        Return a random healthy proxy as a requests-compatible dict.
        Returns None if the pool is empty.
        """
        healthy = [p for p in self._pool if p.failures < self.MAX_FAILURES]
        if not healthy:
            log.warning("Proxy pool exhausted — no healthy proxies left")
            return None
        return random.choice(healthy).dict

    def mark_failure(self, proxy_dict: dict) -> None:
        """Increment failure count for a proxy that returned an error."""
        url = proxy_dict.get("http", "")
        for proxy in self._pool:
            if proxy.url == url:
                proxy.failures += 1
                if proxy.failures >= self.MAX_FAILURES:
                    log.info("Retiring proxy %s after %d failures", url, proxy.failures)
                break

    def size(self) -> int:
        return len([p for p in self._pool if p.failures < self.MAX_FAILURES])

    # ── Patched requests session ───────────────────────────────────────────────

    def make_session(self) -> requests.Session:
        """
        Return a requests.Session pre-configured with a random proxy
        and a realistic User-Agent header.
        """
        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        })
        proxy = self.get()
        if proxy:
            session.proxies.update(proxy)
        return session

    def fetch_with_rotation(
        self,
        url: str,
        max_attempts: int = 4,
        delay: float = 1.5,
    ) -> Optional[requests.Response]:
        """
        Fetch a URL, automatically rotating to a new proxy on failure.

        Args:
            url:          Target URL.
            max_attempts: How many proxy rotations to try.
            delay:        Seconds to wait between attempts.

        Returns:
            requests.Response on success, None if all attempts fail.
        """
        for attempt in range(1, max_attempts + 1):
            proxy = self.get()
            try:
                resp = requests.get(
                    url,
                    proxies=proxy,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (X11; Linux x86_64) "
                            "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
                        )
                    },
                    timeout=10,
                )
                resp.raise_for_status()
                log.debug("Attempt %d succeeded via %s", attempt, proxy)
                return resp
            except Exception as exc:
                log.warning("Attempt %d failed (proxy=%s): %s", attempt, proxy, exc)
                if proxy:
                    self.mark_failure(proxy)
                if attempt < max_attempts:
                    time.sleep(delay)

        log.error("All %d proxy attempts failed for %s", max_attempts, url)
        return None