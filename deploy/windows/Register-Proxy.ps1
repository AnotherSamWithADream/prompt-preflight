# Registers a Scheduled Task that runs the enhancing proxy at logon.
#   powershell -ExecutionPolicy Bypass -File deploy\windows\Register-Proxy.ps1
# Remove with:  Unregister-ScheduledTask -TaskName 'prompt-preflight-proxy' -Confirm:$false

$enhance = (Get-Command enhance -ErrorAction Stop).Source
$action  = New-ScheduledTaskAction -Execute $enhance -Argument '--serve-only'
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName 'prompt-preflight-proxy' -Action $action -Trigger $trigger `
  -Settings $settings -Description 'prompt-preflight enhancing proxy' -Force

Write-Host "Registered. The proxy will start at next logon (or run: Start-ScheduledTask -TaskName prompt-preflight-proxy)."
Write-Host "Then point Claude Code at it:  `$env:ANTHROPIC_BASE_URL = 'http://127.0.0.1:8788'; claude"
