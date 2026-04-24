# Pangam - News Fact-Checking System Blueprint

This repository now includes a production-oriented blueprint for building a **Web Scraping and Fact-Checking System for Latest News**.

## Added in this iteration

- A structured architecture and implementation design document.
- A source onboarding CSV template for trusted feed setup.
- A starter Python scoring module that computes confidence (0–100) and assigns verification status.

## Quick start (scoring module)

```bash
python - <<'PY'
from news_factcheck.scoring import ScoreInputs, compute_confidence_score, derive_status

inputs = ScoreInputs(
    source_reliability=0.92,
    independent_sources=0.75,
    official_confirmation=1.0,
    consistency=0.80,
    recency_clarity=0.90,
    historical_accuracy=0.85,
)
score = compute_confidence_score(inputs)
print("score=", score)
print("status=", derive_status(score, has_conflict=False, source_count=3, has_official=True))
PY
```

## Project docs

- `docs/news_factcheck_system_design.md`
- `docs/source_onboarding_template.csv`
- `news_factcheck/scoring.py`


## Windows local run

Use the included batch files:

1. `install_requirements.bat`  
   Creates `.venv`, upgrades `pip`, and installs `requirements.txt`.
2. `start.bat`  
   Offers two options:
   - Run directly from the current folder.
   - Download (git clone) and then run locally.

After setup, the starter demo executes:

```bat
.venv\Scripts\python.exe -m news_factcheck.demo
```


### GitHub downloadable link (ZIP)

If your repository is on GitHub, generate a direct ZIP download link with:

```powershell
.\github_download_link.ps1 -Repo https://github.com/OWNER/REPO -Ref main
```

If Python is available, this also works:

```bash
py -3 github_download_link.py --repo https://github.com/OWNER/REPO --ref main
```

This prints a link in this format:

`https://codeload.github.com/OWNER/REPO/zip/refs/heads/main`

You can also use **Option 3** in `start.bat` to generate this link interactively (no Python required).


## Download + install (everything included)

For Windows users who want a full project download + install flow in one step:

```powershell
.\download_and_install.ps1 -Repo https://github.com/OWNER/REPO -Ref main -TargetDir news-factcheck-local
```

Also see: `INSTALL_WINDOWS.md`.


If you get `download_and_install.ps1 is not recognized`, it means your current folder does not contain that file.
Use this bootstrap command to download the installer script and run it:

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/OWNER/REPO/main/download_and_install.ps1 -OutFile download_and_install.ps1; .\download_and_install.ps1 -Repo https://github.com/OWNER/REPO -Ref main -TargetDir news-factcheck-local
```


## Download this project

If you do not see the new files on `main`, merge the PR first.

- Download `main` ZIP: `https://codeload.github.com/OWNER/REPO/zip/refs/heads/main`
- Download PR branch ZIP: `https://codeload.github.com/OWNER/REPO/zip/refs/heads/BRANCH_NAME`
- See detailed steps: `DOWNLOAD.md`
