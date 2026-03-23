# OpenClaw Customer Connector

Ein ausgehender Connector für Kundenumgebungen mit intern erreichbaren Zielservern.

## Ziel

Der Connector läuft **in der Kundenumgebung** (z. B. auf einer VPC-/Jump-/Bastion-Maschine) und baut **ausgehende HTTPS-Verbindungen** zu eurem zentralen Control Plane auf.

Von dort zieht er Jobs, sendet Heartbeats und führt intern Remote-Befehle gegen Zielserver aus via:

- SSH
- WinRM

## Warum dieser Connector?

Wenn Zielserver nur intern erreichbar sind (z. B. `192.168.x.x`), kann ein externer Runner auf Hostinger sie nicht direkt erreichen. Der Connector löst das, indem er **im Kundennetz** läuft, aber trotzdem zentral steuerbar bleibt.

## Features

- Outbound-Only-Architektur (keine eingehenden Ports nötig)
- Heartbeats an euer zentrales System
- Job-Polling
- Ausführung via SSH oder WinRM
- Weitergabe von Progress- und Final-Callbacks
- Konfiguration über `config/config.json`
- Windows- und Linux-Setup-Skripte

## Struktur

- `app/` – Connector-Code
- `config/config.example.json` – Beispielkonfiguration
- `docs/CONTROL_PLANE_API.md` – Erwartete Schnittstelle zum zentralen System
- `docs/INSTALL.md` – Installationsanleitung
- `scripts/` – Setup-/Start-Skripte

## Schnellstart Windows

1. Repo klonen oder ZIP entpacken
2. `config/config.example.json` nach `config/config.json` kopieren
3. Werte anpassen
4. PowerShell als Administrator öffnen
5. `scripts/setup_windows.ps1` ausführen
6. Danach `scripts/run_connector.ps1` oder `scripts/install_windows_task.ps1`

## Schnellstart Linux

1. Repo klonen oder ZIP entpacken
2. `config/config.example.json` nach `config/config.json` kopieren
3. Werte anpassen
4. `bash scripts/setup_linux.sh`
5. `bash scripts/run_connector.sh`

## Wichtige Annahmen

Das zentrale System muss mindestens zwei Endpunkte bereitstellen:

- Heartbeat-Endpunkt
- Job-Pull-Endpunkt

Die Job-Antwort soll einen vollständigen Remote-Execute-Job enthalten, inklusive `callback`-Objekt, damit der Connector Progress- und Final-Events direkt an das Dashboard oder den gewünschten Callback senden kann.
