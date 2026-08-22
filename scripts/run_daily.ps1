Set-Location "E:\MyProject"
New-Item -ItemType Directory -Force -Path "logs" | Out-Null

# 로그 로테이션: 날짜별 파일로 남기고, 30일보다 오래된 로그는 삭제
$logFile = "logs\run-$(Get-Date -Format 'yyyy-MM-dd').log"
Get-ChildItem -Path "logs" -Filter "run-*.log" |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |
    Remove-Item -Force

& ".\venv\Scripts\python.exe" "-m" "orchestrator.main" *>> $logFile
