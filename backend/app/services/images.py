import asyncio
import contextlib
import ctypes
import fcntl
import os
import signal
import struct
import sys
import weakref
from collections.abc import AsyncIterator
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.errors import UnsupportedImage

CONTENT_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png"}
NORMALIZED_CONTENT_TYPE = "image/jpeg"

# Reject decompression bombs: a tiny file that expands to an enormous bitmap. Pillow only warns between
# this and twice this, so the API checks the size itself; a 12 MP phone photo is well under it.
MAX_IMAGE_PIXELS = 50_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
# A progressive JPEG is decoded once per scan over the full image, so a file packed with thousands of
# tiny scans costs minutes of CPU. Phone cameras write 1 (baseline) or about 10 (progressive).
MAX_JPEG_SCANS = 32
# The decoder runs in a child process with this much address space, killed when the timeout passes.
DECODE_MEMORY_BYTES = 1536 * 1024 * 1024
# A 1600 px JPEG is well under 1 MB; anything this big from the decoder is not a real result.
MAX_DECODED_BYTES = 16 * 1024 * 1024
_APP_ROOT = str(Path(__file__).resolve().parents[2])  # the directory that contains the app package


def jpeg_scan_count(raw: bytes) -> int:
    """Start-of-scan markers. Image data escapes 0xFF bytes, so a marker can't appear there by accident."""
    return raw.count(b"\xff\xda")


def inspect_image(raw: bytes) -> str:
    """Cheap API-side check (header + verify, no decode). Returns the content type."""
    try:
        # Only the two allowed parsers run here, not every format plugin Pillow ships with.
        with Image.open(BytesIO(raw), formats=list(CONTENT_TYPES)) as image:
            image_format, (width, height) = image.format, image.size
            image.verify()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError):
        raise UnsupportedImage("File is not a valid JPEG or PNG image") from None
    if image_format not in CONTENT_TYPES:
        raise UnsupportedImage("Only JPEG and PNG images are supported")
    if width * height > MAX_IMAGE_PIXELS:
        raise UnsupportedImage("Image dimensions are too large")
    if image_format == "JPEG" and jpeg_scan_count(raw) > MAX_JPEG_SCANS:
        raise UnsupportedImage("JPEG has too many progressive scans")
    return CONTENT_TYPES[image_format]


async def normalize_image(raw: bytes, max_edge: int, timeout: float, require_landlock: bool = True) -> bytes:
    """Decode, apply EXIF rotation, downscale, re-encode as JPEG (drops EXIF/GPS).

    Runs in a child process with CPU, memory and wall-clock limits, killed if it overruns: a hostile
    file can't hold a worker loop (a thread can't be cancelled) or exhaust the worker's memory. On Linux
    the child also can't open network connections, start or signal processes, read files beyond Python's
    own, or be inspected by other processes
    (see _CHILD), so a Pillow exploit stays inside one short-lived decode of the attacker's own image (and only one
    decoder runs at a time on the machine, see _decoding). On Linux without Landlock the decoder refuses
    to run unless require_landlock is False: other same-user processes' environments must stay unreadable
    however the worker is hosted.
    """
    async with _decoding():
        return await _decode_in_child(raw, max_edge, timeout, require_landlock)


_DECODE_SLOTS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = weakref.WeakKeyDictionary()
_lock_file: int | None = None


@contextlib.asynccontextmanager
async def _decoding() -> AsyncIterator[None]:
    """One decoder at a time on this machine: a child is reachable by other same-user processes for a
    moment after it starts, before it confines itself, so no other decoder may exist then. Within a
    process a semaphore queues the work loops; across worker processes an exclusive flock on this
    module's own file, which the app user can't delete or replace in the image (root-owned /app)."""
    async with _DECODE_SLOTS.setdefault(asyncio.get_running_loop(), asyncio.Semaphore(1)):
        lock = await _lock_out_other_processes()
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


