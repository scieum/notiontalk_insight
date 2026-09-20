#!/usr/bin/env python3
"""Create or update a single-file publication PR in scieum/notiontalk_insight.

Dry-run is deliberately offline.  Execute mode requires the operator token
before constructing an API client and uses GitHub's Contents API so no local
branch, index, commit, or worktree is mutated.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.publication import (
    PublicationValidationError,
    count_items,
    load_publication,
    pretty_json,
    publication_path,
    strict_json_load_bytes,
)


REPOSITORY = "scieum/notiontalk_insight"
REPOSITORY_OWNER = "scieum"
TOKEN_ENV = "TALKINSIGHT_GITHUB_TOKEN"
API_VERSION = "2022-11-28"
TEMPLATE_PATH = ROOT / ".github" / "PULL_REQUEST_TEMPLATE" / "publication.md"


class GitHubAPIError(RuntimeError):
    def __init__(self, status: int | None, message: str):
        label = str(status) if status is not None else "network"
        super().__init__(f"GitHub API {label}: {message}")
        self.status = status


class GitHubClient:
    def __init__(self, token: str):
        self.token = token

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        data = None
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            f"https://api.github.com/repos/{REPOSITORY}{path}",
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "talkinsight-publication-automation",
                "X-GitHub-Api-Version": API_VERSION,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(detail).get("message", detail)
            except json.JSONDecodeError:
                pass
            raise GitHubAPIError(exc.code, str(detail)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise GitHubAPIError(None, f"request failed: {reason}") from exc
        return json.loads(body) if body else None


def _not_found(call):
    try:
        return call()
    except GitHubAPIError as exc:
        if exc.status == 404:
            return None
        raise


def render_pr_body(publication: dict[str, Any]) -> str:
    counts = count_items(publication)
    replacements = {
        "content_id": publication["content_id"],
        "title": publication["title"],
        "period": publication["period"]["label"],
        "issue": str(publication["issue"]),
        "topics_count": str(counts["topics"]),
        "faq_count": str(counts["faq"]),
        "unresolved_count": str(counts["unresolved"]),
        "tips_count": str(counts["tips"]),
        "actions_count": str(counts["actions"]),
        "consent_mode": publication["privacy"]["consent_mode"],
        "pii_scan_passed": str(publication["privacy"]["pii_scan_passed"]).lower(),
        "source_ids_stripped": str(publication["privacy"]["source_ids_stripped"]).lower(),
        "content_sha256": publication["content_sha256"],
    }
    body = TEMPLATE_PATH.read_text(encoding="utf-8")
    for key, value in replacements.items():
        body = body.replace("{{" + key + "}}", value)
    if "{{" in body or "}}" in body:
        raise PublicationValidationError("publication PR template has unresolved placeholders")
    return body


def dry_run_plan(
    publication: dict[str, Any], existing_publication: dict[str, Any] | None = None
) -> dict[str, Any]:
    branch = f"publication/{publication['content_id']}"
    path = publication_path(publication["content_id"]).as_posix()
    if existing_publication is None:
        status = "would_create_or_update"
    elif existing_publication["content_id"] != publication["content_id"]:
        raise PublicationValidationError("existing publication content_id does not match")
    elif existing_publication["content_sha256"] == publication["content_sha256"]:
        status = "noop"
    else:
        status = "would_update"
    return {
        "mode": "offline_dry_run",
        "status": status,
        "repository": REPOSITORY,
        "base": "main",
        "branch": branch,
        "path": path,
        "content_sha256": publication["content_sha256"],
        "network_requests": 0,
        "local_git_mutations": 0,
    }


def _contents(client: GitHubClient, path: str, ref: str) -> dict[str, Any] | None:
    encoded_path = urllib.parse.quote(path, safe="/")
    query = urllib.parse.urlencode({"ref": ref})
    result = _not_found(lambda: client.request("GET", f"/contents/{encoded_path}?{query}"))
    if result is not None and not isinstance(result, dict):
        raise GitHubAPIError(500, "contents response was not a file")
    return result


def _decode_content(response: dict[str, Any]) -> dict[str, Any]:
    if response.get("encoding") != "base64" or not isinstance(response.get("content"), str):
        raise GitHubAPIError(500, "contents response was not base64")
    try:
        return strict_json_load_bytes(
            base64.b64decode(response["content"]), source="remote publication"
        )
    except PublicationValidationError as exc:
        raise GitHubAPIError(500, f"remote publication rejected: {exc}") from exc
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise GitHubAPIError(500, "remote publication was not valid UTF-8 JSON") from exc


def _validate_branch_comparison(comparison: Any, expected_path: str) -> None:
    if not isinstance(comparison, dict) or not isinstance(comparison.get("files"), list):
        raise GitHubAPIError(500, "compare response did not contain a files array")
    files = comparison["files"]
    if len(files) != 1 or not isinstance(files[0], dict):
        raise GitHubAPIError(
            409,
            "publication branch must contain exactly one changed file",
        )
    changed = files[0]
    if changed.get("filename") != expected_path:
        raise GitHubAPIError(409, "publication branch contains a non-allowlisted change")
    if changed.get("status") not in {"added", "modified"}:
        raise GitHubAPIError(409, f"publication file has forbidden compare status {changed.get('status')!r}")
    if "previous_filename" in changed:
        raise GitHubAPIError(409, "publication rename metadata is forbidden")


def _execute(
    publication: dict[str, Any], token: str, *, base: str, mutations: list[str]
) -> dict[str, Any]:
    client = GitHubClient(token)
    content_id = publication["content_id"]
    branch = f"publication/{content_id}"
    path = publication_path(content_id).as_posix()
    body = render_pr_body(publication)
    title = f"publication: {publication['title']}"

    base_file = _contents(client, path, base)
    if base_file:
        base_publication = _decode_content(base_file)
        try:
            from lib.publication import validate_publication

            validate_publication(base_publication)
        except PublicationValidationError as exc:
            raise GitHubAPIError(409, f"base publication is invalid: {exc}") from exc
        if base_publication["content_sha256"] == publication["content_sha256"]:
            return {"status": "noop", "reason": "same publication is already on base"}
        raise GitHubAPIError(
            409,
            "content_id already exists on base with another hash; use a revision content_id",
        )

    encoded_ref = urllib.parse.quote(f"heads/{branch}", safe="")
    branch_ref = _not_found(lambda: client.request("GET", f"/git/ref/{encoded_ref}"))
    if branch_ref is None:
        base_ref = client.request("GET", f"/git/ref/heads/{urllib.parse.quote(base, safe='')}")
        client.request(
            "POST",
            "/git/refs",
            {"ref": f"refs/heads/{branch}", "sha": base_ref["object"]["sha"]},
        )
        mutations.append(f"created branch {branch}")
    else:
        compare_base = urllib.parse.quote(base, safe="")
        compare_head = urllib.parse.quote(branch, safe="")
        comparison = client.request("GET", f"/compare/{compare_base}...{compare_head}")
        _validate_branch_comparison(comparison, path)

    branch_file = _contents(client, path, branch)
    same_hash = False
    if branch_file:
        remote = _decode_content(branch_file)
        try:
            from lib.publication import validate_publication

            validate_publication(remote)
        except PublicationValidationError as exc:
            detail = f"remote branch publication is invalid; refusing same-hash no-op: {exc}"
            if mutations:
                detail += f"; partial mutations already completed: {mutations}"
            raise GitHubAPIError(409, detail) from exc
        if remote.get("content_id") != content_id:
            raise GitHubAPIError(409, "remote branch publication content_id does not match its path")
        same_hash = remote["content_sha256"] == publication["content_sha256"]

    if not same_hash:
        payload: dict[str, Any] = {
            "message": f"publication: {content_id}",
            "content": base64.b64encode(pretty_json(publication).encode("utf-8")).decode("ascii"),
            "branch": branch,
        }
        if branch_file:
            payload["sha"] = branch_file["sha"]
        try:
            client.request("PUT", f"/contents/{urllib.parse.quote(path, safe='/')}", payload)
        except GitHubAPIError as exc:
            if mutations:
                raise GitHubAPIError(
                    exc.status,
                    f"{exc}; partial mutations already completed: {mutations}",
                ) from exc
            raise
        mutations.append(f"updated {path} on {branch}")

    query = urllib.parse.urlencode(
        {"state": "open", "head": f"{REPOSITORY_OWNER}:{branch}", "base": base}
    )
    pulls = client.request("GET", f"/pulls?{query}")
    if len(pulls) > 1:
        raise GitHubAPIError(409, f"multiple open PRs found for {branch}")
    if pulls:
        pr = pulls[0]
        if same_hash:
            return {
                "status": "noop",
                "reason": "open PR already has the same content hash",
                "pull_request": pr["html_url"],
            }
        if pr.get("title") != title or pr.get("body") != body:
            pr = client.request(
                "PATCH", f"/pulls/{pr['number']}", {"title": title, "body": body}
            )
            mutations.append(f"updated PR #{pr['number']}")
        return {"status": "updated", "pull_request": pr["html_url"], "mutations": mutations}

    pr = client.request(
        "POST",
        "/pulls",
        {"title": title, "body": body, "head": branch, "base": base},
    )
    mutations.append(f"created PR #{pr['number']}")
    return {"status": "created", "pull_request": pr["html_url"], "mutations": mutations}


def execute(publication: dict[str, Any], token: str, *, base: str = "main") -> dict[str, Any]:
    mutations: list[str] = []
    try:
        return _execute(publication, token, base=base, mutations=mutations)
    except GitHubAPIError as exc:
        if mutations and "partial mutations already completed" not in str(exc):
            raise GitHubAPIError(
                exc.status,
                f"{exc}; partial mutations already completed: {mutations}; inspect the remote branch before retrying",
            ) from exc
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="open or update a TalkInsight publication PR")
    parser.add_argument("publication", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="strictly offline; never calls GitHub")
    mode.add_argument(
        "--execute",
        action="store_true",
        help=f"mutate {REPOSITORY}; requires exact approval and {TOKEN_ENV}",
    )
    parser.add_argument(
        "--existing",
        type=Path,
        help="dry-run only: compare with a local stand-in for the remote branch file",
    )
    args = parser.parse_args()

    try:
        publication = load_publication(args.publication)
        expected_path = publication_path(publication["content_id"])
        if args.publication.resolve() != (ROOT / expected_path).resolve():
            raise PublicationValidationError(
                f"publication must be at the allowlisted path {expected_path}"
            )

        if args.dry_run:
            existing = load_publication(args.existing) if args.existing else None
            print(json.dumps(dry_run_plan(publication, existing), ensure_ascii=False, sort_keys=True))
            return

        if args.existing:
            raise PublicationValidationError("--existing is available only with --dry-run")
        token = os.environ.get(TOKEN_ENV, "")
        if not token:
            raise PublicationValidationError(
                f"{TOKEN_ENV} is not set; stopped before any GitHub request or mutation"
            )
        print(json.dumps(execute(publication, token), ensure_ascii=False, sort_keys=True))
    except (OSError, PublicationValidationError, GitHubAPIError) as exc:
        raise SystemExit(f"[publication-pr] blocked: {exc}") from exc


if __name__ == "__main__":
    main()
