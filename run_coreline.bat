@echo off
rem Starts CORELINE. Uses the computer's Python if it has one; otherwise the Python
rem that comes with Houdini, so university PCs don't need Python installed.
setlocal
rem pushd (not cd) so this also works from a network path like \\server\share\CORELINE.
pushd "%~dp0"
rem Don't write __pycache__ into the shared tool folder.
set "PYTHONDONTWRITEBYTECODE=1"

set "PY="
python -c "import tkinter" >nul 2>nul && set "PY=python"

if not defined PY (
    for /d %%D in ("C:\Program Files\Side Effects Software\Houdini *") do (
        for /d %%P in ("%%D\python3*") do (
            if exist "%%P\python.exe" set "PY=%%P\python.exe"
        )
    )
)

if not defined PY (
    for /d %%D in ("C:\Program Files\Autodesk\Maya20*" "C:\Autodesk\Maya20*") do (
        if exist "%%D\bin\mayapy.exe" set "PY=%%D\bin\mayapy.exe"
    )
)

if not defined PY (
    echo Could not find Python, Houdini or Maya on this computer.
    pause
    exit /b 1
)

"%PY%" -m coreline
if errorlevel 1 pause
popd
