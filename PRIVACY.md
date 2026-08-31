# Privacy Policy

This plugin lets Dify workflows call the ZenRows API to fetch and extract content
from public web pages.

## Data Collection

The plugin itself stores nothing. It processes two things at invocation time:

- **Your ZenRows API key.** Supplied by you when configuring the plugin in Dify.
  Dify stores it as an encrypted credential in your own workspace. The plugin
  reads it only to authenticate each request, and never logs it or writes it to
  disk.
- **The URLs and parameters you pass to a tool.** These are sent to the ZenRows
  API to perform the request you asked for.

The plugin does not collect personal data, does not profile users, and has no
telemetry of its own.

## Data Usage

Requests go to ZenRows and nowhere else:

- `https://api.zenrows.com` — Fetch and Extract
- `https://async.api.zenrows.com` — Batch

ZenRows retrieves the target page and returns its content to your Dify workflow.
Nothing is shared with any other third party. ZenRows' handling of that data is
covered by the ZenRows Privacy Policy: https://www.zenrows.com/privacy-policy

## Data Retention

The plugin retains nothing between invocations — it holds no database, no cache
and no persistent storage. Your API key's lifetime is controlled by your Dify
workspace; remove the credential there to delete it. Data held by the ZenRows
API is governed by the ZenRows Privacy Policy linked above.

## Contact

Privacy questions: support@zenrows.com
