"""Save a URL to a file: any size, any type, straight to disk, never through the model."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from harness.paths import HarnessPaths
from harness.security.net import BlockedAddress
from harness.skills.base import (
    ApprovalRequest,
    Handoff,
    Skill,
    SkillContext,
    SkillError,
    SkillResult,
)
from harness.skills.builtin._files import human_size
from harness.web.fetch import FetchError


class DownloadUrlSkill(Skill):
    name = "download_url"
    description = (
        "Download a URL to a file on disk (a STEP or other CAD model, a PDF, an image, a zip, "
        "a datasheet...). The content goes straight to the file and you never see it: you get "
        "the path, size and type back, then use other skills on the file (open_with_default_app, "
        "pdf_read, view_image...). Use this, never get_url_raw plus write_file, to save "
        "anything from the web. Saved under ~/.harness/downloads (an untrusted location) "
        "unless you give a path."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "path": {
                "type": "string",
                "description": "Optional file (or existing folder) to save to. Never overwrites.",
            },
        },
        "required": ["url"],
    }
    needs_approval = True
    timeout_s = 600
    handoff_description = "The file manager shows the downloaded file."

    def approval_request(self, args, ctx):
        destination = self._destination(args, ctx)
        reason = None
        if ctx.services.path_policy is not None:
            reason = ctx.services.path_policy.denial_reason(destination)
        return ApprovalRequest(
            self.name,
            "Download this file?",
            f"{args['url'].strip()}\n-> {destination}",
            "text",
            reason=reason,
            args=args,
        )

    def _destination(self, args, ctx: SkillContext) -> Path:
        name = _file_name(args["url"])
        if args.get("path"):
            path = ctx.resolve(args["path"])
            return path / name if path.is_dir() else path
        return HarnessPaths().downloads_dir / name

    def run(self, args, ctx: SkillContext) -> SkillResult:
        fetcher = ctx.need("fetcher")
        url = args["url"].strip()
        destination = self._destination(args, ctx)
        if destination.exists():
            raise SkillError(
                f"{destination} already exists; give another path (nothing is overwritten)"
            )
        ctx.say(f"Downloading {destination.name}...")
        try:
            final_url, size, content_type = fetcher.download(
                url, destination, ctx.config.web.max_download_mb * 2**20, ctx.cancel
            )
        except BlockedAddress as exc:
            return SkillResult.fail(f"blocked: {exc}", Handoff.browser(url))
        except FetchError as exc:
            return SkillResult.fail(str(exc), Handoff.browser(url))
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        moved = f" (redirected to {final_url})" if final_url != url else ""
        return SkillResult(
            f"Saved {url}{moved} to {destination}: {human_size(size)}, "
            f"{content_type or 'unknown type'}, sha256 {digest}.",
            Handoff.file_manager(destination),
            data={"path": str(destination), "size": size},
        )


def _file_name(url: str) -> str:
    """A safe file name from the URL's last path segment."""
    name = Path(unquote(urlparse(url.strip()).path)).name
    name = re.sub(r"[^\w.\- ]+", "_", name).strip(" .")
    return name[:150] or "download"
