$ErrorActionPreference = "Stop"
$dir = "C:\Users\admin\Documents\deepseek-harness\default-workspace\clash-auto-switch"
Set-Location $dir

# 1) Get credentials (GCM will pop up a browser window if none stored)
$in = "protocol=https`nhost=github.com`n`n"
$out = $in | git credential fill 2>$null
$passLine = $out | Where-Object { $_ -match '^password=' }
if (-not $passLine) { Write-Output "AUTH_FAILED: no credential"; exit 1 }
$token = ($passLine -replace '^password=', '')

$headers = @{
  Authorization = "token $token"
  Accept        = "application/vnd.github+json"
  "User-Agent"  = "clash-auto-switch-publisher"
}

# 2) Confirm account
try {
  $me = Invoke-RestMethod -Uri "https://api.github.com/user" -Headers $headers -TimeoutSec 30
  $user = $me.login
  Write-Output "GITHUB_ACCOUNT: $user"
} catch {
  Write-Output "API_ERR(whoami): $($_.Exception.Message)"
  exit 1
}

# 3) Create public repo (skip if exists)
$desc = "Clash proxy auto switcher: 30s health checks with real throughput tests, region rules (no HK, JP/SG first), local dashboard and tray, Windows and macOS"
$bytes = [Text.Encoding]::UTF8.GetBytes((@{
  name        = "clash-auto-switch"
  description = $desc
  private     = $false
  has_wiki    = $false
} | ConvertTo-Json))
try {
  $r = Invoke-RestMethod -Method Post -Uri "https://api.github.com/user/repos" -Headers $headers -Body $bytes -ContentType "application/json; charset=utf-8" -TimeoutSec 30
  Write-Output "REPO_CREATED: $($r.full_name)  $($r.html_url)"
} catch {
  $msg = $_.Exception.Message
  if ($msg -match "422" -or $msg -match "already exists") { Write-Output "REPO_EXISTS" }
  else { Write-Output "API_ERR(create): $msg"; exit 1 }
}

# 4) Push
if (git remote get-url origin 2>$null) { git remote remove origin }
git remote add origin "https://github.com/$user/clash-auto-switch.git"
git branch -M main
$push = git push -u origin main 2>&1 | Out-String
$push = $push -replace [regex]::Escape($token), "***"
Write-Output $push
if ($LASTEXITCODE -eq 0) { Write-Output "PUBLISH_OK: https://github.com/$user/clash-auto-switch" }
else { Write-Output "PUSH_FAILED (exit $LASTEXITCODE)" }
