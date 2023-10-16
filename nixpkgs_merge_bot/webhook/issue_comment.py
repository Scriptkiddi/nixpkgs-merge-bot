import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from ..github import GithubClientError, get_github_client
from ..nix import merge_check
from ..settings import Settings
from .http_response import HttpResponse

logger = logging.getLogger(__name__)


@dataclass
class Issue:
    user_id: int
    user_login: str
    text: str
    action: str
    comment_id: int
    repo_owner: str
    repo_name: str
    issue_number: int


def issue_response(action: str) -> HttpResponse:
    return HttpResponse(200, {}, json.dumps({"action": action}).encode("utf-8"))


def issue_comment(body: dict[str, Any], settings: Settings) -> HttpResponse:
    issue = Issue(
        action=body["action"],
        user_id=body["comment"]["user"]["id"],
        user_login=body["comment"]["user"]["login"],
        text=body["comment"]["body"],
        comment_id=body["comment"]["id"],
        repo_owner=body["repository"]["owner"]["login"],
        repo_name=body["repository"]["name"],
        issue_number=body["issue"]["number"],
    )
    logger.debug(f"issue_comment: {issue}")
    if body["issue"].get("pull_request"):
        return issue_response("ignore")
    if issue.action not in ("created", "edited"):
        return issue_response("ignore")
    stripped = re.sub("(<!--.*?-->)", "", issue.text, flags=re.DOTALL)
    bot_name = re.escape(settings.bot_name)
    if not re.match(rf"@{bot_name}\s+merge", stripped):
        return issue_response("no-command")

    check = merge_check(body["issue"]["number"], issue.user_id)
    client = get_github_client(settings)
    client.create_issue_reaction(
        issue.repo_owner,
        issue.repo_name,
        issue.issue_number,
        issue.comment_id,
        "rocket",
    )
    if not check.permitted:
        msg = f"@{issue.user_login} merge not permitted: \n"
        for filename, reason in check.decline_reasons.items():
            msg += f"{filename}: {reason}\n"
        client.create_issue_comment(
            issue.repo_owner,
            issue.repo_name,
            issue.issue_number,
            msg,
        )
        return issue_response("not-permitted")

    try:
        client.merge_pull_request(
            issue.repo_owner, issue.repo_name, issue.issue_number, check.sha
        )
    except GithubClientError as e:
        logger.exception("merge failed")
        msg = "\n".join(
            [
                f"@{issue.user_login} merge failed:",
                "```",
                f"{e.code} {e.reason}: {e.body}",
                "```",
            ]
        )

        client.create_issue_comment(
            issue.repo_owner,
            issue.repo_name,
            issue.issue_number,
            msg,
        )
        return issue_response("merge-failed")

    return issue_response("merge")
