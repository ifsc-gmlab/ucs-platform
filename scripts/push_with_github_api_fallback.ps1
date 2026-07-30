#requires -version 5.1
<#
.SYNOPSIS
Push the current branch normally, then safely fall back to GitHub Git Database API.

.DESCRIPTION
The fallback creates one atomic commit, verifies blob/tree/parent identity, and
updates the branch with force=false. It is intended for networks where
github.com smart HTTP is intercepted but api.github.com remains usable.
#>
[CmdletBinding()]
param(
  [string]$RepoPath = "",
  [string]$Repository = "ifsc-gmlab/ucs-platform",
  [string]$Branch = "main",
  [ValidateRange(1, 8)]
  [int]$PushAttempts = 4,
  [switch]$SkipNormalPush,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($RepoPath) {
  $ResolvedRepoPath = [IO.Path]::GetFullPath($RepoPath)
  if (-not (Test-Path -LiteralPath $ResolvedRepoPath -PathType Container)) {
    throw "Repository path does not exist: $ResolvedRepoPath"
  }
  Set-Location -LiteralPath $ResolvedRepoPath
}

function Invoke-Git {
  param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Args)
  $out = @(& git @Args 2>&1)
  if ($LASTEXITCODE -ne 0) { throw "git $($Args -join ' ') failed: $($out -join ' ')" }
  return ($out -join "`n").Trim()
}

function Invoke-GhJson {
  param([string]$Method, [string]$Endpoint, [string]$Payload = "", [int]$Attempts = 6)
  for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
      $out = if ($Payload) {
        $Payload | gh api --method $Method $Endpoint --input - 2>&1
      } else {
        gh api --method $Method $Endpoint 2>&1
      }
      $exit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $old }
    if ($exit -eq 0) { return (($out -join "`n") | ConvertFrom-Json) }
    if ($attempt -lt $Attempts) { Start-Sleep -Seconds ([Math]::Min(2 * $attempt, 8)) }
  }
  throw "GitHub API failed after $Attempts attempts: $Method $Endpoint"
}

$inside = Invoke-Git rev-parse --is-inside-work-tree
if ($inside -ne "true") { throw "Current directory is not a Git work tree" }
if (Invoke-Git status --porcelain) { throw "Working tree must be clean before push" }
$head = Invoke-Git rev-parse HEAD
$parent = Invoke-Git rev-parse "$head^"
$expectedTree = Invoke-Git show -s --format=%T $head

$remote = Invoke-GhJson GET "repos/$Repository/git/ref/heads/$Branch"
$remoteSha = $remote.object.sha
if ($remoteSha -eq $head) {
  Write-Host "Already synchronized: $head"
  exit 0
}
if ($remoteSha -ne $parent) {
  throw "Remote $Branch changed: expected parent $parent, found $remoteSha"
}
if ($DryRun) {
  Write-Host "DRY RUN: local=$head parent=$parent tree=$expectedTree remote=$remoteSha"
  exit 0
}

if (-not $SkipNormalPush) {
  for ($attempt = 1; $attempt -le $PushAttempts; $attempt++) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
      & git -c http.version=HTTP/1.1 -c http.postBuffer=524288000 `
        -c http.maxRequestBuffer=100M -c core.compression=0 push origin $Branch
      $pushExit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $old }
    $remote = Invoke-GhJson GET "repos/$Repository/git/ref/heads/$Branch"
    if ($remote.object.sha -eq $head) {
      Invoke-Git update-ref "refs/remotes/origin/$Branch" $head
      Write-Host "Normal push verified: $head"
      exit 0
    }
    if ($pushExit -eq 0) { throw "Push returned success but remote SHA did not match" }
    if ($attempt -lt $PushAttempts) { Start-Sleep -Seconds ([Math]::Min(10 * $attempt, 30)) }
  }
}

$remote = Invoke-GhJson GET "repos/$Repository/git/ref/heads/$Branch"
if ($remote.object.sha -ne $parent) { throw "Remote changed before API fallback" }
$remoteCommit = Invoke-GhJson GET "repos/$Repository/git/commits/$parent"
$baseTree = $remoteCommit.tree.sha
$entries = @()
$changes = @(git diff-tree --no-commit-id --name-status --no-renames -r $head)
foreach ($change in $changes) {
  $parts = $change -split "`t", 2
  $status = $parts[0]
  $path = $parts[1]
  if ($status -eq "D") {
    $entries += @{ path=$path; mode="100644"; type="blob"; sha=$null }
    continue
  }
  $expectedBlob = Invoke-Git rev-parse "$head`:$path"
  $tempBlob = [IO.Path]::GetTempFileName()
  try {
    $proc = Start-Process -FilePath "git" `
      -ArgumentList @("cat-file", "blob", $expectedBlob) `
      -RedirectStandardOutput $tempBlob -NoNewWindow -Wait -PassThru
    if ($proc.ExitCode -ne 0) { throw "Cannot materialize Git blob: $path" }
    $payload = @{
      content=[Convert]::ToBase64String([IO.File]::ReadAllBytes($tempBlob))
      encoding="base64"
    } | ConvertTo-Json -Compress
  } finally {
    Remove-Item -LiteralPath $tempBlob -Force -ErrorAction SilentlyContinue
  }
  $blob = Invoke-GhJson POST "repos/$Repository/git/blobs" $payload
  if ($blob.sha -ne $expectedBlob) { throw "Blob SHA mismatch: $path" }
  $mode = ((Invoke-Git ls-tree $head -- $path) -split "\s+")[0]
  $entries += @{ path=$path; mode=$mode; type="blob"; sha=$blob.sha }
}
$treePayload = @{ base_tree=$baseTree; tree=$entries } | ConvertTo-Json -Depth 6 -Compress
$newTree = Invoke-GhJson POST "repos/$Repository/git/trees" $treePayload
if ($newTree.sha -ne $expectedTree) { throw "Tree SHA mismatch; ref was not updated" }

