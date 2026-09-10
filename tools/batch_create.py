import re
from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from zenrows.batch import BatchAPIError

from utils.batch import batch_client, reraise_batch_error, run_summary, wait_for_run
from utils.errors import (
    PASSTHROUGH_ERRORS,
    ToolInvokeError,
    ToolParameterValidationError,
    as_bool,
    require_param,
)

# The API accepts 1-1000 tasks per job and rejects the whole submission
# otherwise, so check here rather than paying a round trip to find out.
MAX_TASKS = 1000

RESPONSE_TYPES = {"markdown": "markdown", "plaintext": "plaintext"}


def _parse_urls(raw: str) -> list[str]:
    """Split on newlines or commas — people paste both."""
    candidates = [u.strip() for u in re.split(r"[\n,]+", raw) if u.strip()]
    if not candidates:
        raise ToolParameterValidationError("At least one URL is required.")
    if len(candidates) > MAX_TASKS:
        raise ToolParameterValidationError(
            f"A batch job takes at most {MAX_TASKS} URLs; {len(candidates)} were given. "
            "Split the list across several jobs."
        )
    bad = [u for u in candidates if not u.startswith(("http://", "https://"))]
    if bad:
        preview = ", ".join(bad[:3])
        more = f" (and {len(bad) - 3} more)" if len(bad) > 3 else ""
        raise ToolParameterValidationError(
            f"Every URL must start with http:// or https://. Invalid: {preview}{more}"
        )
    return candidates


class BatchCreateTool(Tool):
    def _invoke(
        self, tool_parameters: dict[str, Any]
    ) -> Generator[ToolInvokeMessage, None, None]:
        urls = _parse_urls(str(require_param(tool_parameters, "urls", "URLs are required.")))

        zenrows_params: dict[str, Any] = {}
        response_type = (tool_parameters.get("response_type") or "markdown").strip().lower()
        if response_type in RESPONSE_TYPES:
            zenrows_params["response_type"] = RESPONSE_TYPES[response_type]
        if as_bool(tool_parameters.get("js_render")):
            zenrows_params["js_render"] = True
        if as_bool(tool_parameters.get("premium_proxy")):
            zenrows_params["premium_proxy"] = True

        api_key = str(self.runtime.credentials.get("api_key", "")).strip()

        try:
            # `submit_regular` is the closed one-shot job -- the SDK's own
            # docstring: "created with status=closed, so no further add_tasks
            # calls are accepted", which is exactly the body we used to post.
            with batch_client(api_key) as client:
                ref = client.submit_regular(
                    urls=list(urls), zenrows_params=zenrows_params or None
                )
                job_id = ref.job_id
                if not job_id:
                    raise ToolInvokeError("Zenrows accepted the job but returned no job_id.")

                # The submit response already carries the initial run, so the
                # first summary costs no extra round-trip.
                submitted = (
                    ref.submit_response.model_dump(mode="json")
                    if ref.submit_response
                    else {}
                )
                accepted = ref.accepted_tasks

            result = run_summary(submitted)
            result["accepted_tasks"] = accepted

            if as_bool(tool_parameters.get("wait")):
                job = wait_for_run(api_key, job_id)
                result = run_summary(job)
                result["accepted_tasks"] = accepted

            # `run_summary` reads job_id out of whatever payload it was handed.
            # The POST response carries one; GET /jobs/{id} need not, since the
            # id is already in the path — so with `wait` on, the summary's
            # job_id came back null and every downstream node lost the
            # reference. The id from the submission is the authoritative one.
            result["job_id"] = job_id

            if result.get("finished"):
                message = (
                    f"Batch job {job_id} finished: {result.get('successful')} of "
                    f"{result.get('total')} succeeded. Use Batch Results to collect them."
                )
            else:
                message = (
                    f"Batch job {job_id} submitted with {result.get('accepted_tasks')} "
                    f"task(s), currently {result.get('status')}. Check progress with "
                    "Batch Status."
                )

            yield self.create_text_message(message)
            yield self.create_json_message(result)
            # output_schema alone only populates Dify's variable picker. A
            # downstream node can resolve these only if the tool also emits
            # them as variable messages -- otherwise the reference arrives as
            # the literal selector path and the call 404s.
            for key in ("job_id", "run_id", "status", "finished", "accepted_tasks"):
                if key in result:
                    yield self.create_variable_message(key, result[key])
        except BatchAPIError as exc:
            # utils/errors.py owns every user-facing message -- the SDK's
            # own string must never reach a workflow.
            reraise_batch_error(exc, "submitting the batch job")
        except PASSTHROUGH_ERRORS:
            raise
        except Exception as exc:
            raise ToolInvokeError(f"Unexpected error while submitting the batch job: {exc}") from exc
