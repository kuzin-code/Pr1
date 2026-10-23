# Автозапуск сервера АС при входе пользователя в Windows.
# Запуск: правой кнопкой → «Выполнить с помощью PowerShell».
# С правами администратора — задача Планировщика с перезапуском при сбое;
# без прав — ярлык в папке «Автозагрузка» текущего пользователя (права администратора не нужны).
# Удаление: Unregister-ScheduledTask -TaskName "AS-Planirovanie" -Confirm:$false
#           и/или удалить ярлык «АС Планирование.lnk» из shell:startup

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$bat = Join-Path $root "scripts\start_server.bat"
if (-not (Test-Path $bat)) { throw "Не найден $bat" }

try {
    $action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$bat`"" -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask -TaskName "AS-Planirovanie" -Action $action -Trigger $trigger -Settings $settings `
        -Description "Сервер АС «Планирование загрузки и расчёт стоимости» (порт 8000)" -Force -ErrorAction Stop | Out-Null
    Write-Host "Готово: задача Планировщика «AS-Planirovanie» — сервер запускается при входе пользователя $env:USERNAME."
}
catch {
    $startup = [Environment]::GetFolderPath("Startup")
    $link = Join-Path $startup "АС Планирование.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($link)
    $shortcut.TargetPath = $bat
    $shortcut.WorkingDirectory = $root
    $shortcut.WindowStyle = 7  # свёрнутое окно
    $shortcut.Description = "Сервер АС «Планирование загрузки и расчёт стоимости» (порт 8000)"
    $shortcut.Save()
    if (-not (Test-Path $link)) { throw "Не удалось создать ярлык автозапуска: $link" }
    Write-Host "Нет прав на задачу Планировщика — создан ярлык в «Автозагрузке»: $link"
    Write-Host "Готово: сервер запускается при входе пользователя $env:USERNAME (свёрнутым окном)."
}
