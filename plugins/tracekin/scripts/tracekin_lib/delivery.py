"""The HTTPS sender: one event per request, no redirects, no proxies."""
from __future__ import annotations

import json
import urllib.request

from .common import SCHEMA

SEND_TIMEOUT = 10  # Cloud Run cold starts can exceed a couple of seconds.
# The receiver refused this specific event; retrying it would block the queue.
PERMANENT_REJECTIONS = {400, 413, 415, 422}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def describe_http_error(error):
    """Short, log-safe description such as ``HTTP 401 unauthorized``."""
    detail = ""
    try:
        body = json.loads(error.read(512))
        if isinstance(body, dict) and isinstance(body.get("error"), str):
            detail = " " + body["error"][:64]
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return f"HTTP {error.code}{detail}"


def send_https(endpoint, payload, endpoint_token=""):
    body = json.dumps({"schema": SCHEMA, "events": [payload]}).encode()
    headers = {"Content-Type": "application/json", "Idempotency-Key": payload["id"]}
    if endpoint_token:
        headers["Authorization"] = "Bearer " + endpoint_token
    request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=SEND_TIMEOUT) as response:
        result = json.loads(response.read(65537))
        return isinstance(result, dict) and isinstance(result.get("accepted"), list) and payload["id"] in result["accepted"]
