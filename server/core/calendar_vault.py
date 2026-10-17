"""Encrypted backend credentials; the key lives outside the served project root."""
import os,stat
from pathlib import Path
from cryptography.fernet import Fernet,InvalidToken
from django.conf import settings
from django.views.decorators.debug import sensitive_variables
from .calendar_adapter import CalendarError

@sensitive_variables()
def cipher(create=False):
    directory=Path(settings.CALENDAR_VAULT_DIR)
    if create:directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    try:
        info=directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:
            raise CalendarError('vault_unavailable')
        path=directory/'key'
        if create and not path.exists():
            try:
                fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'wb') as target:target.write(Fernet.generate_key())
            except FileExistsError:pass
        info=path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600:
            raise CalendarError('vault_unavailable')
        return Fernet(path.read_bytes())
    except (OSError,ValueError):raise CalendarError('vault_unavailable') from None

@sensitive_variables()
def encrypt(value):return cipher(create=True).encrypt(value.encode()).decode()

@sensitive_variables()
def token_for(connection):
    from django.utils import timezone
    from .models import CalendarCredential
    credential=CalendarCredential.objects.filter(connection=connection,expires_at__gt=timezone.now()).first()
    if not credential or connection.status!='active' or connection.revoked_at:
        raise CalendarError('grant_unavailable')
    try:return cipher().decrypt(credential.ciphertext.encode()).decode()
    except (InvalidToken,ValueError):raise CalendarError('vault_unavailable') from None
