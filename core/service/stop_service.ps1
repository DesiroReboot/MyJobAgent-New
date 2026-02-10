param(
  [string]$ServiceName = "JobInsightCollector"
)

sc.exe stop $ServiceName
