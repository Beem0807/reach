import pytest
from shared.policy import _is_approved, _is_blocked, _is_readonly_blocked


# ---------------------------------------------------------------------------
# _is_blocked (always-blocked commands)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd", [
    "rm -rf /",
    "rm -rf /home",
    "mkfs.ext4 /dev/sda",
    "mkfs -t ext4 /dev/sdb",
    "dd if=/dev/zero of=/dev/sda",
    "shutdown now",
    "shutdown -h now",
    "reboot",
    "poweroff",
    "init 0",
    "init 6",
])
def test_is_blocked(cmd):
    assert _is_blocked(cmd)


@pytest.mark.parametrize("cmd", [
    "ls -la",
    "docker ps",
    "cat /etc/hosts",
    "git status",
    "df -h",
    "ps aux",
    "uptime",
])
def test_not_blocked(cmd):
    assert not _is_blocked(cmd)


# ---------------------------------------------------------------------------
# _is_readonly_blocked (write operations)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd", [
    "rm file.txt",
    "mv old.txt new.txt",
    "chmod 777 file.txt",
    "chown root file.txt",
    "kill 1234",
    "killall nginx",
    "sudo apt-get install vim",
    "docker start myapp",
    "docker stop myapp",
    "docker restart myapp",
    "docker rm myapp",
    "systemctl start nginx",
    "systemctl stop nginx",
    "systemctl restart nginx",
    "apt-get install vim",
    "apt install vim",
    "pip install requests",
    "pip3 install requests",
    "npm install express",
    "wget http://example.com/file",
    "reboot",
    "shutdown",
])
def test_readonly_blocked(cmd):
    assert _is_readonly_blocked(cmd)


@pytest.mark.parametrize("cmd", [
    "ls -la",
    "cat /etc/hosts",
    "docker ps",
    "docker logs myapp",
    "git status",
    "git log",
    "df -h",
    "ps aux",
    "uptime",
    "systemctl status nginx",
    "journalctl -u nginx",
])
def test_readonly_not_blocked(cmd):
    assert not _is_readonly_blocked(cmd)


# ---------------------------------------------------------------------------
# _is_approved (prefix matching)
# ---------------------------------------------------------------------------

def test_approved_exact_match():
    assert _is_approved("docker ps", ["docker ps"])


def test_approved_prefix_match():
    assert _is_approved("docker ps -a --format json", ["docker ps"])


def test_approved_multiple_allowed_commands():
    allowed = ["docker ps", "git status", "df -h"]
    assert _is_approved("git status --short", allowed)
    assert _is_approved("df -h /dev/sda", allowed)


def test_not_approved_not_in_list():
    assert not _is_approved("rm -rf /tmp", ["docker ps", "ls"])


def test_not_approved_empty_list():
    assert not _is_approved("ls", [])


def test_not_approved_partial_word_match():
    # "docker" alone should not match "docker ps" prefix
    assert not _is_approved("docker-compose up", ["docker ps"])


def test_approved_strips_whitespace():
    assert _is_approved("docker ps", ["  docker ps  "])


def test_approved_command_strips_whitespace():
    assert _is_approved("  docker ps -a  ", ["docker ps"])
