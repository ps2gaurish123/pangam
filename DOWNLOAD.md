# How to Download This Project

If you cannot see new files on GitHub main, the changes are likely still in an open PR branch and not merged yet.

## 1) Download from main branch (after merge)

Replace placeholders:

`https://codeload.github.com/OWNER/REPO/zip/refs/heads/main`

Example command (PowerShell):

```powershell
Invoke-WebRequest https://codeload.github.com/OWNER/REPO/zip/refs/heads/main -OutFile project.zip
```

## 2) Download directly from PR branch (before merge)

If your PR branch is `codex/build-news-scraping-and-fact-checking-system`:

`https://codeload.github.com/OWNER/REPO/zip/refs/heads/codex/build-news-scraping-and-fact-checking-system`

## 3) Download from GitHub Actions artifact

This repository now includes a workflow that creates a ZIP artifact on push/PR.

1. Open **Actions** tab.
2. Open the latest **Package Project** run.
3. Download artifact named **project-zip**.

## Why you saw old files

Your screenshot shows `main` still has older files (`README.md`, `GIM_AIM_TRIGGER.PY`, `SEG_RUNTIME_PLAY.PY`).
That means the PR changes have not been merged into `main` yet.
