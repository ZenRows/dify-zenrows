from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from tools.client import fetch
from utils.errors import (
    PASSTHROUGH_ERRORS,
    ToolInvokeError,
    as_bool,
    require_param,
    validate_url,
)

# `html` is the API's default and is expressed by omitting response_type
# entirely, so it is deliberately absent from this map.
RESPONSE_TYPES = {
    "markdown": "markdown",
    "plaintext": "plaintext",
    "pdf": "pdf",
}


def _image_mime(blob: bytes, header_value: str | None) -> str:
    """Work out a screenshot's mime type from its bytes.

    The response header is not trusted: Dify maps an unrecognised mime to a
    generic `.bin` file the user cannot preview, and a header carrying a
    charset suffix or `application/octet-stream` produces exactly that. Magic
    bytes are unambiguous, so check those first and only fall back to the
    header when the signature is unfamiliar.
    """
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if blob[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if blob[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "image/webp"
    if header_value:
        # Strip any `; charset=...` suffix — it stops the mime matching.
        base = header_value.split(";", 1)[0].strip().lower()
        if base.startswith("image/"):
            return base
    return "image/png"


class FetchTool(Tool):
    def _invoke(
        self, tool_parameters: dict[str, Any]
    ) -> Generator[ToolInvokeMessage, None, None]:
        url = validate_url(require_param(tool_parameters, "url", "URL is required."))

        params: dict[str, Any] = {}

        response_type = tool_parameters.get("response_type") or "markdown"
        if response_type in RESPONSE_TYPES:
            params["response_type"] = RESPONSE_TYPES[response_type]

        js_render = as_bool(tool_parameters.get("js_render"))
        screenshot = as_bool(tool_parameters.get("screenshot"))
        wait_for = (tool_parameters.get("wait_for") or "").strip()

        # screenshot and wait_for are browser-side features: without a browser
        # the API silently ignores them, which looks like a plugin bug. Turn
        # js_render on rather than returning something the user did not ask for.
        if screenshot or wait_for:
            js_render = True

        if js_render:
            params["js_render"] = True
        if as_bool(tool_parameters.get("premium_proxy")):
            params["premium_proxy"] = True

        proxy_country = (tool_parameters.get("proxy_country") or "").strip().lower()
        if proxy_country:
            # proxy_country only takes effect with premium_proxy; setting one
            # without the other is a silent no-op, so enable it.
            params["premium_proxy"] = True
            params["proxy_country"] = proxy_country

        if wait_for:
            params["wait_for"] = wait_for
        if screenshot:
            params["screenshot"] = True
            # A screenshot is an image, so a text response_type is meaningless.
            params.pop("response_type", None)

        api_key = str(self.runtime.credentials.get("api_key", "")).strip()

        try:
            response = fetch(api_key, url, params, action="fetching the page")

            if screenshot:
                mime = _image_mime(response.content, response.headers.get("Content-Type"))
                yield self.create_blob_message(
                    blob=response.content,
                    meta={"mime_type": mime, "filename": "screenshot." + mime.split("/")[1]},
                )
                yield self.create_json_message({"url": url, "status_code": response.status_code})
                return

            if response_type == "pdf":
                yield self.create_blob_message(
                    blob=response.content,
                    meta={"mime_type": "application/pdf", "filename": "page.pdf"},
                )
                yield self.create_json_message({"url": url, "status_code": response.status_code})
                return

            content = response.text
            yield self.create_text_message(content)
            yield self.create_json_message(
                {"content": content, "url": url, "status_code": response.status_code}
            )
        except PASSTHROUGH_ERRORS:
            raise
        except Exception as exc:
            raise ToolInvokeError(f"Unexpected error while fetching the page: {exc}") from exc
