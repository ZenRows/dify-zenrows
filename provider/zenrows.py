from typing import Any

from dify_plugin import ToolProvider
from dify_plugin.errors.tool import ToolProviderCredentialValidationError

from tools.client import verify_api_key
from utils.errors import ZenRowsApiError


class ZenRowsProvider(ToolProvider):
    def _validate_credentials(self, credentials: dict[str, Any]) -> None:
        """Runs when a user saves their API key.

        Verifies against the subscription-details endpoint rather than a
        scrape: it is a read of the account's own billing state and does not
        consume credits, so saving a credential is free.
        """
        api_key = credentials.get("api_key")
        if not api_key or not str(api_key).strip():
            raise ToolProviderCredentialValidationError(
                "An API key is required. You can find yours in the ZenRows dashboard."
            )

        try:
            verify_api_key(str(api_key).strip())
        except ZenRowsApiError as exc:
            # 401 with AUTH001/AUTH003 is the ordinary "bad key" answer.
            raise ToolProviderCredentialValidationError(str(exc)) from exc
        except Exception as exc:
            raise ToolProviderCredentialValidationError(
                f"Could not verify the API key: {exc}"
            ) from exc
