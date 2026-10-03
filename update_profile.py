"""Update the GitHub profile README with current account statistics."""

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

USER = "serdire"
JOINED_YEAR = 2026
README_PATH = Path(__file__).resolve().with_name("README.md")
TOKEN = os.environ.get("ACCESS_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""


def gh(payload, token=None):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "profile-readme-stats",
    }
    auth = (token or TOKEN).strip()
    if auth:
        headers["Authorization"] = "Bearer " + auth

    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not connect to the GitHub API: {exc.reason}") from exc

    if result.get("errors"):
        raise RuntimeError(f"GitHub GraphQL error: {result['errors']}")
    return result["data"]


def graphql(query, variables=None, token=None):
    return gh({"query": query, "variables": variables or {}}, token)


def fetch_stats():
    if not TOKEN:
        raise RuntimeError(
            "No GitHub token is configured. Set ACCESS_TOKEN or GITHUB_TOKEN "
            "in the environment, then run this script again."
        )

    year_fields = "\n".join(
        f'y{year}: contributionsCollection(from: "{year}-01-01T00:00:00Z", '
        f'to: "{year + 1}-01-01T00:00:00Z") '
        "{ totalCommitContributions restrictedContributionsCount }"
        for year in range(JOINED_YEAR, datetime.now(timezone.utc).year + 1)
    )
    contributions = graphql(
        f'query {{ user(login: "{USER}") {{ {year_fields} }} }}'
    )["user"]
    commits = sum(
        item["totalCommitContributions"] + item["restrictedContributionsCount"]
        for item in contributions.values()
    )

    repo_query = """
    query($cursor: String) {
      user(login: "serdire") {
        id
        followers { totalCount }
        repositories(first: 100, ownerAffiliations: OWNER, after: $cursor) {
          totalCount
          nodes { name stargazerCount isFork }
          pageInfo { hasNextPage endCursor }
        }
        repositoriesContributedTo(
          first: 1
          contributionTypes: [COMMIT, PULL_REQUEST, REPOSITORY]
        ) {
          totalCount
        }
      }
    }"""
    repos = []
    cursor = None
    while True:
        user = graphql(repo_query, {"cursor": cursor})["user"]
        connection = user["repositories"]
        repos.extend(connection["nodes"])
        if not connection["pageInfo"]["hasNextPage"]:
            break
        cursor = connection["pageInfo"]["endCursor"]

    stats = {
        "followers": user["followers"]["totalCount"],
        "repos": connection["totalCount"],
        "contributed": user["repositoriesContributedTo"]["totalCount"],
        "stars": sum(repo["stargazerCount"] for repo in repos),
        "commits": commits,
    }
    stats.update(
        loc(
            [repo["name"] for repo in repos if not repo["isFork"]],
            user["id"],
        )
    )
    return stats


LOC_QUERY = """
query($owner: String!, $name: String!, $id: ID!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef {
      target {
        ... on Commit {
          history(first: 100, author: {id: $id}, after: $cursor) {
            pageInfo { hasNextPage endCursor }
            nodes { additions deletions }
          }
        }
      }
    }
  }
}"""


def loc(repo_names, user_id):
    additions = deletions = 0
    for name in repo_names:
        cursor = None
        while True:
            repo = graphql(
                LOC_QUERY,
                {
                    "owner": USER,
                    "name": name,
                    "id": user_id,
                    "cursor": cursor,
                },
            )["repository"]
            if repo is None or repo["defaultBranchRef"] is None:
                break
            history = repo["defaultBranchRef"]["target"]["history"]
            additions += sum(commit["additions"] for commit in history["nodes"])
            deletions += sum(commit["deletions"] for commit in history["nodes"])
            if not history["pageInfo"]["hasNextPage"]:
                break
            cursor = history["pageInfo"]["endCursor"]
    return {
        "loc_add": additions,
        "loc_del": deletions,
        "loc": additions - deletions,
    }


def render_stats(stats):
    number = lambda value: f"{value:,}"
    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return "\n".join(
        [
            "| Statistic | Value |",
            "| --- | ---: |",
            f"| Repositories | {number(stats['repos'])} |",
            f"| Repositories contributed to | {number(stats['contributed'])} |",
            f"| Stars on owned repositories | {number(stats['stars'])} |",
            f"| Commits since {JOINED_YEAR} | {number(stats['commits'])} |",
            f"| Followers | {number(stats['followers'])} |",
            f"| Lines added / removed | +{number(stats['loc_add'])} / -{number(stats['loc_del'])} |",
            f"| Net lines of code | {number(stats['loc'])} |",
            "",
            f"_Last updated: {updated}_",
        ]
    )


def update_readme(stats):
    start_marker = "<!-- PROFILE_STATS_START -->"
    end_marker = "<!-- PROFILE_STATS_END -->"
    readme = README_PATH.read_text(encoding="utf-8")
    if readme.count(start_marker) != 1 or readme.count(end_marker) != 1:
        raise RuntimeError(
            f"{README_PATH.name} must contain exactly one "
            f"{start_marker} and one {end_marker} marker."
        )

    start = readme.index(start_marker) + len(start_marker)
    end = readme.index(end_marker)
    if start > end:
        raise RuntimeError("The README stats markers are in the wrong order.")

    updated_readme = (
        readme[:start]
        + "\n\n"
        + render_stats(stats)
        + "\n\n"
        + readme[end:]
    )
    temporary_path = README_PATH.with_suffix(".md.tmp")
    temporary_path.write_text(updated_readme, encoding="utf-8")
    temporary_path.replace(README_PATH)


def selfcheck():
    example = {
        "followers": 12,
        "repos": 4,
        "contributed": 3,
        "stars": 27,
        "commits": 100,
        "loc_add": 500,
        "loc_del": 200,
        "loc": 300,
    }
    rendered = render_stats(example)
    assert "| Repositories | 4 |" in rendered
    assert "| Net lines of code | 300 |" in rendered
    assert "<svg" not in rendered


if __name__ == "__main__":
    selfcheck()
    current_stats = fetch_stats()
    update_readme(current_stats)
    print(f"Updated {README_PATH} with stats for @{USER}.")