async def _lock_out_other_processes() -> int:
    """Wait until no other worker process on this machine is decoding, then hold the lock (returned)."""
    global _lock_file
    if _lock_file is None:
        _lock_file = os.open(__file__, os.O_RDONLY | os.O_CLOEXEC)
    while not _try_lock(_lock_file):
        await asyncio.sleep(0.05)  # polled, so waiting never blocks the event loop
    return _lock_file


def _try_lock(fd: int) -> bool:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


async def _decode_in_child(raw: bytes, max_edge: int, timeout: float, require_landlock: bool = True) -> bytes:
    child = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-B", "-c", _CHILD, _APP_ROOT, str(max_edge), str(int(timeout) + 1), str(DECODE_MEMORY_BYTES), str(int(require_landlock)),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        env={},  # no secrets for the decoder: a Pillow exploit in here must not find the API keys
        start_new_session=True,  # its own process group, killed as a whole below
    )
    try:
        out = await asyncio.wait_for(_exchange(child, raw), timeout)
    except TimeoutError:
        raise UnsupportedImage("The image took too long to decode") from None
    finally:
        if child.returncode is None:  # timed out, cancelled or failed: never leave anything running
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(child.pid, signal.SIGKILL)
            # asyncio reports the exit only once every pipe is closed, and unread output (e.g. past
            # MAX_DECODED_BYTES) keeps stdout open: drain what's left, then wait, each with a bound.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(_discard(child), 5)
    if child.returncode != 0 or not (out.startswith(b"\xff\xd8\xff") and out.endswith(b"\xff\xd9")):
        # Hostile or corrupt files fail in many ways inside Pillow (or hit the limits); all mean "unreadable".
        raise UnsupportedImage("The image could not be decoded")
    return out


async def _discard(child: asyncio.subprocess.Process) -> None:
    if child.stdin is not None:
        child.stdin.close()
    if child.stdout is not None:
        while await child.stdout.read(64 * 1024):
            pass
    await child.wait()


async def _exchange(child: asyncio.subprocess.Process, raw: bytes) -> bytes:
    """Feed the image in and read the result, refusing output past MAX_DECODED_BYTES."""
    stdin, stdout = child.stdin, child.stdout
    assert stdin is not None and stdout is not None

    async def feed() -> None:
        try:
            stdin.write(raw)
            await stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the child gave up early; its exit status says why
        finally:
            stdin.close()

    async def collect() -> bytes:
        out = bytearray()
        while chunk := await stdout.read(64 * 1024):
            out += chunk
            if len(out) > MAX_DECODED_BYTES:
                raise UnsupportedImage("The image could not be decoded")
        return bytes(out)

    _, out = await asyncio.gather(feed(), collect())
    await child.wait()
    return out


# The child: python -I (no environment, no user site), limits first, then decode stdin to stdout.
_CHILD = r"""
import ctypes, platform, resource, signal, struct, sys
signal.signal(signal.SIGXFSZ, signal.SIG_IGN)  # with RLIMIT_FSIZE 0, a file write fails instead of killing
root, max_edge, cpu, memory, require_landlock = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5] == "1"
for limit, value in ((resource.RLIMIT_CPU, cpu), (resource.RLIMIT_AS, memory), (resource.RLIMIT_FSIZE, 0)):
    try:
        resource.setrlimit(limit, (value, value))
    except (ValueError, OSError):
        pass  # macOS doesn't enforce RLIMIT_AS; the wall-clock timeout still applies
if sys.platform == "linux":
    sys.path.insert(0, root)
    from app.services.images import confine_decoder
    from PIL import Image
    Image.preinit()  # load the JPEG/PNG plugins while the filesystem is still open
    if not confine_decoder(ctypes.CDLL(None, use_errno=True), platform.machine(), require_landlock):
        sys.exit(3)  # never decode unconfined on Linux
sys.path.insert(0, root)
from app.services.images import decode_for_model
sys.stdout.buffer.write(decode_for_model(sys.stdin.buffer.read(), max_edge))
"""

