# Privacy Policy

This plugin lets Dify workflows call the Zenrows API to fetch and extract content
from public web pages.

## Data Collection

The plugin itself stores nothing. It processes two things at invocation time:

- **Your Zenrows API key.** Supplied by you when configuring the plugin in Dify.
  Dify stores it as an encrypted credential in your own workspace. The plugin
  reads it only to authenticate each request, and never logs it or writes it to
  disk.
- **The URLs and parameters you pass to a tool.** These are sent to the Zenrows
  API to perform the request you asked for.

The plugin does not collect personal data, does not profile users, and has no
telemetry of its own.

## Data Usage

Requests go to Zenrows and nowhere else:

- `https://api.zenrows.com` — Fetch and Extract
- `https://async.api.zenrows.com` — Batch

Zenrows retrieves the target page and returns its content to your Dify workflow.
Nothing is shared with any other third party. Zenrows' handling of that data is
covered by the Zenrows Privacy Policy: https://www.zenrows.com/privacy-policy

## Data Retention

The plugin retains nothing between invocations — it holds no database, no cache
and no persistent storage. Your API key's lifetime is controlled by your Dify
workspace; remove the credential there to delete it. Data held by the Zenrows
API is governed by the Zenrows Privacy Policy linked above.

## Contact

Privacy questions: support@zenrows.com
