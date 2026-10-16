@echo off
rem Запуск сервера АС для сети участка (http://<IP этого компьютера>:8000).
rem Пишет журнал работы в data\server.log.
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo Не найдено окружение .venv — см. README, раздел «Установка».
  exit /b 1
)
".venv\Scripts\python.exe" run.py --host 0.0.0.0 --port 8000 >> "data\server.log" 2>&1
