import json
from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from tools.client import fetch_raw
from utils.errors import (
    PASSTHROUGH_ERRORS,
    ToolInvokeError,
    ToolParameterValidationError,
    as_bool,
    is_extract_domain_not_enabled,
    raise_for_zenrows_error,
    require_param,
    validate_url,
)

# The four methods are mutually exclusive on the API: `extract` wins over
# `autoparse`, which wins over `css_extractor`, which wins over `outputs`.
# Send exactly one, never a combination.
VALID_OUTPUT_FILTERS = {
    "emails",
    "phone_numbers",
    "headings",
    "images",
    "audios",
    "videos",
    "links",
    "menus",
    "hashtags",
    "metadata",
    "tables",
    "favicon",
}


def _method_params(method: str, css_selectors: str, outputs: str) -> dict[str, Any]:
    if method == "auto":
        return {"extract": "auto"}
    if method == "autoparse":
        return {"autoparse": True}
    if method == "css":
        return {"css_extractor": css_selectors}
    if method == "outputs":
        return {"outputs": outputs}
    raise ToolParameterValidationError(f"Unknown extraction method: {method}")


def _is_empty(data: Any) -> bool:
    """Mirrors the CLI's isEmptyData: a structured method can succeed and still
    return nothing useful, and reporting that as success is misleading."""
    if data is None:
        return True
    if isinstance(data, (list, dict)):
        return len(data) == 0
    if isinstance(data, str):
        return not data.strip()
    return False


class ExtractTool(Tool):
    def _invoke(
        self, tool_parameters: dict[str, Any]
    ) -> Generator[ToolInvokeMessage, None, None]:
        url = validate_url(require_param(tool_parameters, "url", "URL is required."))
        method = (tool_parameters.get("method") or "auto").strip().lower()

        css_selectors = (tool_parameters.get("css_selectors") or "").strip()
        outputs = (tool_parameters.get("outputs") or "").strip()

        if method == "css":
            if not css_selectors:
                raise ToolParameterValidationError(
                    'CSS extraction needs a selector map, e.g. {"title":"h1","price":".price"}.'
                )
            try:
                parsed_selectors = json.loads(css_selectors)
            except ValueError as exc:
                raise ToolParameterValidationError(
                    "CSS selectors must be valid JSON, e.g. "
                    '{"title":"h1","price":".price"}.'
                ) from exc
            if not isinstance(parsed_selectors, dict) or not parsed_selectors:
                raise ToolParameterValidationError(
                    "CSS selectors must be a non-empty JSON object mapping field "
                    'names to selectors, e.g. {"title":"h1"}.'
                )

        if method == "outputs":
            if not outputs:
                raise ToolParameterValidationError(
                    "Output-filter extraction needs a filter list, e.g. emails,links "
                    "(or * for all)."
                )
            if outputs != "*":
                requested = {f.strip() for f in outputs.split(",") if f.strip()}
                unknown = sorted(requested - VALID_OUTPUT_FILTERS)
                if unknown:
                    raise ToolParameterValidationError(
                        f"Unknown output filter(s): {', '.join(unknown)}. Valid "
                        f"filters: {', '.join(sorted(VALID_OUTPUT_FILTERS))}."
                    )

        shared: dict[str, Any] = {}
        if as_bool(tool_parameters.get("js_render")):
            shared["js_render"] = True
        if as_bool(tool_parameters.get("premium_proxy")):
            shared["premium_proxy"] = True

        api_key = str(self.runtime.credentials.get("api_key", "")).strip()

        try:
            params = {**shared, **_method_params(method, css_selectors, outputs)}
            response = fetch_raw(api_key, url, params, action="extracting data")

            fell_back = False
            # The fallback is deliberately narrow. It only applies when the user
            # asked for `auto`: if they chose css or outputs, silently switching
            # to autoparse would return something they did not ask for.
            if method == "auto" and is_extract_domain_not_enabled(
                response.status_code, response.text
            ):
                params = {**shared, **_method_params("autoparse", "", "")}
                response = fetch_raw(
                    api_key, url, params, action="extracting data with autoparse"
                )
                fell_back = True

            raise_for_zenrows_error(
                response.status_code, response.text, action="extracting data"
            )

            try:
                data = response.json()
            except ValueError:
                # A structured method that did not return JSON is a failed
                # extraction, not page content to hand back.
                data = None

            # `extract=auto` answers with an envelope, {parsed, html}, where
            # `parsed` holds the fields and `html` is the source page kept for
            # validation. Return `parsed` — handing back the envelope buries
            # the result under a full page dump. Anyone who wants the HTML
            # should use the Fetch tool, which is what it is for.
            if not fell_back and method == "auto" and isinstance(data, dict) and "parsed" in data:
                data = data["parsed"]

            empty = _is_empty(data)
            method_used = "autoparse" if fell_back else method

            result = {
                "data": data,
                "method_used": method_used,
                "fell_back_to_autoparse": fell_back,
                "empty": empty,
                "url": url,
            }

            if empty:
                yield self.create_text_message(
                    f"No structured data found at {url} using {method_used}."
                )
            else:
                yield self.create_text_message(
                    json.dumps(data, indent=2, ensure_ascii=False)
                )

            yield self.create_json_message(result)
        except PASSTHROUGH_ERRORS:
            raise
        except Exception as exc:
            raise ToolInvokeError(f"Unexpected error while extracting data: {exc}") from exc
