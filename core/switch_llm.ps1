param(
  [Parameter(Mandatory=$true)]
  [ValidateSet("dashscope","deepseek","doubao")]
  [string]$Provider
)

$configPath = Join-Path $PSScriptRoot "config.json"
if (-not (Test-Path $configPath)) {
  Write-Error "config.json not found at $configPath"
  exit 1
}

$config = Get-Content $configPath -Raw | ConvertFrom-Json

$examples = $config.llm.base_url_examples
if (-not $examples) {
  Write-Error "base_url_examples not found in config.json"
  exit 1
}

switch ($Provider) {
  "dashscope" {
    $config.llm.provider = "dashscope"
    $config.llm.model = "qwen-max"
    $config.llm.base_url = $examples.dashscope
  }
  "deepseek" {
    $config.llm.provider = "deepseek"
    $config.llm.model = "deepseek-chat"
    $config.llm.base_url = $examples.deepseek
  }
  "doubao" {
    $config.llm.provider = "doubao"
    $config.llm.model = "doubao-pro-32k"
    $config.llm.base_url = $examples.doubao
  }
}

$config | ConvertTo-Json -Depth 8 | Set-Content $configPath -Encoding UTF8
Write-Host "Switched provider to $Provider"
