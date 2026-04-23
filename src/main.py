import requests
import json
import os
from dotenv import load_dotenv
import paramiko
import re
from pathlib import Path
import threading
from datetime import datetime, timedelta

load_dotenv(Path(__file__).parent / ".env")


def _get_env(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(f"Missing required environment variable: {key}")
    return value

def _get_date(days_back: int = 0) -> str:
    return (datetime.now() - timedelta(days=days_back)).strftime("%Y%m%d")


# ─────────────────────────────────────────────
# Log line patterns — handles 3 different formats:
#
#   Format 1 (orchestrator):
#     2026-04-22 10:00:37,983|INFO|MainThread|Message|file.py|352
#
#   Format 2 (notam_delivery, notam_reader):
#     [2026-04-21 17:33:41]|INFO|Message|file.py|63
#     Note: NO thread field
#
#   Format 3 (notam_assessment, notam_formatting):
#     2026-04-21 21:33:58|INFO|Message|file.cpp|143
#     Note: NO thread field, no milliseconds
#
# Also handles level "ERRO" (shorthand for ERROR in some modules)
# ─────────────────────────────────────────────

# Format 1: with milliseconds and thread field
LOG_PATTERN_1 = re.compile(
    r"(?P<datetime>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})"
    r"\|(?P<level>INFO|WARNING|ERROR|CRITICAL|DEBUG|ERRO)"
    r"\|(?P<thread>[^|]+)"
    r"\|(?P<message>[^|]+)"
    r"\|(?P<file>[\w.]+)"
    r"\|(?P<line>\d+)"
)

# Format 2: bracketed datetime, NO thread
LOG_PATTERN_2 = re.compile(
    r"\[(?P<datetime>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]"
    r"\|(?P<level>INFO|WARNING|ERROR|CRITICAL|DEBUG|ERRO)"
    r"\|(?P<message>[^|]+)"
    r"\|(?P<file>[\w.]+)"
    r"\|(?P<line>\d+)"
)

# Format 3: no brackets, no milliseconds, NO thread
LOG_PATTERN_3 = re.compile(
    r"(?P<datetime>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"
    r"\|(?P<level>INFO|WARNING|ERROR|CRITICAL|DEBUG|ERRO)"
    r"\|(?P<message>[^|]+)"
    r"\|(?P<file>[\w.]+)"
    r"\|(?P<line>\d+)"
)

ALL_PATTERNS = [LOG_PATTERN_1, LOG_PATTERN_2, LOG_PATTERN_3]


def parse_line(raw: str):
    """Try all patterns and return (match, has_thread) or (None, False)."""
    m = LOG_PATTERN_1.match(raw)
    if m:
        return m, True
    m = LOG_PATTERN_2.match(raw)
    if m:
        return m, False
    m = LOG_PATTERN_3.match(raw)
    if m:
        return m, False
    return None, False


# ─────────────────────────────────────────────
# Alert levels — ERRO is treated as ERROR
# ─────────────────────────────────────────────
ALERT_LEVELS = {"ERROR", "CRITICAL", "WARNING", "ERRO"}

# ─────────────────────────────────────────────
# Keyword rules — each keyword maps to:
#   level:  alert severity label
#   files:  set of partial log file names to scope the match
#   module: human-readable module name for the Teams card
# ─────────────────────────────────────────────
ALERT_KEYWORDS = {
    # ── OUTAGE ANALYSIS ──────────────────────────────────────
    "Exception": {
        "level": "ERROR",
        "files": {f"outage_analysis_{_get_date(days_back=1)}.log"},
        "module": "OUTAGE ANALYSIS",
    },

    # ── NOTAM READER ─────────────────────────────────────────
    "Database Error": {
        "level": "ERROR",
        "files": {f"notam_reader_{_get_date(days_back=1)}.log"},
        "module": "NOTAM READER",
    },

    # ── NOTAM DELIVERY ────────────────────────────────────────
    "Action: STORE NOTAM Status: FAIL_DISTRIBUTION": {
        "level": "ERROR",
        "files": {f"notam_delivery_{_get_date(days_back=1)}.log"},
        "module": "NOTAM DELIVERY",
    },
    "Could not close openconnect process": {
        "level": "ERROR",
        "files": {f"notam_delivery_{_get_date(days_back=1)}.log"},
        "module": "NOTAM DELIVERY",
    },
    "VPN connection: Failed": {
        "level": "ERROR",
        "files": {f"notam_delivery_{_get_date(days_back=1)}.log"},
        "module": "NOTAM DELIVERY",
    },
    "Max number of retries exceeded": {
        "level": "ERROR",
        "files": {f"notam_delivery_{_get_date(days_back=1)}.log"},
        "module": "NOTAM DELIVERY",
    },
    "processing failed": {
        "level": "ERROR",
        "files": {f"notam_delivery_{_get_date(days_back=1)}.log"},
        "module": "NOTAM DELIVERY",
    },
    "Failed to instantiate NOTAM Service": {
        "level": "ERROR",
        "files": {f"notam_delivery_{_get_date(days_back=1)}.log"},
        "module": "NOTAM DELIVERY",
    },

    # ── NOTAM ASSESSMENT ──────────────────────────────────────
    "split(): expected number of tokens has not been reached": {
        "level": "ERROR",
        "files": {f"notam_assessment_{_get_date(days_back=1)}.log"},
        "module": "NOTAM ASSESSMENT",
    },
    "Given input settings file does not exist or is not a valid file": {
        "level": "ERROR",
        "files": {f"notam_assessment_{_get_date(days_back=1)}.log"},
        "module": "NOTAM ASSESSMENT",
    },
    "ERROR while establishing a connection to the database": {
        "level": "ERROR",
        "files": {f"notam_assessment_{_get_date(days_back=1)}.log"},
        "module": "NOTAM ASSESSMENT",
    },

    # ── NOTAM READER (from attached log sample) ───────────────
    "Error creating TXT NOTAM file": {
        "level": "ERROR",
        "files": {"pubsub_client.py", f"notam_reader_{_get_date(days_back=1)}.log"},
        "module": "NOTAM READER",
    },
    "Error getting messages from EAD": {
        "level": "ERROR",
        "files": {"main.py", f"notam_reader_{_get_date(days_back=1)}.log"},
        "module": "NOTAM READER",
    },

    # ── NM_B2B (orchestrator) ─────────────────────────────────
    "NM_B2B: Error parsing XML": {
        "level": "ERROR",
        "files": {"ravaps_orchestrator"},
        "module": "NM_B2B",
    },
    "NM_B2B: Unexpected filename": {
        "level": "ERROR",
        "files": {"ravaps_orchestrator"},
        "module": "NM_B2B",
    },
    "NM_B2B: Error deleting": {
        "level": "ERROR",
        "files": {"ravaps_orchestrator"},
        "module": "NM_B2B",
    },

    # ── SWX (orchestrator) ────────────────────────────────────
    "Contact the service provider": {
        "level": "ERROR",
        "files": {"ravaps_orchestrator"},
        "module": "SWX",
    },
    "Authentication Failed. Update connector credentials": {
        "level": "ERROR",
        "files": {"ravaps_orchestrator"},
        "module": "SWX",
    },
    "Unexpected response HTTP": {
        "level": "ERROR",
        "files": {"ravaps_orchestrator"},
        "module": "SWX",
    },
    "Unexpected Error": {
        "level": "ERROR",
        "files": {"ravaps_orchestrator"},
        "module": "SWX",
    },
    "Unexpected HTTP Error": {
        "level": "ERROR",
        "files": {"ravaps_orchestrator"},
        "module": "SWX",
    },
    "HTTP error ocurred": {
        "level": "ERROR",
        "files": {"ravaps_orchestrator"},
        "module": "SWX",
    },
    "Connection error ocurred": {
        "level": "ERROR",
        "files": {"ravaps_orchestrator"},
        "module": "SWX",
    },
    "Timeout error ocurred": {
        "level": "ERROR",
        "files": {"ravaps_orchestrator"},
        "module": "SWX",
    },

    # ── GNASSURE ──────────────────────────────────────────────
    "Program finished": {
        "level": "INFO",
        "files": {"gnassure", "req_gnassure"},
        "module": "GNASSURE",
    },
}

THEME_COLORS = {
    "ERROR":   "FF0000",  # Red
    "ERRO":    "FF0000",  # Red (shorthand)
    "WARNING": "FFA500",  # Orange
    "INFO":    "0076D7",  # Blue
}

LOG_FILES = [
    "/home/ravaps/sw_ravaps/logs/orchestrator/ravaps_orchestrator.log",
    f"/home/ravaps/sw_ravaps/logs/gnassure/gnassure_{_get_date(days_back=1)}.log",
    f"/home/ravaps/sw_ravaps/logs/gnassure/req_gnassure_{_get_date(days_back=1)}.log",
    f"/home/ravaps/sw_ravaps/logs/notam_assessment/notam_assessment_{_get_date(days_back=1)}.log",
    f"/home/ravaps/sw_ravaps/logs/notam_assessment/notam_reader_{_get_date(days_back=1)}.log",
    f"/home/ravaps/sw_ravaps/logs/notam_delivery/notam_delivery_{_get_date(days_back=1)}.log",
    f"/home/ravaps/sw_ravaps/logs/outage_analysis/outage_analysis_{_get_date(days_back=1)}.log",
]


def send_teams_alert(webhook_url: str, match: re.Match, has_thread: bool,
                     source_file: str, keyword_level: str, module: str) -> None:
    level = match.group("level")
    facts = [
        {"name": "Module",     "value": module},
        {"name": "Source",     "value": Path(source_file).name},
        {"name": "Time",       "value": match.group("datetime")},
        {"name": "Log Level",  "value": level},
        {"name": "Alert Type", "value": keyword_level},
    ]
    if has_thread:
        facts.append({"name": "Thread", "value": match.group("thread")})
    facts.append({"name": "Message", "value": match.group("message").strip()})
    facts.append({"name": "File",    "value": f"{match.group('file')}:{match.group('line')}"})

    payload = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "summary": f"{keyword_level} in {module}",
        "themeColor": THEME_COLORS.get(keyword_level, "0076D7"),
        "title": f"🚨 {keyword_level} in {module}" if keyword_level in ALERT_LEVELS else f"🔵 {module} — {keyword_level}",
        "sections": [{"facts": facts}],
    }

    response = requests.post(
        webhook_url,
        data=json.dumps(payload),
        headers={"Content-Type": "application/json"},
        verify=False,
    )
    print(f"  [ALERT SENT] Status: {response.status_code}")


