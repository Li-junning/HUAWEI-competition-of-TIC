"""Generate a server-side password hash without accepting secrets in argv."""
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.access import hash_password

if __name__ == "__main__":
    password = getpass.getpass("访问密码（至少 16 个字符）: ")
    if password != getpass.getpass("再次输入: "):
        raise SystemExit("两次输入不一致")
    try:
        print("VERIFIER_PASSWORD_HASH=" + hash_password(password))
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