# Syscalls the decoder child has no use for, refused with EPERM: network (socket), processes (fork,
# clone, exec), signals and other processes' memory (kill, ptrace, process_vm_*), and io_uring (which
# could open sockets without the socket syscall). Numbers are per architecture.
# Syscalls the decoder child has no use for, refused with EPERM: network (socket), processes (fork,
# clone, exec), anything that acts on another process by pid (signals of every kind, ptrace and
# process_vm_*, scheduling and priority, memory policy, perf, pidfd), io_uring (which could open sockets
# without the socket syscall), and removing or renaming files (the decode lock file). prlimit64 is
# allowed on itself only (pid 0). Numbers are per architecture.
_SECCOMP = {
    "x86_64": (
        0xC000003E,
        [41, 53, 56, 57, 58, 59, 322, 435, 62, 200, 234, 129, 297, 424, 101, 310, 311, 434, 438, 440, 448, 141, 142, 144,
         203, 314, 251, 256, 279, 298, 312, 425, 87, 263, 82, 264, 316],
        [302],
    ),
    "aarch64": (
        0xC00000B7,
        [198, 199, 220, 221, 281, 435, 129, 130, 131, 138, 240, 424, 117, 270, 271, 434, 438, 440, 448, 140, 118, 119,
         122, 274, 30, 238, 239, 241, 272, 425, 35, 38, 276],
        [261],
    ),
}


def _seccomp_program(arch: int, denied: list[int], self_only: list[int], x32_abi: bool) -> bytes:
    """Classic BPF: wrong architecture -> kill; a denied syscall -> EPERM; a self-only syscall -> EPERM
    unless its first argument (the pid) is 0; anything else allowed. Jumps name their targets."""
    ld, jeq, jge, ret = 0x20, 0x15, 0x35, 0x06
    returns = {"allow": 0x7FFF0000, "deny": 0x00050000 | 1, "kill": 0x80000000}  # ERRNO | EPERM
    program: list[tuple[int, int, str | None, str | None]] = [(ld, 4, None, None), (jeq, arch, None, "kill"), (ld, 0, None, None)]
    program += [(jge, 0x40000000, "deny", None)] if x32_abi else []
    program += [(jeq, nr, "deny", None) for nr in denied] + [(jeq, nr, "self", None) for nr in self_only]
    program += [(ret, returns["allow"], None, None)]
    labels = {"self": len(program)}  # args[0] of seccomp_data: low word at 16, high word at 20 (little-endian)
    program += [(ld, 16, None, None), (jeq, 0, None, "deny"), (ld, 20, None, None), (jeq, 0, "allow", "deny")]
    for name in ("allow", "deny", "kill"):
        labels[name] = len(program)
        program.append((ret, returns[name], None, None))

    def offset(i: int, target: str | None) -> int:
        jump = 0 if target is None else labels[target] - (i + 1)
        assert 0 <= jump < 256
        return jump

    return b"".join(
        struct.pack("HBBI", op, offset(i, jt), offset(i, jf), k) for i, (op, k, jt, jf) in enumerate(program)
    )


