## ZenRows

**Author:** zenrows
**Version:** 0.0.1
**Type:** tool
**Repository:** https://github.com/ZenRows/dify-zenrows
**Contact:** support@zenrows.com

### Description

Fetch and extract data from any website with ZenRows, from inside your Dify
workflows and agents. ZenRows handles JavaScript rendering, anti-bot protection
and proxy rotation, so a tool call returns the page content rather than a block
page.

### Setup

1. Get a ZenRows API key from https://app.zenrows.com — the dashboard shows it
   under your account.
2. In Dify, go to **Integrations → Tool Plugin**, install ZenRows, and paste the
   key when prompted.
3. Add a ZenRows tool to any workflow or agent.

**Connection requirements.** The plugin makes outbound HTTPS requests to
`api.zenrows.com` and `async.api.zenrows.com`. Both must be reachable from
wherever your Dify instance runs. No inbound connections and no local services
are required.

### Usage

Add a ZenRows node to a workflow and pass it a URL. The tool returns the page
content, which you can feed into an LLM node, a knowledge base, or any
downstream step.

### License

MIT — see [LICENSE](LICENSE).
