"""Authenticated Reddit API client.

Reddit returns HTTP 403 to *every* unauthenticated request from this network,
including the old `.json` suffix endpoints and old.reddit.com — the body is the
full HTML block page, so the failure looks like a normal page fetch unless you
check the status. OAuth against `oauth.reddit.com` works. Verified 2026-08-17.

Credentials come from the environment, falling back to a 1Password secret
reference (see `config/credentials.example`). Nothing is read from a file on
disk and no secret is written to the harvest output.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Iterator
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import base64
import json

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API_BASE = "https://oauth.reddit.com"
USER_AGENT = "BIFL-Recs/0.1 (harvester; +https://github.com/danielrosehill/BIFL-Recs)"

# 1Password references used when the environment does not carry the credentials.
OP_CLIENT_ID = "op://Daniel - Personal/Reddit Oauth For N8N/Client ID"
OP_CLIENT_SECRET = "op://Daniel - Personal/Reddit Oauth For N8N/Secret"


class RedditError(RuntimeError):
    pass


def _op_read(reference: str) -> str:
    try:
        out = subprocess.run(
            ["op", "read", reference],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:  # op not installed
        raise RedditError(
            "REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET are unset and the 1Password "
            "CLI (`op`) is not on PATH"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RedditError(f"`op read {reference}` failed: {exc.stderr.strip()}") from exc
    return out.stdout.strip()


def _credentials() -> tuple[str, str]:
    client_id = os.environ.get("REDDIT_CLIENT_ID") or _op_read(OP_CLIENT_ID)
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET") or _op_read(OP_CLIENT_SECRET)
    return client_id, client_secret


@dataclass
class RedditClient:
    """Minimal read-only client over the application-only OAuth flow.

    `client_credentials` is enough for every read endpoint used here; no user
    context and therefore no user password is involved.
    """

    token: str
    min_interval: float = 1.1  # ~55 req/min, half the documented 100/min ceiling
    _last_call: float = 0.0

    @classmethod
    def login(cls) -> "RedditClient":
        client_id, client_secret = _credentials()
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        body = urlencode({"grant_type": "client_credentials"}).encode()
        req = Request(
            TOKEN_URL,
            data=body,
            headers={
                "Authorization": f"Basic {basic}",
                "User-Agent": USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        with urlopen(req, timeout=30) as resp:
            payload = json.load(resp)
        if "access_token" not in payload:
            raise RedditError(f"no access_token in token response: {payload}")
        return cls(token=payload["access_token"])

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call = time.monotonic()

    def get(self, path: str, **params: Any) -> Any:
        params.setdefault("raw_json", 1)
        url = f"{API_BASE}{path}?{urlencode({k: v for k, v in params.items() if v is not None})}"
        for attempt in range(5):
            self._throttle()
            req = Request(
                url,
                headers={
                    "Authorization": f"bearer {self.token}",
                    "User-Agent": USER_AGENT,
                },
            )
            try:
                with urlopen(req, timeout=45) as resp:
                    return json.load(resp)
            except HTTPError as exc:
                # 429 is the rate limiter; 5xx is Reddit being Reddit. Both retry.
                if exc.code in (429, 500, 502, 503, 504) and attempt < 4:
                    time.sleep(2 ** attempt * 5)
                    continue
                raise RedditError(f"GET {url} -> HTTP {exc.code}") from exc
        raise RedditError(f"GET {url} exhausted retries")

    def listing(
        self,
        path: str,
        limit: int,
        page_size: int = 100,
        **params: Any,
    ) -> Iterator[dict]:
        """Page a listing endpoint, yielding the `data` dict of each child."""
        after: str | None = None
        seen = 0
        while seen < limit:
            batch = self.get(
                path, limit=min(page_size, limit - seen), after=after, **params
            )
            children = batch.get("data", {}).get("children", [])
            if not children:
                return
            for child in children:
                yield child["data"]
                seen += 1
            after = batch.get("data", {}).get("after")
            if not after:
                return
