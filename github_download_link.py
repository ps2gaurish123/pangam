"""Generate a direct downloadable ZIP link for a GitHub repository ref.

Usage:
  python github_download_link.py --repo https://github.com/OWNER/REPO --ref main
  python github_download_link.py --repo git@github.com:OWNER/REPO.git --ref v1.0.0
"""

from __future__ import annotations

import argparse
import re


GITHUB_PATTERNS = [
    re.compile(r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/.]+?)(?:\.git)?/?$"),
    re.compile(r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/.]+?)(?:\.git)?$"),
]


def parse_repo(repo_url: str) -> tuple[str, str]:
    for pattern in GITHUB_PATTERNS:
        match = pattern.match(repo_url.strip())
        if match:
            return match.group("owner"), match.group("repo")
    raise ValueError(
        "Unsupported GitHub URL format. Use HTTPS or SSH, e.g. "
        "https://github.com/OWNER/REPO or git@github.com:OWNER/REPO.git"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="GitHub repo URL")
    parser.add_argument("--ref", default="main", help="Branch, tag, or commit (default: main)")
    args = parser.parse_args()

    owner, repo = parse_repo(args.repo)
    link = f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{args.ref}"
    print(link)


if __name__ == "__main__":
    main()
