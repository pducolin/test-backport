# SPDX-FileCopyrightText: 2025-present Datadog, Inc. <dev@datadoghq.com>
#
# SPDX-License-Identifier: MIT
from __future__ import annotations

from heapq import merge
import json
import os
from typing import TYPE_CHECKING

from dda.cli.base import dynamic_command, pass_app
from dda.utils.ci import running_in_ci

if TYPE_CHECKING:
    from dda.cli.application import Application


@dynamic_command(
    short_help="Cherry-pick a merged PR changes to another branch",
    context_settings={"help_option_names": [], "ignore_unknown_options": True},
    features=["github"],
)
@pass_app
def cmd(
    app: Application,
) -> None:
    """
    Cherry-pick a merged PR changes to another branch.
    """
    if not running_in_ci():
        app.display_error(
            "This command is meant to be run in CI, not locally. Use `dda github backport` to run it in CI."
        )
        return

    event = get_event()

    # Only handle pull_request events
    original_pr = event.get("pull_request")
    if not original_pr:
        app.display_warning("No pull_request found. Skipping backport.")
        return

    # Merge commit SHA (the commit created on base branch)
    merge_commit_sha = original_pr.get("merge_commit_sha")
    if not original_pr.get("merged", False) or not merge_commit_sha:
        app.display_info(
            "For security reasons, this action should only run on merged PRs."
        )
        return

    original_pr_number = original_pr.get("number")

    # Extract labels and look for backport/<target>
    labels = original_pr.get("labels", [])
    base = find_backport_target(labels)
    if not base:
        app.display_info("No backport/<target> label found. Skipping backport.")
        return

    # Repository info
    repo_name = event.get("repository", {}).get("name", "")

    # Authenticate to GitHub and get a token
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        app.abort("GITHUB_TOKEN is not set")

    # git clone <full_repo_name>
    clone_url = event.get("repository", {}).get("clone_url")
    auth_url = clone_url.replace("https://", f"https://x-access-token:{token}@")
    app.subprocess.run(["git", "clone", auth_url], check=True)
    app.subprocess.run(
        [
            "git",
            "config",
            "--global",
            "user.email",
            "github-actions[bot]@users.noreply.github.com",
        ],
        check=True,
    )
    app.subprocess.run(
        ["git", "config", "--global", "user.name", "github-actions[bot]"], check=True
    )
    app.subprocess.run(["git", "switch", base], cwd=repo_name, check=True)
    head = f"backport-{original_pr_number}-to-{base}"
    app.subprocess.run(["git", "switch", "-c", head], cwd=repo_name, check=True)

    if (
        app.subprocess.run(
            ["git", "cherry-pick", "-x", merge_commit_sha], cwd=repo_name
        )
        != 0
    ):
        app.subprocess.run(["git", "cherry-pick", "--abort"], cwd=repo_name, check=True)
        worktree_path = f".worktrees/backport-${base}"
        error_message = f"""Failed to cherry-pick {merge_commit_sha}
To backport manually, run these commands in your terminal:
```bash
# Fetch latest updates from GitHub
git fetch
# Create a new working tree
git worktree add {worktree_path} {base}
# Navigate to the new working tree
cd {worktree_path}
# Create a new branch
git switch --create {head}
# Cherry-pick the merged commit of this pull request and resolve the conflicts
git cherry-pick -x --mainline 1 {merge_commit_sha}
# Push it to GitHub
git push --set-upstream origin {head}
# Go back to the original working tree
cd ../..
# Delete the working tree
git worktree remove {worktree_path}"""
        app.abort(error_message)

    # git push
    app.subprocess.run(
        ["git", "push", "--set-upstream", "origin", head],
        cwd=repo_name,
        check=True,
    )

    # Create the backport PR
    original_body = original_pr.get("body", "")
    original_labels = get_non_backport_labels(labels)
    original_title = original_pr.get("title")

    # Set outputs
    with open(os.environ["GITHUB_OUTPUT"], "a") as f:
        if original_pr_number:
            f.write(f"pr_number={original_pr_number}\n")
        if base:
            f.write(f"base={base}\n")
        if head:
            f.write(f"head={head}\n")
        if original_labels:
            f.write(f"original_labels={','.join(original_labels)}\n")
        if original_title:
            f.write(f"original_title<<EOF\n{original_title}\nEOF\n")
        if original_body:
            f.write(f"original_body<<EOF\n{original_body}\nEOF\n")

    app.display(f"Cherry-pick PR #{original_pr_number} to branch {head}")


def get_event() -> dict:
    event_path = os.environ["GITHUB_EVENT_PATH"]
    with open(event_path, encoding="utf-8") as f:
        return json.load(f)


def find_backport_target(labels: list[dict]) -> str | None:
    """
    Look for a label of the form 'backport/<target>' and return <target>'.
    """
    for lbl in labels:
        name = lbl.get("name")
        if not name:
            continue
        if name.startswith("backport/"):
            return name.split("/", 1)[1]
    return None


def get_non_backport_labels(labels: list[dict]) -> list[str]:
    """
    Get all labels that are not backport labels.
    """
    non_backport_labels = []
    for label in labels:
        name = label.get("name", "")
        if not name:
            continue
        if name.startswith("backport/"):
            continue
        non_backport_labels.append(name)
    return non_backport_labels
