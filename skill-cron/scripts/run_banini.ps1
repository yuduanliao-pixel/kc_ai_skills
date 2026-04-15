param(
    [string]$JobId = "banini",
    [switch]$WeekdayOnly
)

if ($WeekdayOnly) {
    $dow = (Get-Date).DayOfWeek
    if ($dow -eq 'Saturday' -or $dow -eq 'Sunday') {
        exit 0
    }
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$runner = Join-Path $scriptDir "cron_runner.ps1"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$runner" -JobId "$JobId"
