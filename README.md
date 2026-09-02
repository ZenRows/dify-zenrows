## Zenrows

**Author:** zenrows
**Version:** 0.1.0
**Type:** tool
**Repository:** https://github.com/ZenRows/dify-zenrows
**Contact:** support@zenrows.com

### Description

Fetch and extract data from any website with Zenrows, from inside your Dify
workflows and agents. Zenrows handles JavaScript rendering, anti-bot protection
and proxy rotation, so a tool call returns the page content rather than a block
page.

### Tools

| Tool | What it does |
| --- | --- |
| **Fetch** | Retrieve one page. Optional JavaScript rendering, premium proxies, country geolocation, wait-for-selector and full-page screenshot. Returns HTML, Markdown, plain text, PDF or an image. |
| **Extract** | Return structured JSON instead of page content — site-tailored extraction, general-purpose autoparse, your own CSS selectors, or built-in filters for emails, links, tables and similar. |
| **Batch Create** | Submit up to 1000 URLs as a single asynchronous job. Returns a job ID, or waits for the job if it is small. |
| **Batch Status** | Check how far a job has progressed, how many tasks failed, and what it has cost so far. |
| **Batch Results** | Collect the scraped content from a finished job. |

Use **Fetch** when you want the page. Use **Extract** when you want fields out of
the page. Use **Batch** when you have more URLs than one request should carry.

### Setup

1. Get a Zenrows API key from https://app.zenrows.com — the dashboard shows it
   under your account.
2. In Dify, go to **Integrations → Tool Plugin**, install Zenrows, and paste the
   key when prompted.
3. Add a Zenrows tool to any workflow or agent.

**Connection requirements.** The plugin makes outbound HTTPS requests to
`api.zenrows.com` and `async.api.zenrows.com`. Both must be reachable from
wherever your Dify instance runs. No inbound connections and no local services
are required.

### Usage

Add a Zenrows node to a workflow and pass it a URL. The tool returns the page
content, which you can feed into an LLM node, a knowledge base, or any
downstream step.

### Development

Working on the plugin itself, or reviewing it? See
[CONTRIBUTING.md](CONTRIBUTING.md) — running it against Dify in debug mode,
building the `.difypkg`, validating it, installing it locally, and the known
gotchas.

### License

MIT — see [LICENSE](LICENSE).
