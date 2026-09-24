"""Transport error classification shared by provider adapters."""

import errno


def network_permission_denied(exc: BaseException) -> bool:
    seen: set[int] = set()
    pending = [exc]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, OSError) and (
            getattr(current, "winerror", None) == 10013 or current.errno in {errno.EACCES, errno.EPERM, 10013}
        ):
            return True
        pending.extend(error for error in (current.__cause__, current.__context__) if error is not None)
    return False


