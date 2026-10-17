"""Run locally in a terminal. Prepare verification-only credentials; send nothing."""
import getpass
import argparse
import json
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enable', action='store_true', help='Enable verification mail after saving; sends nothing now.')
    args = parser.parse_args()
    if not sys.stdin.isatty():
        raise SystemExit('Run this script in your local interactive terminal; hidden input is required.')
    target = Path.home() / '.config' / 'supervision' / 'notificator.private.json'
    if target.exists():
        raise SystemExit('Configuration already exists; edit it locally instead of overwriting.')
    token = getpass.getpass('Advisor sending token (hidden input): ').strip()
    if not token:
        raise SystemExit('No token entered; nothing written.')
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if target.parent.is_symlink() or target.parent.stat().st_uid != os.getuid():
        raise SystemExit('Configuration directory must be owned by this user and not a symlink.')
    os.chmod(target.parent, 0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    with os.fdopen(os.open(target, flags, 0o600), 'w') as output:
        json.dump({'host': 'https://notice.6-79.cn', 'account': 'Advisor',
                   'token': token, 'enabled': args.enable,
                   'allowed_purposes': ['email_verification']}, output, indent=2)
    print(f'Private configuration saved at {target}. Verification enabled: {args.enable}. No email sent.')


if __name__ == '__main__':
    main()
