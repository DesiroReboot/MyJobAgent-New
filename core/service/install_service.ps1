param(
  [string]$ServiceName = "JobInsightCollector",
  [string]$DisplayName = "JobInsight Collector",
  [string]$Description = "Collects local activity and stores events for JobInsight",
  [string]$ConfigPath = "config.json"
)

$root = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python).Source
if (-not $python) {
  Write-Error "python not found in PATH"
  exit 1
}

$collector = Join-Path $root "collector_service.py"
$cfg = if ([System.IO.Path]::IsPathRooted($ConfigPath)) { $ConfigPath } else { Join-Path $root $ConfigPath }

$binPath = "\"$python\" \"$collector\" --config \"$cfg\" --daemon"

sc.exe create $ServiceName binPath= $binPath start= auto DisplayName= $DisplayName
sc.exe description $ServiceName $Description
sc.exe start $ServiceName
