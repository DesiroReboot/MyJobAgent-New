param(
  [string]$ServiceName = "JobInsightCollector"
)

sc.exe stop $ServiceName
sc.exe delete $ServiceName
