"""S12: search the web via the configured SearXNG instance, results read by the web reader."""

from __future__ import annotations

from harness.skills.base import Handoff, Skill, SkillContext, SkillResult
from harness.web.searxng import SearchError


class WebSearchSkill(Skill):
    name = "web_search"
    description = (
        "Search the web (through the user's own SearXNG instance). The web reader model reads the results and "
        "reports the relevant ones by number; the numbered URLs are appended verbatim so you can web_fetch them."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "question": {
                "type": "string",
                "description": "What you are trying to find out; guides the reader's report.",
            },
            "count": {
                "type": "integer",
                "description": "Number of results to consider (default 10, max 20).",
            },
        },
        "required": ["query"],
    }
    handoff_description = "The search results page opens in the browser."

    def run(self, args, ctx: SkillContext) -> SkillResult:
        searx = ctx.need("searx")
        reader = ctx.need("web_reader")
        query = args["query"].strip()
        count = max(1, min(20, int(args.get("count") or 10)))
        from urllib.parse import quote_plus

        handoff = Handoff.browser(
            f"{searx.base_url}/search?q={quote_plus(query)}", label="Open results in browser"
        )
        try:
            results = searx.search(query, count=count)
        except SearchError as exc:
            return SkillResult.fail(str(exc), handoff)
        if not results:
            return SkillResult(f"No results for {query!r}.", handoff)
        ctx.check_cancelled()
        document = "\n\n".join(
            f"[{i}] {r.title}\nURL: {r.url}\n{r.snippet}" for i, r in enumerate(results, 1)
        )
        question = (
            args.get("question")
            or f"Which results are most relevant to the search '{query}', and what do they say? Refer to results by their [number]."
        )
        report = reader.read(
            document, question, source=f"SearXNG results for {query!r}", cancel=ctx.cancel
        )
        if not report.ok:
            return SkillResult.fail(report.error or "web reader failed", handoff)
        urls = "\n".join(f"[{i}] {r.url}" for i, r in enumerate(results, 1))
        return SkillResult(
            f"[Web reader report on {len(results)} results for {query!r}]\n{report.text}\n\nResult URLs:\n{urls}",
            handoff,
        )
