Set-Location "E:\MyProject"
New-Item -ItemType Directory -Force -Path "logs" | Out-Null

# 서머타임 자동 조정: 미국 동부 기준 서머타임(EDT) 여부에 따라 다음 실행 트리거
# 시각을 19:30(EDT)/20:30(EST)으로 매번 재확인/재설정한다 (장 시작 3시간 전 분석 시작,
# 매매는 orchestrator 가 config.yaml schedule.trade_time_et(09:31 ET)까지 기다렸다가 실행).
try {
    $easternTz = [System.TimeZoneInfo]::FindSystemTimeZoneById("Eastern Standard Time")
    $isDst = $easternTz.IsDaylightSavingTime((Get-Date))
    $correctTime = if ($isDst) { "19:30" } else { "20:30" }

    $task = Get-ScheduledTask -TaskName "TradingBot Daily Run" -ErrorAction SilentlyContinue
    if ($task) {
        $currentTime = ([datetime]$task.Triggers[0].StartBoundary).ToString("HH:mm")
        if ($currentTime -ne $correctTime) {
            # 트리거만 교체한다 (Register -Force 로 작업을 다시 만들면 지금 돌고 있는 이 실행에 영향을 줄 수 있음)
            $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $correctTime
            Set-ScheduledTask -TaskName "TradingBot Daily Run" -Trigger $trigger | Out-Null
        }
    }
} catch {
    # 스케줄 자동조정 실패는 오늘 실행 자체를 막을 이유가 아니므로 무시하고 계속 진행
}

# 로그 로테이션: 날짜별 파일로 남기고, 30일보다 오래된 로그는 삭제
$logFile = "logs\run-$(Get-Date -Format 'yyyy-MM-dd').log"
Get-ChildItem -Path "logs" -Filter "run-*.log" |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |
    Remove-Item -Force

# run_analysis.py/decision_mapping.py 는 이미지에 구워져 있으므로(docker/Dockerfile)
# 매 실행 전 빌드해 최신 스크립트를 반영한다 (변경 없으면 캐시로 수 초).
docker compose build -q *>> $logFile

& ".\venv\Scripts\python.exe" "-m" "orchestrator.main" *>> $logFile
