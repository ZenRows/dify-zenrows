from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from utils.batch import get_job, run_summary
from utils.errors import PASSTHROUGH_ERRORS, ToolInvokeError, require_param


class BatchStatusTool(Tool):
    def _invoke(
        self, tool_parameters: dict[str, Any]
    ) -> Generator[ToolInvokeMessage, None, None]:
        job_id = str(require_param(tool_parameters, "job_id", "A job ID is required.")).strip()
        api_key = str(self.runtime.credentials.get("api_key", "")).strip()

        try:
            job = get_job(api_key, job_id)
            result = run_summary(job)

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
        except PASSTHROUGH_ERRORS:
            raise
        except Exception as exc:
            raise ToolInvokeError(f"Unexpected error while checking the batch job: {exc}") from exc
