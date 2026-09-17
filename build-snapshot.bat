@echo off
setlocal enabledelayedexpansion

rem Load .env (if present) into this script's environment, e.g. ENDERMAN_GRIEF_TEST_MODE=true -
rem picked up below by both builds since mvn/gradlew inherit it as a child process.
if exist "%~dp0.env" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%~dp0.env") do set "%%A=%%B"
)

echo ============================================
echo  Installing shared messaging module
echo ============================================
rem Installed at its own plain (non-SNAPSHOT) version - it doesn't participate in either
rem platform's release/SNAPSHOT cadence, it's a separately-versioned shared library.
pushd enderman-grief-control-messaging
call mvn install
if errorlevel 1 goto :fail
popd

echo.
echo ============================================
echo  Building SNAPSHOT: Paper plugin
echo ============================================
pushd paper-plugin
for /f "tokens=3 delims=<>" %%V in ('findstr "<revision>" pom.xml') do set "PAPER_VERSION=%%V"
call mvn package "-Drevision=!PAPER_VERSION!-SNAPSHOT"
if errorlevel 1 goto :fail
popd

echo.
echo ============================================
echo  Building SNAPSHOT: Fabric mod
echo ============================================
pushd fabric-mod
call .\gradlew.bat build "-PversionSuffix=-SNAPSHOT"
if errorlevel 1 goto :fail
popd

echo.
echo Both SNAPSHOT builds succeeded.
echo   Paper:  paper-plugin\target\
echo   Fabric: fabric-mod\build\libs\
pause
exit /b 0

:fail
popd
echo.
echo Build FAILED - see output above.
pause
exit /b 1
