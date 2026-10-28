import contextlib
import ctypes


def make_undumpable() -> None:
    """Linux: make /proc/<this process>/environ and /mem readable by root only, so other processes running
    as the same user (the image-decoding child, or the API next to the worker) can't read its secrets."""
    with contextlib.suppress(AttributeError, OSError):  # not Linux
        ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)  # PR_SET_DUMPABLE = 4
