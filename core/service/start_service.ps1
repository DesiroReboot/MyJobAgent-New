param(
  [string]$ServiceName = "JobInsightCollector"
)

sc.exe start $ServiceName
