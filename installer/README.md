# HyperSpace AGI — Installer GUI

Installer grafico noob-friendly per Windows 11. Wizard in 6 step che:

1. **Check prerequisiti** — Docker Desktop, Git, docker compose v2
2. **Scelta backend** — Ollama o LM Studio con test connessione
3. **Selezione modello** — lista dinamica dal backend attivo
4. **Cartella** — dove installare con browse
5. **Installazione** — git clone + .env automatico + docker compose up
6. **Done** — apri la dashboard con un click

## Avvio rapido

```powershell
pip install customtkinter requests
python hyperspace-installer.pyw
```

## Build .exe standalone (distribuibile)

```powershell
cd installer
.\build-exe.ps1
# Output: installer\dist\HyperSpaceAGI-Installer.exe
```

L'exe non richiede Python installato sull'utente finale.

## Requisiti sistema

- Windows 10/11 64-bit
- Docker Desktop avviato
- Git installato
- Python 3.9+ (solo per sviluppo, non per l'exe finale)
- Ollama **oppure** LM Studio in esecuzione
