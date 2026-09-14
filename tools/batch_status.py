from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from utils.batch import get_job, run_summary
from utils.errors import PASSTHROUGH_ERRORS, ToolInvokeError, redact, require_param


class BatchStatusTool(Tool):
    def _invoke(
        self, tool_parameters: dict[str, Any]
    ) -> Generator[ToolInvokeMessage, None, None]:
        job_id = str(require_param(tool_parameters, "job_id", "A job ID is required.")).strip()
        api_key = str(self.runtime.credentials.get("api_key", "")).strip()

        try:
            job = get_job(api_key, job_id)
            result = run_summary(job)
            # Same reason as batch_create: run_summary takes job_id from the
            # payload, and GET /jobs/{id} need not repeat it in the body. The
            # caller gave us the id, so echo that rather than a possible null.
            result["job_id"] = job_id

            if result.get("finished"):
                message = (
                    f"Job {job_id} is {result.get('status')}: "
                    f"{result.get('successful')} of {result.get('total')} succeeded, "
                    f"{result.get('failed')} failed."
                )
            else:
                message = (
                    f"Job {job_id} is {result.get('status')}: "
                    f"{result.get('completed')} of {result.get('total')} done."
                )

            yield self.create_text_message(message)
            yield self.create_json_message(result)
            # output_schema alone only populates Dify's variable picker. A
            # downstream node can resolve these only if the tool also emits
            # them as variable messages -- otherwise the reference arrives as
            # the literal selector path and the call 404s.
            yield self.create_variable_message("job_id", result.get("job_id", job_id))
            for key in ("status", "finished", "total", "completed",
                        "successful", "failed", "failure_reasons", "spend"):
                if key in result:
                    yield self.create_variable_message(key, result[key])
        except PASSTHROUGH_ERRORS:
            raise
        except Exception as exc:
            raise ToolInvokeError(f"Unexpected error while checking the batch job: {redact(exc)}") from exc
