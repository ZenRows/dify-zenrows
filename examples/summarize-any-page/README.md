# Summarize any page

A four-node Dify workflow: a URL goes in, the Zenrows **Fetch** tool returns the page as Markdown, an LLM node summarizes it, and the summary comes out.

```
User Input (url) → Zenrows Fetch (Markdown) → LLM → Output (summary)
```

## Import it

1. Install the [Zenrows plugin](https://marketplace.dify.ai/plugin/zenrows/zenrows) and authorize it with your Zenrows API key (**Integrations** > **Tools** > **Tool Plugin** > **Zenrows**).
2. In **Studio**, choose **Import DSL file** and select [`summarize-any-page.yml`](summarize-any-page.yml).
3. The LLM node uses OpenAI `gpt-5`. Install the OpenAI model provider, or switch the node to any model you already have.
4. Click **Test Run**, enter a URL such as `https://www.scrapingcourse.com/ecommerce/`, and read the summary on the **Result** tab.

## How it is wired

- **Fetch** keeps Adaptive stealth on and every other option at its default, so it escalates to JavaScript rendering or Premium Proxies only when a page needs it.
- The LLM prompt reads Fetch's `text` output. In plugin version 0.1.0, `content` is not populated as a variable, so a prompt that references it fails.
- Each Fetch call is billed like the equivalent Zenrows API request.

Full walkthrough with screenshots: [docs.zenrows.com/integrations/dify](https://docs.zenrows.com/integrations/dify).
