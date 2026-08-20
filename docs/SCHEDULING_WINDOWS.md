# Windows Scheduling

## Register The Task

From the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_scheduled_scan.ps1
```

This creates a task named `Think Tank Scanner` that runs Tuesday to Friday at 5:00am. If the computer is asleep or unavailable at 5:00am, `StartWhenAvailable` lets Windows run it once when the machine becomes available.

## Recommended Manual Setting

Open **Task Scheduler** as Administrator:

1. Open Start.
2. Search for **Task Scheduler**.
3. Right-click and choose **Run as administrator**.
4. Open **Task Scheduler Library**.
5. Double-click **Think Tank Scanner**.
6. On the **General** tab, tick **Run with highest privileges**.
7. Click **OK**.

## Verify The Task

```powershell
Get-ScheduledTask -TaskName "Think Tank Scanner" | Get-ScheduledTaskInfo
Get-ScheduledTask -TaskName "Think Tank Scanner" | Select-Object -ExpandProperty Actions
```

Expected action:

- Program: `C:\Windows\System32\cmd.exe`
- Arguments: `/c "...\scripts\run_scheduled_scan.cmd"`
- Working directory: the repository root

## Logs

Scheduled runs write logs to:

```text
logs\automated_scans\
```

Each run creates one `.out.log` and one `.err.log`.

## Common Results

- `0`: task completed successfully.
- `267009`: task is still running.
- `0x800710E0`: Windows refused to launch the task, often because of principal, privilege, or login-session conditions.

If the task starts but no report appears, check the latest `.out.log` and `.err.log` first.
