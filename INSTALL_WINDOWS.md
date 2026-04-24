# Windows: Download and Install

## Option A (recommended): Download ZIP and install automatically

1. Open PowerShell in a folder where you want the project.
2. Run:

```powershell
.\download_and_install.ps1 -Repo https://github.com/OWNER/REPO -Ref main -TargetDir news-factcheck-local
```

This will:
- generate a GitHub ZIP link,
- download the project,
- extract it,
- run `install_requirements.bat`.

## Option B: Manual project folder install

Inside the project folder run:

```bat
install_requirements.bat
start.bat
```

`start.bat` lets you run demo, clone+run, or generate a ZIP link.

## If `python` command is not found

Use the Windows Python launcher:

```bat
py -3 --version
```

The installer scripts already use `py -3`.
