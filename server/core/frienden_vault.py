"""Private encrypted credential storage shared by the account API adapter."""
import os
import stat
from pathlib import Path
from cryptography.fernet import Fernet
from django.conf import settings
from django.views.decorators.debug import sensitive_variables
from .frienden_api import ProviderError
ChatError = ProviderError
@sensitive_variables()
def cipher(create=False):
    directory=Path(settings.CHAT_VAULT_DIR)
    try:
        if create: directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info=directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700: raise ValueError
        path=directory/'key'
        if create and not path.exists():
            try:
                fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
                with os.fdopen(fd,'wb') as target: target.write(Fernet.generate_key())
            except FileExistsError: pass
        info=path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600: raise ValueError
        return Fernet(path.read_bytes())
    except (OSError,ValueError): raise ChatError('vault_unavailable',503) from None
