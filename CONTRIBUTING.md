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

## Code layout

```
manifest.yaml          plugin metadata, declared outbound hosts, version
provider/zenrows.yaml  credential schema, and the list of tool yamls
provider/zenrows.py    credential validation on save
tools/<name>.yaml      one per tool: parameters, labels, LLM descriptions
tools/<name>.py        one per tool: parameter handling and the response shape
tools/client.py        every HTTP call the plugin makes, in one place
utils/errors.py        the Zenrows error taxonomy and the exceptions Dify shows
utils/batch.py         batch job polling, status vocabulary, run summaries
```

Two rules worth keeping. All HTTP goes through `tools/client.py` — that is what
makes the eventual SDK swap a one-file change. And all error interpretation goes
through `utils/errors.py`, so a tool never decides for itself what an API failure
means.

Each tool is a yaml/py pair and both must be listed in `provider/zenrows.yaml`
under `tools:`. Adding a tool without listing it there silently does nothing.

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

## Installing the package instead of debugging

Debug mode is the fast loop and is right for almost everything. Install the
packaged plugin when you want to test the artifact as it will actually ship —
Dify runs it in its own runtime rather than as a process on your machine, which
is a different path for anything binary (screenshot, PDF) and for the presigned
storage downloads in `batch_results`.

**Stop the debug process first.** Both register as `zenrows/zenrows`. If the
debug connection is live you will either get a conflict or silently test the
wrong one, and the run panel will not tell you which.

1. `Ctrl-C` the `uv run python -m main` process.
2. In Dify: **Integrations → Tool Plugin → Install → Local Package File**, and
   pick the built `.difypkg`. Accept the unverified-plugin warning — that is
   expected for anything not installed from the marketplace.
3. Check the badge on the plugin card. `LOCAL PLUGIN` means you are running the
   package; `DEBUGGING PLUGIN` means you are still on the debug connection.
   That badge is the only reliable way to tell which build a run used.

To go back to debug, uninstall the local plugin first, then start the process
again. Same collision, opposite direction.

Note that Dify may refuse to install the same version number over itself. If you
need to iterate on an installed build rather than in debug, bump the version in
`manifest.yaml`, `pyproject.toml`, `tools/client.py` and `README.md` — or just
use debug mode, which is what it is for.

## The bundled GitHub workflow does not work

`.github/workflows/plugin-publish.yml` came from the scaffold and has never been
run. Do not cut a GitHub release expecting it to publish anything. As generated
it pins the Dify CLI at 0.0.6, runs on `depot-ubuntu-24.04`, checks out a
`zenrows/dify-plugins` fork that does not exist, needs a `PLUGIN_ACTION` PAT
secret we have not created, and pushes a branch named
`bump-<name>-plugin-<version>` while telling `gh pr create` the head is
`<author>:<name>-<version>` — that mismatch alone makes PR creation fail.

It is kept because it is a reasonable starting point if we later want to
automate submission. Until someone fixes it, submission is manual: package,
validate, fork `langgenius/dify-plugins`, add the plugin under
`<Author>/<plugin-name>/`, open a PR.

## Tests

```bash
uv run python tests/test_units.py
```

No framework, no dependencies, exits non-zero on failure. Excluded from the
package.

It deliberately covers a narrow set: every function in it shipped a real bug
during development — string booleans read as truthy, a default-on parameter
that defaulted off, the AUTH010 overloading, and the status filter. It is a
regression net for the things that have actually broken, not a coverage
exercise. Anything you touch in `utils/errors.py` should be pinned here too.

Tool behaviour beyond that is verified by running the plugin against the live
API in a Dify workflow — see Verified below.

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
- Adaptive Stealth Mode, both directions, against
  `scrapingcourse.com/antibot-challenge` with `js_render` and `premium_proxy`
  both off: on, the challenge is bypassed in 25s; off, it fails in 1.1s with
  the REQS002 the SDK docstring warns about
- `batch_results` fetches bodies in parallel — 25 bodies, 13.47s to 3.72s
- Installed as a packaged `.difypkg` on Dify Cloud (not debug mode) and
  `batch_results` run with `include_content` — content came back, so the
  presigned-storage fetch is not blocked and `network.domains` is not enforced
  as a hard egress allowlist. Note this was a sideloaded local plugin; a
  marketplace-verified one could in principle run under a different policy.

## Not verified — please look here first

**The AUTH010 fallback has never fired live.** The gate matches on target domain
*or* caller email, and the account used for testing is on the allowlist, so
`method: auto` returned real data on four unrelated domains. The decision logic
is unit-tested; the HTTP round trip through it is not.
