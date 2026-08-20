$ErrorActionPreference = "Stop"

$taskName = "Think Tank Scanner"
$projectPath = Resolve-Path (Join-Path $PSScriptRoot "..")
$scriptPath = Join-Path $PSScriptRoot "run_scheduled_scan.cmd"

$action = New-ScheduledTaskAction `
    -Execute "C:\Windows\System32\cmd.exe" `
    -Argument "/c `"$scriptPath`"" `
    -WorkingDirectory $projectPath

$trigger = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Tuesday, Wednesday, Thursday, Friday `
    -At 5:00am

$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 72) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Force | Out-Null

Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
