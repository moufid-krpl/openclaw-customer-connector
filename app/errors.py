from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Any

import paramiko
import requests
from winrm.exceptions import (
    AuthenticationError,
    BasicAuthDisabledError,
    InvalidCredentialsError,
    WinRMError,
    WinRMOperationTimeoutError,
    WinRMTransportError,
)


@dataclass
class ConnectorErrorData:
    code: str
    category: str
    phase: str
    message: str
    detail: str | None = None
    retryable: bool = False
    suggested_action: str | None = None
    debug_context: dict[str, Any] | None = None


class ConnectorExecutionError(Exception):
    def __init__(
        self,
        *,
        code: str,
        category: str,
        phase: str,
        message: str,
        detail: str | None = None,
        retryable: bool = False,
        suggested_action: str | None = None,
        debug_context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.data = ConnectorErrorData(
            code=code,
            category=category,
            phase=phase,
            message=message,
            detail=detail,
            retryable=retryable,
            suggested_action=suggested_action,
            debug_context=debug_context or {},
        )


def classify_exception(exc: Exception, *, phase: str, debug_context: dict[str, Any] | None = None) -> ConnectorErrorData:
    debug_context = debug_context or {}

    if isinstance(exc, ConnectorExecutionError):
        return exc.data

    if isinstance(exc, (paramiko.AuthenticationException, InvalidCredentialsError, AuthenticationError)):
        return ConnectorErrorData(
            code="remote_auth_failed",
            category="authentication",
            phase=phase,
            message="Die Authentifizierung zum Zielsystem ist fehlgeschlagen.",
            detail=str(exc),
            retryable=False,
            suggested_action="Benutzername, Passwort oder Authentifizierungsmethode prüfen.",
            debug_context=debug_context,
        )

    if isinstance(exc, BasicAuthDisabledError):
        return ConnectorErrorData(
            code="winrm_basic_auth_disabled",
            category="authentication",
            phase=phase,
            message="WinRM Basic Authentication ist auf dem Zielsystem deaktiviert.",
            detail=str(exc),
            retryable=False,
            suggested_action="NTLM oder CredSSP verwenden oder Basic Auth auf dem Zielsystem aktivieren.",
            debug_context=debug_context,
        )

    if isinstance(exc, paramiko.BadHostKeyException):
        return ConnectorErrorData(
            code="ssh_bad_host_key",
            category="security",
            phase=phase,
            message="Die Host-Key-Prüfung ist fehlgeschlagen.",
            detail=str(exc),
            retryable=False,
            suggested_action="Known Hosts und Host-Key des Zielservers prüfen.",
            debug_context=debug_context,
        )

    if isinstance(exc, paramiko.ssh_exception.NoValidConnectionsError):
        return ConnectorErrorData(
            code="ssh_connection_refused",
            category="network",
            phase=phase,
            message="Es konnte keine SSH-Verbindung zum Zielserver aufgebaut werden.",
            detail=str(exc),
            retryable=True,
            suggested_action="Host, Port, Firewall-Regeln und Erreichbarkeit des Servers prüfen.",
            debug_context=debug_context,
        )

    if isinstance(exc, paramiko.SSHException):
        return ConnectorErrorData(
            code="ssh_protocol_error",
            category="protocol",
            phase=phase,
            message="Während der SSH-Kommunikation ist ein Protokollfehler aufgetreten.",
            detail=str(exc),
            retryable=True,
            suggested_action="SSH-Dienst, Cipher/Algorithmen und Serverlogs prüfen.",
            debug_context=debug_context,
        )

    if isinstance(exc, WinRMOperationTimeoutError):
        return ConnectorErrorData(
            code="winrm_operation_timeout",
            category="timeout",
            phase=phase,
            message="Der WinRM-Befehl hat das Zeitlimit überschritten.",
            detail=str(exc),
            retryable=True,
            suggested_action="Command-Timeout erhöhen oder Zielsystem prüfen.",
            debug_context=debug_context,
        )

    if isinstance(exc, (WinRMTransportError, WinRMError)):
        return ConnectorErrorData(
            code="winrm_transport_error",
            category="network",
            phase=phase,
            message="Beim WinRM-Transport ist ein Fehler aufgetreten.",
            detail=str(exc),
            retryable=True,
            suggested_action="Port, Scheme, Transporttyp und WinRM-Listener prüfen.",
            debug_context=debug_context,
        )

    if isinstance(exc, socket.gaierror):
        return ConnectorErrorData(
            code="remote_dns_resolution_failed",
            category="network",
            phase=phase,
            message="Der Zielhost konnte nicht per DNS aufgelöst werden.",
            detail=str(exc),
            retryable=True,
            suggested_action="Hostname oder DNS-Auflösung prüfen.",
            debug_context=debug_context,
        )

    if isinstance(exc, (socket.timeout, TimeoutError)):
        return ConnectorErrorData(
            code="remote_socket_timeout",
            category="timeout",
            phase=phase,
            message="Die Verbindung zum Zielsystem ist in ein Timeout gelaufen.",
            detail=str(exc),
            retryable=True,
            suggested_action="Netzwerkpfad, Firewall und Timeout-Werte prüfen.",
            debug_context=debug_context,
        )

    if isinstance(exc, requests.RequestException):
        return ConnectorErrorData(
            code="callback_delivery_failed",
            category="callback",
            phase=phase,
            message="Ein HTTP-Request ist fehlgeschlagen.",
            detail=str(exc),
            retryable=True,
            suggested_action="URL, Netzwerkzugang, TLS und Authentifizierung prüfen.",
            debug_context=debug_context,
        )

    return ConnectorErrorData(
        code="connector_unhandled_error",
        category="internal",
        phase=phase,
        message="Ein nicht klassifizierter Fehler ist aufgetreten.",
        detail=str(exc),
        retryable=False,
        suggested_action="Logs und Debug-Kontext prüfen.",
        debug_context=debug_context,
    )