$message = (Invoke-Git log -1 --format=%B $head).TrimEnd()
$commitPayload = @{
  message=$message
  tree=$newTree.sha
  parents=@($parent)
  author=@{
    name=Invoke-Git show -s --format=%an $head
    email=Invoke-Git show -s --format=%ae $head
    date=Invoke-Git show -s --format=%aI $head
  }
  committer=@{
    name=Invoke-Git show -s --format=%cn $head
    email=Invoke-Git show -s --format=%ce $head
    date=Invoke-Git show -s --format=%cI $head
  }
} | ConvertTo-Json -Depth 6 -Compress
$apiCommit = Invoke-GhJson POST "repos/$Repository/git/commits" $commitPayload
$refPayload = @{ sha=$apiCommit.sha; force=$false } | ConvertTo-Json -Compress
$updated = Invoke-GhJson PATCH "repos/$Repository/git/refs/heads/$Branch" $refPayload
if ($updated.object.sha -ne $apiCommit.sha) { throw "Remote ref verification failed" }

# GitHub's API omits the trailing commit-message LF. Reconstruct that exact
# commit object locally so refs can be aligned even while the proxy blocks fetch.
$authorName = (Invoke-Git show -s --format=%an $head).TrimEnd()
$authorEmail = (Invoke-Git show -s --format=%ae $head).TrimEnd()
$authorEpoch = (Invoke-Git show -s --format=%at $head).TrimEnd()
$authorIso = (Invoke-Git show -s --format=%aI $head).TrimEnd()
$authorOffset = $authorIso.Substring($authorIso.Length - 6).Replace(":", "")
$committerName = (Invoke-Git show -s --format=%cn $head).TrimEnd()
$committerEmail = (Invoke-Git show -s --format=%ce $head).TrimEnd()
$committerEpoch = (Invoke-Git show -s --format=%ct $head).TrimEnd()
$committerIso = (Invoke-Git show -s --format=%cI $head).TrimEnd()
$committerOffset = $committerIso.Substring($committerIso.Length - 6).Replace(":", "")
$rawCommit = @(
  "tree $($newTree.sha)"
  "parent $parent"
  "author $authorName <$authorEmail> $authorEpoch $authorOffset"
  "committer $committerName <$committerEmail> $committerEpoch $committerOffset"
  ""
  $message
) -join "`n"
$rawPath = Join-Path ([IO.Path]::GetTempPath()) ("github-api-commit-{0}.txt" -f [Guid]::NewGuid().ToString("N"))
try {
  [IO.File]::WriteAllText($rawPath, $rawCommit, [Text.UTF8Encoding]::new($false))
  $localApiSha = (& git hash-object -t commit -w -- $rawPath).Trim()
  if ($LASTEXITCODE -ne 0) { throw "Cannot write reconstructed API commit object" }
} finally {
  Remove-Item -LiteralPath $rawPath -Force -ErrorAction SilentlyContinue
}
if ($localApiSha -ne $apiCommit.sha) {
  throw "API push succeeded, but local reconstruction SHA mismatch: local=$localApiSha api=$($apiCommit.sha)"
}
Invoke-Git update-ref "refs/heads/$Branch" $apiCommit.sha $head
Invoke-Git update-ref "refs/remotes/origin/$Branch" $apiCommit.sha
Write-Host "API fallback push verified and local refs aligned: $($apiCommit.sha) tree=$($newTree.sha)"
