param(
  [Parameter(Mandatory = $true)]
  [string]$Repo,

  [Parameter(Mandatory = $false)]
  [string]$Ref = "main"
)

$repoTrim = $Repo.Trim()
$owner = $null
$name = $null

if ($repoTrim -match '^https://github\.com/([^/]+)/([^/.]+?)(?:\.git)?/?$') {
  $owner = $Matches[1]
  $name = $Matches[2]
} elseif ($repoTrim -match '^git@github\.com:([^/]+)/([^/.]+?)(?:\.git)?$') {
  $owner = $Matches[1]
  $name = $Matches[2]
} else {
  Write-Error "Unsupported GitHub URL format. Use https://github.com/OWNER/REPO or git@github.com:OWNER/REPO.git"
  exit 1
}

$link = "https://codeload.github.com/$owner/$name/zip/refs/heads/$Ref"
Write-Output $link
