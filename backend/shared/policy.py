import re

BLOCKED_PATTERNS = [
    r"rm\s+-rf\s+/",
    r"mkfs",
    r"dd\s+if=",
    r":\(\)\{\s*:\|:\s*&\s*\}",   # fork bomb
    r"shutdown",
    r"reboot",
    r"poweroff",
    r"init\s+0",
    r"init\s+6",
]

READONLY_BLOCKED = [
    # File operations
    r"\brm\b", r"\bmv\b", r"\bcp\b(?=.*\s/)",
    r"\bchmod\b", r"\bchown\b", r"\bchattr\b",
    r"\btruncate\b", r"\bshred\b", r"\bwipe\b",
    r"\bln\b",
    r"\btee\b",
    r"\bsed\b.*\s-[a-zA-Z]*i",                  # sed -i in-place edit
    r">\s*\S+",                                   # output redirect (> and >>)
    # Process control
    r"\bkill\b", r"\bkillall\b", r"\bpkill\b",
    # System power / init
    r"\breboot\b", r"\bshutdown\b", r"\bpoweroff\b",
    # Service management
    r"\bsystemctl\s+(start|stop|restart|enable|disable|mask|unmask)\b",
    r"\bservice\s+\S+\s+(start|stop|restart|reload)\b",
    # Containers
    r"\bdocker\s+(start|stop|restart|rm|kill|exec|run|pull|build|push|rmi)\b",
    r"\bdocker-compose\s+(up|down|restart|pull|rm)\b",
    r"\bkubectl\s+(apply|delete|create|replace|patch|scale|rollout|exec|run)\b",
    # Package managers
    r"\bapt(-get)?\s+(install|remove|purge|upgrade|autoremove)\b",
    r"\byum\s+(install|remove|update|erase)\b",
    r"\bdnf\s+(install|remove|update|erase)\b",
    r"\bpacman\s+-[A-Za-z]*[SR]\b",
    r"\bapk\s+(add|del|upgrade)\b",
    r"\bsnap\s+(install|remove|refresh)\b",
    r"\bflatpak\s+(install|remove|update)\b",
    r"\bbrew\s+(install|uninstall|upgrade|remove)\b",
    r"\bpip3?\s+install\b",
    r"\bnpm\s+(install|uninstall|update)\b",
    r"\byarn\s+(add|remove|upgrade|install)\b",
    r"\bgem\s+(install|uninstall|update)\b",
    r"\bcargo\s+install\b",
    # File download / execution
    r"\bcurl\b.*\s-[a-zA-Z]*o\b", r"\bwget\b",
    # Disk / filesystem
    r"\bdd\b", r"\bmkfs\b",
    r"\bfdisk\b", r"\bparted\b", r"\bgdisk\b",
    r"\bmount\b", r"\bumount\b",
    # Networking / firewall
    r"\biptables\b", r"\bip6tables\b",
    r"\bufw\s+(allow|deny|enable|disable|delete|reject)\b",
    # User / auth management
    r"\buseradd\b", r"\buserdel\b", r"\busermod\b",
    r"\bgroupadd\b", r"\bgroupdel\b",
    r"\bpasswd\b",
    r"\bsu\b",
    # Scheduled jobs
    r"\bcrontab\b",
    # Privilege escalation
    r"\bsudo\b",
]


def _is_blocked(command: str) -> bool:
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return True
    return False


def _is_readonly_blocked(command: str) -> bool:
    for pattern in READONLY_BLOCKED:
        if re.search(pattern, command, re.IGNORECASE):
            return True
    return False


def _is_approved(command: str, approved_commands: list) -> bool:
    cmd = command.strip()
    return any(cmd.startswith(allowed.strip()) for allowed in approved_commands)
