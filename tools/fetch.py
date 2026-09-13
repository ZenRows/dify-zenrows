from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from tools.client import fetch
from utils.errors import (
    resolve_stealth,
    PASSTHROUGH_ERRORS,
    ToolInvokeError,
    ToolParameterValidationError,
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



def _positive_int(value: object, param_name: str) -> int | None:
    """Read an optional positive integer parameter.

    Dify sends numbers through as strings often enough that int() alone is not
    safe, and a bad value should name the field rather than surfacing as a
    TypeError from deep inside the request build.
    """
    if value is None or value == "":
        return None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        raise ToolParameterValidationError(
            f"{param_name} must be a whole number of milliseconds, for example 2000."
        ) from None
    if parsed <= 0:
        raise ToolParameterValidationError(
            f"{param_name} must be greater than zero; got {parsed}."
        )
    return parsed


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
        js_instructions = (tool_parameters.get("js_instructions") or "").strip()
        wait = _positive_int(tool_parameters.get("wait"), "wait")

        # The API rejects `wait` and `wait_for` together (`prohibits` on both
        # sides of the rule pair). Catch it here so the user gets a sentence
        # naming both fields rather than a 422 they have to decode.
        if wait is not None and wait_for:
            raise ToolParameterValidationError(
                "Set Wait (ms) or Wait for selector, not both — the API rejects "
                "the pair."
            )

        # These are all browser-side features: without a browser the API
        # silently ignores them, which looks like a plugin bug. Turn js_render
        # on rather than returning something the user did not ask for. (Adaptive
        # stealth also satisfies the API's requirement, but it can decline to
        # escalate, so do not rely on it to supply the browser.)
        if screenshot or wait_for or js_instructions or wait is not None:
            js_render = True

        premium_proxy = as_bool(tool_parameters.get("premium_proxy"))
        if js_render:
            params["js_render"] = True
        if premium_proxy:
            params["premium_proxy"] = True

        # Adaptive Stealth Mode, on by default, matching the Extract tool and
        # `ZenRowsClient.extract()` in the Python SDK. Without it a target that
        # needs js_render or premium_proxy fails with REQS002 instead of being
        # escalated. The wire param is `mode`.
        #
        # It does NOT sit alongside the explicit toggles: Zenrows treats
        # `mode=auto` and js_render/premium_proxy as mutually exclusive and
        # rejects a body carrying both. resolve_stealth settles which tier
        # applies, and says so when the request is contradictory.
        if resolve_stealth(
            tool_parameters.get("adaptive_stealth"),
            js_render,
            premium_proxy,
            tool="Fetch",
        ):
            params["mode"] = "auto"

        proxy_country = (tool_parameters.get("proxy_country") or "").strip().lower()
        if proxy_country:
            # proxy_country only takes effect with premium_proxy; setting one
            # without the other is a silent no-op, so enable it.
            params["premium_proxy"] = True
            params["proxy_country"] = proxy_country

        if wait_for:
            params["wait_for"] = wait_for
        if wait is not None:
            params["wait"] = wait
        if js_instructions:
            params["js_instructions"] = js_instructions
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
