param(
  [string]$ServiceName = "JobInsightCollector"
)

sc.exe query $ServiceName
