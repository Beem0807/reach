"""
Command policy enforcement.

Two-tier model:

BLOCKED_PATTERNS  - always rejected, all modes including wild.
                    Reserved for catastrophic / abuse-only operations: raw disk
                    wipes, root-filesystem deletion, privileged container/host
                    escapes, credential exfiltration, and reverse shells.
                    Legitimate admin operations are NOT here.

READONLY_BLOCKED  - rejected in readonly mode and in approved mode (unless the
                    command matches an approved list entry). Covers anything that
                    writes, deletes, installs, or mutates system state.

Wild mode is intentionally permissive. It is designed for personal machines, dev
environments, break-glass debugging, and power users who want full flexibility.
Use Approved mode for production and explicitly allowlist the write operations
the agent is permitted to perform.
"""
import re

# Splits on shell operators: ; && || and single pipe |
# Each segment is checked independently so chained writes are caught.
_SHELL_OPERATORS = re.compile(r'&&|\|\||\|(?!\|)|;')


def _shell_segments(command: str) -> list[str]:
    return [s.strip() for s in _SHELL_OPERATORS.split(command) if s.strip()]


BLOCKED_PATTERNS = [
    # Catastrophic deletion / disk destruction
    r"rm\s+-[a-zA-Z]*r[a-zA-Z]*\s+/(\s|$|\*)",   # rm -rf / or rm -rf /*
    r"rm\s+--no-preserve-root",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r"\bwipefs\b",
    r"\bshred\s+/dev/",
    r":\(\)\{\s*:\|:\s*&\s*\}",                    # fork bomb
    # Privileged container / host escape
    r"\bdocker\s+run\b.*--privileged\b",
    r"\bdocker\s+run\b.*--(pid|network)=host\b",
    r"\bnsenter\b.*--target\s+1\b",
    r"\bchroot\s+/(\s|$)",
    r"\bkubectl\s+run\b.*--privileged\b",
    # Exfiltration
    r"\benv\b.*\|\s*\bcurl\b",
    # Reverse shells
    r"/dev/tcp/",
    r"/dev/udp/",
    r"\bnc\b.*-e\b",
    r"\bncat\b.*-e\b",
    r"\bsocat\b.*\bexec:\b",
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
    r"\breboot\b", r"\bshutdown\b", r"\bpoweroff\b", r"\bhalt\b",
    r"\binit\s+[06]\b", r"\bsystemctl\s+(poweroff|reboot|halt)\b",
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
    # IaC destroy
    r"\bterraform\s+destroy\b",
    r"\bpulumi\s+destroy\b",
    r"\bcdk\s+destroy\b",
    # Cloud destructive operations
    r"\baws\s+ec2\s+terminate-instances\b",
    r"\baws\s+rds\s+delete-db-instance\b",
    r"\baws\s+s3\s+rb\b.*--force\b",
    r"\bgcloud\b.*\binstances\s+delete\b",
    r"\baz\s+vm\s+delete\b",
]


def _is_blocked(command: str) -> bool:
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return True
    return False


def _is_readonly_blocked(command: str) -> bool:
    for segment in _shell_segments(command):
        for pattern in READONLY_BLOCKED:
            if re.search(pattern, segment, re.IGNORECASE):
                return True
    return False


def compute_access_level(
    mode: str,
    running_as_root: bool,
    **_kwargs,
) -> str:
    """Privilege label derived from policy mode and root status."""
    if mode == "wild":
        return "open" if running_as_root else "elevated"
    if mode == "approved":
        return "elevated" if running_as_root else "managed"
    # readonly
    return "managed" if running_as_root else "restricted"


def _is_approved(command: str, approved_commands: list) -> bool:
    cmd = command.strip()
    for allowed in approved_commands:
        allowed = allowed.strip()
        if cmd == allowed or cmd.startswith(allowed + " "):
            return True
    return False
