# Installation

## Windows

1. Repository klonen oder entpacken
2. `config/config.example.json` nach `config/config.json` kopieren
3. Werte anpassen
4. PowerShell als Administrator öffnen
5. Im Repo-Verzeichnis:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1
```

Danach testweise starten:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_connector.ps1
```

Optional als Scheduled Task beim Systemstart installieren:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows_task.ps1
```

## Linux

```bash
cp config/config.example.json config/config.json
bash scripts/setup_linux.sh
bash scripts/run_connector.sh
```

Optional als systemd-Service:

```bash
sudo cp scripts/openclaw-connector.service /etc/systemd/system/openclaw-connector.service
sudo systemctl daemon-reload
sudo systemctl enable --now openclaw-connector
```
