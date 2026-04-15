param(
    [Parameter(Mandatory=$true)]
    [string]$JobId
)

$ErrorActionPreference = "Stop"
$homeDir = $env:USERPROFILE
$env:PATH = (Join-Path $homeDir ".local\bin") + ";" + $env:PATH
$configFile = Join-Path $homeDir ".claude\configs\skill-cron.json"
$logDir = Join-Path $homeDir ".claude\logs\skill-cron"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logFile = Join-Path $logDir "$JobId-$timestamp.log"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

"[$timestamp] Running job: $JobId" | Tee-Object -FilePath $logFile

# ── Telegram helper ────────────────────────────────────────
function Send-Telegram {
    param([string]$BotToken, [string]$ChannelId, [string]$Text)
    $payload = @{
        chat_id                  = $ChannelId
        text                     = $Text
        disable_web_page_preview = $true
    }
    try {
        $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes(($payload | ConvertTo-Json -Depth 4))
        Invoke-RestMethod `
            -Uri "https://api.telegram.org/bot$BotToken/sendMessage" `
            -Method Post `
            -ContentType "application/json; charset=utf-8" `
            -Body $bodyBytes | Out-Null
    } catch {
        "[telegram] send failed: $($_.Exception.Message)" | Tee-Object -FilePath $logFile -Append
    }
}

function Send-ErrorAlert {
    param([string]$Message)
    if (-Not $script:tgToken -or -Not $script:tgChannel) { return }
    $text = "[ERROR] $JobId`n`n$Message`n`n$timestamp"
    Send-Telegram -BotToken $script:tgToken -ChannelId $script:tgChannel -Text $text
}

# Load Telegram credentials as early as possible
$script:tgToken   = $null
$script:tgChannel = $null

if (Test-Path $configFile) {
    try {
        $cfg = Get-Content $configFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $script:tgToken   = $cfg.telegram.bot_token
        $script:tgChannel = $cfg.telegram.channel_id
        if ($cfg.anthropic_api_key) {
            $env:ANTHROPIC_API_KEY = $cfg.anthropic_api_key
        }
    } catch {}
}

# ── Pre-flight checks ──────────────────────────────────────
if (-Not (Test-Path $configFile)) {
    $msg = "config file not found: $configFile"
    "[error] $msg" | Tee-Object -FilePath $logFile -Append
    Send-ErrorAlert -Message $msg
    exit 1
}

$config = Get-Content $configFile -Raw -Encoding UTF8 | ConvertFrom-Json
$job = $config.jobs | Where-Object { $_.id -eq $JobId }
if (-Not $job) {
    $msg = "job not found: $JobId"
    "[error] $msg" | Tee-Object -FilePath $logFile -Append
    Send-ErrorAlert -Message $msg
    exit 1
}

$skillDir = Join-Path $homeDir ".claude\skills\$($job.skill)"
$skillMd = Join-Path $skillDir "SKILL.md"
if (-Not (Test-Path $skillMd)) {
    $msg = "skill file not found: $skillMd"
    "[error] $msg" | Tee-Object -FilePath $logFile -Append
    Send-ErrorAlert -Message $msg
    exit 1
}

function Get-HeadlessPrompt {
    param([string]$Path)

    $content = Get-Content $Path -Raw -Encoding UTF8
    if ($content -notmatch '^\s*---\s*\r?\n') {
        return $null
    }

    $parts = $content -split '---', 3
    if ($parts.Length -lt 3) {
        return $null
    }

    $frontmatter = $parts[1]
    $blockLines = @()
    $inBlock = $false

    foreach ($line in ($frontmatter -split '\r?\n')) {
        if ($inBlock) {
            if ($line -match '^\s+\S') {
                $blockLines += $line.Trim()
                continue
            }
            break
        }

        if ($line -match '^\s*headless-prompt:\s*\|\s*$') {
            $inBlock = $true
            continue
        }

        if ($line -match '^\s*headless-prompt:\s*"(.*)"\s*$') {
            return $matches[1]
        }

        if ($line -match "^\s*headless-prompt:\s*'(.*)'\s*$") {
            return $matches[1]
        }
    }

    if ($blockLines.Count -gt 0) {
        return ($blockLines -join "`n")
    }

    return $null
}

