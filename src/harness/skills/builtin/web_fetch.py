"""S2: read a web page through the web reader (SEC2, SEC3, SEC4)."""

from __future__ import annotations

from harness.security.net import BlockedAddress
from harness.skills.base import Handoff, Skill, SkillContext, SkillResult
from harness.web.fetch import FetchError


class WebFetchSkill(Skill):
    name = "web_fetch"
    description = (
        "Fetch a specific web page and have the web reader model answer a question about it or summarize it. "
        "You never see the raw page; you get the reader's report. Prefer this over get_url_raw."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "question": {
                "type": "string",
                "description": "What you want to know from the page. Omit for a summary.",
            },
        },
        "required": ["url"],
    }
    handoff_description = "The URL opens in the user's default browser."

    def run(self, args, ctx: SkillContext) -> SkillResult:
        fetcher = ctx.need("fetcher")
        reader = ctx.need("web_reader")
        url = args["url"].strip()
        handoff = Handoff.browser(url)
        try:
            page = fetcher.fetch(url)
        except BlockedAddress as exc:
            return SkillResult.fail(f"blocked: {exc}", handoff)
        except FetchError as exc:
            return SkillResult.fail(str(exc), handoff)
        ctx.check_cancelled()
        document = page.text
        if page.title:
            document = f"Title: {page.title}\n\n{document}"
        if page.links:
            links = "\n".join(f"- {text}: {href}" for text, href in page.links[:100])
            document += f"\n\nLinks on the page:\n{links}"
        result = reader.read(
            document, args.get("question"), source=page.final_url, cancel=ctx.cancel
        )
        if not result.ok:
            return SkillResult.fail(result.error or "web reader failed", handoff)
        header = (
            f"[Web reader report for {page.final_url}"
            + (" (page truncated)" if page.truncated else "")
            + "]\n"
        )
        return SkillResult(header + (result.text or ""), handoff)
