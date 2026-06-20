# build-exe.ps1 — Crea l'eseguibile standalone per Windows
# Richiede: pip install pyinstaller customtkinter requests
# Uso: cd installer && .\build-exe.ps1

Write-Host "[1/3] Installazione dipendenze..." -ForegroundColor Cyan
pip install -r requirements.txt pyinstaller

Write-Host "`n[2/3] Build .exe con PyInstaller..." -ForegroundColor Cyan
pyinstaller `
    --onefile `
    --windowed `
    --name "HyperSpaceAGI-Installer" `
    --add-data "../docker-compose.yml;." `
    --add-data "../.env.example;." `
    --icon NONE `
    hyperspace-installer.pyw

Write-Host "`n[3/3] Fatto!" -ForegroundColor Green
Write-Host "Eseguibile: dist\HyperSpaceAGI-Installer.exe" -ForegroundColor Yellow