def confine_decoder(libc: ctypes.CDLL, machine: str, require_landlock: bool = True) -> bool:
    """In the decoder child, on Linux, before it touches the image: become non-dumpable (other
    same-uid processes can't read its memory or pipes) and install the syscall filter. False if any
    step fails, and then the child must not decode."""
    if machine not in _SECCOMP:
        return False
    arch, denied, self_only = _SECCOMP[machine]
    code = _seccomp_program(arch, denied, self_only, x32_abi=machine == "x86_64")
    filters = ctypes.create_string_buffer(code, len(code))

    class SockFprog(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.c_void_p)]

    fprog = SockFprog(len(code) // 8, ctypes.addressof(filters))
    pr_set_dumpable, pr_set_no_new_privs, pr_set_seccomp, seccomp_mode_filter = 4, 38, 22, 2
    zero = ctypes.c_ulong(0)
    return (
        libc.prctl(ctypes.c_int(pr_set_dumpable), zero, zero, zero, zero) == 0
        and libc.prctl(ctypes.c_int(pr_set_no_new_privs), ctypes.c_ulong(1), zero, zero, zero) == 0
        and _landlock(libc, require_landlock)
        and libc.prctl(ctypes.c_int(pr_set_seccomp), ctypes.c_ulong(seccomp_mode_filter), ctypes.byref(fprog), zero, zero) == 0
    )


def _landlock(libc: ctypes.CDLL, required: bool = True) -> bool:
    """Second layer, where the kernel has Landlock: the child may only read Python's own files and the
    system libraries (lazy imports), nothing else on disk: not /proc (other processes' environments),
    /etc or /tmp. Also no TCP, and no signals or abstract sockets outside itself. False if it can't be
    set up, or if the kernel lacks Landlock while it is required (DECODER_REQUIRE_LANDLOCK, default on)."""
    create, add_rule, restrict = 444, 445, 446  # same numbers on x86_64 and aarch64
    abi = libc.syscall(create, None, ctypes.c_size_t(0), ctypes.c_uint32(1))  # LANDLOCK_CREATE_RULESET_VERSION
    if abi < 1:
        return not required
    handled_fs = (1 << 13) - 1  # ABI 1: execute .. make_sym
    handled_fs |= (1 << 13 if abi >= 2 else 0) | (1 << 14 if abi >= 3 else 0) | (1 << 15 if abi >= 5 else 0)
    attr = struct.pack("QQQ", handled_fs, 0b11 if abi >= 4 else 0, 0b11 if abi >= 6 else 0)  # fs, tcp, scope
    size = 8 if abi < 4 else 16 if abi < 6 else 24
    ruleset = libc.syscall(create, ctypes.create_string_buffer(attr[:size], size), ctypes.c_size_t(size), ctypes.c_uint32(0))
    if ruleset < 0:
        return False
    readable = {p for p in sys.path if p and os.path.isdir(p)} | {"/usr/lib", "/usr/local/lib", "/lib", "/lib64", "/usr/lib64"}
    try:
        for path in sorted(readable):
            try:
                beneath = os.open(path, os.O_PATH | os.O_CLOEXEC)
            except OSError:
                continue
            rule = struct.pack("=Qi", 0b1101, beneath)  # execute | read_file | read_dir
            added = libc.syscall(add_rule, ctypes.c_int(ruleset), ctypes.c_int(1), ctypes.create_string_buffer(rule, 12), ctypes.c_uint32(0))
            os.close(beneath)
            if added != 0:
                return False
        return bool(libc.syscall(restrict, ctypes.c_int(ruleset), ctypes.c_uint32(0)) == 0)
    finally:
        os.close(ruleset)


def decode_for_model(raw: bytes, max_edge: int) -> bytes:
    if jpeg_scan_count(raw) > MAX_JPEG_SCANS:  # also for images stored before the upload check existed
        raise UnsupportedImage("JPEG has too many progressive scans")
    image: Image.Image = Image.open(BytesIO(raw), formats=list(CONTENT_TYPES))
    # For JPEG, let the decoder scale down while decoding (1/2 to 1/8), so a 50 MP photo never needs a
    # full-size bitmap in memory; the exact resize to max_edge happens below.
    image.draft("RGB", (max_edge, max_edge))
    image.load()
    # Phones often store the photo sideways plus a rotation tag; apply it so the model sees it upright.
    image = ImageOps.exif_transpose(image)
    image.thumbnail((max_edge, max_edge))

    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        background = Image.new("RGB", image.size, "white")
        background.paste(image, mask=image.getchannel("A"))
        image = background
    elif image.mode != "RGB":
        image = image.convert("RGB")

    out = BytesIO()
    image.save(out, "JPEG", quality=85, optimize=True)
    return out.getvalue()
