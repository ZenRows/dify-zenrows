import json
from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from tools.client import (
    DEFAULT_MAX_RESULTS,
    MAX_RESULTS_CEILING,
    fetch_result_bodies,
)
from zenrows.batch import BatchAPIError

from utils.batch import batch_client, reraise_batch_error
from utils.errors import (
    PASSTHROUGH_ERRORS,
    ToolInvokeError,
    ToolParameterValidationError,
    as_bool,
    require_param,
)


def _coerce_max_results(value: Any) -> int:
    if value is None or value == "":
        return DEFAULT_MAX_RESULTS
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        raise ToolParameterValidationError("max_results must be a number.") from None
    if parsed < 1:
        raise ToolParameterValidationError("max_results must be at least 1.")
    if parsed > MAX_RESULTS_CEILING:
        raise ToolParameterValidationError(
            f"max_results cannot exceed {MAX_RESULTS_CEILING}. For larger jobs, page "
            "through the results or fetch the content outside the workflow."
        )
    return parsed


class BatchResultsTool(Tool):
    def _invoke(
        self, tool_parameters: dict[str, Any]
    ) -> Generator[ToolInvokeMessage, None, None]:
        job_id = str(require_param(tool_parameters, "job_id", "A job ID is required.")).strip()
        max_results = _coerce_max_results(tool_parameters.get("max_results"))
        include_content = as_bool(tool_parameters.get("include_content", True))
        # "all" is an explicit option rather than an empty value: Dify rejects the
        # whole tool declaration if a select option has `value: ""`, and without a
        # third option a user who picks a filter has no way back to every task.
        status_filter = (tool_parameters.get("status_filter") or "").strip().lower() or None
        if status_filter == "all":
            status_filter = None

        api_key = str(self.runtime.credentials.get("api_key", "")).strip()

        try:
            rows: list[dict[str, Any]] = []
            cursor: str | None = None
            more_available = False

            # Page until we have enough rows. The API paginates with an opaque
            # cursor and returns next_cursor only while more pages exist.
            while len(rows) < max_results:
                with batch_client(api_key) as client:
                    page = client.list_results(
                        job_id,
                        status=status_filter or None,
                        cursor=cursor or None,
                    )

                # Dump each TaskResult to a plain dict so everything below --
                # and the tool's declared output shape -- is unchanged. The
                # models carry no aliases, and mode="json" renders `url` and
                # the status enum as strings.
                rows.extend(r.model_dump(mode="json") for r in page.results)
                cursor = page.next_cursor
                if not cursor:
                    break

            if len(rows) > max_results:
                more_available = True
                rows = rows[:max_results]
            elif cursor:
                more_available = True

            results: list[dict[str, Any]] = []
            for row in rows:
                entry: dict[str, Any] = {
                    "task_id": row.get("task_id"),
                    "url": row.get("url"),
                    "status": row.get("status"),
                }
                if row.get("error"):
                    entry["error"] = row["error"]
                results.append(entry)

            if include_content:
                # Fetch every body in one parallel pass rather than one per
                # row: sequentially this dominated the tool's runtime.
                fetchable = [
                    i for i, row in enumerate(rows) if row.get("status") == "successful"
                ]
                bodies = fetch_result_bodies(
                    [rows[i].get("result_url") for i in fetchable]
                )
                for i, body in zip(fetchable, bodies):
                    if body is None:
                        # Either over the per-body size cap or the presigned
                        # URL would not download. Flag it and keep going.
                        results[i]["content"] = None
                        results[i]["content_unavailable"] = True
                    else:
                        results[i]["content"] = body

            payload = {
                "results": results,
                "returned": len(results),
                "truncated": more_available,
            }

            if not results:
                yield self.create_text_message(
                    f"No results for job {job_id}"
                    + (f" with status {status_filter}." if status_filter else ".")
                )
            else:
                summary = f"{len(results)} result(s) from job {job_id}"
                if more_available:
                    summary += f" (more available — raise max_results, currently {max_results})"
                yield self.create_text_message(summary + ".")

            yield self.create_json_message(payload)
            # output_schema alone only populates Dify's variable picker. A
            # downstream node can resolve these only if the tool also emits
            # them as variable messages -- otherwise the reference arrives as
            # the literal selector path and the call 404s.
            for key in ("results", "returned", "truncated"):
                yield self.create_variable_message(key, payload[key])
        except BatchAPIError as exc:
            reraise_batch_error(exc, "collecting the batch results")
        except PASSTHROUGH_ERRORS:
            raise
        except Exception as exc:
            raise ToolInvokeError(f"Unexpected error while collecting batch results: {exc}") from exc
