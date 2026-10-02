"""Optional, account-bound cookie state persistence."""
import hashlib
import json
import os
import tempfile


_STATE_VERSION = 1


def _fingerprint(base_cookies):
    raw = json.dumps(base_cookies, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _valid_cookie_dict(value):
    return isinstance(value, dict) and all(
        isinstance(key, str) and key and isinstance(item, str)
        and not any(char in key + item for char in ('\r', '\n', '\x00'))
        for key, item in value.items()
    )


def load_cookies(path, base_cookies):
    """Load state only if it is valid and tied to the exact base cookie set."""
    base = dict(base_cookies)
    if not path:
        return base
    try:
        with open(path, "r", encoding="utf-8") as stream:
            state = json.load(stream)
    except FileNotFoundError:
        return base
    except (OSError, ValueError, TypeError, UnicodeError):
        raise RuntimeError("Unable to load cookie state") from None
    if (not isinstance(state, dict) or state.get("version") != _STATE_VERSION
            or not isinstance(state.get("fingerprint"), str)
            or not _valid_cookie_dict(state.get("cookies"))):
        raise RuntimeError("Invalid cookie state")
    if state["fingerprint"] != _fingerprint(base):
        return base
    saved = state["cookies"]
    if (saved.get("wr_vid") != base.get("wr_vid")
            or not saved.get("wr_skey", "").strip()):
        raise RuntimeError("Invalid cookie state identity")
    return dict(saved)


def save_cookies(path, base_cookies, current_cookies):
    """Atomically persist cookie state with restrictive permissions; empty path is a no-op."""
    if not path:
        return
    base, current = dict(base_cookies), dict(current_cookies)
    if not _valid_cookie_dict(current):
        raise ValueError("Cookie state must contain string names and values")
    directory = os.path.dirname(os.path.abspath(path))
    temp_path = None
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".cookie-state-", dir=directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"version": _STATE_VERSION,
                           "fingerprint": _fingerprint(base),
                           "cookies": current}, stream, sort_keys=True,
                          separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, path)
            temp_path = None
            os.chmod(path, 0o600)
        finally:
            if temp_path is not None:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
    except (OSError, TypeError, ValueError):
        raise RuntimeError("Unable to save cookie state") from None
