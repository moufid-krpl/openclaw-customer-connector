# Control Plane API für den OpenClaw Customer Connector

Diese Datei beschreibt die erwartete Schnittstelle zwischen eurem zentralen System und dem Connector.

## 1. Heartbeat

### Request
`POST <heartbeat_url>`

### Header
- `X-API-Key`
- `Content-Type: application/json`

### Beispiel-Body
```json
{
  "connector_id": "customer-vpc-connector-01",
  "connector_name": "OpenClaw Customer Connector",
  "status": "idle",
  "current_job_id": null,
  "current_mode": null,
  "last_error": null,
  "supported_modes": ["ssh", "winrm"],
  "metadata": {
    "customer_id": "customer-001",
    "site_id": "site-primary",
    "supported_os": ["linux", "windows"]
  },
  "timestamp": "2026-03-23T00:00:00Z"
}
```

### Erwartete Response
- `200 OK`
- optional Body, wird aktuell nicht ausgewertet

---

## 2. Job Pull

### Request
`POST <job_pull_url>`

### Beispiel-Body
```json
{
  "connector_id": "customer-vpc-connector-01",
  "connector_name": "OpenClaw Customer Connector",
  "customer_id": "customer-001",
  "site_id": "site-primary",
  "supported_modes": ["ssh", "winrm"],
  "supported_os": ["linux", "windows"],
  "metadata": {
    "environment": "customer-vpc"
  }
}
```

### Mögliche Responses

#### Kein Job
- `204 No Content`

oder

```json
{
  "status": "idle"
}
```

#### Job vorhanden
```json
{
  "job": {
    "tenant_id": "tenant-acme",
    "command_id": "cmd-001",
    "command": "Get-ChildItem C:\\Temp",
    "target": {
      "mode": "winrm",
      "host": "192.168.12.17",
      "port": 5985,
      "username": "Administrator",
      "os_type": "windows",
      "auth_method": "password",
      "password": "<SECRET>",
      "winrm_transport": "ntlm",
      "winrm_scheme": "http",
      "winrm_server_cert_validation": "ignore"
    },
    "callback": {
      "url": "https://dashboard.example.com/api/v1/tenants/tenant-acme/ssh/callback",
      "api_key": "<DASHBOARD_CALLBACK_KEY>"
    },
    "timeout_seconds": 120,
    "connection_timeout_seconds": 15,
    "progress_updates_enabled": true,
    "progress_callback_interval_ms": 2000,
    "suppress_terminal_animations": true,
    "max_callback_console_chars": 12000,
    "treat_nonzero_exit_code_as_error": true,
    "callback_error_detail_level": "standard",
    "callback_include_debug_context": true,
    "redact_secrets_in_output": true,
    "shell_prefix": "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command"
  }
}
```

Der Connector akzeptiert alternativ auch direkt den Job als Root-Objekt, solange die Felder eines `RemoteExecuteRequest` vorhanden sind.

---

## 3. Ergebnisrückgabe / Progress

Der Connector ruft **nicht** einen separaten Control-Plane-Result-Endpunkt auf, sondern verwendet das `callback`-Objekt aus dem Job.

Dadurch kann das zentrale System denselben Callback-Flow wie beim bestehenden Runner verwenden.

### Progress-Callback
- `event_type = progress`
- `status = running`
- `sequence`
- `console_snapshot`
- `console_render_mode = replace`

### Final-Callback
- `event_type = final`
- `status = completed` oder `error`
- zusätzlich `stdout`, `stderr`, `exit_code`, `error`

---

## 4. Empfehlung für Lovable / Zentralsystem

Das Dashboard sollte künftig je Kundenumgebung zwischen diesen Ausführungsarten unterscheiden:

- zentraler externer Runner
- interner Customer Connector

Für interne Netze mit privaten Adressen sollte bevorzugt der Connector genutzt werden.
