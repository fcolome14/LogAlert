import requests
import json
import os
from dotenv import load_dotenv
import paramiko
import re
from pathlib import Path

load_dotenv(Path(__file__).parent / ".env")


def _get_env(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(f"Missing required environment variable: {key}")
    return value


# Matches lines like:
# 2026-04-22 10:00:37,983|INFO|MainThread|Message here...|file.py|352
LOG_PATTERN = re.compile(
    r"(?P<datetime>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})"
    r"\|(?P<level>INFO|WARNING|ERROR|CRITICAL|DEBUG)"
    r"\|(?P<thread>[^|]+)"
    r"\|(?P<message>[^|]+)"
    r"\|(?P<file>[\w.]+)"
    r"\|(?P<line>\d+)"
)

ALERT_LEVELS = {"ERROR", "CRITICAL", "WARNING"}

ALERT_KEYWORDS = [
    "Authentication expired",
    "No content available",
    "Database connected",
    "error",
    "failed",
    "timeout",
]

THEME_COLORS = {
    "ERROR":    "FF0000",  # Red
    "CRITICAL": "FF0000",  # Red
    "WARNING":  "FFA500",  # Orange
    "INFO":     "0076D7",  # Blue (test mode only)
    "DEBUG":    "808080",  # Gray (test mode only)
}


def send_teams_alert(webhook_url: str, match: re.Match) -> None:
    level = match.group("level")
    payload = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "summary": "Log Alert",
        "themeColor": THEME_COLORS.get(level, "0076D7"),
        "title": f"🚨 {level} Detected" if level in ALERT_LEVELS else f"🔵 [{level}] Log Entry",
        "sections": [{
            "facts": [
                {"name": "Time",    "value": match.group("datetime")},
                {"name": "Level",   "value": level},
                {"name": "Thread",  "value": match.group("thread")},
                {"name": "Message", "value": match.group("message").strip()},
                {"name": "File",    "value": f"{match.group('file')}:{match.group('line')}"},
            ]
        }]
    }

    response = requests.post(
        webhook_url,
        data=json.dumps(payload),
        headers={"Content-Type": "application/json"},
        verify=False
    )
    print(f"  [ALERT SENT] Status: {response.status_code}")


def main():
    webhook_url = _get_env("WEBHOOK_URL")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            hostname=_get_env("SSH_HOST"),
            username=_get_env("SSH_USER"),
            key_filename=_get_env("SSH_KEY_PATH"),
            look_for_keys=False,
            allow_agent=False,
        )
        print("[OK] SSH connected")

        command = "tail -f /home/ravaps/sw_ravaps/logs/orchestrator/ravaps_orchestrator"
        stdin, stdout, stderr = client.exec_command(command)

        print("[OK] Watching logs...\n")

        for line in stdout:
            raw = line.strip()

            match = LOG_PATTERN.match(raw)

            if not match:
                # Line didn't match expected format — print it so you can debug
                print(f"[UNMATCHED] {raw}")
                continue

            level = match.group("level")
            dt = match.group("datetime")
            message = match.group("message").strip().lower()
            file = match.group("file")
            lineno = match.group("line")

            if any(keyword.lower() in message for keyword in ALERT_KEYWORDS):
                print(f"[MATCH] {match.group('datetime')} | {message}")
                send_teams_alert(webhook_url, match)

    except KeyboardInterrupt:
        print("\n[STOP] Monitoring stopped")
    except Exception as e:
        print(f"[ERROR] {e}")
    finally:
        client.close()
        print("[OK] SSH connection closed")


if __name__ == "__main__":
    main()