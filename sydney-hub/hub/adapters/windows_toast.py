from __future__ import annotations

import logging

from hub.adapters.process import CommandRunner, subprocess_runner

log = logging.getLogger(__name__)

_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$text = $xml.GetElementsByTagName('text')
$text.Item(0).AppendChild($xml.CreateTextNode($env:HUB_TOAST_TITLE)) | Out-Null
$text.Item(1).AppendChild($xml.CreateTextNode($env:HUB_TOAST_MESSAGE)) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
$appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
"""


class WindowsToastNotifier:
    """Desktop notification through PowerShell. Text travels via env vars, never string-built."""

    def __init__(self, runner: CommandRunner = subprocess_runner) -> None:
        self._run = runner

    def notify(self, title: str, message: str) -> None:
        try:
            result = self._run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", _SCRIPT],
                timeout=20, env={"HUB_TOAST_TITLE": title, "HUB_TOAST_MESSAGE": message},
            )
            if result.returncode != 0:
                log.warning("Toast failed: %s", result.stderr.strip()[:200])
        except Exception:
            log.exception("Toast notification failed")