$prompt = Get-HeadlessPrompt -Path $skillMd
if (-Not $prompt) {
    $msg = "unable to read headless-prompt from $skillMd"
    "[error] $msg" | Tee-Object -FilePath $logFile -Append
    Send-ErrorAlert -Message $msg
    exit 1
}

# ── Run claude ─────────────────────────────────────────────
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
try {
    $output = (& claude -p $prompt --allowedTools "Bash,Read,Glob,Grep" 2>&1) | Out-String
    $exitCode = $LASTEXITCODE
} catch {
    $msg = "claude execution failed: $($_.Exception.Message)"
    "[error] $msg" | Tee-Object -FilePath $logFile -Append
    Send-ErrorAlert -Message $msg
    exit 1
}

$output | Tee-Object -FilePath $logFile -Append

if ($exitCode -ne 0) {
    $snippet = ($output -split '\r?\n' | Select-Object -Last 10) -join "`n"
    $msg = "claude -p exited with code $exitCode`n`nLast output:`n$snippet"
    "[error] $msg" | Tee-Object -FilePath $logFile -Append
    Send-ErrorAlert -Message $msg
    exit $exitCode
}

# ── Push output to Telegram ────────────────────────────────
if (-Not [string]::IsNullOrWhiteSpace($output)) {
    $telegram = $config.telegram
    if ($telegram.bot_token -and $telegram.channel_id) {
        function Split-TextChunks {
            param([string]$Text, [int]$Limit = 4000)
            $chunks = @()
            $remaining = $Text.Trim()
            while ($remaining.Length -gt $Limit) {
                $cut = $remaining.LastIndexOf("`n`n", $Limit)
                if ($cut -lt 0) { $cut = $remaining.LastIndexOf("`n", $Limit) }
                if ($cut -lt 0) { $cut = $Limit }
                $chunks += $remaining.Substring(0, $cut).TrimEnd()
                $remaining = $remaining.Substring($cut).TrimStart("`n")
            }
            if ($remaining.Length -gt 0) {
                $chunks += $remaining.TrimEnd()
            }
            return $chunks
        }

        $header = "[$($job.label)]`n`n"
        $fullText = $header + $output
        $chunks = Split-TextChunks -Text $fullText -Limit 4000
        $allSent = $true
        foreach ($chunk in $chunks) {
            try {
                $payload = @{
                    chat_id                  = $telegram.channel_id
                    text                     = $chunk
                    disable_web_page_preview = $true
                } | ConvertTo-Json -Depth 4
                $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($payload)
                Invoke-RestMethod -Uri "https://api.telegram.org/bot$($telegram.bot_token)/sendMessage" -Method Post -ContentType "application/json; charset=utf-8" -Body $bodyBytes | Out-Null
            } catch {
                "[telegram] chunk failed: $($_.Exception.Message)" | Tee-Object -FilePath $logFile -Append
                $allSent = $false
            }
            Start-Sleep -Milliseconds 500
        }
        if ($allSent) {
            "[telegram] sent" | Tee-Object -FilePath $logFile -Append
        } else {
            "[telegram] partial send (some chunks failed)" | Tee-Object -FilePath $logFile -Append
        }
    }
} else {
    $msg = "claude returned no output"
    "[warn] $msg" | Tee-Object -FilePath $logFile -Append
    Send-ErrorAlert -Message $msg
}

# ── Cleanup old logs ───────────────────────────────────────
Get-ChildItem -Path $logDir -Filter "$JobId-*.log" | Sort-Object LastWriteTime -Descending | Select-Object -Skip 50 | Remove-Item -Force -ErrorAction SilentlyContinue

"[done] $JobId" | Tee-Object -FilePath $logFile -Append
