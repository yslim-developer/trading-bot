Set-Location "E:\MyProject"
New-Item -ItemType Directory -Force -Path "logs" | Out-Null

# 서머타임 자동 조정: 미국 동부 기준 서머타임(EDT) 여부에 따라 다음 실행 트리거
# 시각을 22:40(EDT)/23:40(EST)으로 매번 재확인/재설정한다. config.yaml의
# run_time_kst는 EDT 기준값이므로 이 값과 맞물려 있다.
try {
    $easternTz = [System.TimeZoneInfo]::FindSystemTimeZoneById("Eastern Standard Time")
    $isDst = $easternTz.IsDaylightSavingTime((Get-Date))
    $correctTime = if ($isDst) { "22:40" } else { "23:40" }

    $task = Get-ScheduledTask -TaskName "TradingBot Daily Run" -ErrorAction SilentlyContinue
    if ($task) {
        $currentTime = ([datetime]$task.Triggers[0].StartBoundary).ToString("HH:mm")
        if ($currentTime -ne $correctTime) {
            $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-File E:\MyProject\scripts\run_daily.ps1"
            $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $correctTime
            $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable
            Register-ScheduledTask -TaskName "TradingBot Daily Run" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
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

& ".\venv\Scripts\python.exe" "-m" "orchestrator.main" *>> $logFile
