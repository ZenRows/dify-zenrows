# Working on this plugin

Notes for anyone running or reviewing this locally. This file is excluded from
the packaged `.difypkg` — it is not part of the published plugin.

## What this is

A Dify Tool plugin wrapping the Zenrows API. Five tools: Fetch, Extract, and
Batch create / status / results.

It talks to the REST API directly rather than using the `zenrows` Python SDK.
That is not a preference — Extract and Batch exist on the SDK's `main` but were
never published, so PyPI still serves 1.4.0 from Nov 2024. Every HTTP call is
contained in `tools/client.py` so the swap is one file when the SDK ships.
Tracked in ACT-1598.

## Requirements

- Python 3.12+ (`dify-plugin>=0.9.0` requires it; the scaffold's `>=3.11` was wrong)
- [`uv`](https://docs.astral.sh/uv/)
- The Dify CLI — `brew install langgenius/dify/dify`

## Running it against Dify

The plugin runs on your machine and registers itself into Dify over a debug
connection. There is no hot reload: **every change needs a restart**, and YAML
changes need the tool list re-checked (see Gotchas).

1. Get a debug key: in Dify, **Plugins → Debugging**. That panel shows the host,
   port and key.
2. Copy `.env.example` to `.env` and fill in:

   ```
   INSTALL_METHOD=remote
   REMOTE_INSTALL_URL=debug.dify.ai:5003
   REMOTE_INSTALL_KEY=<key from the Debugging panel>
   ```

3. Install and run:

   ```bash
   uv sync --python 3.12
   uv run python -m main
   ```

   Success is one line, `Installed tool: zenrows`, with nothing after it. A
   `handshake failed, invalid key` loop means the key has been rotated — get a
   fresh one from the Debugging panel.

4. In Dify, **Integrations → Tool Plugin**. The card should read
   **Zenrows 0.1.0** and be marked `DEBUGGING PLUGIN`, with 5 actions listed.
   Add a tool to any workflow, paste a Zenrows API key when prompted, and run
   the node.

## Packaging and validation

```bash
cd ..
dify plugin package ./zenrows -o zenrows-0.1.0.difypkg

git clone https://github.com/langgenius/dify-marketplace-toolkit
brew install yq

python3 dify-marketplace-toolkit/validator/validate-difypkg.py \
  zenrows-0.1.0.difypkg \
  --pr-body-file pr-body.md \
  --output-dir validation-report
```

Exit 0 and "Blocking failures: 0" is the bar. `--pr-body-file` is not optional:
without it the sensitive-capability disclosure check is skipped, and that one is
blocking when it runs for real on the submission PR.

## Gotchas worth knowing before you change anything

**A malformed tool YAML takes down the whole plugin, not just that tool.** Dify
rejects the entire declaration, nothing registers, and the connection drops with
no useful error. An empty `value:` on a select option is enough to trigger it.
`yaml.safe_load` parses it fine and the marketplace validator does not catch it.
After any YAML change, restart and confirm all five actions still list.

**Dify sends booleans as strings.** An unticked toggle arrives as `"0"` or
`"False"`, both truthy in Python. Everything goes through `as_bool()` in
`utils/errors.py`. Using a raw truthiness check would silently enable premium
proxy on every call.

**`AUTH010` means two different things** — "your plan doesn't include Extract"
and "Extract isn't enabled for this domain yet". Only the second is safe to
recover from by falling back to autoparse; falling back on the first silently
downgrades every call and never tells the user to upgrade. They are only
distinguishable by the `detail` text. `is_domain_scoped_extract_restriction()`
mirrors `ScraperApiException::isDomainScopedExtractRestriction` in the Zenrows
app — if that logic changes upstream, change it here too.

**Our caps are deliberately not the SDK's.** The SDK defaults to 100,000 files
at 50 MiB and waits up to 300s, which suits a CLI writing to disk. Dify kills an
invocation at 120s and the result goes into a workflow variable, so this plugin
uses 25 results (raisable to 200), 1 MiB per body, and a 100s wait budget.

## Verified

Against the live API in a Dify workflow:

- Credential validation, including a bad key
- Fetch: Markdown, JavaScript rendering with wait-for-selector, screenshot
- Extract: all four methods; the empty-result case returns `empty: true` and an
  explanation rather than failing or returning silence
- Batch: 3-URL and 30-URL jobs end to end; `max_results` boundaries, both URL
  separators, mixed valid and invalid URLs, the 1000 cap
- Errors: 401 bad key, 404 unknown job, REQS001 blocked domain
- `batch_results` fetches bodies in parallel — 25 bodies, 13.47s to 3.72s

## Not verified — please look here first

**The AUTH010 fallback has never fired live.** The gate matches on target domain
*or* caller email, and the account used for testing is on the allowlist, so
`method: auto` returned real data on four unrelated domains. The decision logic
is unit-tested; the HTTP round trip through it is not.

**Everything ran in debug mode**, outside Dify's sandbox. The plugin has never
run as a properly installed package.

**`network.domains` may or may not be enforced at runtime.** `batch_results`
downloads bodies from presigned storage URLs whose hostname only exists at
request time, so it cannot be declared statically. If Dify enforces the
allowlist, that call would be blocked once installed for real. Unresolved —
see the notes on ACT-1564.

**Adaptive Stealth Mode was added last** to match `ZenRowsClient.extract()` and
has not been exercised against a site that actually needs escalation.
