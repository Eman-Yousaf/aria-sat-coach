# Ship Aria to Azure App Service.
#
# The point of this script is the allowlist. The working directory contains
# .wwebjs_auth/ -- 333 MB, and a live WhatsApp credential that would let anyone
# holding it message as you. It also contains .env. Zipping the folder and
# pushing it is a one-line mistake that uploads both, so this names what goes
# rather than what stays.

param(
    [string]$App   = "aria-sat-coach",
    [string]$Group = "aria-sat-rg"
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$zip  = Join-Path ([System.IO.Path]::GetTempPath()) "aria-deploy.zip"

# Every module the app imports, plus the two build artifacts. The question bank
# and dashboard are generated ahead of time on purpose: regenerating either at
# boot would need an LLM key and hours, and would serve different questions on
# every deploy.
$include = @(
    "*.py",
    "question_bank.json",
    "dashboard.html",
    "requirements.txt"
)

$files = @()
foreach ($pattern in $include) {
    $files += Get-ChildItem -Path $root -Filter $pattern -File
}

# Belt and braces: even though the patterns above cannot match these, a future
# edit to $include should not be able to leak them.
$forbidden = @(".env", ".wwebjs_auth", "reminders.db", "chroma_db")
$leaks = $files | Where-Object { $forbidden -contains $_.Name }
if ($leaks) {
    throw "refusing to deploy, would include: $($leaks.Name -join ', ')"
}

Write-Host "packing $($files.Count) files"
$files | ForEach-Object { Write-Host "  $($_.Name)" }

if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path $files.FullName -DestinationPath $zip

$mb = [math]::Round((Get-Item $zip).Length / 1MB, 2)
Write-Host "`n$zip is $mb MB - deploying to $App"

# az writes an informational warning to stderr, and Windows PowerShell turns
# any native stderr into a terminating error under ErrorActionPreference=Stop.
# The deploy is fine; the shell is not. Check the exit code instead.
$ErrorActionPreference = "Continue"
az webapp deploy --name $App --resource-group $Group --src-path $zip --type zip
if ($LASTEXITCODE -ne 0) { throw "deploy failed with exit code $LASTEXITCODE" }
Write-Host "`ndeployed. verify: https://$App.azurewebsites.net/api/health"
