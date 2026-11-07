# Legt eine Aufgabe an, die VGrow Cockpit (inoffiziell) bei jeder Anmeldung im Hintergrund startet.
# Der Aufgabenname VGrow-Control bleibt: ein neuer Name liesse die alte Aufgabe weiterlaufen (Alarme doppelt).
# Entfernen: Unregister-ScheduledTask -TaskName "VGrow-Control" -Confirm:$false
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\pythonw.exe"
$log = Join-Path $env:LOCALAPPDATA "vgrow-control\server.log"

# pythonw: no console window; the server then writes its own log to $log
$action = New-ScheduledTaskAction -Execute $python -Argument "-m vgrow" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)

Register-ScheduledTask -TaskName "VGrow-Control" -Action $action -Trigger $trigger -Settings $settings `
    -Description "VGrow Cockpit (inoffiziell, kein VIVOSUN-Produkt): Handy-Seite und Alarme fuer die Grow-Box" -Force | Out-Null
Write-Host "Aufgabe VGrow-Control angelegt. Log: $log"
Write-Host "Jetzt starten: Start-ScheduledTask -TaskName VGrow-Control"
