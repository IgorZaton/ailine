"""Helpers for normalizing arbitrary Git remote URLs to a viewable HTTP form."""


def normalize_git_url(repo_url: str, commit_hash: str) -> str:
    """Convert SSH Git URL to HTTP GitHub URL with commit hash."""
    if repo_url.startswith("git@"):
        parts = repo_url.replace("git@", "").replace(".git", "").split(":")
        if len(parts) == 2:
            return f"https://{parts[0]}/{parts[1]}/commit/{commit_hash}"
    elif repo_url.startswith("https://"):
        return f"{repo_url.replace('.git', '')}/commit/{commit_hash}"
    return repo_url
