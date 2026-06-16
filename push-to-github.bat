@echo off
echo ================================================
echo  AuditGuard AI — Push to GitHub
echo ================================================
echo.

cd /d "%~dp0"

echo [1/5] Initializing git (safe to re-run)...
git init
git config user.name "Sylesh Kona"
git config user.email "syleshkona@gmail.com"
git branch -M main

echo [2/5] Creating .gitignore...
(
echo __pycache__/
echo *.py[cod]
echo .env
echo *.db
echo *.log
echo server_*.log
echo maxshield.db
) > .gitignore

echo [3/5] Adding remote origin...
git remote remove origin 2>nul
git remote add origin https://github.com/Sylesh29/AuditGuard-AI.git

echo [4/5] Staging all files...
git add .

echo [5/5] Committing and pushing...
git commit -m "feat: AuditGuard AI — 4-agent manufacturing data rescue system"
git push -u origin main

echo.
echo ================================================
echo  Done! Check: https://github.com/Sylesh29/AuditGuard-AI
echo ================================================
pause