def watch_log(client: paramiko.SSHClient, log_path: str, webhook_url: str) -> None:
    file_name = Path(log_path).name
    print(f"[WATCH] {file_name}")

    try:
        _, stdout, stderr = client.exec_command(f"tail -f {log_path}")

        for line in stdout:
            raw = line.strip()
            if not raw:
                continue

            match, has_thread = parse_line(raw)

            if not match:
                # Uncomment to debug unmatched lines:
                # print(f"[UNMATCHED | {file_name}] {raw}")
                continue

            level   = match.group("level")
            message = match.group("message").strip()

            # Check all registered keywords against this log file + message
            match_meta = next(
                (
                    meta
                    for keyword, meta in ALERT_KEYWORDS.items()
                    if keyword.lower() in message.lower()
                    and any(f in log_path for f in meta["files"])
                ),
                None,
            )

            # Also alert on any ERRO/ERROR/CRITICAL line even without a keyword match
            is_alert_level = level in ALERT_LEVELS

            if match_meta:
                print(f"[KEYWORD MATCH | {file_name}] {match.group('datetime')} | {match_meta['level']} | {match_meta['module']} | {message}")
                send_teams_alert(webhook_url, match, has_thread,
                                 source_file=log_path,
                                 keyword_level=match_meta["level"],
                                 module=match_meta["module"])

            elif is_alert_level:
                print(f"[LEVEL MATCH | {file_name}] {match.group('datetime')} | {level} | {message}")
                send_teams_alert(webhook_url, match, has_thread,
                                 source_file=log_path,
                                 keyword_level=level,
                                 module=file_name)

    except Exception as e:
        print(f"[ERROR | {file_name}] {e}")


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
        transport = client.get_transport()
        transport.set_keepalive(30)
        print("[OK] SSH connected\n")

        threads = []
        for log_path in LOG_FILES:
            t = threading.Thread(
                target=watch_log,
                args=(client, log_path, webhook_url),
                daemon=True,
                name=Path(log_path).name,
            )
            t.start()
            threads.append(t)

        for t in threads:
            t.join()

    except KeyboardInterrupt:
        print("\n[STOP] Monitoring stopped")
    except Exception as e:
        print(f"[ERROR] {e}")
    finally:
        client.close()
        print("[OK] SSH connection closed")


if __name__ == "__main__":
    main()