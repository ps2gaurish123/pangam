param(
  [Parameter(Mandatory = $true)]
  [string]$Repo,

  [Parameter(Mandatory = $false)]
  [string]$Ref = "main",

  [Parameter(Mandatory = $false)]
  [string]$TargetDir = "news-factcheck-local"
)

$ErrorActionPreference = "Stop"

function Get-GitHubZipLink {
  param(
    [Parameter(Mandatory = $true)]
    [string]$RepoUrl,
    [Parameter(Mandatory = $true)]
    [string]$RefName
  )

  $repoTrim = $RepoUrl.Trim()
  $owner = $null
  $name = $null

  if ($repoTrim -match '^https://github\.com/([^/]+)/([^/.]+?)(?:\.git)?/?$') {
    $owner = $Matches[1]
    $name = $Matches[2]
  } elseif ($repoTrim -match '^git@github\.com:([^/]+)/([^/.]+?)(?:\.git)?$') {
    $owner = $Matches[1]
    $name = $Matches[2]
  } else {
    throw "Unsupported GitHub URL format. Use https://github.com/OWNER/REPO or git@github.com:OWNER/REPO.git"
  }

  return "https://codeload.github.com/$owner/$name/zip/refs/heads/$RefName"
}

Write-Host "[INFO] Generating download link..."
$link = Get-GitHubZipLink -RepoUrl $Repo -RefName $Ref

$zipPath = Join-Path $PWD "news-factcheck.zip"
$extractRoot = Join-Path $PWD "news-factcheck-extract"

if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
if (Test-Path $extractRoot) { Remove-Item $extractRoot -Recurse -Force }
if (Test-Path $TargetDir) { throw "Target directory already exists: $TargetDir" }

Write-Host "[INFO] Downloading project ZIP from: $link"
Invoke-WebRequest -Uri $link -OutFile $zipPath

Write-Host "[INFO] Extracting..."
Expand-Archive -Path $zipPath -DestinationPath $extractRoot -Force

$first = Get-ChildItem -Path $extractRoot -Directory | Select-Object -First 1
if (-not $first) { throw "Extraction failed" }
Move-Item -Path $first.FullName -Destination $TargetDir

Write-Host "[INFO] Running installer..."
Push-Location $TargetDir
cmd /c install_requirements.bat
if ($LASTEXITCODE -ne 0) {
  Pop-Location
  throw "install_requirements.bat failed"
}

Write-Host "[OK] Installed at $TargetDir"
Write-Host "[INFO] Run demo with:"
Write-Host "  .venv\Scripts\python.exe -m news_factcheck.demo"
Pop-Location
