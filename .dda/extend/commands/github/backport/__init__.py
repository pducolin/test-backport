from __future__ import annotations

import os
import json

from typing import TYPE_CHECKING
from github import Github

from dda.cli.base import dynamic_command, pass_app

from dda.utils.ci import running_in_ci

if TYPE_CHECKING:
    from dda.cli.application import Application


@dynamic_command(
    short_help="Backport a merged PR to a release branch",
    context_settings={"help_option_names": [], "ignore_unknown_options": True},
)
@pass_app
def cmd(
    app: Application,
) -> None:
    """
    Backport a merged PR to a release branch.
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
        app.display_error("No pull_request found. Skipping backport.")
        return

    # Merge commit SHA (the commit created on base branch)
    merge_commit_sha = original_pr.get("merge_commit_sha")
    if not is_pr_merged_in(original_pr, "main") or not merge_commit_sha:
        app.display_error(
            "For security reasons, this action should only run on merged PRs."
        )
        return

    original_pr_number = original_pr.get("number")

    # Extract labels and look for backport/<target>
    labels = original_pr.get("labels", [])
    target_branch_name = find_backport_target(labels)
    if not target_branch_name:
        app.display_error("No backport/<target> label found. Skipping backport.")
        return

    app.display(
        f"Backport #{original_pr_number} to target branch: {target_branch_name}"
    )

    # Repository info
    repo_owner = event.get("repository", {}).get("owner", {}).get("login")
    repo_name = event.get("repository", {}).get("name", "")
    full_repo_name = f"{repo_owner}/{repo_name}"

    # Authenticate to GitHub and get the repository
    token = app.config.github.auth.token
    gh = Github(token)
    repo = gh.get_repo(full_repo_name)

    # Ensure target branch exists
    try:
        repo.get_branch(target_branch_name)
    except Exception as e:
        app.display_error(
            f"Target branch '{target_branch_name}' does not exist or cannot be accessed: {e}"
        )
        return

    # Get the target branch head
    target_ref_name = f"heads/{target_branch_name}"
    target_ref = repo.get_git_ref(target_ref_name)
    target_head_sha = target_ref.object.sha
    target_head_commit = repo.get_git_commit(target_head_sha)

    # Get the original merge commit object
    original_commit = repo.get_commit(merge_commit_sha)

    # Create backport commit
    backport_commit = repo.create_git_commit(
        message=original_commit.commit.message,
        tree=repo.get_git_tree(original_commit.commit.tree.sha),
        parents=[target_head_commit],
        author=original_commit.commit.author,
        # Do NOT set committer -> GitHub App/Actions user (Verified)
    )

    # Push the backport commit to the backport branch
    backport_branch_name = f"backport/{target_branch_name}/backport-{original_pr_number}-to-{target_branch_name}"
    app.display(f"Backport branch name: {backport_branch_name}")
    try:
        repo.create_git_ref(ref=f"refs/{backport_branch_name}", sha=backport_commit.sha)
    except Exception as e:
        app.display_error(
            f"Failed to push backport commit to '{backport_branch_name}': {e}"
        )
        return

    # Create the backport PR
    original_body = original_pr.get("body", "")
    backport_body = f"""Backport {merge_commit_sha} from #{original_pr_number}.

___

{original_body}"""
    backport_labels = [get_non_backport_labels(labels) + ["backport", "bot"]]
    backport_title = f"[Backport {target_branch_name}] {original_pr.get('title')}"

    backport_pr = repo.create_pull(
        title=backport_title,
        body=backport_body,
        base=target_branch_name,
        head=backport_branch_name,
    )

    backport_pr.add_to_labels(*backport_labels)

    app.display("Backport workflow finished, PR created: {backport_pr.html_url}")


def get_event() -> dict:
    event_path = os.environ["GITHUB_EVENT_PATH"]
    with open(event_path) as f:
        return json.load(f)


def is_pr_merged_in(pr: dict, base_branch: str) -> bool:
    """
    Check if the PR is merged into the given base branch.
    """
    return pr.get("merged", False) and pr.get("base", {}).get("ref", "") == base_branch


def find_backport_target(labels):
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


def get_non_backport_labels(labels):
    """
    Get all labels that are not backport labels.
    """
    return [
        label for label in labels if not label.get("name", "").startswith("backport/")
    ]
