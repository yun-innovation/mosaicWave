"""Response language of the API (doc/i18n.md § API language).

Reads the language a request names (``X-MosaicWave-Language`` / ``?lang=``) into
``request.state.language`` and stamps ``Content-Language`` and ``Vary`` on API answers. The
caller's saved ``user.locale`` is set on ``request.state.user_locale`` by the auth
dependencies, so the answer language is request → saved → English (``mosaicwave.i18n``).
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mosaicwave.i18n import LANGUAGE_HEADER, requested_language, response_language


class LanguageMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return
        scope.setdefault("state", {})["language"] = requested_language(Request(scope))

        async def send_with_language(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.add_vary_header(LANGUAGE_HEADER)
                headers.add_vary_header("Cookie")
                if headers.get("content-type", "").startswith("application/json"):
                    headers["Content-Language"] = response_language(Request(scope))
            await send(message)

        await self.app(scope, receive, send_with_language)
