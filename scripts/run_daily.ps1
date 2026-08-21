Set-Location "E:\MyProject"
New-Item -ItemType Directory -Force -Path "logs" | Out-Null
& ".\venv\Scripts\python.exe" "-m" "orchestrator.main" "--dry-run" *>> "logs\run.log"
