#!/usr/bin/env python3
"""ccctl — transport and control for custom Claude Code system prompts.

One file, stdlib only, runs on Linux, macOS and Windows. State (config,
changelog, snapshots) lives in the directory it is run from, so each machine
keeps its own history.

    python3 ccctl.py init [FORGEJO]:[OWNER]/claude-code-dispositions.git
    python3 ccctl.py status            # what binary, what version, patched?
    python3 ccctl.py pull              # fetch newest custom prompts from Forgejo
    python3 ccctl.py apply             # pull + snapshot + patch + verify
    python3 ccctl.py apply --stock     # enter the stock arm on this version
    python3 ccctl.py analyze [VER]     # stage incoming version, diff, verdicts
    python3 ccctl.py update [VER]      # analyze + patch staged + verify + swap
    python3 ccctl.py update --stock    # ... but deliberately WITHOUT the tranche
    python3 ccctl.py check-update      # after a CC update: did our targets drift?
    python3 ccctl.py flags             # what is the SERVER arming this week?
    python3 ccctl.py diff stock 2.1.233 2.1.240 [FRAGMENT]
    python3 ccctl.py diff custom [FRAGMENT]
    python3 ccctl.py prune             # drop stale ~320 MB parked binaries
    python3 ccctl.py restore           # tweakcc --restore, byte-exact rollback

Per machine it needs git, node with tweakcc >= 4.3.3, and Claude Code — all
of which `init` and `status` verify themselves, including that the resolved
`claude` is the real native binary and not a launcher shim (it validates size
and content, and falls back to the versions directory if PATH lies). Bootstrap
by copying this one file; `init` clones the rest (sparse — the corpus stays on
the server).
"""

import argparse
import difflib
import functools
import hashlib
import json
import os
import re
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# When this process started, for the tripwire's own clock (`tripwire_slow`).
STARTED = time.monotonic()

HERE = Path.cwd()
CONFIG = HERE / "ccctl.json"
STATE = HERE / "ccctl-state.json"
STOCK_SENTINEL = HERE / "stock-arm"
CHANGELOG = HERE / "changelog.md"
SNAPSHOTS = HERE / "snapshots"
STAGING = HERE / "staging"
ANALYSIS = HERE / "analysis"
TWEAKCC_DIR = Path.home() / ".tweakcc"
FRONTMATTER = re.compile(r"^<!--.*?-->\s*", re.S)

# Whether to run tweakcc's own feature patches before ours. Wanted wherever it
# works: it is what puts tweakcc's "patches applied" banner in CC, which is the
# only in-CC indicator that ours landed (d0f7f0b).
#
# True everywhere since 2026-08-26. It used to read `sys.platform != "darwin"`,
# because tweakcc "could not patch CC on darwin at all" — the bundle failed to
# parse and tweakcc reverted. That was never a darwin defect: the Mac's PATH
# node was 22, which cannot parse the `using` declarations CC's Bun-built bundle
# carries, so tweakcc's own `node --check` rejected a valid bundle. See
# `node_exe()`. Measured both directions on the Mac that day — the UNPATCHED
# extracted bundle fails under node 22 and passes under node 25, so no patch was
# ever implicated, and the full set then applied there (11 of 16; the other 5
# are anchor misses this build has on every platform).
#
# Safe as an unconditional default even where no capable node exists: tweakcc
# then rejects the set as before and `assemble()` falls back to our patches
# alone (694f1db), which is exactly what darwin was getting from the False.
TWEAKCC_PATCHES_DEFAULT = True

# Per-machine knobs, all overridable in ccctl.json (spec/40 D5). ccctl.json is
# per machine by construction — it lives in this state dir and is never
# committed — so a knob that differs per machine needs no machine-keyed shape.
CONFIG_DEFAULTS = {
    "channel": "stable",         # release pointer `analyze`/`update` consult
    "applyEngine": "span",       # "span" (byte-precise, all OSes) | "tweakcc" (--apply; linux-proven)
    "updatePolicy": "gate-on-clean",  # "analyze-only" | "gate-on-clean" | "force"
    "keepStaging": False,        # keep staged binary after a successful swap
    "macosCodesign": True,       # macOS: ad-hoc re-sign the patched binary before launch
    "tweakccPatches": TWEAKCC_PATCHES_DEFAULT,   # see above; platform-derived
}


# ---------------------------------------------------------------- plumbing

def die(msg):
    sys.stdout.flush()
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def log_entry(action, detail):
    """Append one changelog entry; create the file with a header on first use."""
    if not CHANGELOG.exists():
        CHANGELOG.write_text("# ccctl changelog\n\nNewest first.\n\n", encoding="utf-8")
    stamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    lines = CHANGELOG.read_text(encoding="utf-8").splitlines(keepends=True)
    head, body = lines[:4], lines[4:]
    entry = f"## {stamp} — {action}\n\n{detail.rstrip()}\n\n"
    CHANGELOG.write_text("".join(head) + entry + "".join(body), encoding="utf-8")


def run(cmd, **kw):
    # .cmd/.bat shims (npm on Windows) cannot be spawned directly by CreateProcess
    if sys.platform == "win32" and str(cmd[0]).lower().endswith((".cmd", ".bat")):
        cmd = ["cmd", "/c", *cmd]
    # Bare `claude` CLI calls self-update unless this is in their env —
    # settings.json env covers sessions only (incident 2026-08-19, spec/30).
    # DISABLE_UPDATES additionally blocks `claude update`/`install` (spec/40).
    env = {**os.environ, "DISABLE_AUTOUPDATER": "1", "DISABLE_UPDATES": "1",
           **kw.pop("env", {})}
    try:
        return subprocess.run(cmd, capture_output=True, text=True, env=env, **kw)
    except FileNotFoundError:
        die(f"'{cmd[0]}' is not installed or not on PATH")
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess(cmd, 124, "", f"timeout after {e.timeout}s")
    except OSError as e:                     # noexec mounts, permissions, ...
        return subprocess.CompletedProcess(cmd, 1, "", str(e))


def which(name):
    path = shutil.which(name)
    if not path:
        die(f"'{name}' not found on PATH — install it first")
    return path


def npm_prefix_bins(home=None):
    """Where `npm i -g` puts binaries for an operator whose ~/.npmrc sets a
    user prefix. Only an interactive shell's rc file puts that on PATH, so a
    command run over ssh, from cron or from a CC hook does not see it."""
    home = Path(home) if home else Path.home()
    bins = []
    try:
        for line in (home / ".npmrc").read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*prefix\s*=\s*(.+?)\s*$", line)
            if m:
                prefix = Path(os.path.expanduser(m.group(1)))
                bins.append(str(prefix if sys.platform == "win32" else prefix / "bin"))
    except OSError:
        pass
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        bins.append(str(Path(os.environ["APPDATA"]) / "npm"))
    return bins


def tweakcc_candidates(home=None):
    """Every tweakcc on PATH in PATH order, then any in npm's user prefix.
    `shutil.which` stops at the first, and on Linux 2026-09-25 the first in a
    non-interactive shell was a root-installed 4.0.13 in /usr/bin shadowing the
    operator's 4.3.3."""
    seen, found = set(), []
    for d in os.environ.get("PATH", "").split(os.pathsep) + npm_prefix_bins(home):
        path = shutil.which("tweakcc", path=d) if d else None
        if path and os.path.realpath(path) not in seen:
            seen.add(os.path.realpath(path))
            found.append(path)
    return found


@functools.lru_cache(maxsize=1)
def tweakcc():
    """The first tweakcc >= 4.3 among `tweakcc_candidates()`. Each is asked
    for its version under `node_exe()`, not its `#!/usr/bin/env node` line,
    because the shells that miss the npm prefix also miss node. Cached per
    process."""
    found = tweakcc_candidates()
    if not found:
        die("'tweakcc' not found on PATH or in npm's prefix — npm i -g tweakcc")
    rejected = []
    for path in found:
        out = run([*node_argv(path), "--version"]).stdout.strip()
        m = re.match(r"(\d+)\.(\d+)", out)
        if m and (int(m.group(1)), int(m.group(2))) >= (4, 3):
            for old, why in rejected:
                print(f"note: skipping tweakcc at {old} ({why}); using {path}", file=sys.stderr)
            return path
        rejected.append((path, f"{out} is too old" if m else f"no usable --version output: {out!r}"))
    die("no tweakcc >= 4.3.3 found — " + "; ".join(f"{p}: {why}" for p, why in rejected)
        + " — npm i -g tweakcc")


def _is_real_cc_binary(path):
    """The native CC binary, not a launcher shim: big, and self-identifying."""
    try:
        if not path.is_file() or path.stat().st_size < 50_000_000:
            return False
    except OSError:
        return False
    # whole-file: this asks what the file IS, before any bundle can be parsed
    return bool(binary_contains(path, ["Claude Code"], live=False))


@functools.lru_cache(maxsize=1)
def claude_binary():
    """Resolve the real native binary; `which claude` may be a shim (Windows)."""
    candidates = []
    found = shutil.which("claude")
    if found:
        candidates.append(Path(found).resolve())
    version_dirs = [Path.home() / ".local" / "share" / "claude" / "versions"]
    if os.environ.get("LOCALAPPDATA"):
        version_dirs.append(Path(os.environ["LOCALAPPDATA"]) / "claude" / "versions")
    for vdir in version_dirs:
        if vdir.is_dir():
            # bare version names only — ccctl itself parks .stock/.pre-swap/.part
            # siblings here, and they must never shadow the real file
            newest = sorted((p for p in vdir.iterdir()
                             if re.fullmatch(r"\d+(\.\d+)+", p.name)),
                            key=lambda p: [int(x) for x in p.name.split(".")])
            candidates.extend(reversed(newest))
    for cand in candidates:
        if _is_real_cc_binary(cand):
            if found and cand != Path(found).resolve():
                print(f"note: 'claude' on PATH is a launcher shim; using {cand}")
            return cand
    if not found:
        die("'claude' not found on PATH and no install under ~/.local/share/claude/versions")
    die(f"could not locate the native Claude Code binary "
        f"(PATH gives {found}, which fails validation — size or content)")


def cc_version():
    # same binary claude_binary() resolved — `which claude` can disagree
    # (npm shim earlier on PATH, ~/.local/bin missing under cron/sudo), and a
    # split brain here corrupts verdicts, report names and post_verify.
    out = run([str(claude_binary()), "--version"]).stdout.strip()
    m = re.match(r"([\d.]+)", out)
    return m.group(1) if m else die(f"cannot parse claude --version: {out!r}")


def repo_dir(cfg):
    return (HERE / cfg["repoPath"]).resolve()


def git(cfg, *args):
    return run(["git", "-C", str(repo_dir(cfg)), *args])


def repo_commit(cfg):
    return git(cfg, "rev-parse", "--short", "HEAD").stdout.strip()


def repo_edits(cfg):
    edits = repo_dir(cfg) / "edits"
    return sorted(p for p in edits.glob("*.md") if p.name != "README.md")


def repo_markers(cfg):
    mfile = repo_dir(cfg) / "edits" / "MARKERS.txt"
    if not mfile.exists():
        return []
    return [l.strip() for l in mfile.read_text(encoding="utf-8").splitlines() if l.strip()]


def repo_antimarkers(cfg):
    """Stock sentences this tranche DELETED. A correct patched binary contains
    zero of them; a hit means an apply silently reverted, or an upstream release
    put the text back somewhere we no longer patch.

    Markers prove our text went in. They cannot prove stock text came out —
    nothing did, until 2026-08-25, and that is the half that catches a
    regression. Comment lines start with `#`.

    [remark] An anti-marker MUST be verified to occur zero times in a correctly
    patched binary before it is added. Several obvious candidates fail that test:
    the stock "Being readable and being concise…" sentence reads 3 in stock and
    2 after a correct apply, because the bundled model-migration skill doc quotes
    the same paragraph as an example. Measure both binaries, do not reason about
    it. See edits/ANTIMARKERS.txt for the recorded counts."""
    afile = repo_dir(cfg) / "edits" / "ANTIMARKERS.txt"
    if not afile.exists():
        return []
    return [l.strip() for l in afile.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")]


# ----------------------------------------------------- the stock arm
# A/B against the stock disposition is the project's only instrument (spec/00
# §10.1, README "Method" step 5), and until now the B arm had no route: the
# pipeline could install a patched binary or refuse, and nothing else. So
# "spend a day on Anthropic's defaults" meant either hand-copying a downloaded
# binary past every check, or leaving the machine on an old version.
#
# The stock arm closes that. It is a DECLARED state, not an absence of one:
# the binary is assembled by the same staged-verify-swap path, carries
# tweakcc's own patch set (so the banner still says a pipeline built it), and
# is verified by the inverse assertion — every marker absent rather than every
# marker present. Zero markers by intent and zero markers by accident are the
# same bytes; only the declaration tells them apart, which is why the tripwire
# reads the declaration and not just the count.

def stock_arm(state=None):
    """The declared stock arm, or None. ccctl-state.json is authoritative."""
    if state is None:
        state = load_json(STATE, {})
    return state.get("stockArm")


def sync_stock_sentinel(arm):
    """Mirror the arm into a one-line file for interpreter-free tripwires
    (tools/cc-doctor.sh is bash and must not need python on a mac that has
    only python3.13-from-the-store). Derived, never authoritative: rewritten
    or removed to match state on every command that reads the arm, so a
    hand-deleted sentinel heals on the next `status`."""
    try:
        if arm:
            STOCK_SENTINEL.write_text(
                f"{arm['ccVersion']} {arm['when']} {arm.get('note', '')}\n".rstrip() + "\n",
                encoding="utf-8")
        elif STOCK_SENTINEL.exists():
            STOCK_SENTINEL.unlink()
    except OSError:
        pass                                 # a tripwire aid, never a blocker


def set_stock_arm(version, note):
    """Declare the live binary a deliberate stock arm. `applied` is left alone
    on purpose: it records when the tranche was last applied, which is still
    true history — the arm is a separate fact, and fudging one into the other
    is how a status report starts lying."""
    state = load_json(STATE, {})
    arm = {"ccVersion": version, "note": note,
           "when": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")}
    state["stockArm"] = arm
    save_json(STATE, state)
    sync_stock_sentinel(arm)
    return arm


def clear_stock_arm():
    """End the arm. Every path that puts our text back calls this."""
    state = load_json(STATE, {})
    had = state.pop("stockArm", None)
    if had:
        save_json(STATE, state)
    sync_stock_sentinel(None)
    return had


# ------------------------------------------------- live bundle window
# CC is a Bun single-file executable: the JS lives in a blob that ELF carries
# in a section named `.bun`, framed as [u64 length][blob] and terminated by a
# 16-byte trailer. The legacy Linux tweakcc repacker appends on every write;
# current code-split adhocs use bun_source_patch instead (no per-target append).
# On that legacy path, `--apply` and `adhoc-patch` APPEND a fresh copy
# past the end of the writable PT_LOAD, repoints the section header and the
# `BUN_COMPILED` pointer at it, extends the segment, and leaves the previous
# copy in the file as unreferenced bytes. Measured on 2.1.241: +253 MB per
# invocation, after which every bundle string occurs twice, then three times,
# and so on. macOS and win32 do not do this — repackMachO/repackPE grow the
# section in place, so there is only ever one copy there.
#
# So on linux a whole-file search cannot tell the live text from its corpses:
# locate_span's uniqueness test fails, the adhoc anchors go ambiguous, and a
# marker "verified present" may be sitting in a stale copy. Everything that
# locates, counts or verifies our text therefore reads THIS window, never the
# raw file.
#
# The section header is what tweakcc rewrites and what tweakcc reads back, so
# it names the live copy. It is not, however, what the *runtime* follows: that
# is an 8-byte little-endian vaddr (`BUN_COMPILED`) parked in a 16 KiB-aligned
# word inside the writable PT_LOAD, which Bun's own --compile sets up the same
# way. The two agree on every binary measured here, pristine and patched. The
# window is validated rather than assumed — a mismatched length header or a
# missing trailer means we did not find a well-formed blob, and the caller
# falls back to the whole file rather than trusting a half-parse.

BUN_TRAILER = b"\n---- Bun! ----\n"


def bun_window(binary):
    """[start, end) file offsets of the live embedded JS bundle, or None when
    this is not an ELF carrying a well-formed `.bun` (win32, macOS, anything
    unparseable) — callers then search the whole file, as before."""
    try:
        with open(binary, "rb") as fh:
            hdr = fh.read(64)
            if hdr[:6] != b"\x7fELF\x02\x01":         # ELF64, little-endian
                return None
            shoff, = struct.unpack_from("<Q", hdr, 0x28)
            shentsize, shnum, shstrndx = struct.unpack_from("<HHH", hdr, 0x3A)
            if not shoff or not shnum or shstrndx >= shnum or shentsize < 64:
                return None                           # extended/absent shdrs: do not guess
            fh.seek(shoff)
            shdrs = fh.read(shentsize * shnum)
            if len(shdrs) < shentsize * shnum:
                return None

            def sh(i):                                # sh_name, sh_type, sh_flags, sh_addr,
                return struct.unpack_from("<IIQQQQ", shdrs, i * shentsize)  # sh_offset, sh_size

            stroff, strsize = sh(shstrndx)[4:6]
            fh.seek(stroff)
            names = fh.read(strsize)
            for i in range(shnum):
                nameoff, _type, _flags, _addr, off, size = sh(i)
                if names[nameoff:names.find(b"\0", nameoff)] != b".bun":
                    continue
                if size < 8 + len(BUN_TRAILER):
                    return None
                fh.seek(off)
                length, = struct.unpack("<Q", fh.read(8))
                if 8 + length != size:                # u32-framed (Bun < 1.3.4) or not a blob
                    return None
                fh.seek(off + size - len(BUN_TRAILER))
                if fh.read(len(BUN_TRAILER)) != BUN_TRAILER:
                    return None
                return off, off + size
    except (OSError, struct.error, IndexError):
        return None
    return None


def macho_bun_window(binary):
    """[start, end) file offsets of the blob in a Mach-O 64 `__BUN,__bun`
    section, validated by the same u64-prefix + trailer framing as the ELF
    `.bun` path, or None. darwin stores the bundle as its own segment — the
    file ends with __LINKEDIT, so bun_source_patch's trailing-blob fallback
    never fires there, and a whole-file trailer search is ambiguous because
    the Bun runtime carries its own copy of the trailer string (2 hits on
    darwin-arm64 2.1.251, measured 2026-08-29)."""
    try:
        with open(binary, "rb") as fh:
            hdr = fh.read(32)
            if len(hdr) < 32 or hdr[:4] != b"\xcf\xfa\xed\xfe":  # MH_MAGIC_64, LE
                return None
            ncmds, sizeofcmds = struct.unpack_from("<II", hdr, 16)
            cmds = fh.read(sizeofcmds)
            pos = 0
            for _ in range(ncmds):
                if pos + 8 > len(cmds):
                    return None                       # truncated load commands: do not guess
                cmd, cmdsize = struct.unpack_from("<II", cmds, pos)
                if cmdsize < 8 or pos + cmdsize > len(cmds):
                    return None
                if cmd == 0x19 and cmds[pos + 8:pos + 24].rstrip(b"\0") == b"__BUN":  # LC_SEGMENT_64
                    nsects, = struct.unpack_from("<I", cmds, pos + 64)
                    for i in range(nsects):
                        s = pos + 72 + i * 80         # section_64 structs follow the segment
                        if s + 80 > pos + cmdsize:
                            return None
                        if cmds[s:s + 16].rstrip(b"\0") != b"__bun":
                            continue
                        size, off = struct.unpack_from("<QI", cmds, s + 40)
                        if size < 8 + len(BUN_TRAILER):
                            return None
                        fh.seek(off)
                        length, = struct.unpack("<Q", fh.read(8))
                        if 8 + length != size:        # same framing check as the ELF path
                            return None
                        fh.seek(off + size - len(BUN_TRAILER))
                        if fh.read(len(BUN_TRAILER)) != BUN_TRAILER:
                            return None
                        return off, off + size
                pos += cmdsize
    except (OSError, struct.error, IndexError):
        return None
    return None


def pe_bun_window(binary):
    """[start, end) file offsets of the blob in a Windows PE, or None.

    win32 keeps the bundle as a trailing overlay, but — unlike the plain
    trailing-blob case bun_source_patch already handled — the file does NOT end
    with it. Signed builds append an Authenticode certificate table (10,400
    bytes on both 2.1.241 and 2.1.251), with alignment padding in front of it,
    so 10,690 bytes sit after the trailer. The ends-with-trailer fallback
    therefore never fired on win32: every write fell through to tweakcc, and
    tweakcc cannot write a code-split bundle at all (#969). That is the whole
    reason this machine could not carry the 2.1.251 round while linux and macOS
    could — not the prompts, not the anchors, just an unreachable window.

    Read from the section table, the same way the ELF path reads `.bun` there:
    the section's VIRTUAL size is the blob's true length, while SizeOfRawData is
    rounded up to file alignment (290 bytes of padding on 2.1.251), so the raw
    extent overshoots the blob and only the virtual size frames it correctly.

    A backwards trailer search is kept as a fallback for a build that ships the
    blob with no section to read. It is ambiguous on its own — the Bun runtime
    carries its own copy of the trailer string, 2 hits in every win32 build
    measured, same as the darwin note on macho_bun_window — so, like the
    section path, it is accepted only when the framing agrees: the u64 length
    prefix must describe the window and the window must end with the trailer.

    Deliberately NOT read from the certificate-table directory, which looks
    like the obvious way to find where the blob stops and does not survive
    contact: padding sits between the two, and on an already-patched binary the
    directory is stale — the live patched 2.1.241 records offset+size
    337,745,056 against a 337,737,673-byte file, 7,383 bytes past its own EOF,
    because tweakcc shrinks the file and never fixes the directory up. That is
    exactly the class of binary we re-patch most."""

    def framed(fh, wstart, wend):
        """(wstart, wend) when the u64 prefix and trailer agree, else None."""
        if wstart < 0 or wend - wstart < 56 + len(BUN_TRAILER):
            return None
        fh.seek(wend - len(BUN_TRAILER))
        if fh.read(len(BUN_TRAILER)) != BUN_TRAILER:
            return None
        fh.seek(wstart)
        prefix, = struct.unpack("<Q", fh.read(8))
        fh.seek(wend - 48)
        bytecount, = struct.unpack("<Q", fh.read(8))
        if prefix != (wend - wstart) - 8 or bytecount + 48 != (wend - wstart) - 8:
            return None
        return wstart, wend

    try:
        size = Path(binary).stat().st_size
        if size < 56 + len(BUN_TRAILER):
            return None
        with open(binary, "rb") as fh:
            if fh.read(2) != b"MZ":                   # PE only; ELF/Mach-O have exact paths
                return None
            fh.seek(0x3C)
            e_lfanew, = struct.unpack("<I", fh.read(4))
            fh.seek(e_lfanew)
            if fh.read(4) == b"PE\0\0":
                coff = fh.read(20)
                nsec, optsz = struct.unpack_from("<H", coff, 2)[0], struct.unpack_from("<H", coff, 16)[0]
                fh.seek(e_lfanew + 24 + optsz)
                table = fh.read(40 * nsec)
                for i in range(nsec):
                    hdr = table[i * 40:(i + 1) * 40]
                    if len(hdr) < 40 or hdr[:8].rstrip(b"\0") != b".bun":
                        continue
                    vsize, _vaddr, _rawsz, rawptr = struct.unpack_from("<IIII", hdr, 8)
                    return framed(fh, rawptr, rawptr + vsize)   # virtual size, not raw
            # No .bun section: fall back to the trailer. ~10 KB is appended in
            # practice; 8 MB is slack without reading a 200 MB file into memory.
            tail_len = min(size, 8 << 20)
            fh.seek(size - tail_len)
            idx = fh.read(tail_len).rfind(BUN_TRAILER)
            if idx < 0:
                return None
            wend = size - tail_len + idx + len(BUN_TRAILER)
            fh.seek(wend - 48)
            bytecount, = struct.unpack("<Q", fh.read(8))
            return framed(fh, wend - (bytecount + 56), wend)
    except (OSError, struct.error):
        return None


def live_blob(binary):
    """The live bundle's bytes (see bun_window), or the whole file when there
    is no window. Offsets into the result are window-relative, which is all
    the plan/apply path needs — it works in `old`/`new` byte strings, never in
    absolute file offsets."""
    window = bun_window(binary)
    if not window:
        return Path(binary).read_bytes()
    with open(binary, "rb") as fh:
        fh.seek(window[0])
        return fh.read(window[1] - window[0])


# ------------------------------------------------- code-split bundle writer
# CC 2.1.243 turned on Bun's code splitting: the entrypoint module is a ~20 KB
# loader and the application lives in ~2000 sibling `chunk-<hash>.js` modules
# inside the blob's virtual filesystem. tweakcc's writer only ever patches the
# module its `isClaudeModule()` matches — the loader stub — so from 2.1.243 on
# every tweakcc write reports "String not found in content" (upstream #969,
# closed unmerged as of 2026-08-29). Reading was never affected: our
# plan/verify path greps the whole window and the prompt text is still plain
# bytes. Only the WRITE path needed a replacement, and it is this one.
#
# Blob layout (validated against 2.1.241 and 2.1.251, linux-x64):
#
#   [u64 dataLen]                       == len(blob) - 8
#   [data region: name/contents/sourcemap/bytecode/moduleInfo/originPath
#    byte runs, NUL-separated, addressed by explicit (offset,length) pairs]
#   [module structs, 52 bytes each: 6 x (u32 offset, u32 length) + 4 x u8;
#    older builds use 36 = 4 pairs + 4 bytes]
#   [optional source-hash / builtin-bytecode / string-table metadata]
#   [compileExecArgv string][NUL]
#   [32-byte header: u64 byteCount, modulesPtr(off,len), u32 entryPointId,
#    argvPtr(off,len), u32 flags]       byteCount == len(blob) - 56
#   [16-byte trailer "\n---- Bun! ----\n"]
#
# All offsets are relative to the byte after the length prefix. Because every
# region is reached through an explicit offset, nothing requires the layout to
# be gapless — which is what makes a SIZE-PRESERVING in-place rewrite
# possible: splice the edit into the one module that holds it, shift every
# offset past the splice point, and let a pad of NULs in front of the header
# absorb the delta. The file never grows, no ELF section moves, and the
# +253 MB append-a-fresh-copy disease of the tweakcc path does not exist here.
# The tranche is a large net deletion, so the pad only ever grows; a patch
# that would need more pad than exists dies before touching anything.
#
# Edits explicitly discard the edited module's bytecode (and, on modern
# 52-byte structs, its module-info and origin runs) and zero its source hash.
# This makes Bun reparse the changed source even for a same-length edit.

BUN_STRUCT_RUNS = {52: 6, 36: 4}


def bun_table(blob):
    """Parse the module table of a bundle window (prefix + data + header +
    trailer, as returned by live_blob). None when it does not parse as one —
    callers then fall back to the tweakcc write path."""
    if len(blob) < 56 + len(BUN_TRAILER) or not blob.endswith(BUN_TRAILER):
        return None
    hdr_at = len(blob) - 48
    try:
        prefix, = struct.unpack_from("<Q", blob, 0)
        bytecount, mo, ml, entry, ao, al, flags = struct.unpack_from("<QIIIIII", blob, hdr_at)
    except struct.error:
        return None
    if prefix != len(blob) - 8 or bytecount + 48 != len(blob) - 8:
        return None

    def parse(ss):
        nruns = BUN_STRUCT_RUNS[ss]
        mods = []
        for i in range(ml // ss):
            at = mo + 8 + i * ss
            runs = [struct.unpack_from("<II", blob, at + 8 * j) for j in range(nruns)]
            flagbytes = blob[at + 8 * nruns: at + 8 * nruns + 4]
            no, nl = runs[0]
            name = blob[no + 8: no + 8 + nl]
            if nl == 0 or nl > 512 or no + 8 + nl > hdr_at:
                return None
            try:
                name.decode("utf-8")
            except UnicodeDecodeError:
                return None
            mods.append({"runs": runs, "flags": flagbytes, "name": name})
        return mods

    # ml can divide both struct sizes (2.1.251/linux: 102492 = 52*1971 = 36*2847);
    # the names-decode sanity test above is what disambiguates, same idea as
    # tweakcc's detectModuleStructSize.
    for ss in (52, 36):
        if ml % ss == 0 and mo + 8 + ml <= hdr_at:
            mods = parse(ss)
            if mods:
                # Bun 1.3+ may append optional tables immediately after the
                # module structs.  Their pointed-at regions need relocation
                # too when the in-place source splice moves bytes.
                at = mo + ml
                source_hash_at = None
                builtins = []
                bytecode_strings = None
                module_info_strings = None
                try:
                    if flags & 32:
                        source_hash_at = at
                        at += len(mods) * 4
                    if flags & 64:
                        count, = struct.unpack_from("<I", blob, at + 8)
                        at += 4
                        if count > 100000 or at + count * 12 + 8 > hdr_at:
                            return None
                        for _ in range(count):
                            record_at = at
                            ident, off, length = struct.unpack_from("<III", blob, at + 8)
                            if length and off + 8 + length > hdr_at:
                                return None
                            builtins.append({"id": ident, "off": off, "len": length,
                                             "record_at": record_at})
                            at += 12
                    if flags & 128:
                        record_at = at
                        off, length = struct.unpack_from("<II", blob, at + 8)
                        if length and off + 8 + length > hdr_at:
                            return None
                        bytecode_strings = {"off": off, "len": length, "record_at": record_at}
                        at += 8
                    if flags & 256:
                        struct.unpack_from("<I", blob, at + 8)
                        at += 4
                    if flags & 512:
                        record_at = at
                        off, length = struct.unpack_from("<II", blob, at + 8)
                        if length and off + 8 + length > hdr_at:
                            return None
                        module_info_strings = {"off": off, "len": length, "record_at": record_at}
                        at += 8
                    if at + 8 > hdr_at:
                        return None
                except struct.error:
                    return None
                return {"hdr_at": hdr_at, "mo": mo, "ml": ml, "ss": ss, "entry": entry,
                        "ao": ao, "al": al, "flags": flags, "mods": mods,
                        "tail_end": at, "source_hash_at": source_hash_at,
                        "builtins": builtins, "bytecode_strings": bytecode_strings,
                        "module_info_strings": module_info_strings}
    return None


def bundle_window(target):
    """((wstart, wend), None) for the bundle in any binary format, or
    (None, reason) — the reason reads "not code-split: …" so writers can
    hand it straight back to a caller that falls back to tweakcc."""
    window = bun_window(target) or macho_bun_window(target) or pe_bun_window(target)
    if window:
        return window, None
    # PE stores the blob as trailing overlay; when the FILE ends with the
    # trailer, the header pins the blob's extent and the window derives
    # from the end. Anything else is not ours to write into.
    size = Path(target).stat().st_size
    if size < 56 + len(BUN_TRAILER):
        return None, "not code-split: file too small to hold a bundle"
    with open(target, "rb") as fh:
        fh.seek(size - 48)
        tail = fh.read(48)
    if not tail.endswith(BUN_TRAILER):
        return None, "not code-split: no bundle window and no trailing blob"
    bytecount, = struct.unpack_from("<Q", tail, 0)
    wstart = size - (bytecount + 56)
    if wstart < 0:
        return None, "not code-split: trailing blob header is malformed"
    return (wstart, size), None


def bundle_sources(target):
    """{module name: source bytes} of a code-split bundle, or None when the
    file carries no parseable module table."""
    window, _why = bundle_window(target)
    if window is None:
        return None
    with open(target, "rb") as fh:
        fh.seek(window[0])
        blob = fh.read(window[1] - window[0])
    table = bun_table(blob)
    if table is None:
        return None
    out = {}
    for m in table["mods"]:
        off, length = m["runs"][1]
        out[m["name"].decode("utf-8", "replace")] = blob[off + 8:off + 8 + length].rstrip(b"\0")
    return out


def module_parse_error(node, source):
    """None when `node --check` accepts `source` as an ES module, else node's
    one-line SyntaxError (with the line number when node gives one)."""
    fh = tempfile.NamedTemporaryFile("wb", suffix=".mjs", delete=False)
    try:
        fh.write(source)
        fh.close()
        r = run([node, "--check", fh.name], timeout=120)
        if r.returncode == 0:
            return None
        lines = r.stderr.splitlines()
        err = next((ln for ln in lines if re.match(r"^\w*Error\b", ln)), None)
        at = re.search(r":(\d+)\s*$", lines[0]) if lines else None
        return ((err or (r.stderr.strip()[-200:] or f"exit {r.returncode}"))
                + (f" (line {at.group(1)})" if at else ""))
    finally:
        try:
            os.unlink(fh.name)
        except OSError:
            pass


def changed_module_parse_errors(source, staged, node=None):
    """[(module, error)] for every module the build changed that no longer
    parses, or None when the check cannot run (no module table, no node).

    Why this exists: `--version` never loads the application chunks, so a
    patched module with a syntax error passes every check `assemble` had and
    only dies when a real session starts. Measured on macOS, CC 2.1.270,
    2026-09-13: tweakcc's AGENTS.md patch rebuilt a reader the minifier had
    named `$$t` with `String.replace`, whose replacement string turns `$$`
    into `$`, so the module declared `$t` twice. 15/15 markers, `--version`
    fine, `claude -p` → `SyntaxError: Cannot declare an async function that
    shadows ... '$t'`. Linux's name for the same function had no `$`.

    Only modules whose bytes differ are checked, and one that already fails
    to parse in the source is skipped — node is a proxy for Bun's parser, and
    a construct only Bun accepts must not block a build we did not break."""
    before, after = bundle_sources(source), bundle_sources(staged)
    if before is None or after is None:
        return None
    node = node or node_exe()
    if not shutil.which(node) and not Path(node).exists():
        return None
    broken = []
    for name, src in after.items():
        old = before.get(name)
        if old == src:
            continue
        err = module_parse_error(node, src)
        if err and (old is None or module_parse_error(node, old) is None):
            broken.append((name, err))
    return broken


def bun_source_patch(target, old, new):
    """Exact-string replace inside ONE module of a code-split bundle, in place
    and size-preserving. Returns (ok, detail); (False, "not code-split: …")
    means the caller should use the tweakcc path instead. Any other failure is
    fatal to the caller — nothing has been written unless ok is True."""
    window, why = bundle_window(target)
    if window is None:
        return False, why
    wstart, wend = window
    with open(target, "rb") as fh:
        fh.seek(wstart)
        blob = fh.read(wend - wstart)
    table = bun_table(blob)
    if table is None:
        return False, "not code-split: bundle window has no parseable module table"
    if len(table["mods"]) <= 20:
        # pre-split builds keep the whole app in the entry module; tweakcc
        # handles those and its path is the battle-tested one there
        return False, f"not code-split: {len(table['mods'])} modules"

    if blob.count(old) != 1:
        return False, f"old bytes occur {blob.count(old)}x in the bundle window — need exactly 1"
    p = blob.index(old)                      # absolute in blob
    ss, nruns = table["ss"], BUN_STRUCT_RUNS[table["ss"]]
    owner = next((i for i, m in enumerate(table["mods"])
                  if m["runs"][1][0] + 8 <= p < m["runs"][1][0] + 8 + m["runs"][1][1]), None)
    if owner is None:
        return False, "old bytes are not inside any module's contents run"
    oco, ocl = table["mods"][owner]["runs"][1]
    if p + len(old) > oco + 8 + ocl:
        return False, "old bytes straddle a module boundary — mis-anchor"

    delta = len(new) - len(old)
    hdr_at = table["hdr_at"]

    # ---- 4-byte alignment of the regions the splice pushes along -----------
    #
    # Byte runs — module names, module SOURCE, the run[5] map — sit at whatever
    # offset the writer left them at: measured on 2.1.261 and 2.1.268, their
    # `off+8` takes all four residues mod 4. The BYTECODE runs do not. Every one
    # of the 1616-1651 modules that carries bytecode has run[3] and run[4] at a
    # CONSTANT residue mod 4, and all 134 builtin regions are at `off+8 % 4 == 0`.
    # JSC maps cached bytecode in place and reads u32s straight out of it, so a
    # region that moves by a non-multiple of 4 is fatal: CC dies at startup with
    # STATUS_STACK_BUFFER_OVERRUN (0xC0000409, exit 3221226505) and prints
    # nothing at all — no stack, no message, just a fast-fail.
    #
    # This writer shifts every region after the cut by `delta`, so `delta % 4`
    # must be 0. That held by accident until 2026-09-11: the 2.1.261 tranche's
    # cumulative delta was -11396 = 4 x -2849, so nothing ever moved out of
    # alignment and the constraint was invisible. Changing ONE floor sentence by
    # two bytes surfaced it, and the symptom points nowhere near the cause —
    # every patch reports OK, the module table re-parses, markers verify, and
    # the binary simply refuses to start. Measured the same day against a
    # tweakcc-rebuilt 2.1.261: deltas of 0/4/8/12/16/20 launch, 1/2/3/5/6/7/13
    # all fast-fail.
    #
    # So round the splice up to a multiple of 4 with filler spaces appended to
    # the OWNER MODULE'S SOURCE, just before its terminating NUL. That is JS
    # whitespace at the tail of a module that ends `...};\n`, it is inside the
    # one run whose length we already adjust, and it keeps the padding out of
    # the patched text itself — a fragment span is the interior of a template
    # literal, so padding `new` directly would put stray spaces in prompt prose.
    # DO NOT "improve" this to `-delta if delta < 0 else (-delta) % 4`, which
    # would make a shrinking edit move nothing at all and looks strictly safer.
    # It would break the pipeline: shrinking edits are where the pad COMES FROM.
    # A tweakcc-rebuilt blob starts at pad -1, the six fragment cuts free the
    # slack, and the floor's ~1.2 KB growth spends it. Freeze the shrinks at
    # zero delta and the pad never grows, so the floor is refused every time.
    pad_fill = (-delta) % 4
    total = delta + pad_fill                 # what every later region moves by
    ends = [off + 8 + ln for m in table["mods"] for off, ln in m["runs"] if ln]
    ends += [table["tail_end"] + 8, table["ao"] + 8 + table["al"]]
    ends += [b["off"] + 8 + b["len"] for b in table["builtins"] if b["len"]]
    for shared in (table["bytecode_strings"], table["module_info_strings"]):
        if shared and shared["len"]:
            ends.append(shared["off"] + 8 + shared["len"])
    pad = hdr_at - max(ends) - 1             # keep the terminating NUL
    if total > pad:
        return False, (f"edit grows the bundle by {total} bytes (edit {delta} + {pad_fill} "
                       f"alignment filler) but only {pad} bytes of pad exist before the "
                       f"header — apply a shrinking edit first")

    cut = p + len(old)                       # absolute; runs at/after it shift
    fill_at = oco + 8 + ocl                  # absolute; owner source end, pre-splice
    # The filler goes at the owner's source end, so bytes between the cut and
    # there move by `delta` while everything beyond moves by `total`. No run may
    # start in that interval or the two-tier shift would be wrong for it —
    # nothing should, since the interval is inside one module's own source.
    inside = [off for m in table["mods"] for off, ln in m["runs"]
              if ln and cut <= off + 8 < fill_at]
    if inside:
        return False, (f"{len(inside)} run(s) start inside the owner module's source between "
                       f"the cut and its end — the bundle layout is not what this writer models")
    shift = lambda off: (off + total if off + 8 >= fill_at
                         else off + delta if off + 8 >= cut else off)
    mods2 = []
    for i, m in enumerate(table["mods"]):
        runs = [(shift(off), ln) for off, ln in m["runs"]]
        if i == owner:
            runs[1] = (oco, ocl + total)
            # Do not rely on source-length invalidation: same-length edits are
            # common in prompt patches.  These are cache/metadata runs, never
            # runtime source required by Bun's parser.
            runs[3] = (0, 0)
            if nruns == 6:
                runs[4] = (0, 0)
                runs[5] = (0, 0)
        mods2.append({"runs": runs, "flags": m["flags"]})
    mo2, ao2 = shift(table["mo"]), shift(table["ao"])

    body = bytearray(blob[8:hdr_at])
    bp = p - 8
    # Highest position first, so `bp` still addresses the same bytes.
    if pad_fill:
        body[fill_at - 8: fill_at - 8] = b" " * pad_fill
    body[bp:bp + len(old)] = new
    if total > 0:
        del body[len(body) - total:]
    elif total < 0:
        body.extend(b"\x00" * -total)
    structs = bytearray()
    for m in mods2:
        for off, ln in m["runs"]:
            structs += struct.pack("<II", off, ln)
        structs += m["flags"]
    body[mo2: mo2 + table["ml"]] = structs
    # The metadata table itself shifts with the splice, but its absolute
    # pointers do not.  Rewrite every such pointer after relocating its
    # record; this is required for CC 2.1.257's non-empty builtin table.
    if table["source_hash_at"] is not None:
        source_hash_at = shift(table["source_hash_at"])
        struct.pack_into("<I", body, source_hash_at + owner * 4, 0)
    for builtin in table["builtins"]:
        struct.pack_into("<III", body, shift(builtin["record_at"]), builtin["id"],
                         shift(builtin["off"]), builtin["len"])
    for shared in (table["bytecode_strings"], table["module_info_strings"]):
        if shared:
            struct.pack_into("<II", body, shift(shared["record_at"]),
                             shift(shared["off"]), shared["len"])
    hdr = struct.pack("<QIIIIII", len(blob) - 56, mo2, table["ml"], table["entry"],
                      ao2, table["al"], table["flags"])
    out = blob[:8] + bytes(body) + hdr + blob[hdr_at + 32:]
    if len(out) != len(blob):
        die(f"bun_source_patch produced a {len(out)}-byte blob from a {len(blob)}-byte one — bug")
    check = bun_table(out)
    if check is None or out.count(new) < 1:
        die("bun_source_patch self-check failed: rewritten table does not re-parse — bug, "
            "nothing was written")
    with open_for_write_retrying(target) as fh:
        fh.seek(wstart)
        fh.write(out)
    return True, f"Replaced 1 occurrence in module {table['mods'][owner]['name'].decode()}"


def open_for_write_retrying(path, attempts=10, delay=1.0):
    """`open(path, "r+b")`, retried while win32 refuses it. A freshly written
    .exe is read by the virus scanner for a moment after tweakcc finishes, and
    an open for writing then fails with a sharing violation (PermissionError)
    that is gone a second later — measured on the 2.1.283 apply, 2026-09-28,
    where it killed the assemble and the identical re-run passed. Anything
    still locked after `attempts` is re-raised: that is a real holder."""
    for i in range(attempts):
        try:
            return open(path, "r+b")
        except PermissionError:
            if sys.platform != "win32" or i == attempts - 1:
                raise
            time.sleep(delay)


def binary_contains(binary, needles, live=True):
    """Which of `needles` occur literally in the binary. Chunked, overlap-safe.
    `live=True` confines the search to the live bundle window, which is what
    every question about our patched text wants. Pass live=False to ask about
    the file as a whole — "is this a CC binary at all", "is this byte-for-byte
    free of our markers" — where a hit anywhere is the honest answer."""
    if not needles:
        return set()
    needles = [n.encode("utf-8") for n in needles]
    found = set()
    overlap = max((len(n) for n in needles), default=0)
    tail = b""
    window = bun_window(binary) if live else None
    remaining = window[1] - window[0] if window else None
    with open(binary, "rb") as fh:
        if window:
            fh.seek(window[0])
        while True:
            want = 1 << 22 if remaining is None else min(1 << 22, remaining)
            if want <= 0 or not (chunk := fh.read(want)):
                break
            if remaining is not None:
                remaining -= len(chunk)
            hay = tail + chunk
            for n in needles:
                if n not in found and n in hay:
                    found.add(n)
            if len(found) == len(needles):
                break                    # nothing left to look for
            tail = hay[-overlap:]
    return {n.decode("utf-8") for n in found}


# ---------------------------------------------------- span apply engine
# Byte-precise fragment replacement, platform-independent (spec/40 D3).
# Origin: tools/apply-win32.py, where it was built because tweakcc --apply's
# fragment writer no-ops on the win32 CC build. Each fragment is a template
# literal split into static `pieces` by ${VAR} interpolations; we locate the
# byte span covering all pieces (anchored on the longest globally-unique
# piece), rebuild it with the edited static text around the original
# interpolation bytes, and write through `tweakcc adhoc-patch`.

# CC's bundle is built by Bun and carries `using` declarations (ES2026 explicit
# resource management) — 43 of them in 2.1.241. tweakcc verifies its patched
# bundle with `node --check` before writing it, so a node that cannot parse
# `using` rejects a PERFECTLY VALID bundle and tweakcc reverts, reporting
#
#     ✖ The patched bundle failed to parse. ... SyntaxError: Unexpected identifier
#
# Node 22 cannot parse them; 24+ can. This is the whole of the "tweakcc cannot
# patch CC on darwin" story (2026-08-25): that Mac's PATH node is 22 and its
# homebrew node is 25, so a version mismatch wore the platform's name for a
# round. Measured 2026-08-26 — the UNPATCHED bundle tweakcc extracts fails
# `node --check` under 22 and passes under 25, so no patch was ever at fault.
#
# Probe, don't configure: same rule as the .bun window and the platform slug.
_USING_PROBE = "function f(){ using r = { [Symbol.dispose](){} }; return r }\n"


def node_parses_using(exe):
    """True if `exe` can parse a `using` declaration. One probe per exe."""
    fh = tempfile.NamedTemporaryFile("w", suffix=".cjs", delete=False)
    try:
        fh.write(_USING_PROBE)
        fh.close()
        return run([exe, "--check", fh.name]).returncode == 0
    except OSError:
        return False
    finally:
        try:
            os.unlink(fh.name)
        except OSError:
            pass


@functools.lru_cache(maxsize=1)
def node_exe():
    """The node tweakcc must run under: the first on this machine that parses
    `using`. Falls back to PATH node, so the caller still gets tweakcc's own
    diagnostic rather than a message invented here."""
    seen, candidates = set(), []
    for cand in (shutil.which("node"), "/opt/homebrew/bin/node", "/usr/local/bin/node",
                 str(Path.home() / ".local" / "bin" / "node")):
        if cand and cand not in seen and Path(cand).exists():
            seen.add(cand)
            candidates.append(cand)
    # version managers keep theirs off PATH until a shell activates them
    for root in (Path.home() / ".nvm" / "versions" / "node",
                 Path.home() / ".volta" / "tools" / "image" / "node"):
        if root.is_dir():
            for v in sorted(root.iterdir(), reverse=True):
                exe = v / "bin" / ("node.exe" if sys.platform == "win32" else "node")
                if exe.exists() and str(exe) not in seen:
                    seen.add(str(exe))
                    candidates.append(str(exe))
    for cand in candidates:
        if node_parses_using(cand):
            return cand
    return candidates[0] if candidates else "node"


def node_argv(shim):
    """argv prefix that runs the tweakcc at `shim` via node directly, bypassing
    the shim. Needed on win32 where cmd.exe mangles large multi-line adhoc-patch
    args, and on POSIX wherever PATH has no node for the shebang to find. Falls
    back to the shim (run() wraps it)."""
    shim = Path(shim)
    resolved = shim.resolve()
    node = node_exe()
    if resolved.suffix in (".mjs", ".js"):     # linux/macos: bin symlink -> dist entry
        return [node, str(resolved)]
    for root in (shim.parent / "node_modules" / "tweakcc",):  # win32: shim + sibling tree
        pkg = root / "package.json"
        if pkg.exists():
            rel = json.loads(pkg.read_text(encoding="utf-8"))["bin"]["tweakcc"]
            entry = (root / rel).resolve()
            if entry.exists():
                return [node, str(entry)]
    return [str(shim)]


@functools.lru_cache(maxsize=1)
def tweakcc_node():
    """argv prefix for every tweakcc call ccctl makes: the chosen tweakcc,
    run under the chosen node."""
    return node_argv(tweakcc())


def adhoc_patch(target, old, new):
    """Exact-string replace in `target`. old/new are bytes in the binary's own
    encoding (ASCII + \\uXXXX escapes). Returns (ok, output).

    Code-split builds (CC >= 2.1.243) are written by our own in-place bundle
    writer — tweakcc's writer only reaches the loader stub there (#969). Older
    single-module builds keep the tweakcc path, which is the one every live
    machine's current binary was built with."""
    if any(b > 127 for b in old + new):
        die("non-ASCII bytes in a patch payload — the argv latin-1/UTF-8 round-trip "
            "would corrupt them; the binary stores non-ASCII as \\uXXXX escapes, so "
            "this means a span captured raw high bytes (mis-anchor)")
    ok, out = bun_source_patch(target, old, new)
    if ok or not out.startswith("not code-split"):
        return ok, out
    r = run([*tweakcc_node(), "adhoc-patch", "-s", old.decode("latin-1"),
             new.decode("latin-1"), "-p", str(target),
             "--confirm-possible-dangerous-patch"], timeout=600)
    out = (r.stdout + r.stderr).strip()
    return "Replaced 1" in out, out


def enc_match(s):
    """Encode fragment text the way the binary stores it: real newlines,
    non-ASCII as \\uXXXX escapes."""
    return "".join(c if ord(c) < 128 else "\\u%04x" % ord(c) for c in s).encode("latin-1")


def enc_new(s):
    """Encode replacement text for insertion into a template literal."""
    s = s.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
    return "".join(c if ord(c) < 128 else "\\u%04x" % ord(c) for c in s).encode("latin-1")


def edit_text(path):
    """An edit fragment's payload: frontmatter comment stripped, LF-normalized."""
    raw = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    return FRONTMATTER.sub("", raw).rstrip("\n")


def locate_span(blob, pieces):
    """(start, end, gaps) of the template literal whose static runs are
    `pieces`, or None. gaps are the original interpolation byte runs."""
    encs = [enc_match(p) for p in pieces]
    uniq = [(len(e), i) for i, e in enumerate(encs) if blob.count(e) == 1]
    if not uniq:
        return None
    anchor = max(uniq)[1]
    aidx = blob.find(encs[anchor])
    off = [None] * len(pieces)
    off[anchor] = (aidx, aidx + len(encs[anchor]))
    for i in range(anchor + 1, len(pieces)):
        s = blob.find(encs[i], off[i - 1][1])
        if s < 0:
            return None
        off[i] = (s, s + len(encs[i]))
    for i in range(anchor - 1, -1, -1):
        s = blob.rfind(encs[i], 0, off[i + 1][0])
        if s < 0:
            return None
        off[i] = (s, s + len(encs[i]))
    gaps = [blob[off[i][1]:off[i + 1][0]] for i in range(len(pieces) - 1)]
    return off[0][0], off[-1][1], gaps


def build_replacement(body, gaps):
    """Edited static text spliced around the original interpolation bytes.
    An edit with NO ${VAR}s replaces the whole span as static text even when
    the stock fragment had interpolations — that is an authored deletion
    (outcome-first-communication-style does exactly this). None when a
    partially-interpolated edit no longer matches.

    [remark] The braces belong to us, not to the gap. tweakcc splits a template
    literal at *identifier* boundaries, not at interpolation boundaries: a stock
    piece ends with `${`, the gap is the bare minified identifier (`Il`, `Ri`),
    and the next piece opens with `}`. An edit's `${VAR}` token consumes both
    braces along with the name, so splicing the raw gap back in emitted
    `one call to the Il tool` and `Ri({` into the live prompt — correct JS,
    corrupt prose, and invisible to marker verification because the markers are
    the surrounding sentence. Live on all three machines from 2.1.234 until
    2026-08-25; see C-05 and TODO item 9. We re-emit `${` and `}` around each
    gap. This is exact for the only shape that reaches this branch: the regex
    matches `${NAME}` and nothing else, so a stock interpolation carrying more
    than a bare identifier (`${r?"a":"b"}`) cannot be authored piecewise at all
    and falls to the whole-span path below. An edit body must therefore never
    contain a literal `${` outside a `${VAR}` token — enc_new escapes it, which
    ships the scaffolding as text."""
    segs = re.split(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", body)
    if len(segs) == len(gaps) + 1:
        out = enc_new(segs[0])
        for i, gap in enumerate(gaps):
            out += b"${" + gap + b"}" + enc_new(segs[i + 1])
        return out
    if len(segs) == 1:
        return enc_new(body)
    return None


def plan_fragments(blob, prompt_data, edit_files):
    """Locate every edit fragment in `blob`. Returns a list of dicts with
    status AUTO (old+new ready) or MANUAL (with a reason and a fix hint)."""
    plan = []
    for f in edit_files:
        item = {"name": f.stem, "kind": "fragment"}
        entry = prompt_data.get(f.stem)
        if not entry:
            item.update(status="MANUAL", reason="fragment id not in tweakcc prompt data for this version",
                        fix="check `ccctl.py diff stock` output — the fragment may have been renamed or removed upstream; "
                            "re-derive the edit against the nearest new fragment, or convert it to an adhoc patch")
        else:
            span = locate_span(blob, entry["pieces"])
            if not span:
                item.update(status="MANUAL", reason="template-literal span not locatable in binary",
                            fix="tweakcc's piece data may not match this build; run `tweakcc unpack` on the staged "
                                "binary and hand-locate the fragment text, then adhoc-patch it")
            else:
                start, end, gaps = span
                old = blob[start:end]
                new = build_replacement(edit_text(f), gaps)
                if new is None:
                    item.update(status="MANUAL", reason="interpolation count changed upstream (edit's ${VAR}s no longer match)",
                                fix=f"compare `ccctl.py diff stock <old> <new> {f.stem}` and update edits/{f.name} "
                                    "to the new interpolation structure")
                elif gaps and max(len(g) for g in gaps) > 64:
                    # legitimate gaps are 1-2 byte minified identifiers; a big
                    # one means locate_span anchored across unrelated code
                    item.update(status="MANUAL",
                                reason=f"interpolation gap of {max(len(g) for g in gaps)} bytes — mis-anchored span suspected",
                                fix="inspect the fragment in the unpacked JS (`tweakcc unpack`); the piece data "
                                    "and the binary disagree about this fragment's layout")
                elif blob.count(old) != 1:
                    item.update(status="MANUAL", reason="located span is not unique in binary",
                                fix="widen the fragment's unique anchor: inspect duplicates via `tweakcc unpack`")
                elif b"\\$" in new:
                    # enc_new escapes `${` to `\${`, which is valid JS and emits
                    # the two characters `${` as PROMPT TEXT. An edit body that
                    # keeps stock's ternary therefore ships the scaffolding —
                    # variable name, both branches, braces — to the reader. Live
                    # for months on all three machines; no marker could see it,
                    # because the markers are the surrounding prose and that
                    # prose is intact. C-05 / TODO item 9.
                    item.update(status="MANUAL",
                                reason="replacement would emit escaped template scaffolding as prompt text",
                                fix=f"edits/{f.name} contains a `${{` that is not a bare ${{VAR}} token. "
                                    "Flatten the fragment to unconditional prose (pick one branch, or state "
                                    "both facts plainly); an interpolation carrying more than a bare "
                                    "identifier cannot be authored piecewise by this engine")
                else:
                    item.update(status="AUTO", old=old, new=new, span=[start, end])
        plan.append(item)
    # located spans must be pairwise disjoint — an overlap means at least one
    # replacement would clobber part of another fragment
    autos = sorted((i for i in plan if "span" in i), key=lambda i: i["span"][0])
    for a, b in zip(autos, autos[1:]):
        if a["span"][1] > b["span"][0]:
            for i in (a, b):
                i.pop("old", None), i.pop("new", None), i.pop("span", None)
                i.update(status="MANUAL",
                         reason=f"located span overlaps another fragment's ({a['name']} / {b['name']})",
                         fix="the piece data mis-locates one of these fragments in this build; "
                             "inspect both via `tweakcc unpack` before applying anything")
    return plan


def ident_led(pattern, blob, literal):
    r"""`list(re.finditer(pattern, blob))` for a pattern that OPENS with a
    minified identifier, `([\w$]+)`, directly followed by `literal`.

    Python's engine has no literal prefix to skip to in such a pattern, so it
    tries every byte of the bundle: about 4 s a pattern on 2.1.278's 118 MB,
    three of them in this planner, and that made the SessionStart tripwire
    (which re-plans against the pristine) take 14 s against its 10 s hook
    timeout. Found 2026-09-21. The literal is found first, the identifier is
    walked back from it, and the pattern is matched there, which returns the
    same matches, since an identifier match always starts where the name does."""
    rx = re.compile(pattern)
    out, i = [], blob.find(literal)
    while i != -1:
        start = i
        while start > 0 and (blob[start - 1:start].isalnum() or blob[start - 1:start] in (b"_", b"$")):
            start -= 1
        m = rx.match(blob, start)
        if m and (not out or m.start() >= out[-1].end()):
            out.append(m)
        i = blob.find(literal, i + 1)
    return out


# ---- the bypass/auto-mode shell block, located by digest rather than by quote
#
# [remark] This locator used to BE the block: about fifty words of Anthropic's
# prose, spelled out in this file and again in edits/adhoc-2.1.234.json, because
# a literal locator is the text it locates. It is now a digest instead —
# SHA-256 of the template literal's body with every `${...}` rewritten to `${}`.
# Normalising the interpolations is what makes the value portable: the
# identifiers inside them are minified per build AND per platform, the prose is
# not. Measured identical on 2.1.234 (the run edits/adhoc-2.1.234.json
# recorded), on stock 2.1.273 linux-x64 and win32-x64, and on stock 2.1.278.
#
# It fails closed harder than the regex did, not softer. The regex asserted the
# prose it spelled out; the digest asserts every byte of the block, including
# the parts the regex glossed as `.`, so a one-character upstream edit is a
# MANUAL rather than a silent partial match. What it cannot do is tell you WHAT
# changed — for that, diff the block against the previous release's pristine.
#
# The scan is the cheap half of the old regex: bounded, backtick-delimited
# spans, no `[\w$]+` prefix to backtrack over 145 MB of bundle. The assignment
# target is read backwards off the match so the replaced run is byte-identical
# to what the regex produced.
SHELL_BLOCK_SHA256 = "4e966188d3a7cda8e2bb9f416c5241bfdf1888673098b66b7f4e5378a42a38ef"
SHELL_BLOCK_NORM_LEN = 323   # body length once each `${...}` is `${}`
SHELL_BLOCK_INTERPS = 5      # shell tool, three dedicated tools, shell tool again
INTERP_BYTES = re.compile(rb"\$\{[^{}]*\}")
IDENT_TAIL = re.compile(rb"[\w$]+$")


@functools.lru_cache(maxsize=8)
def _template_span_re(lo, hi):
    """Any `=`...`` run of `lo`..`hi` body bytes. Cached because the bounds are
    derived from SHELL_BLOCK_NORM_LEN, which the tests swap for a fixture."""
    return re.compile(("=`([^`]{%d,%d})`" % (lo, hi)).encode("ascii"), re.S)


def shell_block_spans(blob):
    """Every `X=`...`` whose body hashes to the stock shell block.

    Returns (var, body, start, end) per hit, where start..end bounds the whole
    assignment — the same run the old prose regex replaced. `var` is empty when
    the digest matched but nothing assignment-shaped precedes it, which is a
    finding, not a miss, so it is returned rather than skipped."""
    lo = SHELL_BLOCK_NORM_LEN
    hi = SHELL_BLOCK_NORM_LEN + 100      # room for five long minified identifiers
    out = []
    for m in _template_span_re(lo, hi).finditer(blob):
        body = m.group(1)
        norm = INTERP_BYTES.sub(b"${}", body)
        if len(norm) != SHELL_BLOCK_NORM_LEN:
            continue
        if hashlib.sha256(norm).hexdigest() != SHELL_BLOCK_SHA256:
            continue
        im = IDENT_TAIL.search(blob[max(0, m.start() - 64):m.start()])
        var = im.group(0) if im else b""
        out.append((var, body, m.start() - len(var), m.end()))
    return out


SUBAGENT_DELEGATION_OPT_IN = (
    "If you are a subagent, do not spawn further subagents unless the user, a CLAUDE.md file, "
    "or a skill explicitly asks you to delegate further. A task assignment alone is not that opt-in. "
    "This restriction takes precedence over the delegation guidance below.")


def plan_adhocs(blob):
    """Re-derive the tranche's adhoc patches against THIS build's minified
    identifiers (they differ per platform build: linux tXS/LI/xl = win32
    iXS/DH/Rl). Returns plan items like plan_fragments.

    Identifiers are matched with [\\w$]+, never \\w+: `$` is legal in a JS
    identifier and the minifier does emit it. 2.1.245/win32-x64 named one of
    the dedicated tools `$r`, which made adhoc #2 read as "upstream rewrote
    the block" when the prose was byte-identical — a false MANUAL that only
    a hand-grep of the binary could clear."""
    plan = []
    item = {"name": "delegation-override-cut", "kind": "adhoc"}
    m1s = list(re.finditer(rb"function ([\w$]+)\(e\)\{let t=([\w$]+)\(\)\?\.tengu_heron_brook;", blob))
    # 2.1.257 split this into three adjacent dynamic sections: an Opus-5
    # default plus the old and new server-driven override paths. Cut them at
    # the shared registration site so none can re-introduce the delegation
    # prohibition. The deliberately unreachable `let t=` preserves the
    # cross-version verification marker used by already-patched 2.1.241/251.
    #
    # 2.1.293 gave heron_brook a model-default fallback behind the server text:
    # `()=>serverText()??haikuGuidance(h,s)`. The fallback is Haiku 5.5's
    # early-stopping guidance (capability `haiku_5_5_early_stopping_guidance`,
    # flag `tengu_idempotent_wolf`), which is not a delegation prohibition and
    # agrees with the delivering-work fragment, so the cut removes the server
    # text and keeps the fallback (notes/2026-10-08-win32-2.1.294.md §2).
    m1n = ident_led(
        rb'([\w$]+)\("opus5_reduced_delegation",\(\)=>\{'
        rb'if\(![\w$]+\([\w$]+\)\)return null;'
        rb'if\(![\w$]+\("tengu_slate_bittern",!0\)\)return null;'
        rb'let ([\w$]+)=[\w$]+\(\)\?\.value;'
        rb'if\(\2\?\.includes\([\w$]+\)\|\|\2\?\.includes\([\w$]+\)\)return null;'
        rb'return [\w$]+\}\),'
        rb'\1\("heron_brook",\(\)=>[\w$]+\(\)(?:\?\?([\w$]+\([\w$,]*\)))?\),'
        rb'\1\("brook_heron",\(\)=>[\w$]+\([\w$]+\)\)', blob,
        b'("opus5_reduced_delegation",()=>{')
    if len(m1s) == 1 and not m1n:
        m1 = m1s[0]
        fn, acc = m1.group(1).decode(), m1.group(2).decode()
        item.update(status="AUTO",
                    old=(f"function {fn}(e){{let t={acc}()?.tengu_heron_brook;").encode("latin-1"),
                    new=(f"function {fn}(e){{return null;let t={acc}()?.tengu_heron_brook;").encode("latin-1"))
    elif len(m1n) == 1 and not m1s:
        section = m1n[0].group(1).decode()
        fallback = (m1n[0].group(3) or b"null").decode("latin-1")
        new1 = (f'{section}("opus5_reduced_delegation",function(){{return null;let t=0}}),'
                f'{section}("heron_brook",()=>{fallback}),'
                f'{section}("brook_heron",()=>null)').encode("latin-1")
        item.update(status="AUTO", old=m1n[0].group(0), new=new1)
    elif m1s or m1n:
        item.update(status="MANUAL",
                    reason=f"delegation anchors match {len(m1s)}x legacy / {len(m1n)}x split — ambiguous",
                    fix="multiple delegation section shapes are present; inspect their callers and suppress "
                        "each injected prohibition before applying anything")
    else:
        item.update(status="MANUAL", reason="delegation override section not found in binary",
                    fix="upstream renamed or removed the gate; grep the unpacked JS for heron_brook, "
                        "brook_heron, and reduced-delegation successors, then re-derive the cut "
                        "(intent: edits/targets.json and spec/00; the original locator is "
                        "edits/adhoc-2.1.234.json)")
    plan.append(item)

    item = {"name": "subagent-delegation-opt-in", "kind": "adhoc"}
    # The shared preamble's sentence is found by digest (AGENT_PREAMBLE); what
    # makes an occurrence the template is the `${intro}. ` that leads into it.
    nested_matches = []
    for at in AGENT_PREAMBLE.find_all(blob):
        lead = AGENT_PREAMBLE_LEAD.search(blob[max(0, at - 64):at])
        if lead:
            nested_matches.append(lead.group(0) + blob[at:at + AGENT_PREAMBLE.length])
    if len(nested_matches) == 1:
        old = nested_matches[0]
        item.update(status="AUTO", old=old, new=old + b"\n\n" + SUBAGENT_DELEGATION_OPT_IN.encode("ascii"))
    else:
        item.update(status="MANUAL",
                    reason=f"shared Agent description preamble matches {len(nested_matches)}x",
                    fix="locate the shared Agent tool description before its compact/lean/full branches; "
                        "restore the subagent-only opt-in boundary there")
    plan.append(item)

    item = {"name": "bypass-auto-shell-block-invert", "kind": "adhoc"}
    m2s = shell_block_spans(blob)
    if len(m2s) > 1:
        item.update(status="MANUAL", reason=f"the stock shell block hashes {len(m2s)}x — ambiguous",
                    fix="the shell block is duplicated in this build; inspect the unpacked JS before patching")
        plan.append(item)
        return plan
    m2 = m2s[0] if m2s else None
    if m2 and m2[0]:
        var, body, start, end = m2
        interps = [g.decode() for g in INTERP_BYTES.findall(body)]
        shell, d1, d2, d3 = interps[0], interps[1], interps[2], interps[3]
        new2 = (f"{var.decode()}=`Prefer the dedicated {d1}, {d2}, and {d3} tools as usual. "
                f"Use the {shell} tool to batch "
                f"work when one invocation genuinely replaces several separate calls.`")
        item.update(status="AUTO", old=blob[start:end], new=new2.encode("latin-1"))
    elif m2:
        item.update(status="MANUAL",
                    reason="the stock shell block is present but is no longer assigned to a variable",
                    fix="the block's byte run no longer starts at an assignment target; read the span out "
                        "of the unpacked JS and re-derive what has to be replaced with it")
    else:
        item.update(status="MANUAL",
                    reason="no template literal in this build hashes to the stock auto-mode shell block "
                           "(SHELL_BLOCK_SHA256)",
                    fix="upstream rewrote the bypass-permissions shell block, or moved it out of a template "
                        "literal; diff it against the previous release's pristine, re-derive the inversion, "
                        "and re-measure SHELL_BLOCK_SHA256 "
                        "(intent: edits/targets.json and spec/00; the recorded shape and digest are "
                        "edits/adhoc-2.1.234.json)")
    plan.append(item)

    # Opus 5.5 in 2.1.280 chooses the relaxed Bash-first wording by MODEL
    # DEFAULT, before GrowthBook's cozy_teapot setting is considered. The
    # earlier shell-block inversion only edits the strict wording, so this
    # default silently bypasses it in SDK bypass mode. Cut just that model
    # fallback; explicit client-data and GrowthBook arms retain their choice.
    item = {"name": "shell-steer-model-default-cut", "kind": "adhoc"}
    shell_default = re.compile(
        rb'function ([\w$]+)\(\)\{return ([\w$]+)\([\w$]+\(\)\?\.\[[\w$]+\]\)\?\?'
        rb'(\(([\w$]+)\([^\n;{}]{1,100}\)\?"relaxed":void 0\))'
        rb'\?\?\2\([^\n;{}]{1,100}\)\?\?"strict"\}')
    defaults = list(shell_default.finditer(blob))
    if len(defaults) == 1:
        m = defaults[0]
        body = gate_predicate_body(blob, m.group(4), m.start())
        owner = re.search(rb'bashFirstSteerVariant=[\w$]+\(' + re.escape(m.group(1)) + rb'\)',
                          blob[m.end():m.end() + 10000])
        if body is None or not CAPABILITY_TEST.search(body) or not owner:
            item.update(status="MANUAL",
                        reason="the relaxed shell fallback cannot be tied to a model capability "
                               "and bashFirstSteerVariant",
                        fix="read the shell steer selector and its model predicate, then cut only "
                            "the model default while keeping client-data and GrowthBook arms")
        else:
            old = m.group(0)
            new = old.replace(b"??" + m.group(3), b"", 1)
            item.update(status="AUTO", old=old, new=new)
    elif defaults or b"opus_5_5_prompt_bundle" in blob:
        item.update(status="MANUAL",
                    reason=f"relaxed shell model-default selector matches {len(defaults)}x",
                    fix="read bashFirstSteerVariant in this bundle; the Opus 5.5 default must "
                        "not bypass the strict shell text without an explicit server arm")
    else:
        item.update(status="NOOP", reason="no Opus 5.5 shell model default in this build")
    plan.append(item)

    # ---- lean-models-reach-the-communication-fragment (C-06, 2026-08-25)
    #
    # [remark] This is the patch that makes the whole tranche reach him. The
    # selector `qAE` returns ONE of four communication blocks, chosen by model
    # capability, and the choice was never checked:
    #
    #   1. turn_updates gate            -> a one-line recap instruction  (never fires)
    #   2. zAE(t)||xyp(t)               -> outcome-first-communication-style  [OURS]
    #   3. ek(t)  (lean models)         -> one sentence about code comments
    #   4. fallthrough                  -> communication-style               [OURS]
    #
    # `zAE` requires `fable_5_mitigations`, which only claude-fable-5 carries.
    # `ek` is the lean-prompt flag, which claude-opus-5 DOES carry. So Opus 5
    # returned at branch 3 and never saw a communication block at all — not
    # overridden, absent. Verified three ways: the baked model catalog
    # (`capabilities:[...,"lean_prompt",...]`, no `fable_5_mitigations`), the
    # gate functions, and `~/.claude.json` (no `basalt_cove`, no `turn_updates`).
    #
    # Adding `||ek(e)` to branch 2's condition routes lean models there instead.
    # Opus 5 and Fable 5 then share one fragment; Sonnet and Haiku are untouched
    # (not lean, still branch 4). The alternative considered and rejected was
    # deleting branch 3's early return, which drops lean models to branch 4 —
    # that reaches a 190-character fragment instead of the one holding the
    # contract, and it would mean maintaining the prose in two places.
    #
    # The anchor derives four identifiers so it survives re-minification, but
    # the patched span deliberately STOPS at the backtick: one byte further and
    # it would overlap the outcome-first fragment's own span, making the two
    # patches order-dependent.
    # ---- disposition-floor-under-every-output-style (C-06 follow-up, 2026-08-25)
    #
    # [remark] The widest-reach edit in the tranche, and the only one that does
    # not care which model, which style or which branch is in play.
    #
    # `rHE` renders the selected output style into the prompt, and it is called
    # from exactly one place: `I2("output_style",()=>rHE(c))` in the SHARED
    # section list — not the lean/non-lean fork, not a model-gated branch. So
    # every model gets it, always. With no style selected it returns null and
    # the section is filtered out.
    #
    # We make it always return, and append the reader facts after whatever style
    # body is present. Consequences, in order of why they matter:
    #
    #   - A built-in style can no longer displace the disposition. `Concise`
    #     ends with "these rules win", which would otherwise outrank everything
    #     this project patches; now our text sits after it and answers on a
    #     different axis.
    #   - The operator keeps the /config knob. Style selects the MODE — how much
    #     narration, how proactive. The floor states WHO IS READING, plus the
    #     operator's default for what a reply keeps and in what form (2026-09-21,
    #     below), which a style may change. A mode cannot outrank a fact, which
    #     is why the closing sentence draws that line explicitly instead of
    #     escalating with a precedence claim.
    #   - It reaches Sonnet and Haiku subagent-adjacent sessions too, where the
    #     communication fragment is 190 characters and says none of this.
    #
    # ASCII only, deliberately: adhoc replacements are built with
    # `.encode("latin-1")` rather than through `enc_new`, so an em dash here
    # would raise; write it as `\\u2014`, which is ASCII here and an em dash in
    # the JS string the binary evaluates. String concatenation rather than a template literal, so the
    # replacement carries no `${` and no `$` for JS `replace()` to reinterpret
    # (the footgun TODO item 5 documents). Single quotes only, for the same
    # reason: the body is emitted inside a double-quoted JS string literal.
    #
    # 2026-08-29, item 21 follow-up: the "final message always reaches them"
    # paragraph is the restate rule, and it is the half of stock the 2026-08-25
    # tranche actually cost us. Stock's outcome-first fragment ends its
    # visibility paragraph by telling the model to "restate it in that final
    # message", inside a block gated on `fable_5_mitigations` — a mitigation
    # Anthropic ships FOR THIS MODEL. Our whole-span replacement kept "everything
    # belongs in the final message" and dropped the restate clause, so Fable 5
    # was free to conclude it had already delivered and point back at it. It
    # then did exactly that, live (spec/15 section 5.3).
    #
    # It goes in the floor rather than back into the fragment on the fragment's
    # own terms: that note says the reporting contract does not live there, and
    # saying it in both places is the C-03 rule-stacking this project refuses.
    # The floor also reaches further than stock's version ever did — every model,
    # every style, and headless sessions (verified on the gateway's own config).
    #
    # PHRASED AS A DELIVERY FACT, NOT A RESTATEMENT QUOTA (operator's ruling,
    # 2026-08-29, on a first draft that read "Anything you said earlier in the
    # turn may never have reached them, and nothing tells you when it did not
    # arrive. Restate the conclusions here..."). Three things were wrong with it:
    #
    #   - "Restate the conclusions" is a standing output-token tax on every turn,
    #     charged whether or not anything was lost.
    #   - A blanket restate rule reads as "carry your mid-turn conclusions with
    #     you", which spends attention on bookkeeping for the length of the turn.
    #   - "may ... and nothing tells you" describes the harness as a mystery. A
    #     model cannot act on a hedge. Stock's own version has the same defect.
    #
    # So state the mechanism and let the model derive the rest: the final message
    # always arrives, earlier prose may go out on a channel this reader's
    # transport drops, and the resulting rule is a PROHIBITION (never point back)
    # rather than a QUOTA (always restate). A prohibition costs nothing to obey
    # and needs no state held across the turn — at the moment of writing the
    # final message, everything it needs to check is already in context.
    #
    # The "may" that survives is load-bearing and names its variable: whether the
    # channel is dropped genuinely depends on the transport (interactive CC
    # renders narration blocks; headless drops them). That is a conditional, not
    # a hedge.
    #
    # 2026-09-21, TODO 30 / C-08: "nothing has to be cut, it has to be second"
    # is gone, and the floor now carries the one length signal the prompt has.
    # Masking `turn_updates` (item 27) removed stock's "Close with a short
    # recap" from Fable, which was the ONLY length pressure left on the lean
    # models — nothing in our text replaced it, and the floor's own permission
    # clause licensed the opposite. Measured on this machine's transcripts:
    # Fable closers went from a median of 60 words under the stock line to 299
    # under ours (57 against 263 inside one harness and one project), and Opus
    # 5, which never had the stock line, ran long throughout. That is spec/00
    # section 7.4: a suppressive clause was load-bearing. The replacement is a
    # SELECTION rule, not a cap (spec/15 section 6 still holds): it drops items,
    # it does not compress them, so it does not manufacture coinage. Its words
    # are the operator's own CLAUDE.md ("what changes what I do next", "work
    # that went right is not news") and HT-568's ("I'll ask for concrete details
    # when it matters to me"), which is also its legal exit. The form sentence
    # is his "no house format" rule. A style may still change both.
    # Evidence: notes/2026-09-21-length-regime.md.
    FLOOR = (
        "# How to report back\\n\\n"
        "The person reading this was elsewhere while you worked. The longer you work, "
        "the less of it they see: after a run of many tool calls, the last message is "
        "the only part they read, often hours later, against several other windows. "
        "Every name, number and piece of shorthand that arrived during the work is new "
        "to them.\\n\\n"
        "So open with where things stand, and name the thing you worked on. If they read "
        "two sentences and stop, they should know whether to act. Then, in a few "
        "short lines, what changed and what it touched: which repos, which remotes, "
        "which live systems. Then, as one-line bullets, what happens if they do nothing "
        "\\u2014 each one a consequence, not an explanation, and none carrying the fix. "
        "Suggestions and offers come last, where they can be skipped, and as statements, "
        "not questions. How the work was "
        "done, checked or diagnosed is not in the message: a turn of fifty tool calls "
        "that ended well is reported in the same few lines as a turn of two. When "
        "the ask was to find out, the analysis is the answer; when it was to fix, an "
        "analysis worth keeping goes in `reports/` in the active repo with one line "
        "pointing at it \\u2014 and most turns need none. They will ask for detail when "
        "it matters to them.\\n\\n"
        "The final message always reaches them. Prose written earlier in the turn may go "
        "out on a channel their transport drops, and you cannot tell from here which "
        "happened. So say what they need in the final message, once, and never point "
        "back at earlier output as though they read it.\\n\\n"
        "A phrase you coined this turn is not a name. Say what it means the first time "
        "you use it, or say what changed instead of naming it. The same goes for an "
        "abbreviation, and for a label that lives only in your record of the run.\\n\\n"
        "The reader usually sees this in a narrow and/or low-height terminal pane, in "
        "small text, beside other sessions. The exceptions are dedicated agent "
        "environments in a meta-harness, CC-CLI wrappers like T3 Code, chat interfaces "
        "like Discord or Telegram, and anywhere the surrounding context says otherwise. "
        "There, write for that medium.\\n\\n"
        "For the pane: short sentences, a thought per short paragraph, a blank line "
        "between them. A bullet is one line. Bold marks a word worth finding, not the "
        "opening of every bullet. An emoji or glyph is welcome where it marks "
        "something. For faces, use cats (\\ud83d\\ude38 \\ud83d\\ude39 \\ud83d\\ude3c "
        "\\ud83d\\ude40), not yellow smileys like \\ud83d\\ude03 or \\ud83d\\ude42. People "
        "and gestures are fine.\\n\\n"
        "Voice is welcome too: dry humour, play, warmth, when the work leaves room for "
        "them. Never in place of the facts, and never padding. A style may set how "
        "much you write, in what form, and how proactive you are. It does not change "
        "who is reading."
    )
    # 2026-10-08, operator: the 09-25 form sentence ("A list is for content that
    # is one; a paragraph does not open with a bold label") left the shape he
    # actually got: 3-5-line paragraphs with no break inside them, and bullets
    # whose bold lead carries a dense paragraph. In his words, the CLI's "small
    # text, many different panes" need "slightly shorter sentences spread across
    # more lines, less dense paragraph blocks, the occasional emoji & glyph",
    # and some voice. The pane sentence and its exceptions are his wording. The
    # footprint is now counted in lines, not sentences, and a bullet is one line,
    # matching spec/15 §5.4's width row. A blank line, not a single newline,
    # because CC's markdown renders a lone newline inside a paragraph as a soft
    # break (`breaks:!1`) and a GUI wrapper may show it as a space. Emoji are
    # left to the model ("let Claude be Claude"). Evidence and the inventory of
    # every form-steering layer: notes/2026-10-08-reply-form.md.
    # 2026-09-25, item 30 re-measured on win32 after living with the 09-21
    # sentence: the operator's verdict was "very verbose reporting again ...
    # tough to manage", with four sessions on screen closing in 200-400 words
    # of bold-labelled paragraphs. tools/closing_lengths.py --by prompt on this
    # machine: Opus 5.5 cli median 257 words, 66% bold-label bullets; Fable
    # 5.1 cli median 249. Installation was intact (markers, deletions, the
    # section in the delivered prompt), so the text itself is what under-
    # delivers. Two additions, both still selection rules under spec/15 §6 (no
    # whole-message cap): the report does not scale with the turn ("a turn of
    # fifty tool calls that ended well is reported in the same few sentences as
    # a turn of two"), which names the actual failure — verification narrated
    # at length — and the form sentence now names the shape the count found,
    # a paragraph opened by a bold label, which the models did not read as a
    # "list". Evidence: notes/2026-09-25-win32-length-recount.md.
    #
    # SUPERSEDED THE SAME DAY (2026-09-25, operator AMA on closing messages).
    # Both clauses above were aimed at the right failure and framed wrong:
    #   - "prose, in paragraphs that do not open with a bold label" banned the
    #     one list the operator wants: terse bullets of consequences. The form
    #     sentence now bans only the bold-label paragraph.
    #   - The keep-list ("what failed or was skipped, with the output; scope
    #     you left out; ...") selected by CATEGORY, and a category invites the
    #     account behind it ("with the output") — method narrative. He asked
    #     for CONSEQUENCES of doing nothing, one per bullet, no fix attached.
    #   - Neither the footprint (repos, remotes, live systems) nor a layer order
    #     was stated; he reads first line + bullets across parallel sessions.
    #   - "They will ask" left the transcript as the only outlet for an analysis,
    #     so the pull to explain landed in the closer. `reports/` in the active
    #     repo is the outlet — on a fix ask, rarely, one pointer line.
    # The paragraph is now the layered contract: state, footprint, consequence
    # bullets, suggestions last. Still a shape, not a cap (spec/15 section 6).
    # Evidence: notes/2026-09-25-report-contract-ama.md.
    # Same day, stock-conflict audit: offers go last "as statements, not
    # questions". CC's session-state classifier reads the closing message, and
    # its rubric (win32 2.1.280 file, ~201953300) says a closing offer that is
    # not a question is "done", while a closing question offering more work is
    # "a question to the owner" — the thread is then flagged
    # as waiting on him. Across parallel sessions that is a false "needs you".
    # 2026-09-11: "A style above" lost its "above". On 2.1.268 the style body is
    # not above anything — it arrives as an attachment message after the whole
    # system prompt (see the third shape below), so the old wording asserted a
    # position that is false on current CC, and a sentence that misdescribes the
    # prompt it sits in invites the model to discount the rest of it.
    #
    # DROPPING THE WORD WAS THE WHOLE EDIT. The first attempt wrote "An output
    # style", which is worse for a reason that has nothing to do with accuracy:
    # the operator is on record rejecting that product term — "output style
    # [which should be called BEHAVIOR STYLE, it has much larger impact than
    # just to do with output reporting style]" (TODO 23, 2026-09-07, verbatim).
    # Trading a positional locator for a vendor label is exactly the swap §1.1
    # says he does not want. Bare "A style" keeps his noun, claims no position,
    # and is true on all three shapes. No marker or anti-marker quotes this
    # sentence.
    # Three shapes, because three CC generations are live on the fleet at once.
    #
    # 2.1.241 and earlier inline the template literal in the renderer itself.
    # 2.1.251 split it: the renderer became a null-guard that delegates to a
    # two-argument helper, and the section list gained a `_d()?null:` gate in
    # front of the call. `_d()` is `staticSystemPromptEnabled`
    # (`CLAUDE_CODE_CARVED_SLATE`, flag default false) — off unless someone
    # turns it on, but if it is ever on, output_style and language are dropped
    # from the prompt wholesale and the floor goes with them. Nothing else in
    # the tranche depends on that gate.
    #
    # The 2.1.251 anchor carries the `var <v>="output_style";` prefix on
    # purpose: both `T8t` and `_ue` name unrelated functions elsewhere in the
    # bundle (an ANSI stripper and a Windows cert probe), so a body-only match
    # is not safe to assume unique. With the prefix it matches exactly once.
    #
    # 2.1.268 finished what that gate started: `staticSystemPromptEnabled` is
    # no longer consulted for these two sections because THE SECTIONS ARE GONE.
    # Measured on the 2.1.268 win32 blob against the 2.1.261 one:
    #
    #   - the shared dynamic list lost both `<h>("language",...)` and
    #     `<h>(<v>,...)` with `<v>="output_style"`; the renderer pair
    #     (`var <v>="output_style";function <f>(e){...}`) is not in the bundle
    #     at all, which is why both anchors above score 0 and this target went
    #     MANUAL;
    #   - the attachment generator that used to be gated on that flag
    #     (`if(!<p>(e)||!_d())return[]`) dropped the `||!_d()` half, so the
    #     `output_style_instructions` / `language` attachments now fire
    #     unconditionally. The style body reaches the model as a meta MESSAGE
    #     after the entire system prompt, not as a prompt section.
    #
    # So the old anchor's premise — one renderer, one shared-section caller,
    # floor appended after the style body — has no counterpart to re-derive.
    #
    # WHY NOT PATCH THE ATTACHMENT RENDERER INSTEAD. It is the obvious move and
    # it is wrong: `<f>(e){if(e===null)return "The output style was reset...";
    # return <tpl>(<sanitize>(e.name),e.prompt)}` only runs when an attachment
    # is emitted, and one is emitted only when a NON-DEFAULT style is present
    # and differs from the last one emitted. Measured on the 2.1.268 bundle:
    # the generator computes the attachment as
    # `!<style> && <configured>()!==<default> ? void 0 : $4e(<prev>,<cur>,<eq>)`
    # with `<configured>() = <ctx>()?.outputStyle||<default>`, and
    # `$4e(prev,cur,eq)` returns undefined when there is no previous attachment
    # and the current style is null. So a session on the default style never
    # calls the renderer at all, and the floor's whole point is that it has no
    # gate. Reach beats position; the floor is not a style, it is a fact about
    # the reader. (The sibling `output_style` attachment has its own
    # `==="default"` early return, but it carries the style NAME and a turn
    # reminder to a different renderer — do not mistake it for this one.)
    #
    # That is an argument against using it INSTEAD OF a section, not against
    # using it as well — see TODO 24 for the complementary variant, which is an
    # operator call because it is a precedence stance, not a repair.
    #
    # SO: carry the floor on an always-present section of the same shared list.
    # `context_management` is a bare string constant with no predicate in front
    # of it, and `q = await k$t(U)` is spliced into the returned array after
    # whichever of the lean (`y0s(I,n)`) and full branches ran, so the list
    # reaches both. It sits two entries past where the output_style section used
    # to sit: 2.1.261's order was `language, output_style, bg-session,
    # [scratchpad], context_management`. Appending to an existing registered
    # name rather than inventing one costs nothing and buys the `k$t` name-keyed
    # cache and one less list entry; a section body carrying two `#` headings is
    # exactly what the 2.1.251 shape already produced. (It is NOT that an
    # unknown name would be dropped — the resolver passes any name through
    # unchanged. An earlier draft of this comment claimed a hazard that does not
    # exist; the reasons above are the real ones.)
    #
    # The one thing this shape cannot do is sit after the style body, because
    # nothing in the system prompt can any more. The closing sentence carries
    # that weight alone now, which is why it no longer says "above".
    #
    # PRECEDENCE MATTERS. On 2.1.251..2.1.261 BOTH a renderer shape and the
    # section shape match; applying both would install the floor twice. The
    # renderer shapes are tried first and the section shape is the fallback,
    # so a build that still renders the style into the prompt keeps the
    # stronger placement and only a build that has removed it falls through.
    item = {"name": "disposition-floor-under-every-output-style", "kind": "adhoc"}
    m5s = list(re.finditer(rb"function ([\w$]+)\(e\)\{if\(e===null\)return null;"
                           rb"return`# Output Style: \$\{e\.name\}\n\$\{e\.prompt\}`\}", blob))
    m5n = list(re.finditer(rb'var ([\w$]+)="output_style";function ([\w$]+)\(e\)'
                           rb"\{if\(e===null\)return null;"
                           rb"return ([\w$]+)\(e\.name,e\.prompt\)\}", blob))
    m5c = ident_led(rb'([\w$]+)\("context_management",\(\)=>([\w$]+)\),', blob,
                    b'("context_management",()=>')
    if len(m5s) == 1:
        fn = m5s[0].group(1).decode()
        old5 = m5s[0].group(0)
        new5 = (f'function {fn}(e){{return(e===null?"":"# Output Style: "+e.name+"\\n"'
                f'+e.prompt+"\\n\\n")+"{FLOOR}"}}').encode("latin-1")
        item.update(status="AUTO", old=old5, new=new5)
    elif len(m5n) == 1:
        var, fn, render = (g.decode() for g in m5n[0].groups())
        old5 = m5n[0].group(0)
        new5 = (f'var {var}="output_style";function {fn}(e)'
                f'{{return(e===null?"":{render}(e.name,e.prompt)+"\\n\\n")'
                f'+"{FLOOR}"}}').encode("latin-1")
        item.update(status="AUTO", old=old5, new=new5)
    elif len(m5c) == 1:
        sec, body = (g.decode() for g in m5c[0].groups())
        old5 = m5c[0].group(0)
        new5 = (f'{sec}("context_management",()=>{body}+"\\n\\n"+"{FLOOR}"),').encode("latin-1")
        item.update(status="AUTO", old=old5, new=new5)
    else:
        item.update(status="MANUAL",
                    reason=f"output-style floor anchor matches {len(m5s)}x inline / "
                           f"{len(m5n)}x delegating / {len(m5c)}x section-list — "
                           "expected exactly 1 of one shape",
                    fix="upstream changed how the selected output style reaches the prompt; "
                        "re-derive against the shared section list — either the style renderer "
                        "and its single caller, or an always-present section to carry the floor "
                        "(spec/15-reporting-contract.md section 5.2)")
    plan.append(item)

    item = {"name": "lean-models-get-the-communication-fragment", "kind": "adhoc"}
    m3s = list(re.finditer(rb"if\(([\w$]+)\(t\)\|\|([\w$]+)\(t\)\)\{let ([\w$]+)=([\w$]+)\(t\);"
                           rb"return`# Communicating with the user", blob))
    m4s = list(re.finditer(rb"function ([\w$]+)\(e\)\{return [\w$]+\(\)\.leanPrompt\(e\)\}", blob))
    # 2.1.257 passes both the resolved model and the session object through the
    # full-communication branch, and calls the lean predicate directly in the
    # following one-line code-comment branch. Derive that exact predicate from
    # the selector instead of guessing through the accessor implementation.
    m3n = list(re.finditer(
        rb"if\(([\w$]+)\(([\w$]+),([\w$]+)\)\|\|([\w$]+)\(\2\)\)"
        rb"\{let ([\w$]+)=([\w$]+)\(\2,\3\);return`# Communicating with the user", blob))
    if len(m3s) == 1 and len(m4s) == 1 and not m3n:
        cond_a, cond_b, gap, gae = (g.decode() for g in m3s[0].groups())
        lean = m4s[0].group(1).decode()
        old3 = f"if({cond_a}(t)||{cond_b}(t)){{let {gap}={gae}(t);return`".encode("latin-1")
        new3 = f"if({cond_a}(t)||{cond_b}(t)||{lean}(e)){{let {gap}={gae}(t);return`".encode("latin-1")
        if blob.count(old3) != 1:
            item.update(status="MANUAL", reason=f"patched prefix is not unique ({blob.count(old3)}x)",
                        fix="widen the anchor; the selector shape changed in this build")
        else:
            item.update(status="AUTO", old=old3, new=new3)
    elif len(m3n) == 1 and not m3s:
        cond_a, model, ctx, cond_b, gap, gae = (g.decode() for g in m3n[0].groups())
        tail = blob[m3n[0].end():m3n[0].end() + 10_000]
        lean_matches = list(re.finditer(
            rb'if\(([\w$]+)\(' + re.escape(ctx.encode("latin-1"))
            + rb'\)\)return"Write code that reads like', tail))
        if len(lean_matches) != 1:
            item.update(status="MANUAL",
                        reason=f"lean branch after communication selector matches {len(lean_matches)}x — expected 1",
                        fix="find the predicate gating the code-comment-only branch and extend the 2.1.257 "
                            "selector derivation")
        else:
            lean = lean_matches[0].group(1).decode()
            old3 = (f"if({cond_a}({model},{ctx})||{cond_b}({model}))"
                    f"{{let {gap}={gae}({model},{ctx});return`").encode("latin-1")
            new3 = (f"if({cond_a}({model},{ctx})||{cond_b}({model})||{lean}({ctx}))"
                    f"{{let {gap}={gae}({model},{ctx});return`").encode("latin-1")
            if blob.count(old3) != 1:
                item.update(status="MANUAL", reason=f"patched prefix is not unique ({blob.count(old3)}x)",
                            fix="widen the anchor; the selector shape changed in this build")
            else:
                item.update(status="AUTO", old=old3, new=new3)
    elif m3s or m3n:
        item.update(status="MANUAL",
                    reason=f"communication-selector anchors match {len(m3s)}x legacy / {len(m3n)}x current",
                    fix="upstream restructured qAE's branches; re-trace which block each model family "
                        "receives before patching anything (spec/15-reporting-contract.md section 5.1)")
    else:
        item.update(status="MANUAL", reason="communication selector not found",
                    fix="locate the full communication block and its lean-model fallback, then re-derive "
                        "the selector (spec/15-reporting-contract.md section 5.1)")
    plan.append(item)

    # ---- willow-tern-writing-style-cut (2026-08-29, operator's ruling)
    #
    # 2.1.251 added `system-prompt-writing-for-the-user`, rendered by the
    # `willow_tern` section. It legislates prose shape: no em-dashes, counts
    # belong in a table or on their own line, bulleted lists for parallel
    # items, file and flag names kept out of prose. Three of those four are
    # the direct opposite of the operator's own CLAUDE.md, which asks for
    # exact names (`file.conf`, `commit-sha`, `--flag`) and no list, table or
    # header the content did not ask for. Operator's call, 2026-08-29: no.
    #
    # Cut rather than counter-write. A competing prose-shape block would just
    # be two sets of formatting rules arguing inside one prompt, and this
    # project's own fragments already say what shape to use. Same idiom as
    # `delegation-override-cut`: prepend `return null;` so the section
    # renderer yields nothing and the section list filters the entry out.
    #
    # Gate, for whoever reads this later:
    #   C_t(e) = CLAUDE_CODE_WILLOW_TERN            (env, forces ON)
    #         || clientData[tengu_willow_tern]      (server-pushed, the only
    #                                                OFF switch, not local)
    #         || (model has opus_5_prompt_bundle && flag(tengu_willow_tern))
    # The flag defaults false, so the section is dark today. It is patched
    # anyway: the env var can only force it on, the off switch is not ours to
    # set, and a server-side flip would land on Opus 5 silently.
    #
    # NOOP, not MANUAL, when the anchor is absent. It is absent on 2.1.241,
    # which two machines still run, and it would be absent again if upstream
    # dropped the section — in both cases there is nothing to suppress and
    # nothing wrong. This is the only target in the tranche whose correct
    # state can be "not present".
    item = {"name": "willow-tern-writing-style-cut", "kind": "adhoc"}
    m6s = list(re.finditer(rb"function ([\w$]+)\(e\)\{if\(!([\w$]+)\(e\)\)return null;"
                           rb'return ([\w$]+)\("tengu_willow_tern_applied"', blob))
    if len(m6s) == 1:
        fn, gate, tel = (g.decode() for g in m6s[0].groups())
        old6 = m6s[0].group(0)
        new6 = (f'function {fn}(e){{return null;if(!{gate}(e))return null;'
                f'return {tel}("tengu_willow_tern_applied"').encode("latin-1")
        item.update(status="AUTO", old=old6, new=new6)
    elif m6s:
        item.update(status="MANUAL", reason=f"willow_tern renderer matches {len(m6s)}x — ambiguous",
                    fix="the section renderer is duplicated in this build; inspect the unpacked JS "
                        "and decide which occurrence(s) to cut")
    else:
        item.update(status="NOOP",
                    reason="no willow_tern section in this build — nothing to suppress")
    plan.append(item)

    # ---- silent-turn-reminder-off (2026-09-29, operator's rule)
    #
    # The reminder ("the user hasn't heard from you in a while", every 5 silent
    # tool calls) is item 27's narration pressure in per-turn form, and the
    # tranche removes it. Operator, 2026-09-29: a server arm is accepted only
    # when it does not conflict with an established behavioural-outcome patch;
    # this one does, so it is switched off for every route, arms included.
    #
    # History, because the route kept moving. Item 31 (2026-09-21) cut the
    # capability out of the gate's model clauses and deliberately let server
    # arms through, on a broader reading of the 2026-09-11 ruling. 2.1.280 added
    # a second clause that armed it; 2.1.283 moved the clause into a local;
    # 2.1.284 armed it from Sonnet 5.5's own catalogue entry AND from client
    # data. Chasing each decider meant one locator per route and a green check
    # whenever a new route arrived. The capability has ONE consumer, the
    # predicate the attachment list calls:
    #
    #   function oVt(e){let n=Ue(e);return w1("silent_turn_reminder",n,e,
    #                                          a.CLAUDE_CODE_SILENT_TURN_REMINDER)}
    #
    # so `return!1&&` there switches it off whatever the env, the clauses, the
    # catalogue or the server say. The clause and catalogue parsers stay as
    # detectors (gate_drift, catalogue_drift); they no longer carry the mask.
    item = {"name": "silent-turn-reminder-off", "kind": "adhoc"}
    stock = list(re.finditer(REMINDER_PREDICATE, blob))
    if len(stock) == 1:
        old9 = stock[0].group(0)
        item.update(status="AUTO", old=old9, new=b"return!1&&" + old9[len(b"return "):])
    elif stock:
        item.update(status="MANUAL", reason=f"reminder predicate matches {len(stock)}x — ambiguous",
                    fix="inspect the unpacked JS; the capability has grown a second consumer")
    elif re.search(REMINDER_PREDICATE_CUT, blob):
        item.update(status="NOOP", reason="the reminder predicate is already switched off")
    else:
        item.update(status="MANUAL",
                    reason="no `return <gate>(\"silent_turn_reminder\",…,"
                           "….CLAUDE_CODE_SILENT_TURN_REMINDER)` predicate in this build",
                    fix="find what now decides whether the silent_turn_reminder attachment is "
                        "added (grep the attachment name) and re-derive the cut there. Until then "
                        "set CLAUDE_CODE_SILENT_TURN_REMINDER=0 in settings.json env")
    plan.append(item)

    # ---- git-commit-authority (2026-09-24, TODO 38, C-02)
    #
    # The Bash tool's compact `# Git` section (lean models, and every model
    # with a context window above the standard one: Opus 5.5 [1m], Fable) says
    # that commits and pushes wait until the user asks for them, and to branch
    # first on the default branch. It sits in the tool description, next to the action, and
    # it beat the delivery fragment's task authority: on 2026-09-24 a finished
    # port in the operator's private dotfiles repo was left uncommitted until
    # he asked, and his reply was that commit and push are implicit there. The
    # replacement says what his 2026-09-05 ruling says (notes/2026-09-05-task-
    # authority.md): committing finished work and pushing it to his own private
    # repositories is part of the task; a PR, or a push, to anyone else's
    # repository is the named exception. "Branch first" goes too: his repos
    # commit to their default branch, and the repository's own instructions and
    # history are the better guide than a blanket rule.
    #
    # The long `# Committing changes with git` block (200k-window models) and
    # its three agent-prompt copies say the same thing at far greater length;
    # they are C-02's follow-up. `status --delivered` reports this target as
    # DISPLACED where the long block holds the slot.
    #
    # The sentence also sits in the bundle's string table of template pieces,
    # unescaped and followed by no interpolation, so the anchor carries the
    # `${` that opens the next interpolation in the live template. That copy
    # stays stock after the apply, which is why this target has a marker but
    # no anti-marker.
    item = {"name": "git-commit-authority", "kind": "adhoc"}
    hits8 = [i for i in GIT_COMPACT.find_all(blob)
             if blob[i + GIT_COMPACT.length:i + GIT_COMPACT.length + 2] == b"${"]
    n8 = len(hits8)
    if n8 == 1:
        old8 = blob[hits8[0]:hits8[0] + GIT_COMPACT.length + 2]
        item.update(status="AUTO", old=old8, new=GIT_COMPACT_NEW.encode("latin-1") + b"${")
    elif n8 > 1:
        item.update(status="MANUAL", reason=f"compact git sentence matches {n8}x in template form — ambiguous",
                    fix="the Bash tool's compact # Git section is duplicated in this build; read each "
                        "occurrence in the unpacked JS before patching")
    else:
        item.update(status="MANUAL", reason="compact git sentence not found in template form",
                    fix="upstream rewrote or moved the Bash tool's compact # Git section; grep the unpacked "
                        "JS for 'return`# Git' and 'only when the user asks', then re-derive the "
                        "replacement (intent: TODO 38 and notes/complaints.md C-02)")
    plan.append(item)

    # ---- action-caution-outcome-line (2026-09-25, item 30, stock-conflict audit)
    #
    # The `action_caution` section (lean models) ends with an outcome sentence
    # whose failure clause reads "say so with the output". "With the
    # output" is the clause the floor's 2026-09-25 contract took out of its own
    # keep-list: it asks for the account behind a failure in the closing
    # message, which the operator's AMA puts in no layer. Only that clause
    # changes, to "say so and where the output is": the failure still gets
    # named, the output gets a pointer instead of a paste. The rest of the
    # sentence (skipped steps, plain "done and verified") is consonant.
    #
    # The sentence occurs twice in the win32 2.1.280 file: in the section
    # builder, and in a string table outside the live bundle window. The
    # anchor carries the builder's own prologue (`function <f>(e){if(!<lean>(e))
    # return null;return"For actions that are hard to reverse ...`), so the
    # table copy can neither make the plan ambiguous nor satisfy it. It stays
    # stock, which is why this target has a marker and no anti-marker. The
    # replacement is 8 bytes longer; the writer takes length changes (as
    # git-commit-authority's does), so nothing is padded.
    item = {"name": "action-caution-outcome-line", "kind": "adhoc"}
    m9s = list(re.finditer(rb'function [\w$]+\(e\)\{if\(![\w$]+\(e\)\)return null;return"'
                           + re.escape(ACTION_CAUTION_HEAD) + rb'(?:[^"\\]|\\.){0,1200}?'
                           + re.escape(ACTION_CAUTION_STOCK), blob))
    if len(m9s) == 1 and blob.count(m9s[0].group(0)) == 1:
        old9 = m9s[0].group(0)
        item.update(status="AUTO", old=old9,
                    new=old9[:-len(ACTION_CAUTION_STOCK)] + ACTION_CAUTION_NEW.encode("latin-1"))
    elif m9s:
        item.update(status="MANUAL", reason=f"action_caution builder with the outcome clause matches "
                                            f"{len(m9s)}x — ambiguous",
                    fix="the action_caution section builder is duplicated in this build; read each "
                        "occurrence in the unpacked JS before patching")
    else:
        item.update(status="MANUAL", reason="action_caution builder with 'say so with "
                                            "the output;' not found",
                    fix="upstream rewrote or moved the action_caution section; grep the unpacked JS "
                        "for 'For actions that are hard to reverse' and 'Report outcomes faithfully', "
                        "then re-derive the clause (intent: spec/15 section 5.5)")
    plan.append(item)

    # ---- bash-audience-note-result-not-output (2026-09-25, item 30, audit)
    #
    # The per-turn `bash_output_audience_note` reminder, injected after Bash
    # output the user's terminal truncates, ends with a sentence asking for
    # whatever the user needs to be put "in your reply". Left alone on
    # 2026-09-15 as consonant;
    # under the closing-message contract it pulls command output into the
    # closer. The last sentence becomes "Say what it means for them, not what
    # it printed." The first sentence (only you see the output) is the
    # delivery fact and stays. The note's gate is not touched: the reminder
    # still fires where upstream sends it, with our last sentence.
    #
    # Anchored on the whole string literal, quotes included, which occurs once
    # in the win32 2.1.280 file; no table copy, so the stock sentence is also
    # an anti-marker. 10 bytes shorter, not padded.
    item = {"name": "bash-audience-note-result-not-output", "kind": "adhoc"}
    tail = BASH_NOTE_TAIL.length
    m10s = [m for m in re.finditer(rb'"' + re.escape(BASH_NOTE_HEAD) + rb'(?:[^"\\]|\\.){0,400}?"', blob)
            if BASH_NOTE_TAIL.matches(m.group(0)[-tail - 1:-1])]
    if len(m10s) == 1:
        old10 = m10s[0].group(0)
        item.update(status="AUTO", old=old10,
                    new=old10[:-tail - 1] + BASH_NOTE_NEW.encode("latin-1") + b'"')
    elif m10s:
        item.update(status="MANUAL", reason=f"Bash audience note literal matches {len(m10s)}x — ambiguous",
                    fix="the bash_output_audience_note text is duplicated in this build; read each "
                        "occurrence in the unpacked JS before patching")
    else:
        item.update(status="MANUAL", reason="Bash audience note literal not found",
                    fix="upstream rewrote or moved bash_output_audience_note; grep the unpacked JS "
                        "for 'Only you see that command' and re-derive the last sentence (intent: "
                        "spec/15 section 5.5)")
    plan.append(item)
    return plan


# action-caution-outcome-line. Double-quoted JS string: ASCII, no `"`.
ACTION_CAUTION_HEAD = b"For actions that are hard to reverse"
ACTION_CAUTION_STOCK = b"say so with the output;"
ACTION_CAUTION_NEW = "say so and where the output is;"
# bash-audience-note-result-not-output. Double-quoted JS string: ASCII, no `"`.
BASH_NOTE_HEAD = b"Only you see that command's output"
BASH_NOTE_NEW = "Say what it means for them, not what it printed."


class StockSentence:
    """A stock sentence located by its opening words, its length and a SHA-256
    of its bytes, so that finding it does not mean quoting it.

    Same idea as SHELL_BLOCK_SHA256: a literal locator is the text it locates,
    and this repository publishes its tools without Anthropic's prompt text.
    The digest asserts every byte, so it fails closed exactly as the quoted
    literal did: an upstream edit of one character is a miss, and a miss is
    MANUAL. `prefix` only narrows the scan; it is a few words, not the text."""

    def __init__(self, prefix, length, sha256):
        self.prefix, self.length, self.sha256 = prefix, length, sha256

    def matches(self, data):
        return len(data) == self.length and hashlib.sha256(data).hexdigest() == self.sha256

    def find_all(self, blob):
        out, i = [], blob.find(self.prefix)
        while i != -1:
            if self.matches(blob[i:i + self.length]):
                out.append(i)
            i = blob.find(self.prefix, i + 1)
        return out

    def in_text(self, text):
        return bool(self.find_all(text.encode("utf-8")))

    def __str__(self):
        return self.prefix.decode("utf-8") + "..."


# The audience note's last sentence, the one the adhoc replaces. 58 bytes.
BASH_NOTE_TAIL = StockSentence(
    b"If the user needs", 58, "e7c04de92b146c7c008fa8ad690b0716328ba0c6f61bafafce44572aff503b75")
# The compact # Git section's commit sentence, the one git-commit-authority
# replaces. 81 bytes, measured on stock 2.1.280 linux-x64.
GIT_COMPACT = StockSentence(
    b"- Commit or push only", 81, "e756a970d2483f2b167c6e746845db6a29beed511eb05545e29b61a2c053d38e")
# The Agent tool description's shared preamble sentence, the one
# subagent-delegation-opt-in appends to. 68 bytes, measured on stock 2.1.294
# linux-x64. In the bundle it follows an interpolated intro, `${x}. `.
AGENT_PREAMBLE = StockSentence(
    b"Each agent type has", 68, "c04a05f67a159e4ba0ac4dc12acde4d5c8eb706b3f1592ead2d8904dab1d0a4a")
AGENT_PREAMBLE_LEAD = re.compile(rb"\$\{[\w$]+\}\. $")
# ASCII only: adhoc_patch refuses high bytes, and the payload lands inside a
# template literal, so no backtick and no "${".
GIT_COMPACT_NEW = ("- Committing finished work is part of the task, and so is pushing it to the "
                   "operator's own private repositories. Follow the repository's instructions and "
                   "history for branches and commit style. Pushing to anyone else's repository, or "
                   "opening a PR there, needs the operator's go-ahead.")


def span_apply(target, plan):
    """Write every AUTO item of `plan` into `target` (a staged copy, never the
    live binary). Dies on the first failure — a staged copy is disposable.
    Uniqueness was proven against the ORIGINAL blob; each patch mutates the
    file, so re-assert against the current bytes before every write. On linux
    every write also relocates the bundle, so the window is re-read per item
    rather than computed once."""
    for item in plan:
        if item["status"] != "AUTO":
            die(f"span_apply given a non-AUTO item: {item['name']} — analyze/gate first")
        n = live_blob(target).count(item["old"])
        if n != 1:
            die(f"{item['name']}: old bytes occur {n}x after earlier patches — aborting "
                f"(staged copy is disposable; a prior replacement collided with this one)")
        ok, out = adhoc_patch(target, item["old"], item["new"])
        print(f"  {item['name']:<52} {'OK' if ok else 'FAIL'}")
        if not ok:
            die(f"adhoc-patch failed for {item['name']}:\n{out[-500:]}")
        # "Replaced 1" proves a write happened, not that OUR bytes landed. JS
        # replace() honours $&, $1, $<name> and friends in the replacement, and
        # replacements do carry `$` (2.1.245 minified a tool identifier to `$r`,
        # giving a replacement containing `${$r}`). Assert the exact bytes — in
        # the LIVE window, since on linux this write just appended a new bundle
        # copy and left the pre-patch one in the file. Whole-file here would let
        # an earlier item's identical replacement, sitting in an abandoned copy,
        # answer for this one.
        if item["new"] not in live_blob(target):
            die(f"{item['name']}: tweakcc reported success but the exact replacement bytes are not "
                f"in the live bundle — a `$` sequence in the replacement was most likely "
                f"reinterpreted. Nothing was swapped; staging is disposable.")


# ------------------------------------------- instruction-effective scan
# Recall-oriented safety net for prompt-ish text OUTSIDE tweakcc's extraction
# (spec/00 §2.3: extraction is incomplete — reminders, tool descriptions and
# injected blocks live off the fragment map). Sentence-level so minified-
# identifier churn between builds doesn't register; still noisy (~1-1.5k
# sentences per release step, validated on 2.1.233->234), so the report
# buckets it separately and never gates on it.

SENT_RE = re.compile(rb"[A-Z][A-Za-z0-9 ,;:'\"()`$@{}\[\]/\\.&%#*+=<>_\n\t-]{50,600}?[.!?:](?=[ \n\"`)\]]|$)")
INTERP_RE = re.compile(r"\$\{[^{}]*\}")
CODEISH_RE = re.compile(r"=>|;var |;let |;const |\(\)\{|&&|\|\||===")


def instruction_sentences(path):
    """Normalized prose sentences literally present in a CC binary's live
    bundle — orphaned copies would otherwise contribute stale prose."""
    blob = live_blob(path)
    out = set()
    for m in SENT_RE.finditer(blob):
        s = m.group(0).decode("ascii", errors="ignore")
        prev = None
        while prev != s:                      # ${...${...}...} to fixpoint
            prev, s = s, INTERP_RE.sub("${}", s)
        s = re.sub(r"\s+", " ", s).strip()
        if CODEISH_RE.search(s):
            continue
        words = [w for w in s.split(" ") if w.isalpha()]
        if len(words) < 8 or sum(len(w) for w in words) / len(words) < 3.0:
            continue
        symbols = sum(1 for c in s if not (c.isalnum() or c in " ,.'\"-:;()`"))
        if symbols / len(s) < 0.05:
            out.add(s)
    return out


# ------------------------------------------------- stock prompt snapshots

def stock_body(entry):
    """Reconstruct a fragment's editable body from tweakcc's prompt JSON."""
    pieces, idents = entry["pieces"], entry["identifiers"]
    names = entry["identifierMap"]
    out = []
    for i, piece in enumerate(pieces):
        out.append(piece)
        if i < len(idents):
            out.append(names[str(idents[i])])
    return "".join(out)


def load_snapshot(version):
    for path in (SNAPSHOTS / f"prompts-{version}.json",
                 TWEAKCC_DIR / "prompt-data-cache" / f"prompts-{version}.json"):
        if path.exists():
            data = load_json(path, None)
            if data:
                return {e["id"]: e for e in data["prompts"]}
    return None


def ensure_prompt_data(version):
    """Prompt data for `version` in tweakcc's cache; None if upstream lags.
    Fetched directly from tweakcc's data repo (verified: the same URL and
    content tweakcc itself caches — spec/40), so it works for versions that
    are not installed yet. Falls back to running tweakcc if the direct
    fetch path ever drifts from tweakcc's."""
    cache = TWEAKCC_DIR / "prompt-data-cache" / f"prompts-{version}.json"
    if prompt_data_status(version) in ("cached", "available"):
        return cache
    run([*tweakcc_node(), "--list-system-prompts", version], timeout=180)
    return cache if cache.exists() else None


def snapshot(version):
    src = ensure_prompt_data(version)
    if not src:
        return None
    SNAPSHOTS.mkdir(exist_ok=True)
    dst = SNAPSHOTS / src.name
    if not dst.exists():
        shutil.copy2(src, dst)
        print(f"snapshotted stock prompts for {version} -> {dst.relative_to(HERE)}")
    return dst


# ------------------------------------------------- release channel client
# Endpoints verified first-hand 2026-08-21 (spec/40): plain-text channel
# pointers, per-version manifest with sha256 per platform, raw binaries.
# The pipeline never shells out to `claude update`/`install` — their channel
# handling has a bad upstream track record (#69319 et al.).

RELEASE_BASE = "https://downloads.claude.ai/claude-code-releases"
PROMPT_DATA_URL = ("https://raw.githubusercontent.com/Piebald-AI/tweakcc/"
                   "refs/heads/main/data/prompts/prompts-{ver}.json")


class NetworkTrouble(Exception):
    """Transport-level failure (offline, DNS, timeout) — not an HTTP status."""


def http_get(url, timeout=30, fatal=True):
    """GET url -> bytes; None on 404. Transport errors die when fatal,
    else raise NetworkTrouble so probes can degrade gracefully."""
    req = urllib.request.Request(url, headers={"User-Agent": "ccctl"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        die(f"HTTP {e.code} fetching {url}")
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        if fatal:
            die(f"cannot reach {url} ({e})")
        raise NetworkTrouble(str(e))


def cc_platform():
    """This machine's release-platform key (8 exist; detection mirrors
    Anthropic's install.sh, incl. musl and Rosetta)."""
    machine = (os.uname().machine if hasattr(os, "uname") else
               os.environ.get("PROCESSOR_ARCHITEW6432")     # x64 python on ARM64 win
               or os.environ.get("PROCESSOR_ARCHITECTURE", "AMD64"))
    m = machine.lower()
    if m.startswith("arm") and "64" not in m:
        die(f"no Claude Code build exists for 32-bit ARM ({machine})")
    arch = "arm64" if m in ("arm64", "aarch64") else "x64"
    if sys.platform == "win32":
        return f"win32-{arch}"
    if sys.platform == "darwin":
        try:
            r = subprocess.run(["sysctl", "-n", "sysctl.proc_translated"],
                               capture_output=True, text=True)
            if r.stdout.strip() == "1":     # Rosetta: the real hardware is arm64
                arch = "arm64"
        except OSError:
            pass
        return f"darwin-{arch}"
    musl = any(Path(p).exists() for p in
               ("/lib/libc.musl-x86_64.so.1", "/lib/libc.musl-aarch64.so.1"))
    if not musl:
        r = subprocess.run(["sh", "-c", "ldd /bin/ls 2>&1 | grep -q musl"],
                           capture_output=True)
        musl = r.returncode == 0
    return f"linux-{arch}-musl" if musl else f"linux-{arch}"


def discover(channel):
    """Latest version on `channel` ('stable' | 'latest')."""
    if channel not in ("stable", "latest"):
        die(f"unknown channel {channel!r} — 'stable' or 'latest'")
    body = http_get(f"{RELEASE_BASE}/{channel}")
    ver = (body or b"").decode("ascii", errors="replace").strip()
    if not re.match(r"^\d+\.\d+\.\d+", ver):
        die(f"channel pointer {channel} gave non-version content: {ver[:80]!r}")
    return ver


def fetch_manifest(version):
    body = http_get(f"{RELEASE_BASE}/{version}/manifest.json")
    if body is None:
        die(f"no release manifest for {version} — version typo, or too old/new?")
    return json.loads(body)


def download_binary(version, dest):
    """Download this platform's pristine binary for `version` to `dest`,
    sha256-verified against the release manifest."""
    plat = cc_platform()
    man = fetch_manifest(version)
    entry = man.get("platforms", {}).get(plat)
    if not entry:
        die(f"release {version} has no {plat} build (has: {', '.join(man.get('platforms', {}))})")
    url = f"{RELEASE_BASE}/{version}/{plat}/{entry['binary']}"
    print(f"downloading {url} ({entry['size'] / 1e6:.0f} MB)...")
    req = urllib.request.Request(url, headers={"User-Agent": "ccctl"})
    h = hashlib.sha256()
    tmp = dest.with_name(dest.name + ".part")
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as out:
            got = 0
            while chunk := r.read(1 << 20):
                out.write(chunk)
                h.update(chunk)
                got += len(chunk)
                if got % (64 << 20) < (1 << 20):
                    print(f"  ...{got >> 20} MB", flush=True)
    except (urllib.error.URLError, OSError) as e:
        tmp.unlink(missing_ok=True)
        die(f"download failed ({e})")
    except BaseException:                    # Ctrl-C etc: no stale .part
        tmp.unlink(missing_ok=True)
        raise
    if h.hexdigest() != entry["checksum"]:
        tmp.unlink(missing_ok=True)
        die(f"checksum mismatch for {url} — got {h.hexdigest()}, manifest says {entry['checksum']}")
    if got != entry["size"]:
        tmp.unlink(missing_ok=True)
        die(f"size mismatch for {url} — got {got}, manifest says {entry['size']}")
    os.replace(tmp, dest)
    dest.chmod(0o755)
    print(f"  verified sha256 + size, saved to {dest}")
    return dest


def carried_map(version):
    """The provenance line of a CARRIED-FORWARD prompt map for `version`, or
    None. spec/45 'Carried-forward map': when upstream has no map for a
    release, an earlier published map that measurably still describes every
    fragment we edit may be seeded under the new version's name. A seeded
    file is then preferred over the real one forever — the cache never
    re-downloads and `load_snapshot` prefers the snapshot copy — so the seed
    leaves this sidecar beside it, and `status` and `analyze` say so on every
    run until the real map replaces both files."""
    side = TWEAKCC_DIR / "prompt-data-cache" / f"prompts-{version}.carried"
    try:
        return side.read_text(encoding="utf-8").strip() or "carried (no provenance recorded)"
    except OSError:
        return None


def prompt_data_status(version):
    """'cached' | 'available' (downloaded now) | 'lagging' (upstream 404)
    | 'unknown' (network trouble; callers treat it like lagging)."""
    cache = TWEAKCC_DIR / "prompt-data-cache" / f"prompts-{version}.json"
    if cache.exists():
        return "cached"
    try:
        body = http_get(PROMPT_DATA_URL.format(ver=version), fatal=False)
    except NetworkTrouble:
        return "unknown"
    if body is None:
        return "lagging"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(body)     # same content tweakcc would cache
    return "available"


def tweakcc_latest():
    """Newest tweakcc on npm, or None if that can't be determined."""
    npm = shutil.which("npm")
    if not npm:
        return None
    r = run([npm, "view", "tweakcc", "version"], timeout=60)
    out = r.stdout.strip()
    return out if re.match(r"^\d+\.\d+\.\d+$", out) else None


# ------------------------------------------ tweakcc's own build, and overlays

OVERLAY_NAME = re.compile(r"^(?P<twk>\d+(?:\.\d+)*)-cc-(?P<cc>\d+(?:\.\d+)*)\.patch$")
# Build-state verdicts with nothing to report: no advisory, no report section,
# no fallback hint. Everything else is actionable and gets printed.
QUIET_BUILD = ("ok", "no-overlays", "unknown")


def ver_key(version):
    """Comparable tuple for a dotted version. Absent parts sort low, so
    `ver_key("2.1.9") < ver_key("2.1.10")` the way humans read releases."""
    return tuple(int(n) for n in re.findall(r"\d+", version or "")[:4])


def overlay_inventory(cfg):
    """This repo's tweakcc source overlays as (cc_key, cc_ver, twk_ver, path),
    oldest CC version first. The filename IS the record: `<twk>-cc-<cc>.patch`."""
    out = []
    for p in sorted((repo_dir(cfg) / "tools" / "tweakcc-overlays").glob("*.patch")):
        m = OVERLAY_NAME.match(p.name)
        if m:
            out.append((ver_key(m.group("cc")), m.group("cc"), m.group("twk"), p))
    return sorted(out)


def overlay_pin(cfg):
    """The upstream tweakcc commit the overlay build is pinned to, or None.

    Since 2026-09-13 the pin lives in `release.json` (`tweakcc.commit`), which
    `build_local_tweakcc.py` reads at import; a checkout from before that still
    carries the literal `EXPECTED_SHA = "..."` in the builder, so that is the
    fallback. Read rather than imported: one value, and a sibling script whose
    name is not an identifier is not worth import machinery."""
    release = load_json(repo_dir(cfg) / "release.json", None)
    pin = ((release or {}).get("tweakcc") or {}).get("commit")
    if isinstance(pin, str) and re.fullmatch(r"[0-9a-f]{7,40}", pin):
        return pin
    src = repo_dir(cfg) / "tools" / "build_local_tweakcc.py"
    if not src.exists():
        return None
    m = re.search(r'^EXPECTED_SHA\s*=\s*"([0-9a-f]{7,40})"',
                  src.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else None


def overlay_pin_ref(cfg):
    """The remote ref that carries the pin: `release.json` `tweakcc.fetchRef`,
    or `main`. A pin taken from a PR head is not on `main` once the PR is
    squash-merged (PR 993: main's TREE equals the pin's, its history does not),
    so `git fetch origin main` cannot make the pin checkable — measured on
    Linux 2026-09-13, where the stale-clone fix line failed with "unknown
    revision" until `pull/993/head` was fetched by hand."""
    release = load_json(repo_dir(cfg) / "release.json", None)
    ref = ((release or {}).get("tweakcc") or {}).get("fetchRef")
    return ref if isinstance(ref, str) and ref.strip() else "main"


def tweakcc_clone():
    """The git clone the installed `tweakcc` actually runs from, or None when it
    is an ordinary published install.

    Detected, never configured, like the `.bun` window and the platform slug:
    npm links a global package by symlink, so the module tree resolves INTO the
    clone, which carries `.git` next to a tweakcc `package.json`. Walking up
    from the resolved entry point beats any recorded path — the fleet keeps its
    three clones in three different places (spec/45) — and the package name is
    checked so an unrelated parent repo cannot answer for it."""
    argv = tweakcc_node()
    if len(argv) < 2:
        return None
    try:
        here = Path(argv[1]).resolve()
        for cand in (here, *here.parents):
            pkg = cand / "package.json"
            if not (cand / ".git").exists() or not pkg.is_file():
                continue
            if json.loads(pkg.read_text(encoding="utf-8")).get("name") == "tweakcc":
                return cand
    except (OSError, ValueError):
        return None
    return None


def clone_head(clone):
    """(sha, dirty) for a tweakcc clone, or (None, False). `safe.directory` is
    forced because a clone can be owned by another SID on win32 — this repo's
    `.work/tweakcc-dev` is, and git refuses those by default."""
    base = ["git", "-c", "safe.directory=%s" % clone.as_posix(), "-C", str(clone)]
    head = run([*base, "rev-parse", "HEAD"])
    if head.returncode != 0:
        return None, False
    dirty = run([*base, "status", "--porcelain", "--untracked-files=no"])
    return head.stdout.strip(), bool(dirty.stdout.strip())


def tweakcc_build_state(cfg, target):
    """Does the tweakcc that is about to run carry this repo's overlay for CC
    `target`? Advisory, never blocking.

    THIS IS THE FACT A CLEAN ANALYSIS DOES NOT CONTAIN. `analyze` never runs
    tweakcc's patch set, so "tweakcc current" and "prompt data available" can
    both be true while its feature patches are about to be rejected. Measured
    on win32 2026-09-08: the analysis read CLEAN, 11/11 AUTO, and 2.1.261 still
    needed an overlay for Anthropic's rebuilt model menu that no local build
    had yet. Getting this wrong costs the in-CC banner (a `tweakcc-fallback`
    assemble), never a wrongly-patched prompt — which is why it reports and
    does not gate, and why `overall` keeps meaning "our targets apply"."""
    blank = {"clone": None, "head": None, "pin": None, "overlayCc": None, "fix": None}
    if not cfg.get("repoPath"):
        # assemble() is legitimately called with a partial cfg (its tests do),
        # and this is a diagnostic: it reports or stays quiet, it never decides.
        return {**blank, "verdict": "unknown",
                "line": "no repo configured here — overlay state unknown"}
    inv = overlay_inventory(cfg)
    twk = run([*tweakcc_node(), "--version"]).stdout.strip()
    build = str(repo_dir(cfg) / "tools" / "build_local_tweakcc.py")
    if not inv:
        return {**blank, "verdict": "no-overlays",
                "line": "no tweakcc overlays in this repo — nothing to check"}

    newest_key, newest_cc, newest_twk, path = inv[-1]
    pin = overlay_pin(cfg)
    clone = tweakcc_clone()
    head, dirty = clone_head(clone) if clone else (None, False)
    common = {"clone": str(clone) if clone else None, "head": head, "pin": pin,
              "overlayCc": newest_cc}

    if ver_key(twk) > ver_key(newest_twk):
        return {**common, "verdict": "overlay-behind-tweakcc",
                "line": "installed tweakcc %s is NEWER than the newest overlay's %s (%s) — "
                        "it may be upstream now" % (twk, newest_twk, path.name),
                "fix": "confirm the repaired patch shipped in tweakcc %s; if it did, retire %s "
                       "and drop the pin in %s. If it did not, the overlay needs rebasing onto "
                       "%s before this update earns a banner." % (twk, path.name, build, twk)}
    if ver_key(target) > newest_key:
        return {**common, "verdict": "uncovered",
                "line": "newest overlay covers CC %s; target %s is newer — tweakcc's feature "
                        "patches are UNPROVEN on this build" % (newest_cc, target),
                "fix": "expect a possible `tweakcc-fallback` (no in-CC banner; the tranche still "
                       "verifies). If it happens, adapt %s for %s, rebuild (`python %s <clone> "
                       "--skip-install`) and re-`apply`. spec/45 lists the fleet's clones."
                       % (path.name, target, build)}
    if clone is None:
        return {**common, "verdict": "published",
                "line": "tweakcc %s is a published install, not a local clone — this repo's "
                        "overlay for CC %s is NOT in it" % (twk, newest_cc),
                "fix": "build locally so the overlay is present: clone tweakcc, detach at %s, "
                       "then `python %s <clone>`. spec/45 is the provisioning convention."
                       % (pin or "the pinned SHA", build)}
    if dirty:
        return {**common, "verdict": "dirty-clone",
                "line": "tweakcc clone %s has uncommitted tracked changes — what is built there "
                        "is not the reviewed tree" % clone,
                "fix": "inspect and revert them, then `python %s %s --skip-install`."
                       % (build, clone)}
    if not pin:
        # Without a pin the clone cannot be judged, and a guard that cannot
        # judge must say so rather than read "ok" (which is what it did when
        # the pin moved out of the builder's literal on 2026-09-13).
        return {**common, "verdict": "no-pin",
                "line": "no overlay pin found (release.json tweakcc.commit or the builder's "
                        "EXPECTED_SHA) — cannot tell whether clone %s carries %s"
                        % ((head or "?")[:7], path.name),
                "fix": "record the reviewed tweakcc commit in release.json (spec/45), then re-run."}
    if head and not head.startswith(pin) and not pin.startswith(head):
        return {**common, "verdict": "stale-clone",
                "line": "tweakcc clone is at %s, but %s is pinned to %s — the overlay for CC %s "
                        "is not in this build" % (head[:7], path.name, pin[:7], newest_cc),
                "fix": "git -C %s fetch origin %s && git -C %s checkout --detach %s && "
                       "python %s %s --skip-install"
                       % (clone, overlay_pin_ref(cfg), clone, pin, build, clone)}
    return {**common, "verdict": "ok",
            "line": "local clone at %s == overlay pin, %s covers CC %s >= target %s"
                    % ((head or "?")[:7], path.name, newest_cc, target), "fix": None}


# --------------------------------------------------- stage, assemble, swap

def versions_dirs():
    dirs = [Path.home() / ".local" / "share" / "claude" / "versions"]
    if os.environ.get("LOCALAPPDATA"):
        dirs.append(Path(os.environ["LOCALAPPDATA"]) / "claude" / "versions")
    return [d for d in dirs if d.is_dir()]


# How many to keep per class. Not one number for all three: they are not worth
# the same thing, and a uniform rule was keeping 322 MB of files that cannot
# serve any rollback at all (operator, 2026-08-25: "that's like... a TON").
#
#   previous live binaries  1  One step back to the last patched build is a
#                              real move: a round goes bad, you revert without
#                              re-running apply. Two steps back is not — from
#                              there you want STOCK, which lives elsewhere.
#                              All of these are patched, so none is a rollback
#                              to stock, and each is re-creatable by one apply.
#   pristine per version    1  Genuinely valuable (this is what apply reads),
#                              but the INSTALLED version's copy is protected
#                              unconditionally on top of this count, so `1`
#                              here means one *older* version kept for a
#                              downgrade. Re-downloadable, ~380 MB, verified.
#   parked tweakcc backups  0  Copies of our own output, parked precisely
#                              BECAUSE they are useless as a backup. Keeping
#                              any is keeping a file whose only property is
#                              that nothing may restore from it.
PRUNE_KEEP = {
    "previous live binaries": 1,
    "pristine per version": 1,
    "parked patched tweakcc backups": 0,
}


def parked_groups(markers):
    """The three classes of ~320 MB rollback artifact this tool leaves behind,
    each newest-first. Only ever suffixed copies — never the live binary and
    never a bare `versions/<ver>` directory, which is CC's own install."""
    live = launcher_path()
    groups = [
        ("previous live binaries", sorted(
            live.parent.glob(live.name + ".pre-swap*"),
            key=lambda p: p.stat().st_mtime, reverse=True)),
        ("pristine per version", sorted(
            (p for d in versions_dirs() for p in d.glob("*.stock")),
            key=lambda p: p.stat().st_mtime, reverse=True)),
        ("parked patched tweakcc backups", sorted(
            TWEAKCC_DIR.glob("native-binary.backup.patched-*"),
            key=lambda p: p.stat().st_mtime, reverse=True)),
    ]
    return [(name, files) for name, files in groups if files]


def prune_protected(markers):
    """Artifacts that must survive whatever the retention count says.

    The `.stock` for the version that is INSTALLED is not an old copy of
    anything — it is the pristine source `apply` and `analyze` read on POSIX
    (`pristine_source`). Pruning it by age would trade a few hundred MB for a
    384 MB re-download and a machine that cannot patch while offline."""
    keep = set()
    try:
        ver = cc_version()
    except SystemExit:
        return keep
    for d in versions_dirs():
        cand = d / (ver + ".stock")
        if cand.exists():
            keep.add(cand.resolve())
    return keep


def is_stock(path, markers):
    """No edit markers present == stock (pristine) binary. With no markers to
    check, the answer is unknowable — treat as NOT stock so nothing patched
    ever gets trusted (or parked) as pristine.

    Whole-file on purpose: a binary whose live bundle is clean but which still
    carries our text in an orphaned copy is not something to park as pristine."""
    return not binary_contains(path, markers, live=False) if markers else False


def pristine_source(version, markers):
    """A stock binary for `version` on this machine, or None.
    Order: versions/<ver> (the native installer keeps these pristine — unless
    a past in-place patch dirtied it), then tweakcc's native-binary.backup."""
    for vdir in versions_dirs():
        # after a POSIX ccctl update, versions/<ver> is patched and the
        # pristine lives alongside as versions/<ver>.stock (spec/40)
        for cand in (vdir / version, vdir / (version + ".stock")):
            if cand.exists() and is_stock(cand, markers):
                return cand
    backup = TWEAKCC_DIR / "native-binary.backup"
    if backup.exists() and is_stock(backup, markers):
        # backup is a copy of whichever binary tweakcc last touched — gate on version
        probe = STAGING / ("verback.exe" if sys.platform == "win32" else "verback")
        STAGING.mkdir(exist_ok=True)
        shutil.copyfile(backup, probe)
        probe.chmod(0o755)
        out = run([str(probe), "--version"]).stdout.strip()
        probe.unlink(missing_ok=True)
        if out.startswith(version):
            return backup
    return None


def staged_name(version):
    return STAGING / (f"claude-{version}-assembled" + (".exe" if sys.platform == "win32" else ""))


def pristine_name(version):
    return STAGING / (f"claude-{version}-pristine" + (".exe" if sys.platform == "win32" else ""))


def launcher_path():
    """~/.local/bin/claude(.exe) — fixed, no XDG override (verified, spec/40)."""
    return Path.home() / ".local" / "bin" / ("claude.exe" if sys.platform == "win32" else "claude")


def macos_resign(pristine, staged):
    """Re-sign a patched Mach-O so it will launch (Apple Silicon SIGKILLs an
    invalid signature), carrying the STOCK binary's identifier and
    entitlements forward.

    Why not --preserve-metadata=entitlements: by the time we get here tweakcc's
    repackMachO has already stripped the Developer ID signature and ad-hoc
    re-signed with `codesign -s - -f`, so there is nothing left on the file to
    preserve — measured on a real arm64 Mac 2026-08-21: stock carries 5
    entitlements, the preserve form produced 0, and the identifier had become
    the staging filename. Both are read off the pristine binary instead.

    Hardened runtime is deliberately NOT restored: ad-hoc signatures have no
    Team ID, and without the runtime flag JIT is permitted by default (which
    is what the Bun payload needs)."""
    if not shutil.which("codesign"):
        die("codesign not found — install Xcode Command Line Tools; a byte-patched "
            "binary is SIGKILLed unsigned on Apple Silicon")
    ident = None
    r = run(["codesign", "-dv", str(pristine)])
    for line in (r.stdout + r.stderr).splitlines():
        if line.startswith("Identifier="):
            ident = line.split("=", 1)[1].strip()
    ents = run(["codesign", "-d", "--entitlements", "-", "--xml", str(pristine)]).stdout
    cmd = ["codesign", "--force", "--sign", "-"]
    entfile = None
    if ents.lstrip().startswith("<?xml"):
        entfile = staged.with_name(staged.name + ".entitlements.plist")
        entfile.write_text(ents, encoding="utf-8")
        cmd += ["--entitlements", str(entfile)]
    if ident:
        cmd += ["--identifier", ident]
    try:
        r = run(cmd + [str(staged)])
        if r.returncode != 0:
            die(f"codesign failed on staged binary:\n{r.stderr[-500:]}")
        r = run(["codesign", "--verify", "--deep", "--strict", str(staged)])
        if r.returncode != 0:
            die(f"codesign verify failed on staged binary:\n{r.stderr[-500:]}")
    finally:
        if entfile:
            entfile.unlink(missing_ok=True)
    # prove the carry-forward actually happened rather than trusting the flags
    got = run(["codesign", "-d", "--entitlements", "-", "--xml", str(staged)]).stdout
    want = set(re.findall(r"com\.apple\.security\.[a-z.-]+", ents))
    lost = want - set(re.findall(r"com\.apple\.security\.[a-z.-]+", got))
    if lost:
        die(f"re-signing dropped entitlements {sorted(lost)} — the patched binary would "
            f"lose those capabilities; nothing was swapped")
    print(f"codesigned (ad-hoc, id={ident or 'default'}, {len(want)} entitlements carried)")


def backup_is_patched(markers):
    """Is tweakcc's native-binary.backup one of OUR binaries? None when the
    question does not apply (no backup, or no markers to judge by).

    Cached in ccctl-state.json against the file's (size, mtime, marker set),
    because the honest answer costs a full ~340 MB scan and the cheap case —
    a STOCK backup — is the one that cannot short-circuit: binary_contains
    stops early only once it has found every needle, and a stock backup
    contains none of them. `status --quiet` runs from a SessionStart hook on
    every machine, so an uncached scan there would be a 340 MB read at the
    start of every session."""
    bk = TWEAKCC_DIR / "native-binary.backup"
    if not bk.exists() or not markers:
        return None
    st = bk.stat()
    key = f"{st.st_size}:{st.st_mtime_ns}:{hash(tuple(markers)) & 0xffffffff}"
    state = load_json(STATE, {})
    cached = state.get("backupVerdict")
    if cached and cached.get("key") == key:
        return cached["patched"]
    patched = not is_stock(bk, markers)
    state["backupVerdict"] = {"key": key, "patched": patched}
    save_json(STATE, state)
    return patched


def park_patched_backup(markers):
    """Move tweakcc's `native-binary.backup` aside if it is a PATCHED binary.

    Two things reach for that file believing it is stock: `--apply`, which
    restores from it before patching, and `--restore`, which is one of the two
    rollback paths `status` advertises. Once one of our rounds has run, it can
    be a copy of our own output — the file is kept (rollback artifacts are kept
    here, not pruned), just moved where nothing will restore it by accident.

    No markers to check means the answer is unknowable; leave it alone rather
    than park something on a guess."""
    backup = TWEAKCC_DIR / "native-binary.backup"
    if not markers or not backup.exists() or is_stock(backup, markers):
        return None
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%dT%H%M%S")
    parked = backup.with_name(f"native-binary.backup.patched-{stamp}")
    os.rename(backup, parked)
    print(f"  tweakcc backup was a PATCHED binary — parked as {parked.name} "
          f"(it would have been restored over the staged copy)")
    return parked


def feature_patch_ids(output):
    """Read tweakcc's own feature inventory without selecting prompt IDs."""
    plain = re.sub(r"\x1b\[[0-9;]*m", "", output)
    ids = re.findall(r"^  ([a-z][a-z0-9-]+)\s*$", plain, re.M)
    if "patches-applied-indication" not in ids:
        raise ValueError("tweakcc --list-patches format changed; cannot isolate feature patches")
    return list(dict.fromkeys(ids))


def tweakcc_apply_to(staged, markers):
    """Run tweakcc's OWN patch set (themes, QoL, and the 'tweakcc patches
    applied' banner) into `staged`, before our string patches.

    Returns `(True, detail)` when the patch set landed and `(False, reason)`
    when THIS BUILD rejected it — that is a survivable outcome the caller can
    fall back from, not a fault. Genuine setup problems (no tweakcc config at
    all) still die() here, because falling back would hide them.

    The banner is the point: it is the visible indicator inside CC, and
    because it can only reach the live binary through this pipeline — which
    refuses to swap unless every one of our markers verifies — banner present
    means our patches are present, and banner absent means they are not.
    Operator rule, 2026-08-24.

    Two hazards, both handled here (measured on win32 2026-08-24):
      * `--apply` has no path flag; it patches whatever `ccInstallationPath`
        in tweakcc's config points at (and restores from its backup first).
        We point it at the staged copy and put the config back afterwards,
        so the live install is never touched.
      * `--apply` also writes any fragments in ~/.tweakcc/system-prompts/.
        On linux patch.sh copies OUR edits there, which would pre-modify the
        text our span patches then look for. The directory is hidden for the
        duration so tweakcc applies code patches only.
      * `--apply` RESTORES FROM native-binary.backup before patching, and that
        backup is whatever binary tweakcc last touched — which, after one of
        our rounds, is a PATCHED one. Measured on win32 2026-08-25: the backup
        was byte-identical (SHA256 + mtime) to the previous round's output, so
        the second apply had its staged pristine copy silently replaced by
        already-patched bytes and every target "stopped locating". A patched
        backup is worthless as a rollback source anyway — `pristine_source`
        already refuses it (is_stock gate) — so it is parked aside rather than
        left where --apply and `--restore` will both reach for it."""
    cfgfile = TWEAKCC_DIR / "config.json"
    prompts = TWEAKCC_DIR / "system-prompts"
    hidden = TWEAKCC_DIR / "system-prompts.ccctl-hidden"
    saved = cfgfile.read_text(encoding="utf-8") if cfgfile.exists() else None
    if saved is None:
        die(f"no {cfgfile} — run tweakcc once interactively to create its config")
    park_patched_backup(markers)
    moved = False
    try:
        data = json.loads(saved)
        data["ccInstallationPath"] = str(staged)
        cfgfile.write_text(json.dumps(data, indent=2), encoding="utf-8")
        if prompts.is_dir():
            if hidden.exists():
                shutil.rmtree(hidden, ignore_errors=True)
            os.rename(prompts, hidden)
            moved = True
        inventory = run([*tweakcc_node(), "--list-patches"], timeout=60)
        if inventory.returncode:
            die("tweakcc --list-patches failed; cannot isolate feature patches")
        try:
            ids = feature_patch_ids(inventory.stdout)
        except ValueError as exc:
            die(str(exc))
        # Hiding the prompt directory alone is insufficient: tweakcc startup
        # regenerates it, then tries to rewrite every stock prompt. Select the
        # dynamic feature inventory; tweakcc still honors each config condition.
        r = run([*tweakcc_node(), "--apply", "-y", "--patches", ",".join(ids)], timeout=900)
        out = (r.stdout + r.stderr).strip()
        # Keep diagnostics even when tweakcc returns success with partial
        # failures; the banner alone cannot identify which feature drifted.
        ANALYSIS.mkdir(parents=True, exist_ok=True)
        diagnostic = ANALYSIS / (staged.name + ".tweakcc.log")
        diagnostic.write_text(out + "\n", encoding="utf-8")
        if r.returncode != 0:
            print(f"  tweakcc: --apply exited {r.returncode} — this build rejects the patch set")
            return False, f"tweakcc --apply exited {r.returncode}: {out[-400:]}"
        if "with some failures" in out:
            print(f"  tweakcc: applied with optional patch failures; inspect {diagnostic}")
        else:
            print("  tweakcc: patch set applied")
        # [remark] This check used to sit AFTER the try/finally, where it was
        # unreachable — every path above returns, so it never ran once. Found
        # 2026-08-26 while turning `tweakccPatches` on for darwin; confirmed by
        # walking the AST, not by reading. It matters exactly when the knob is
        # on, which until now was never on the machine anyone was looking at.
        #
        # It also no longer die()s. Exiting 0 while leaving no marker is a build
        # that took the patch set and dropped it silently — which is precisely
        # what the fallback in assemble() exists for (694f1db), and falling back
        # yields a correct disposition-only binary instead of no binary at all.
        # The absent banner is then recorded in state as `tweakccFallback`, so
        # `status` explains it rather than reporting it as our patches missing.
        if not binary_contains(staged, ["tweakcc"]):
            print("  tweakcc: exited 0 but left no marker in the staged binary")
            return False, "tweakcc --apply exited 0 but left no patches-applied marker"
        return True, out[-400:]
    finally:
        cfgfile.write_text(saved, encoding="utf-8")
        if moved:
            if prompts.exists():
                shutil.rmtree(prompts, ignore_errors=True)
            os.rename(hidden, prompts)


def stage_copy(version, source):
    """A fresh, disposable staging copy of `source`. Overwrites any previous
    one — the point of staging is that nothing here is precious."""
    STAGING.mkdir(exist_ok=True)
    staged = staged_name(version)
    shutil.copyfile(source, staged)
    staged.chmod(0o755)
    return staged


def tweakcc_pass(cfg, version, staged, plan):
    """Run tweakcc's own patch set into `staged` and re-locate `plan` against
    the result. Returns `(replan, None)` when the patch set landed and every
    target still locates, or `(None, reason)` when this build rejects it.

    Re-locating is not optional: `plan` was located against the pristine bytes
    and tweakcc has since rewritten parts of the binary. Verified 2026-08-24
    that all targets survive tweakcc's code patches — this keeps that true
    rather than assuming it."""
    ok, detail = tweakcc_apply_to(staged, repo_markers(cfg))
    if not ok:
        return None, detail
    data = load_snapshot(version)
    blob = live_blob(staged)
    replan = plan_fragments(blob, data, repo_edits(cfg)) + plan_adhocs(blob)
    replan = [i for i in replan if i["status"] != "NOOP"]
    bad = [i for i in replan if i["status"] != "AUTO"]
    if bad:
        for i in bad:
            print(f"  {i['name']}: {i['status']} — {i.get('reason', '')}")
        reason = (f"{len(bad)} of {len(replan)} targets no longer locate after tweakcc's "
                  f"patch set ({', '.join(i['name'] for i in bad[:3])}"
                  f"{', …' if len(bad) > 3 else ''})")
        # ALL of them failing is a different animal from SOME of them failing.
        # A rejected patch set damages the bundle here and there; a staged copy
        # that was replaced wholesale — which is what a restored patched backup
        # does — stops matching everything at once, including the adhoc regexes,
        # which do not depend on prompt data at all. Say which one this looks
        # like instead of letting the fallback file it under "build says no".
        if len(bad) == len(replan) and binary_contains(staged, repo_markers(cfg)):
            reason += ("; NOTE: every target failed AND the staged copy already carries our "
                       "markers — this is a pre-patched binary in staging, not a rejected "
                       "patch set. Check tweakcc's native-binary.backup")
        # A third cause is possible here and is NOT a reason to fall back: on a
        # platform that should have a .bun window, failing to parse one means
        # the search is matching orphaned bundle copies (TODO item 7), and
        # dropping tweakcc's patches would not help. Say so rather than let the
        # fallback quietly "fix" it.
        if bun_window(staged) is None and sys.platform not in ("darwin", "win32"):
            reason += "; NOTE: no .bun window parsed on a platform that should have one — " \
                      "suspect orphaned-copy matching (TODO item 7), not the patch set"
        return None, reason
    if len(replan) != len(plan):
        # Not a patch-set problem: plan_fragments emits one item per edit file,
        # so a count change means the repo's edit set moved between analyze and
        # apply. Falling back would apply a different tranche than was analyzed.
        die(f"re-plan holds {len(replan)} targets, the analysis had {len(plan)} — refusing")
    return replan, None


def assemble(cfg, version, source, plan, stock=False):
    """Copy `source` to staging, apply tweakcc's patch set then `plan`, verify
    markers + launch. Returns the staged path; disposable on failure.

    `stock=True` builds the B arm: `plan` must be empty, and the verification
    inverts — every marker must be ABSENT. Same staging, same launch test, same
    swap; only the assertion differs, because "no edits landed" has to be
    proven on this binary rather than assumed from having skipped the step."""
    if stock and plan:
        die(f"assemble(stock=True) given {len(plan)} plan items — refusing to half-build an arm")
    staged = stage_copy(version, source)
    fallback = None
    if cfg.get("tweakccPatches", TWEAKCC_PATCHES_DEFAULT):
        if stock:
            # No re-location step: there is no plan to re-locate, and a target
            # that stopped locating after tweakcc's pass says nothing about a
            # binary we are deliberately not patching. Falling back over it
            # would drop the banner for a reason that does not apply here.
            ok, why = tweakcc_apply_to(staged, repo_markers(cfg))
            replan = [] if ok else None
        else:
            replan, why = tweakcc_pass(cfg, version, staged, plan)
        if replan is not None:
            plan = replan
        else:
            # This build rejects tweakcc's feature patches. That is survivable:
            # the patch set is wanted only for its in-CC banner, and OUR patches
            # do not depend on it. Retry once without it, from a FRESH copy of
            # the pristine source — the one on disk has had a failed tweakcc
            # pass over it and is not trustworthy as a base. Staging is
            # disposable, so this costs a file copy, not a round.
            #
            # Why this exists: macOS hit exactly this on 2026-08-25 and it cost
            # a round to diagnose, because the operator had to recognise the
            # symptom and hand-set a config key. Deriving `tweakccPatches` from
            # the platform (TWEAKCC_PATCHES_DEFAULT) fixed the known case; this
            # fixes the general one, including builds nobody has met yet. It
            # tests the actual build rather than predicting from the platform.
            print(f"  tweakcc patch set rejected by this build: {why}")
            print("  falling back: re-staging WITHOUT tweakcc's patches (ours do not need them)")
            staged = stage_copy(version, source)
            fallback = why
    span_apply(staged, plan)
    if sys.platform == "darwin" and cfg.get("macosCodesign", True):
        macos_resign(Path(source), staged)
    markers = repo_markers(cfg)
    present = binary_contains(staged, markers)
    anti = repo_antimarkers(cfg)
    if stock:
        # The inverse assertion. A marker found here means either the source
        # was not pristine after all, or upstream has independently converged
        # on wording this repo also uses — both are worth stopping for, and
        # neither is something to shrug past into a swap.
        if present:
            for m in sorted(present):
                print(f"  OUR TEXT IN A STOCK BUILD: {m[:70]}")
            die(f"{len(present)} marker(s) present in a binary that should carry none — the source "
                f"may not be pristine, or upstream now ships this wording. Nothing was swapped; "
                f"staging is disposable")
        # Anti-markers are stock sentences the tranche deletes, so a stock arm
        # should carry all of them. Reported, never gating: upstream deleting
        # one of them itself is news about their prompt, not a build failure —
        # and it is exactly the kind of news this arm exists to collect.
        back = binary_contains(staged, anti) if anti else set()
        for a in sorted(set(anti) - back):
            print(f"  NOTE: stock sentence absent from stock {version}: {a[:70]}")
    else:
        missing = [m for m in markers if m not in present]
        if missing:
            for m in missing:
                print(f"  MARKER MISSING in staged: {m[:70]}")
            die("staged binary failed marker verification — nothing was swapped; staging is disposable")
        revived = sorted(binary_contains(staged, anti)) if anti else []
        if revived:
            for a in revived:
                print(f"  ANTI-MARKER PRESENT in staged: {a[:70]}")
            die("staged binary still carries stock text this tranche deletes — nothing was swapped; "
                "staging is disposable")
        # The pristine bytes are right here, so this is the cheapest place to
        # ask the question the marker count cannot: was the sentence ever in
        # the stock build? One that is not has been scoring as a deletion that
        # holds (see antimarkers_live). Reported, never gating — the binary is
        # correct; the instrument is what needs the edit.
        in_stock = binary_contains(source, anti) if anti else set()
        for a in (a for a in anti if a not in in_stock):
            print(f"  NOTE: DEAD anti-marker — absent from stock {version} too, so it proves "
                  f"nothing: {a[:60]}")
    # `--version` below never loads an application chunk, so a module that no
    # longer parses sails through it (macOS, 2.1.270: see
    # changed_module_parse_errors). Parse every changed module first.
    broken = changed_module_parse_errors(source, staged)
    if broken is None:
        print("  NOTE: changed-module parse check skipped (no module table or no node) — "
              "`--version` alone does not load the chunks a session needs")
    elif broken:
        for name, err in broken:
            print(f"  MODULE DOES NOT PARSE: {name}: {err}")
        die(f"{len(broken)} changed module(s) no longer parse — `--version` would still pass and "
            f"every real session would crash. Nothing was swapped; staging is disposable")
    # Exit code and stderr are part of the verdict: a Bun abort() before any
    # output gives an empty --version, and "gave ''" alone sent the 2026-09-05
    # Linux round (notes/2026-09-05-bun-source-alignment.md) hunting in the
    # wrong place. A hung binary must not hang the apply either.
    try:
        launch = run([str(staged), "--version"], timeout=60)
    except subprocess.TimeoutExpired:
        die("staged binary did not answer --version within 60 s — nothing was swapped")
    out = launch.stdout.strip()
    if launch.returncode != 0 or not out.startswith(version):
        die(f"staged binary does not launch cleanly (exit {launch.returncode}, --version gave "
            f"{out!r}, stderr: {launch.stderr[-1500:]!r}) — nothing was swapped")
    verdict = (f"0/{len(markers)} markers (stock arm, as intended)" if stock
               else f"{len(markers)}/{len(markers)} markers")
    print(f"staged binary assembled + verified: {verdict}, launches as {out.splitlines()[0]}")
    # Record how this binary was built, so `status` can explain an absent banner
    # instead of reporting it as a disagreement — with the knob on, banner-absent
    # is normally the signal that our patches are missing, and after a fallback
    # it is not. Cleared on any assemble where the patch set did land, so a
    # tweakcc fix retires the note by itself.
    state = load_json(STATE, {})
    if fallback:
        state["tweakccFallback"] = {"ccVersion": version, "reason": fallback,
                                    "when": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")}
        # Name the most likely cause rather than leaving the next reader to
        # correlate a stack trace with a build they cannot see. The overlay
        # state is the cause that has actually bitten (CC 2.1.261 rebuilt the
        # model menu); when it is fine, this says nothing and the platform
        # note below still applies.
        try:
            bs = tweakcc_build_state(cfg, version)
        except Exception as exc:                        # noqa: BLE001 - see below
            # Broad on purpose. We are already on the failure path of the patch
            # set; a diagnostic that raises here would turn a survivable
            # fallback into no binary at all, which is exactly backwards.
            bs = {"verdict": "unknown", "line": f"overlay state unreadable ({exc!r})", "fix": None}
        hint = ("" if bs["verdict"] in QUIET_BUILD else
                f"\n- likely cause ({bs['verdict']}): {bs['line']}\n- fix: {bs['fix']}")
        log_entry("tweakcc-fallback", f"- CC `{version}`: tweakcc's patch set was rejected by this "
                                      f"build; assembled without it.\n- reason: {fallback}{hint}\n"
                                      f"- consequence: no in-CC banner on this binary. "
                                    + (f"Verified stock: 0/{len(markers)} markers.\n" if stock else
                                       f"Our markers verified {len(markers)}/{len(markers)} as usual.\n")
                                    + (f"- if this platform keeps rejecting it, that belongs in "
                                       f"`TWEAKCC_PATCHES_DEFAULT`, not in a config file."))
        print("  NOTE: this binary has no tweakcc banner — the in-CC indicator is unavailable on it.")
        if bs["verdict"] not in QUIET_BUILD:
            print(f"  likely cause ({bs['verdict']}): {bs['line']}\n  fix: {bs['fix']}")
    else:
        state.pop("tweakccFallback", None)
    save_json(STATE, state)
    return staged


def safe_put(src, dst, may_move=False):
    """Land `src` at `dst` without ever exposing a partial file: write to
    dst.part (copy, or rename when told the source is disposable and may
    simply move), then atomically replace. A crash mid-copy leaves only a
    .part file, which nothing ever trusts."""
    part = dst.with_name(dst.name + ".part")
    if may_move:
        try:
            os.rename(src, part)
        except OSError:                      # cross-device: fall back to copy
            shutil.copyfile(src, part)
    else:
        shutil.copyfile(src, part)
    part.chmod(0o755)
    os.replace(part, dst)


def swap_in(live, staged):
    """Replace the live binary with the verified staged one. The running exe
    can be renamed on every OS even while locked for writes (win32)."""
    # A previously parked binary may still be EXECUTING (Windows refuses to
    # delete a running image, and a session started before the last swap holds
    # exactly that inode) — take the next free slot instead of forcing it.
    park = live.with_name(live.name + ".pre-swap")
    if park.exists():
        try:
            park.unlink()
        except OSError:
            n = 1
            while park.with_name(f"{park.name}.{n}").exists():
                n += 1
            park = park.with_name(f"{park.name}.{n}")
            print(f"note: previous {live.name}.pre-swap is in use (running session?) — "
                  f"parking as {park.name}")
    os.rename(live, park)
    try:
        try:
            os.rename(staged, live)
        except OSError:                      # cross-device staging dir
            shutil.move(str(staged), str(live))
        live.chmod(0o755)
    except BaseException:
        os.replace(park, live)               # put the old binary back, even
        raise                                # over a partially-copied file
    return park


def place(cfg, version, staged, pristine, label="patched"):
    """Put the verified `staged` binary live, per platform launcher semantics
    (spec/40), keeping a pristine copy of `version` on disk. Returns the path
    it now lives at. `label` only names it in the output — a stock-arm swap
    that announced "patched binary" would be a false line in the one report
    the operator reads."""
    vdirs = versions_dirs()
    vdir = vdirs[0] if vdirs else Path.home() / ".local" / "share" / "claude" / "versions"
    vdir.mkdir(parents=True, exist_ok=True)
    vfile = vdir / version
    launcher = launcher_path()
    markers = repo_markers(cfg)
    pristine = Path(pristine)

    if sys.platform == "win32":
        # invariant: versions/<ver> stays pristine; the launcher copy is patched.
        if not vfile.exists():
            safe_put(pristine, vfile, may_move=pristine.parent == STAGING)
            print(f"pristine {version} installed at {vfile}")
        if launcher.exists():
            park = swap_in(launcher, staged)
            print(f"swapped {label} binary into {launcher} (old parked at {park.name})")
        else:
            launcher.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staged), str(launcher))
            print(f"placed {label} binary at {launcher}")
        return launcher

    # POSIX: the launcher is a symlink; it must resolve to a patched file.
    # Patched binary becomes versions/<ver>; pristine parked at versions/<ver>.stock.
    # Order matters — the live symlink may resolve through vfile at every step:
    # park the pristine by COPY first (never rename vfile away), then land the
    # staged binary next to vfile (same directory = same filesystem) and
    # os.replace() it in atomically, then repoint the launcher. A failure at
    # any point leaves either the old or the new binary fully live, never a
    # dangling symlink or a half-copied file.
    stock = vfile.with_name(vfile.name + ".stock")
    if not stock.exists():
        src = None
        if vfile.exists() and is_stock(vfile, markers):
            src = vfile
        elif pristine.exists() and pristine != vfile:
            src = pristine
        if src is not None:
            safe_put(src, stock, may_move=src.parent == STAGING)
    tmp = vfile.with_name(vfile.name + ".new")
    if tmp.exists():
        tmp.unlink()
    try:
        os.rename(staged, tmp)
    except OSError:                          # staging on another filesystem
        shutil.move(str(staged), str(tmp))
    tmp.chmod(0o755)
    os.replace(tmp, vfile)                   # atomic within versions/
    launcher.parent.mkdir(parents=True, exist_ok=True)
    ltmp = launcher.with_name(f"{launcher.name}.tmp.{os.getpid()}")
    if ltmp.exists() or ltmp.is_symlink():
        ltmp.unlink()
    os.symlink(vfile, ltmp)                  # atomic symlink activation, the
    os.replace(ltmp, launcher)               # same dance the installer does
                                             # (replace, not rename: the old
                                             # launcher normally exists)
    print(f"{label} binary at {vfile} (pristine at {stock.name}), {launcher} -> {vfile.name}")
    return vfile


# --------------------------------------------------------------- analyze

def prompt_map_diff(base, target, ours):
    """added/removed/changed fragment ids between two prompt-data snapshots,
    with the ones we edit flagged."""
    added = sorted(set(target) - set(base))
    removed = sorted(set(base) - set(target))
    changed = sorted(k for k in set(base) & set(target)
                     if stock_body(base[k]) != stock_body(target[k]))
    return {"added": added, "removed": removed, "changed": changed,
            "oursChanged": sorted(set(changed) & ours),
            "oursRemoved": sorted(set(removed) & ours)}


def fragment_review_matches(review, entry, edit):
    """A review applies only to the exact stock/replacement pair, on any OS.

    Hash reconstructed prompt text rather than JSON metadata or checkout line
    endings. Saving an edit alone must never acknowledge unseen upstream drift.
    """
    if not isinstance(review, dict) or not review.get("reason"):
        return False
    digest = lambda text: hashlib.sha256(text.encode("utf-8")).hexdigest()
    return (review.get("stockSha256") == digest(stock_body(entry))
            and review.get("editSha256") == digest(edit_text(edit)))


def analyze_core(cfg, target_ver):
    """Everything `analyze` and `update` need to decide: tweakcc status,
    staged pristine binary, per-target verdicts, diffs, report files.
    Returns the report dict plus the pristine path and the executable plan."""
    installed = cc_version()
    markers = repo_markers(cfg)
    edits = repo_edits(cfg)
    ours = {f.stem for f in edits}

    print(f"analyze: installed {installed} -> target {target_ver} ({cc_platform()})")
    pstat = prompt_data_status(target_ver)
    twk_now = run([*tweakcc_node(), "--version"]).stdout.strip()
    twk_new = tweakcc_latest()
    print(f"tweakcc        : {twk_now} installed"
          + (f", {twk_new} on npm" if twk_new and twk_new != twk_now else " (current)"))
    print(f"prompt data    : {pstat} for {target_ver}"
          + (" — tweakcc's data repo lags this CC version (usually hours)" if pstat == "lagging" else "")
          + (" — network unreachable, working from local caches only" if pstat == "unknown" else ""))
    carried = carried_map(target_ver)
    if carried:
        print(f"  CARRIED FORWARD: {carried}. The stock-prompt diff below is against that "
              f"map, not {target_ver}'s own; the edited fragments were measured identical.")
    build_state = tweakcc_build_state(cfg, target_ver)
    print(f"tweakcc build  : {build_state['line']}")
    if build_state["verdict"] not in QUIET_BUILD:
        # Loud, but never a verdict — tweakcc_build_state's docstring says why
        # this must not be able to flip `overall`.
        print(f"  ADVISORY ({build_state['verdict']}) — {build_state['fix']}")
    if ver_key(target_ver) < ver_key(installed):
        print(f"NOTE: target {target_ver} is OLDER than the installed {installed} — "
              f"this is a DOWNGRADE")

    pristine = pristine_source(target_ver, markers)
    if not pristine:
        STAGING.mkdir(exist_ok=True)
        dest = pristine_name(target_ver)
        if not dest.exists():
            download_binary(target_ver, dest)
        pristine = dest
    print(f"pristine source: {pristine}")
    blob = live_blob(pristine)

    target_data = load_snapshot(target_ver)
    frag_plan = plan_fragments(blob, target_data, edits) if target_data else []
    adhoc_plan = plan_adhocs(blob)

    base_data = load_snapshot(installed)
    if not base_data and installed != target_ver:
        # fresh machine: without the installed version's stock snapshot the
        # whole REVIEW mechanism is blind — try to fetch it before degrading
        if prompt_data_status(installed) in ("cached", "available"):
            base_data = load_snapshot(installed)
    base_missing = installed != target_ver and not base_data
    pmap = prompt_map_diff(base_data, target_data, ours) if (base_data and target_data
                                                             and installed != target_ver) else None

    # upstream-changed detection for the fragments we edit
    upstream_changed = set(pmap["oursChanged"]) if pmap else set()
    upstream_removed = set(pmap["oursRemoved"]) if pmap else set()
    reviews = load_json(repo_dir(cfg) / "edits" / "reviews.json", {})
    edit_by_name = {f.stem: f for f in edits}

    verdicts = []
    for item in frag_plan + adhoc_plan:
        if item["status"] == "AUTO" and item["name"] in upstream_removed:
            item.update(verdict="MANUAL", reason="fragment removed upstream",
                        fix=f"the stock fragment no longer exists in {target_ver}; decide whether the edit "
                            f"is obsolete or must move — see `ccctl.py diff stock {installed} {target_ver}`")
        elif item["status"] == "AUTO" and item["name"] in upstream_changed:
            if fragment_review_matches(reviews.get(item["name"]),
                                       target_data[item["name"]], edit_by_name[item["name"]]):
                item.update(verdict="AUTO", reason="stock/replacement pair reviewed in edits/reviews.json")
            else:
                item.update(verdict="REVIEW",
                        reason="upstream changed this fragment's stock text; auto-apply would discard their change",
                        fix=f"read the hunk in the analysis report (or `ccctl.py diff stock {installed} "
                            f"{target_ver} {item['name']}`), fold anything worth keeping into edits/{item['name']}.md, "
                            f"record the stock/replacement hashes and reason in edits/reviews.json (see edits/README.md), "
                            f"then re-run analyze; `update --policy force` explicitly bypasses REVIEW")
        else:
            item["verdict"] = item["status"]
        v = {k: val for k, val in item.items() if k not in ("old", "new")}  # bytes stay out of the report
        if v["verdict"] == "MANUAL" and pmap and (
                item["name"] in upstream_removed or "not in tweakcc prompt data" in v.get("reason", "")):
            near = [a for a in pmap["added"]
                    if difflib.SequenceMatcher(None, a, item["name"]).ratio() > 0.5]
            if near or pmap["added"]:
                v["fix"] += (f" | candidate successors among the {len(pmap['added'])} fragments "
                             f"added in {target_ver}: " + ", ".join(near[:5] or pmap["added"][:5]))
        verdicts.append(v)
    if not target_data:
        for f in edits:
            verdicts.append({"name": f.stem, "kind": "fragment", "verdict": "BLOCKED",
                             "reason": f"tweakcc prompt data lags {target_ver}",
                             "fix": "wait (upstream: 'within a few hours') and re-run analyze; "
                                    "the adhoc patches are independent of prompt data"})
    if base_missing:
        print(f"WARNING: no stock prompt snapshot for installed {installed} — "
              f"upstream-change (REVIEW) detection is BLIND on this run")
        verdicts.append({"name": "(base-snapshot)", "kind": "meta", "verdict": "REVIEW",
                         "reason": f"no stock prompt snapshot for installed {installed}; "
                                   f"upstream rewrites of edited fragments would go unnoticed",
                         "fix": f"fetch it (`ccctl.py snapshot {installed}`, needs tweakcc data "
                                f"for {installed}) and re-run analyze; or accept blindness "
                                f"deliberately with `update --policy force`"})

    # instruction-effective scan, pristine vs pristine
    scan = None
    base_pristine = pristine_source(installed, markers) if installed != target_ver else None
    if base_pristine:
        print("instruction scan (sentence-level, recall-oriented)...")
        A = instruction_sentences(base_pristine)
        B = instruction_sentences(pristine)
        scan = {"base": len(A), "target": len(B),
                "added": sorted(B - A), "removed": sorted(A - B)}
        print(f"  {len(scan['added'])} sentences added, {len(scan['removed'])} removed "
              f"(full lists in the report)")
    elif installed != target_ver:
        print("instruction scan skipped: no pristine binary for the installed version on this machine")

    order = {"MANUAL": 0, "BLOCKED": 0, "REVIEW": 1, "AUTO": 2, "NOOP": 3}
    overall = ("BLOCKED" if not target_data else
               "MANUAL" if any(v["verdict"] == "MANUAL" for v in verdicts) else
               "REVIEW" if any(v["verdict"] == "REVIEW" for v in verdicts) else "CLEAN")
    verdicts.sort(key=lambda v: order.get(v["verdict"], 3))

    report = {
        "when": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "machine": sys.platform, "ccPlatform": cc_platform(),
        "installed": installed, "target": target_ver,
        "tweakcc": {"installed": twk_now, "npmLatest": twk_new, "promptData": pstat},
        "tweakccBuild": build_state,
        "overall": overall, "verdicts": verdicts,
        "planTotal": len(frag_plan) + len(adhoc_plan),
        "baseSnapshot": "MISSING" if base_missing else "present",
        "promptMap": pmap, "instructionScan": scan,
        "pristine": str(pristine), "repoCommit": repo_commit(cfg),
    }
    write_report(cfg, report, base_data, target_data)
    # executable plan: verdict-filtered (REVIEW is mechanically appliable and
    # only reaches span_apply under `update --policy force`)
    plan = {"AUTO": [i for i in frag_plan + adhoc_plan if i["verdict"] == "AUTO"],
            "REVIEW": [i for i in frag_plan + adhoc_plan if i["verdict"] == "REVIEW"]}
    return report, Path(pristine), plan


def write_report(cfg, report, base_data, target_data):
    """analysis/<old>-to-<new>.{json,md} — the md is written for whoever
    (operator or agent) has to act on it (spec/40 D6)."""
    ANALYSIS.mkdir(exist_ok=True)
    stem = f"{report['installed']}-to-{report['target']}"
    save_json(ANALYSIS / f"{stem}.json", report)

    L = [f"# CC update analysis: {report['installed']} -> {report['target']}",
         f"", f"- when: {report['when']}  machine: {report['machine']} ({report['ccPlatform']})",
         f"- overall verdict: **{report['overall']}**",
         f"- tweakcc: {report['tweakcc']['installed']} installed"
         + (f" ({report['tweakcc']['npmLatest']} on npm)" if report['tweakcc']['npmLatest'] else ""),
         f"- prompt data for target: {report['tweakcc']['promptData']}",
         f"- tweakcc build: {report.get('tweakccBuild', {}).get('line', 'n/a')}",
         f"- repo commit: {report['repoCommit']}", ""]

    L.append("## Verdicts\n")
    L.append("| target | kind | verdict | reason |")
    L.append("| --- | --- | --- | --- |")
    for v in report["verdicts"]:
        L.append(f"| {v['name']} | {v['kind']} | {v['verdict']} | {v.get('reason', '')} |")
    L.append("")

    todo = [v for v in report["verdicts"] if v["verdict"] not in ("AUTO", "NOOP")]
    if todo:
        L.append("## What to do (operator or agent)\n")
        for i, v in enumerate(todo, 1):
            L.append(f"{i}. **{v['name']}** ({v['verdict']}): {v.get('reason', '')}")
            L.append(f"   - fix: {v.get('fix', 'n/a')}")
            L.append(f"   - done when: `ccctl.py analyze {report['target']}` shows this target AUTO")
        L.append("")
    else:
        L.append("## What to do\n\nNothing — every target is AUTO. "
                 f"`ccctl.py update {report['target']}` performs the full staged update.\n")

    bs = report.get("tweakccBuild") or {}
    if bs.get("verdict") not in (None, *QUIET_BUILD):
        L.append("## tweakcc's own patch set — advisory, does not block\n")
        L.append(f"- state: **{bs['verdict']}** — {bs['line']}")
        L.append(f"- fix: {bs['fix']}")
        L.append("- why this is not a verdict: a rejected patch set costs the in-CC banner"
                 " (a `tweakcc-fallback` assemble), never a wrongly-patched prompt. `analyze`"
                 " never runs the patch set, so a CLEAN overall says nothing about it.\n")

    if report.get("promptMap"):
        p = report["promptMap"]
        L.append(f"## Stock prompt map {report['installed']} -> {report['target']}\n")
        L.append(f"- {len(p['added'])} added, {len(p['removed'])} removed, {len(p['changed'])} changed")
        for k in p["oursChanged"]:
            L.append(f"- CHANGED + WE EDIT IT: **{k}** (hunk below)")
        for k in p["oursRemoved"]:
            L.append(f"- REMOVED + WE EDIT IT: **{k}**")
        L.append(f"- full listing: `ccctl.py diff stock {report['installed']} {report['target']}`\n")
        for k in p["oursChanged"]:
            if base_data and target_data:
                diff = "".join(difflib.unified_diff(
                    stock_body(base_data[k]).splitlines(keepends=True),
                    stock_body(target_data[k]).splitlines(keepends=True),
                    f"{k}@{report['installed']}", f"{k}@{report['target']}"))
                L.append(f"### upstream hunk: {k}\n\n```diff\n{diff}\n```\n")

    if report.get("instructionScan"):
        s = report["instructionScan"]
        L.append("## Instruction-effective scan (outside tweakcc's extraction — noisy, recall-oriented)\n")
        L.append(f"- sentences: {s['base']} -> {s['target']}, "
                 f"{len(s['added'])} added, {len(s['removed'])} removed")
        L.append(f"- full lists in {stem}.json (`instructionScan.added/removed`); skim for anything")
        L.append("  that reads like new default-behavior instructions worth a complaint or an edit\n")

    (ANALYSIS / f"{stem}.md").write_text("\n".join(L), encoding="utf-8")
    print(f"report: {ANALYSIS / (stem + '.md')} (+ .json)")


# ------------------------------------------------------------- commands

def cmd_init(args):
    if CONFIG.exists():
        die(f"{CONFIG.name} already exists here")
    which("git")
    binary = claude_binary()
    print(f"toolchain ok: git, tweakcc {run([*tweakcc_node(), '--version']).stdout.strip()}, "
          f"claude {cc_version()} at {binary}")
    cfg = {"remote": args.url, "repoPath": "repo"}
    dest = HERE / cfg["repoPath"]
    if not dest.exists():
        print(f"sparse-cloning {args.url} (corpus and baseline stay on the server)...")
        r = run(["git", "clone", "--filter=blob:none", "--no-checkout", args.url, str(dest)])
        if r.returncode != 0:
            die(f"clone failed:\n{r.stderr}")
        run(["git", "-C", str(dest), "sparse-checkout", "set", "edits", "tools", "spec", "notes"])
        r = run(["git", "-C", str(dest), "checkout"])
        if r.returncode != 0:
            die(f"checkout failed:\n{r.stderr}")
    save_json(CONFIG, cfg)
    log_entry("init", f"- remote: `{args.url}`\n- machine: `{sys.platform}`\n"
                      f"- tweakccPatches (platform default, not written to config): "
                      f"`{str(TWEAKCC_PATCHES_DEFAULT).lower()}`")
    print(f"initialized. repo at {dest}, config in {CONFIG.name}")
    # Deliberately not written into ccctl.json: keeping it in the tool means a
    # later fix (or a newly-broken build) reaches every machine on `git pull`
    # instead of needing three configs edited. Named here so it is not a surprise.
    print(f"tweakccPatches : {str(TWEAKCC_PATCHES_DEFAULT).lower()} (platform default for "
          f"{sys.platform}; override in {CONFIG.name} to change)")


def require_cfg():
    cfg = load_json(CONFIG, None)
    if not cfg:
        die(f"no {CONFIG.name} here — run: ccctl.py init <git-url>")
    return {**CONFIG_DEFAULTS, **cfg}


def pull_divergence(cfg):
    """ahead/behind against the tracked upstream, with both one-line logs, or
    None when there is nothing to compare against."""
    up = git(cfg, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if up.returncode != 0:
        return None
    up = up.stdout.strip()
    counts = git(cfg, "rev-list", "--left-right", "--count", f"{up}...HEAD")
    parts = counts.stdout.split()
    if counts.returncode != 0 or len(parts) != 2:
        return None
    return {"upstream": up, "behind": int(parts[0]), "ahead": int(parts[1]),
            "local": git(cfg, "log", "--oneline", f"{up}..HEAD").stdout.strip(),
            "remote": git(cfg, "log", "--oneline", f"HEAD..{up}").stdout.strip()}


def divergence_message(repo, d, stderr=""):
    """The message a --ff-only refusal deserves, or None when divergence is not
    what happened (so the caller falls back to git's own words).

    Pure so it can be pinned by tests: this text is the whole repair. git's own
    hint stops at "you need to either merge or rebase", which does not know
    that THIS repo reconciles machine rounds by merge and records the
    resolutions in the merge commit — so without this, every fleet round costs
    the next agent the same archaeology (measured 2026-09-08)."""
    if not d or not (d["ahead"] and d["behind"]):
        return None

    def indent(text):
        return "  " + (text or "(none)").replace("\n", "\n  ")

    return (f"git pull cannot fast-forward: this checkout and {d['upstream']} have BOTH moved — "
            f"{d['ahead']} local, {d['behind']} remote.\n\n"
            f"local only ({d['ahead']}):\n{indent(d['local'])}\n\n"
            f"{d['upstream']} only ({d['behind']}):\n{indent(d['remote'])}\n\n"
            f"This repo reconciles two machines' rounds by MERGE, not rebase — the merge commit "
            f"is where\nthe resolutions get recorded (precedent: `git -C {repo} show bad7880`).\n\n"
            f"  git -C {repo} merge --no-ff {d['upstream']}\n\n"
            f"Resolve, commit, then re-run. Read the incoming commits first: a fleet round often\n"
            f"carries pipeline repairs this machine needs (spec/40, spec/45).\n\n"
            f"git said:\n{indent(stderr.strip())}")


def downgrade_refusal(channel, target, installed, allow=False):
    """Why an older target is refused, or None when the update may proceed.

    A bare `update` follows the configured channel, and a channel can serve
    something OLDER than what is installed: measured 2026-09-08, `stable`
    pointed at 2.1.236 while this machine ran 2.1.261. Nothing downstream would
    have objected — it is a legitimate staged swap onto a real release — and 25
    releases would have gone backwards without anyone being asked."""
    if allow or ver_key(target) >= ver_key(installed):
        return None
    return (f"target {target} is OLDER than the installed {installed} — refusing a silent "
            f"downgrade.\n"
            f"  channel `{channel}` currently serves {target}; a bare `update` follows it.\n"
            f"  name the version you mean (`update <VER>`), or pass --allow-downgrade to roll "
            f"back deliberately.")


def cmd_pull(args, quiet=False):
    cfg = require_cfg()
    before = repo_commit(cfg)
    r = git(cfg, "pull", "--ff-only")
    if r.returncode != 0:
        # A --ff-only refusal is the normal way a fleet round arrives: another
        # machine pushed while this one sat still, and this one has commits of
        # its own. git's own hint ("you need to either merge or rebase") does
        # not know that THIS repo reconciles machine rounds by merge, so the
        # next agent pays for the archaeology. Answer it here instead.
        msg = divergence_message(repo_dir(cfg), pull_divergence(cfg), r.stderr)
        die(msg or f"git pull failed:\n{r.stderr}")
    after = repo_commit(cfg)
    if before != after:
        summary = git(cfg, "log", "--oneline", f"{before}..{after}").stdout.strip()
        log_entry("pull", f"- `{before}` -> `{after}`\n```\n{summary}\n```")
        print(f"updated {before} -> {after}\n{summary}")
    elif not quiet:
        print(f"already up to date at {after}")
    return after


def adhoc_landed(binary, version, markers):
    """Which adhoc patches are NOT in the live binary. Returns (missing, note).

    [remark] Markers cannot cover an adhoc. A marker is a literal string, and
    every byte an adhoc patch touches is a minified identifier that changes
    between builds — `zAE`, `ek`, `Il`. So the tranche's structural patches had
    no tripwire at all, and on 2026-08-25 `status --check` reported a confident
    `11/11 markers, 3/3 deletions hold` on a binary that was missing the one
    patch deciding whether any of it reached the operator's model.

    The fix is a derived check rather than a literal one: re-plan the adhocs
    against the PRISTINE binary (where their anchors still exist), then assert
    each replacement's exact bytes are present in the live window. Planning
    against the live binary cannot work — a correctly applied patch has already
    destroyed its own anchor, which is why the plan reads MANUAL there.

    Degrades to a note rather than a failure when no pristine copy is parked:
    an unverifiable check must not read as a passing one."""
    src = pristine_source(version, markers)
    if not src:
        return [], "no parked pristine for this version — adhocs unverifiable"
    try:
        plan = plan_adhocs(live_blob(src))
    except Exception as e:
        return [], f"could not re-derive adhocs from {Path(src).name}: {e}"
    live = live_blob(binary)
    missing, unplannable = [], []
    for p in plan:
        if p.get("status") == "NOOP":
            continue
        if p.get("status") != "AUTO":
            unplannable.append(p["name"])
        elif p["new"] not in live:
            missing.append(p["name"])
    note = ""
    if unplannable:
        note = ("could not derive against pristine: " + ", ".join(unplannable) +
                " — these are unchecked, not verified")
    return missing, note


def antimarkers_live(binary, version, anti, markers):
    """`(revived, dead, note)` for the anti-marker list.

    `revived` is the old question: which deleted stock sentences are back in
    the live binary. `dead` is the one nobody asked until 2026-09-03: which of
    them are not in the STOCK binary either.

    An anti-marker upstream has deleted itself scores as a passing deletion
    forever — absent is absent, and the count cannot tell "we removed it" from
    "there was nothing to remove". Measured that day: "To leave something
    undone because it is hard" reads 0 in stock 2.1.251, 2.1.257 and 2.1.259,
    so `3/3 stock sentences confirmed gone` had been a 2-of-3 check reporting
    itself as 3-of-3 for at least three releases. The stock arm's inverted
    check is what surfaced it — in that arm the sentence should have been
    present, and it was not.

    Same shape as `adhoc_landed`: derive against the parked pristine rather
    than reason about the literal, and degrade to a note when none is parked,
    because an unverifiable check must not read as a passing one."""
    revived = sorted(binary_contains(binary, anti)) if anti else []
    if not anti:
        return revived, [], ""
    src = pristine_source(version, markers)
    if not src:
        return revived, [], ("no parked pristine for this version — a deletion that holds cannot "
                             "be told from an anti-marker upstream already removed")
    try:
        in_stock = binary_contains(src, anti)
    except Exception as e:
        return revived, [], f"could not read stock {Path(src).name}: {e}"
    return revived, [a for a in anti if a not in in_stock], ""


def checkpoint_line(cfg, ver):
    """One line: which CC release the edits in this repo were last checked
    against, and whether that is the one installed here.

    [remark] Reports, never gates. Nothing in the apply path consults
    `release.json`'s `ccVersion` — the things that protect the install are the
    per-target locators, the marker and anti-marker counts and the pristine
    hash `build_ab.py` demands, and all of them are derived against THIS
    binary. The checkpoint is bookkeeping about when the edits were last read
    against upstream's text, and letting it sit silently behind the installed
    version is how it went five days and five releases stale without anything
    saying so. It is not an approval, and advancing it is a routine step."""
    release = load_json(repo_dir(cfg) / "release.json", None) or {}
    pinned = release.get("ccVersion")
    when = release.get("observedAt")
    if not pinned:
        return "release.json names no version — run the spec/40 release step"
    stamp = f" on {when}" if when else ""
    if ver.startswith(pinned):
        return f"CC {pinned}{stamp} — the version installed here"
    newer = ver_key(ver) > ver_key(pinned)
    return (f"CC {pinned}{stamp}; this machine runs {ver}"
            + (" — advance it with the spec/40 release step (routine, no approval)"
               if newer else " — this machine is BEHIND the checkpoint"))


def cmd_status(args):
    if getattr(args, "live", False):
        return cmd_live(args)
    if getattr(args, "delivered", False):
        return cmd_delivered(args)
    cfg = require_cfg()
    binary = claude_binary()
    ver = cc_version()
    markers = repo_markers(cfg)
    present = binary_contains(binary, markers) if markers else set()
    state = load_json(STATE, {})

    # The declared arm decides what "correct" means for every check below, so
    # it is resolved first. A record naming a version that is no longer
    # installed describes a binary that is gone: it is reported as stale and
    # otherwise ignored, never used to excuse an unpatched binary nobody chose.
    arm = stock_arm(state)
    arm_stale = bool(arm) and not ver.startswith(arm["ccVersion"])
    armed = bool(arm) and not arm_stale
    sync_stock_sentinel(arm)

    anti = repo_antimarkers(cfg)
    # In the arm the live binary IS the stock reference, so asking the parked
    # pristine the same question twice buys nothing — 220 MB of scan for an
    # answer already on screen.
    if armed:
        revived, dead_anti, anti_note = (sorted(binary_contains(binary, anti)) if anti else []), [], ""
    else:
        revived, dead_anti, anti_note = antimarkers_live(binary, ver, anti, markers)
    adhoc_missing, adhoc_note = ([], "") if armed else adhoc_landed(binary, ver, markers)

    if armed and getattr(args, "check", False):
        # Exit 3, not 0 and not 2. Not 0 because the tranche is not live and a
        # script asking "is it patched" must not read yes; not 2 because this
        # is the state the operator asked for, and an agent that reads FAIL
        # here will helpfully re-apply and end the experiment it was hired to
        # run. The distinct code is the whole point.
        if present:
            print(f"MARKERS PRESENT  : {len(present)}/{len(markers)} in a declared stock arm")
            print(f"FAIL  CC {ver}: state declares a stock arm (since {arm['when']}) but the "
                  f"binary carries our text — state and binary disagree")
            sys.exit(2)
        print(f"STOCK ARM  CC {ver}: 0/{len(markers)} markers, deliberate since {arm['when']}"
              f" — {arm['note']}")
        print("           not a fault; `ccctl.py apply` ends the arm and puts the tranche back.")
        sys.exit(3)

    if armed and getattr(args, "quiet", False):
        # The tripwire speaks in the B arm rather than falling silent. Silence
        # would be defensible — nothing is wrong — but the failure this guards
        # against is forgetting which arm a session is in, and a stock session
        # cannot be told from a patched one by looking at it.
        if present:
            print(f"[ccctl] ARM DISAGREEMENT: state says stock arm since {arm['when']}, but "
                  f"{len(present)}/{len(markers)} of our markers are live in CC {ver} — run ccctl.py status")
        else:
            print(f"[ccctl] STOCK ARM (deliberate, since {arm['when'][:16].replace('T', ' ')}): "
                  f"CC {ver} runs Anthropic's default prompts — `ccctl.py apply` ends it")
        if backup_is_patched(markers):
            print("[ccctl] tweakcc's native-binary.backup is a PATCHED binary — roll back via "
                  "versions/<ver>.stock instead.")
        return

    if getattr(args, "check", False):
        # One command, same answer on all three machines, scriptable. `status`
        # alone prints a report a human reads; this is the assertion. Both
        # halves must hold: our text in, stock text out.
        missing = [mk for mk in markers if mk not in present]
        for mk in missing:
            print(f"MARKER MISSING   : {mk}")
        for a in revived:
            print(f"DELETED TEXT LIVE: {a}")
        for nm in adhoc_missing:
            print(f"ADHOC NOT LANDED : {nm}")
        # Not a fault in the binary — the tranche is applied correctly — but a
        # decayed instrument, and one that decays SILENTLY (absent reads as a
        # deletion that holds). It is called out on the OK line rather than
        # failing the check, so nobody's CI turns red over a maintenance item
        # and nobody's status claims a check that stopped checking.
        for a in dead_anti:
            print(f"DEAD ANTI-MARKER : {a}")
        if dead_anti:
            print(f"                   (absent from stock {ver} too — it proves nothing now; "
                  f"replace it in edits/ANTIMARKERS.txt per the rule in that file)")
        if adhoc_note:
            print(f"NOTE             : {adhoc_note}")
        if anti_note:
            print(f"NOTE             : {anti_note}")
        if missing or revived or adhoc_missing:
            print(f"FAIL  CC {ver} at repo {repo_commit(cfg)}: "
                  f"{len(present)}/{len(markers)} markers, {len(revived)} anti-marker(s) present, "
                  f"{len(adhoc_missing)} adhoc(s) missing")
            sys.exit(2)
        print(f"OK    CC {ver} at repo {repo_commit(cfg)}: "
              f"{len(markers)}/{len(markers)} markers, "
              f"{len(anti) - len(dead_anti)}/{len(anti)} deletions hold"
              + (f" + {len(dead_anti)} DEAD anti-marker(s)" if dead_anti else "")
              + ", adhocs landed")
        return

    if getattr(args, "quiet", False):
        if arm_stale:
            print(f"[ccctl] STALE stock-arm record: declared for CC {arm['ccVersion']}, this is "
                  f"CC {ver} — the arm no longer describes this binary; `ccctl.py apply` clears it")
        if markers and len(present) < len(markers):
            print(f"[ccctl] STOCK PROMPTS: {len(present)}/{len(markers)} markers in CC {ver} — run ccctl.py apply")
        if adhoc_missing:
            print(f"[ccctl] ADHOC PATCH MISSING in CC {ver}: {', '.join(adhoc_missing)} — "
                  f"markers cannot see this; run ccctl.py apply")
        if revived:
            # Distinct from the count above and not implied by it: a binary can
            # read 10/10 and still carry stock text we deleted, if an upstream
            # release moved that text somewhere this tranche no longer patches.
            print(f"[ccctl] DELETED TEXT IS BACK: {len(revived)} anti-marker(s) live in CC {ver} — "
                  f"first: {revived[0][:60]!r} — run ccctl.py status")
        if dead_anti:
            # Third warning, and the quietest failure of the three: nothing is
            # wrong with the binary, but one of the checks guarding it stopped
            # checking and says nothing. It only fires until someone edits one
            # file, so it is not a standing nag.
            print(f"[ccctl] DEAD ANTI-MARKER: {len(dead_anti)} of {len(anti)} are absent from stock "
                  f"{ver} too, so they score as passing deletions forever — "
                  f"first: {dead_anti[0][:60]!r} — replace it in edits/ANTIMARKERS.txt")
        # Second warning, because the first cannot catch it: a patched backup
        # leaves the binary perfectly fine and only breaks the ROLLBACK, which
        # nobody discovers until they need it. Verified 2026-08-25 that this is
        # not currently true on any of the three machines; the check is here so
        # that stops depending on anyone having looked.
        if backup_is_patched(markers):
            print("[ccctl] tweakcc's native-binary.backup is a PATCHED binary — `tweakcc --restore` "
                  "would report success and change nothing. ccctl's own restore refuses it, and the "
                  "next apply parks it; roll back via versions/<ver>.stock instead.")
        # Fourth warning, and the only one a full marker check cannot reach: a
        # model-keyed capability can take the branch BEFORE our fragment and
        # leave every marker verifying. Said at session start because that is
        # when a fresh session is being built with these settings.
        for cap, var, verdict in model_gates_unmasked(binary=binary):
            how = MODEL_GATES[cap][2]
            if verdict == "FORCED ON":
                print(f"[ccctl] MODEL GATE FORCED ON: {var} is set to a value CC reads as TRUE, "
                      f"which turns {cap} on for EVERY model — it {MODEL_GATES[cap][1].lower()}. "
                      f"Set it to \"0\" or remove it.")
            elif verdict == "UNVERIFIED":
                print(f"[ccctl] MODEL GATE UNVERIFIED: no {cap} predicate found in CC {ver}, "
                      f"so nothing can say whether it is on — re-read what adds it "
                      f"(AGENTS.md update step 1)")
            elif how == "binary":
                print(f"[ccctl] MODEL GATE UNMASKED: {cap} {MODEL_GATES[cap][1].lower()} "
                      f"wherever a model clause, a catalogue entry or a server arm turns it on, "
                      f"and this binary does not switch it off — run ccctl.py apply "
                      f"(markers cannot see this)")
            else:
                print(f"[ccctl] MODEL GATE UNMASKED: {cap} is on by model on Fable 5.1, which "
                      f"{MODEL_GATES[cap][1].lower()} — add \"{var}\": \"0\" to the env block of "
                      f"{Path.home() / '.claude' / 'settings.json'} (markers cannot see this)")
        return

    data = ensure_prompt_data(ver)
    print(f"machine        : {sys.platform}")
    print(f"toolchain      : git ok, tweakcc {run([*tweakcc_node(), '--version']).stdout.strip()} ok")
    print(f"cc binary      : {binary}")
    print(f"cc version     : {ver}")
    print(f"repo commit    : {repo_commit(cfg)} ({len(repo_edits(cfg))} edit fragments)")
    print(f"tweakcc data   : {'available' if data else 'LAGGING ' + ver + ' (apply impossible right now)'}")
    print(f"last checked   : {checkpoint_line(cfg, ver)}")
    carried = carried_map(ver)
    if carried:
        print(f"                 CARRIED FORWARD: {carried} — replace it when upstream "
              f"publishes {ver} (spec/45 'Carried-forward map')")
    if armed:
        print(f"prompt arm     : STOCK by intent since {arm['when']} — {arm['note']}")
        print(f"                 Anthropic's defaults + tweakcc's patch set; "
              f"`ccctl.py apply` returns the tranche")
    elif arm_stale:
        print(f"prompt arm     : STALE stock-arm record for CC {arm['ccVersion']} (installed is "
              f"{ver}) — ignored; `ccctl.py apply` clears it")
    if markers:
        expected = "0" if armed else str(len(markers))
        print(f"patch state    : {len(present)}/{len(markers)} markers in binary"
              + (f"  (arm expects {expected}"
                 + ("" if str(len(present)) == expected else " — DISAGREES") + ")" if armed else ""))
        for mk in (markers if not armed else []):
            if mk not in present:
                print(f"                 MISSING: {mk[:64]}")
        for mk in (sorted(present) if armed else []):
            print(f"                 UNEXPECTED IN STOCK ARM: {mk[:64]}")
    else:
        print("patch state    : no edits authored yet")
    if anti and armed:
        # In the B arm these sentences are supposed to be here. An absent one
        # is not a pass — it means upstream deleted it themselves, which is a
        # finding about their prompt and belongs on screen, not in silence.
        print(f"deleted text   : {len(revived)}/{len(anti)} present, as a stock build should")
        for a in (a for a in anti if a not in revived):
            print(f"                 UPSTREAM DROPPED IT TOO: {a[:64]}")
    elif anti:
        print(f"deleted text   : {len(anti) - len(revived) - len(dead_anti)}/{len(anti)} stock "
              f"sentences confirmed gone"
              + (f", {len(dead_anti)} DEAD (absent from stock too)" if dead_anti else ""))
        for a in revived:
            print(f"                 STILL PRESENT: {a[:64]}")
        for a in dead_anti:
            print(f"                 DEAD, PROVES NOTHING: {a[:64]}")
        if anti_note:
            print(f"                 {anti_note}")
    if armed:
        print("adhoc patches  : n/a in the stock arm (none applied)")
    elif adhoc_missing or adhoc_note:
        print("adhoc patches  : " + (f"{len(adhoc_missing)} NOT LANDED" if adhoc_missing else "landed"))
        for nm in adhoc_missing:
            print(f"                 MISSING: {nm}")
        if adhoc_note:
            print(f"                 {adhoc_note}")
    else:
        print("adhoc patches  : landed (re-derived against the parked pristine)")
    banner = bool(binary_contains(binary, ["tweakcc"]))
    fell_back = state.get("tweakccFallback")
    if armed and cfg.get("tweakccPatches", TWEAKCC_PATCHES_DEFAULT):
        # The banner's usual meaning ("our patches are live") is suspended for
        # the duration: in this arm it means only that the pipeline built this
        # binary, which is still worth showing and is still true. Saying so
        # beats letting the normal coupling report a DISAGREEMENT that isn't.
        print(f"in-CC indicator: tweakcc banner {'present' if banner else 'ABSENT'} — in the stock "
              f"arm it means the pipeline built this binary, NOT that our patches are live")
    elif cfg.get("tweakccPatches", TWEAKCC_PATCHES_DEFAULT):
        # these two must agree: the banner is only meaningful as a signal for
        # our patches if it never appears without them, and vice versa
        ok = banner == bool(markers and len(present) == len(markers))
        if not banner and fell_back:
            # The one case where absent-banner is expected with the knob on:
            # assemble met a build that rejects the patch set and went without
            # it. Our markers still verified; the indicator is simply not
            # available on this binary. Reporting that as a disagreement would
            # train the operator to ignore a line that means something.
            print(f"in-CC indicator: unavailable — tweakcc's patch set was rejected when this "
                  f"binary was built ({fell_back['when'][:10]}); ours applied without it")
        else:
            print(f"in-CC indicator: tweakcc banner {'present' if banner else 'ABSENT'}"
                  + ("" if ok else "  <- DISAGREES with patch state above; do not trust the banner"))
    else:
        print("in-CC indicator: off (tweakccPatches=false) — no banner in CC even when patched"
              + ("  <- but the binary HAS one; config and binary disagree" if banner else ""))
    if state.get("applied"):
        a = state["applied"]
        print(f"last applied   : CC {a['ccVersion']}, repo {a['repoCommit']}, {a['when']}")
    # Sections the MODEL decides, which neither the marker count above nor the
    # server arms below can see. Read only, never gates --check: this is a
    # settings-layer fact about the next session, not a fault in this binary.
    try:
        rows, intact = model_gates(binary=binary, version=ver)
        bad = [(c, v, verdict) for c, v, _w, verdict in rows
               if verdict in ("UNMASKED", "FORCED ON", "UNVERIFIED")]
        need = [r for r in rows if r[3] != "n/a"]
        print(f"model gates    : {len(need) - len(bad)}/{len(need)} masked"
              + (f" — {len(bad)} NOT" if bad else "")
              + ("" if intact is not False else
                 f"; the model clauses are NOT the ones this table describes "
                 f"— re-read the gate in CC {ver}"))
        for cap, var, what, verdict in rows:
            if verdict == "UNMASKED" and MODEL_GATES[cap][2] == "binary":
                print(f"                 UNMASKED: {cap} — {what}")
                print(f"                           this binary does not switch it off; "
                      f"ccctl.py apply")
            elif verdict == "UNMASKED":
                print(f"                 UNMASKED: {cap} — {what}")
                print(f"                           add \"{var}\": \"0\" to settings.json env")
            elif verdict == "UNVERIFIED":
                print(f"                 UNVERIFIED: {cap} — no predicate for it found in this "
                      f"binary; re-read what adds it")
            elif verdict == "masked by env":
                print(f"                 {cap}: masked by the env line only; `apply` switches it off "
                      f"in the binary, which also covers harnesses that skip user settings")
            elif verdict == "FORCED ON":
                print(f"                 FORCED ON: {var} reads as TRUE, so {cap} is on for "
                      f"EVERY model — it {what.lower()}")
                print(f"                           set it to \"0\" or remove it")
        if intact is False:
            for line in model_gate_drift(binary, ver):
                print(f"                 GATE DRIFT: {line}")
            print(f"                 a clause decides its sections BEFORE any server flag, "
                  f"so an unknown one can replace our text with every marker intact")
        print(f"                 (masking is the precondition, not the proof — "
              f"`status --delivered` is the proof, and it is free)")
    except Exception as exc:                               # noqa: BLE001
        print(f"model gates    : unreadable ({exc!r})")
    # Which model a subagent gets when the caller names none. Read from the
    # bundle's alias table, because the pin is an alias and a release can move
    # what it means without anything else here noticing (operator, 2026-09-22).
    try:
        cur = subagent_model_state(binary)
        line, moved = subagent_model_summary(cur, state.get("subagentModel"))
        print(f"subagent model : {'MOVED: ' if moved else ''}{line}")
        if cur["pin"] and cur["pin"] != "inherit":
            print(f"                 reaches: {', '.join(SUBAGENT_PIN_REACHES)}")
            print(f"                 ignored by: "
                  + "; ".join(f"{a} — {w}" for a, w in sorted(SUBAGENT_PIN_IGNORED.items())))
        else:
            print(f"                 inherit the parent: {', '.join(SUBAGENT_PIN_REACHES)}")
            print(f"                 own defaults: "
                  + "; ".join(f"{a} — {w}" for a, w in sorted(SUBAGENT_PIN_IGNORED.items())))
    except Exception as exc:                               # noqa: BLE001
        print(f"subagent model : unreadable ({exc!r})")
    # What the last capture of the delivered prompt found, free to repeat but
    # a minute to run, so the recorded answer is shown until CC moves.
    try:
        print(f"delivered      : {delivered_summary(ver, state)}")
    except Exception as exc:                               # noqa: BLE001
        print(f"delivered      : unreadable ({exc!r})")
    # What the last spent turn established, free on every status afterwards.
    # This is the half of the answer that cost money; not showing it would
    # mean paying for it again.
    try:
        print(f"live read-back : {live_summary(ver, state)}")
    except Exception as exc:                               # noqa: BLE001
        print(f"live read-back : unreadable ({exc!r})")
    # Client data: named experiments per model and entrypoint, the channel
    # that put upstream's shell-block text in place of ours on Opus 5 under an
    # SDK host while the flag cache said otherwise (2026-09-21).
    try:
        exps = {}
        for (model, ep), (data, _at) in client_data_slots().items():
            if data.get("experimentKey"):
                exps.setdefault(data["experimentKey"], []).append(f"{model}/{ep}")
        print("client data    : " + ("; ".join(f"{k} on {', '.join(sorted(v))}" for k, v in sorted(exps.items()))
                                     + " — `ccctl.py flags`" if exps else "no experiment in any cached slot"))
    except Exception as exc:                               # noqa: BLE001
        print(f"client data    : unreadable ({exc!r})")
    # Sections the SERVER decides. Surfaced here without being asked, because
    # the whole point is that these move without anything local changing, and
    # nobody runs a command to check for news they do not know exists. Read
    # only; `flags` is what records and explains. Never gates --check: an
    # experiment arm is upstream's call, not a fault in this binary.
    try:
        arms, _when, have = server_arms()
        if have:
            on = [k for k, v in arms.items() if arms_armed(v)]
            moved = arms_drift(arms, state)
            line = f"{len(on)}/{len(arms)} armed"
            if moved:
                line += f", {len(moved)} CHANGED since last check — `ccctl.py flags`"
            elif not state.get("serverArms"):
                line += ", never recorded here — `ccctl.py flags`"
            else:
                line += ", no change"
            print(f"server arms    : {line}")
    except Exception as exc:                               # noqa: BLE001
        # A diagnostic must never be the thing that breaks `status`, which is
        # what an operator runs when something is already wrong.
        print(f"server arms    : unreadable ({exc!r})")
    verdict, detail = tripwire_state()
    print(f"tripwire       : {verdict if verdict == 'ok' else verdict.upper()} — {detail}")
    if verdict == "ok":
        try:
            hook = tripwire_hook()
            elapsed, err = tripwire_timed(hook)
            if err:
                print(f"                 timing: could not run it ({err})")
            else:
                speed, text = tripwire_slow(elapsed, hook.get("timeout"))
                print(f"                 {'' if speed == 'ok' else speed + ': '}{text}")
            last, since = tripwire_kills(hook)
            if last:
                when = last[0][:16].replace("T", " ")
                print(f"                 last killed by CC: {when} UTC, after {last[1] or '?'} ms"
                      + (f"; {since} session start(s) recorded since, none killed" if since
                         else " — the latest recorded session start"))
        except Exception as exc:                           # noqa: BLE001
            print(f"                 timing: unreadable ({exc!r})")
    # two independent rollback paths; the span engine creates the second one
    # only (it never invokes tweakcc --apply, which is what writes the first)
    paths = []
    bk = TWEAKCC_DIR / "native-binary.backup"
    if bk.exists():
        # Only a STOCK backup is a restore path; a patched one is a trap, and
        # saying so here is cheaper than finding out during a rollback.
        paths.append("tweakcc backup (PATCHED — not a rollback source)"
                     if backup_is_patched(markers) else "tweakcc backup")
    # `.stock` copies are pristine by construction — ccctl only creates one from
    # a binary it verified stock. `.pre-swap` copies are NOT: they are whatever
    # was live before a swap, which after the first round is one of ours. Calling
    # them "parked pristine" (as this line did until 2026-08-25, measured: all
    # five on win32 were patched) invites a rollback to a patched binary. They
    # are not re-verified here — that is five 340 MB scans for a status line —
    # so they are named for what they provably are: previous, not pristine.
    stock = sorted(p.name for d in versions_dirs() for p in d.glob("*.stock"))
    if stock:
        paths.append("parked pristine: " + ", ".join(stock[:3]))
    prev = list(launcher_path().parent.glob(launcher_path().name + ".pre-swap*"))
    if prev:
        newest = max(prev, key=lambda p: p.stat().st_mtime)
        paths.append(f"{len(prev)} previous binaries (unverified; newest {newest.name})")
    print(f"restore path   : {'; '.join(paths) if paths else 'NO BACKUP'}")


def cmd_apply(args):
    cfg = require_cfg()
    if not args.no_pull:
        cmd_pull(args, quiet=True)
    edits = repo_edits(cfg)
    markers = repo_markers(cfg)
    if getattr(args, "stock", False):
        return enter_stock_arm(cfg, cc_version(), reason=getattr(args, "reason", None))
    if not edits:
        die("no edit fragments in repo edits/ — nothing to apply")
    if not markers:
        die("edits exist but MARKERS.txt is empty — refusing an unverifiable apply")

    ver = cc_version()
    if not ensure_prompt_data(ver):
        die(f"tweakcc prompt data lags CC {ver}. Wait for upstream support, then retry.")
    snapshot(ver)

    if cfg["applyEngine"] == "tweakcc":
        apply_engine_tweakcc(cfg, ver, edits, markers)
    else:
        apply_engine_span(cfg, ver, edits, markers)

    binary = claude_binary()
    commit = repo_commit(cfg)
    ended = clear_stock_arm()               # our text is back: the arm is over
    if ended:
        print(f"stock arm ended (ran from {ended['when']} on CC {ended['ccVersion']})")
        log_entry("stock-arm-end", f"- CC `{ver}`: tranche re-applied, stock arm over "
                                   f"(ran from {ended['when']} on CC {ended['ccVersion']}).")
    state = load_json(STATE, {})
    state["applied"] = {"ccVersion": ver, "repoCommit": commit,
                        "when": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")}
    save_json(STATE, state)
    log_entry("apply", f"- CC `{ver}`, repo `{commit}`, engine `{cfg['applyEngine']}`\n"
                       f"- {len(edits)} fragments + adhocs, {len(markers)}/{len(markers)} "
                       f"markers verified in `{binary.name}`")
    print(f"PATCHED and verified: CC {ver}, repo {commit}")
    prune_nudge(markers)


def enter_stock_arm(cfg, ver, reason=None, pristine=None):
    """Build and swap in a deliberate stock binary for `ver`: pristine bytes
    plus tweakcc's own patch set, nothing of ours. Same staged-verify-swap
    path as an apply, and the same rollback artifacts — the arm is a state the
    pipeline holds, not a step outside it.

    A pristine source is mandatory and is never synthesised from the live
    binary: on a patched machine the live binary IS the tranche, and "stock"
    assembled from it would be a quiet no-op that verified nothing."""
    markers = repo_markers(cfg)
    if not markers:
        die("MARKERS.txt is empty — a stock arm is verified by every marker being ABSENT, "
            "which is unfalsifiable with no markers to check")
    src = pristine or pristine_source(ver, markers)
    if not src:
        die(f"no pristine {ver} binary on this machine — run `ccctl.py analyze {ver}` to "
            f"download one (checksum-verified), then retry")
    print(f"stock arm: assembling pristine {ver} + tweakcc's patch set, none of ours")
    staged = assemble(cfg, ver, src, [], stock=True)
    live = place(cfg, ver, staged, Path(src), label="stock")
    arm = set_stock_arm(ver, reason or "operator-declared stock arm")
    post_verify(cfg, ver, stock=True)
    log_entry("stock-arm", f"- CC `{ver}`: entered the STOCK ARM — Anthropic's default prompts, "
                           f"tweakcc's patch set, none of this repo's.\n"
                           f"- reason: {arm['note']}\n"
                           f"- verified 0/{len(markers)} markers in `{live}`; "
                           f"`ccctl.py apply` ends the arm and puts the tranche back.")
    print(f"STOCK ARM live: CC {ver}, 0/{len(markers)} markers, by intent. "
          f"Start a fresh session to use it; `ccctl.py apply` ends the arm.")
    prune_nudge(markers)
    return live


def apply_engine_span(cfg, ver, edits, markers):
    """Default engine: patch a staged copy of the pristine binary, verify,
    swap it live (spec/40 D3). Never writes the live binary in place."""
    pristine = pristine_source(ver, markers)
    if not pristine:
        die(f"no pristine {ver} binary on this machine (versions/{ver} is patched or missing, "
            f"backup unusable) — run `ccctl.py analyze {ver}` to download one, then retry")
    data = load_snapshot(ver)
    if not data:
        die(f"no prompt data for {ver} despite the earlier check — retry")
    blob = live_blob(pristine)
    plan = [i for i in plan_fragments(blob, data, edits) + plan_adhocs(blob)
            if i["status"] != "NOOP"]
    bad = [i for i in plan if i["status"] != "AUTO"]
    if bad:
        for i in bad:
            print(f"  {i['name']}: {i['status']} — {i.get('reason', '')}")
        die(f"{len(bad)} target(s) not auto-appliable — run `ccctl.py analyze {ver}` "
            f"for a full report with fix instructions")
    staged = assemble(cfg, ver, pristine, plan)
    place(cfg, ver, staged, Path(pristine))
    post_verify(cfg, ver)


def apply_engine_tweakcc(cfg, ver, edits, markers):
    """Legacy engine: tweakcc --apply for the fragments (linux-proven; the
    fragment writer no-ops on win32 builds), then the adhoc patches in place.
    --apply restores from tweakcc's backup first, so adhocs go strictly after."""
    if sys.platform == "win32":
        die("applyEngine 'tweakcc' does not work on win32 (fragment writes no-op, live exe "
            "is write-locked) — use the default 'span' engine")
    print("NOTE: the legacy tweakcc engine writes the LIVE install in place — a running "
          "claude session holds the binary (ETXTBSY) and a failure leaves it half-patched. "
          "Close sessions first; the default 'span' engine avoids all of this.")
    target = TWEAKCC_DIR / "system-prompts"
    target.mkdir(parents=True, exist_ok=True)
    for f in edits:
        shutil.copy2(f, target / f.name)
    print(f"synced {len(edits)} fragment(s) into {target}")

    r = run([*tweakcc_node(), "--apply", "-y"], timeout=600)
    print(r.stdout.strip()[-2000:])
    if r.returncode != 0:
        die(f"tweakcc --apply failed:\n{r.stderr[-2000:]}")

    binary = claude_binary()
    for item in plan_adhocs(live_blob(binary)):
        if item["status"] == "NOOP":
            print(f"  adhoc {item['name']}: n/a — {item.get('reason', '')}")
            continue
        if item["status"] != "AUTO":
            die(f"adhoc {item['name']}: {item.get('reason', 'not derivable')} — "
                f"{item.get('fix', '')}")
        ok, out = adhoc_patch(binary, item["old"], item["new"])
        print(f"  adhoc {item['name']}: {'OK' if ok else 'FAIL'}")
        if not ok:
            die(f"adhoc-patch failed — the LIVE binary is now fragments-patched but "
                f"adhoc-less; run `ccctl.py restore` (or retry with no session running):\n{out[-500:]}")

    present = binary_contains(binary, markers)
    missing = [m for m in markers if m not in present]
    if missing:
        for m in missing:
            print(f"  MARKER MISSING: {m[:70]}")
        die("apply reported success but markers are absent — run ccctl.py restore")


def resolve_target(cfg, args):
    return args.version or discover(getattr(args, "channel", None) or cfg["channel"])


def post_verify(cfg, version, stock=False):
    """The binary a fresh session would run: right version, and the marker
    count the declared arm calls for — all of them, or none of them."""
    claude_binary.cache_clear()
    binary = claude_binary()
    ver = cc_version()
    markers = repo_markers(cfg)
    present = binary_contains(binary, markers)
    if not ver.startswith(version):
        die(f"post-swap: `claude --version` gives {ver}, expected {version}")
    if stock:
        if present:
            die(f"post-swap: {len(present)}/{len(markers)} markers in {binary} but the stock arm "
                f"calls for none — rollback: restore the .pre-swap/.stock parked binary")
        print(f"post-verify OK: CC {ver}, stock arm — 0/{len(markers)} markers in {binary}")
        return binary
    if len(present) < len(markers):
        die(f"post-swap: only {len(present)}/{len(markers)} markers in {binary} — "
            f"rollback: restore the .pre-swap/.stock parked binary")
    print(f"post-verify OK: CC {ver}, {len(present)}/{len(markers)} markers in {binary}")
    return binary


def cmd_analyze(args):
    cfg = require_cfg()
    target = resolve_target(cfg, args)
    report, _pristine, _plan = analyze_core(cfg, target)
    print(f"\noverall: {report['overall']}")
    if report["overall"] == "CLEAN":
        print(f"clean — `ccctl.py update {target}` performs the full staged update.")
    sys.exit({"CLEAN": 0, "REVIEW": 2, "MANUAL": 3, "BLOCKED": 4}[report["overall"]])


def cmd_update(args):
    cfg = require_cfg()
    if not getattr(args, "no_pull", False):
        cmd_pull(args, quiet=True)
    stock = getattr(args, "stock", False)
    if not repo_markers(cfg):
        die("MARKERS.txt is empty — refusing an unverifiable update"
            + (" (a stock arm is verified by every marker being ABSENT)" if stock else ""))
    if not stock and not repo_edits(cfg):
        die("no edit fragments in repo edits/ — nothing to carry through an update")
    target = resolve_target(cfg, args)
    installed = cc_version()
    refusal = downgrade_refusal(cfg["channel"], target, installed,
                                getattr(args, "allow_downgrade", False))
    if refusal:
        die(refusal)
    report, pristine, plan = analyze_core(cfg, target)
    policy = getattr(args, "policy", None) or cfg["updatePolicy"]

    if policy == "analyze-only":
        print(f"\npolicy analyze-only: stopping after analysis ({report['overall']}).")
        return

    if stock:
        # The verdicts are advisory on this path — nothing of ours is applied,
        # so a drifted fragment cannot break a binary that will not carry it.
        # They still price the RETURN trip, which is the one thing an operator
        # in the B arm has no other way to learn, so say it here rather than
        # let them find out when the arm ends.
        if report["overall"] == "CLEAN":
            print(f"\nreturn trip: analysis is CLEAN — `ccctl.py apply` puts the tranche back "
                  f"on {target} whenever you want it.")
        else:
            print(f"\nreturn trip: analysis is {report['overall']} — the stock arm is unaffected "
                  f"(none of our targets are applied), but returning to the patched arm on "
                  f"{target} needs those items fixed first:\n  "
                  f"{ANALYSIS / (installed + '-to-' + target + '.md')}")
        live = enter_stock_arm(cfg, target, pristine=pristine,
                               reason=f"update {installed} -> {target} in the stock arm "
                                      f"(Anthropic's default prompts, deliberately)")
        snapshot(target)
        if not cfg["keepStaging"]:
            shutil.rmtree(STAGING, ignore_errors=True)
        print(f"UPDATED to CC {target} in the STOCK ARM (was {installed}). "
              f"Start a fresh session to use it.")
        return live
    if report["overall"] == "BLOCKED":
        die(f"tweakcc prompt data lags {target} — nothing was changed. Re-run later "
            f"(upstream usually catches up within hours).")
    if report["overall"] == "MANUAL":
        die(f"manual fixes needed first — nothing was changed. Instructions: "
            f"{ANALYSIS / (installed + '-to-' + target + '.md')}")
    if report["overall"] == "REVIEW" and policy != "force":
        die(f"upstream changed fragment(s) we edit — re-review first (or `update --policy force`). "
            f"Hunks: {ANALYSIS / (installed + '-to-' + target + '.md')}")

    items = plan["AUTO"] + (plan["REVIEW"] if policy == "force" else [])
    total = report["planTotal"]
    if len(items) < total:
        die(f"plan holds {len(items)}/{total} targets — refusing a partial apply")
    staged = assemble(cfg, target, pristine, items)
    live = place(cfg, target, staged, pristine)

    # record the swap BEFORE verification — if post_verify dies, state must
    # not keep naming the old version as the applied one
    ended = clear_stock_arm()               # our text is back: the arm is over
    if ended:
        print(f"stock arm ended (ran from {ended['when']} on CC {ended['ccVersion']})")
    commit = repo_commit(cfg)
    state = load_json(STATE, {})
    state["applied"] = {"ccVersion": target, "repoCommit": commit, "verified": False,
                        "when": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")}
    save_json(STATE, state)
    post_verify(cfg, target)
    state["applied"]["verified"] = True
    save_json(STATE, state)
    snapshot(target)

    log_entry("update", f"- CC `{installed}` -> `{target}` (policy {policy}, overall {report['overall']})\n"
                        f"- {len(items)} targets applied, verified in `{live}`")
    if not cfg["keepStaging"]:
        shutil.rmtree(STAGING, ignore_errors=True)
    print(f"UPDATED and verified: CC {installed} -> {target}, repo {commit}. "
          f"Start a fresh session to use it.")
    report_subagent_model(live, state)


def report_subagent_model(binary, state):
    """Close an update by saying what a subagent will now be, and raise it as
    an ASK only when the answer changed under him.

    The operator pins the `opus` ALIAS deliberately (2026-09-22), so the pin can
    keep reading the same while the model behind it changes with a release. No
    marker moves when that happens and no fragment is involved, so this is the
    only place in the pipeline that would ever notice. Asked once per real
    change: the resolution is recorded here, and an unchanged one is a one-line
    statement rather than a question (the ask-once rule)."""
    try:
        cur = subagent_model_state(binary)
        last = (state or {}).get("subagentModel")
        line, moved = subagent_model_summary(cur, last)
    except Exception as exc:                               # noqa: BLE001
        print(f"subagent model: unreadable ({exc!r})")
        return
    if moved:
        print(f"\nASK THE OPERATOR — subagent model: {line}")
        print(f"  {SUBAGENT_MODEL_VAR} is an alias, and this release moved what it means.")
        print(f"  Pin a different one in settings.json env, or keep it and say so.")
    else:
        print(f"subagent model: {line}")
    st = load_json(STATE, {})
    st["subagentModel"] = {
        "pin": cur["pin"], "resolved": cur["resolved"], "force": cur["force"],
        "seenAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")}
    save_json(STATE, st)


# ------------------------------------------------- server experiment arms
#
# Several sections this tranche depends on are not decided by the binary. They
# are decided by a GrowthBook flag the server assigns per account, which is how
# Anthropic runs A/B tests — so the same patched binary can carry a fragment on
# Monday and not on Tuesday, with nothing local changing. The operator's rule:
# a server arm is ACCEPTED and surfaced — interfering would corrupt data
# collection that may well improve the product he uses (2026-09-11) — but ONLY
# when it does not conflict with an established behavioural-outcome patch
# (2026-09-29, narrowing a reading that had been applied to everything). An
# arm that displaces our text or turns on something the tranche removes is
# masked with the narrowest override, like any other gate.
#
# CC caches the assignment locally in ~/.claude.json under
# `cachedGrowthBookFeatures`, so this reads the server's current answer with no
# network call and no API spend.
#
# The trap this exists to catch: `ln(env, flag, model)` short-circuits on the
# env var. Once `CLAUDE_CODE_BISON_CAIRN=1` is in settings.json, a live session
# can no longer tell you whether the server ALSO armed it — the section is
# there either way. The env var buys determinism and costs visibility, and this
# is where the visibility is given back.
#
# key -> (env var that forces it, what it gates, whose text ends up in the prompt)
SERVER_ARMS = {
    # RETIRED as GrowthBook flags in 2.1.278: these three moved into the
    # capability gate (`bison_cairn`, `larch_cistern`, `amber_astrolabe`), so
    # the server no longer assigns a `tengu_` arm for them and the cached
    # features file will never mention them again. The env vars still work —
    # the gate reads them first — and the rows are kept so a machine on an
    # older CC still gets the answer. `cmd_flags` says "retired" for a flag
    # whose name is no longer in the binary; see MODEL_GATE_CLAUSES.
    "tengu_bison_cairn": ("CLAUDE_CODE_BISON_CAIRN", "delivering_work_max section", "OURS"),
    "tengu_larch_cistern": ("CLAUDE_CODE_LARCH_CISTERN", "overcorrection section", "OURS"),
    "tengu_amber_astrolabe": ("CLAUDE_CODE_AMBER_ASTROLABE", "autonomy_append section", "stock"),
    "tengu_amber_sextant": (None, "master enable for autonomy_append", "stock"),
    "tengu_cedar_lantern": ("CLAUDE_CODE_ACT_DONT_REDERIVE", "act_dont_rederive section", "stock"),
    "tengu_willow_tern": (None, "willow_tern writing style — we cut it", "stock"),
    "tengu_thistle_grebe": (None, "subagent_steer_delegation — NOT cut (TODO 24)", "stock"),
    "tengu_slate_bittern": (None, "opus5_reduced_delegation — we cut it", "stock"),
    "tengu_heron_brook": (None, "heron_brook — we cut it", "stock"),
    "tengu_brook_heron": (None, "brook_heron — we cut it", "stock"),
    "tengu_fennel_godwit": (None, "suppresses the model-capability arm", "stock"),
    "tengu_ochre_wren": (None, "intro frame wording", "stock"),
    # CC 2.1.280's built-in plugins are each gated by a flag of this kind, and
    # this is the one that carries prompt text against ours: `responsive-mode`
    # replaces the focus-mode section with an instruction to "respond
    # IMMEDIATELY", ahead of any thinking or tool call, and injects a
    # <responsive-mode> reminder on every composer turn. It is off by default
    # (`isOnByDefault` is a literal false) and the flag only makes it
    # available, so this row is a watch, not a mask — but it is the narration
    # of item 27 rebuilt as a plugin, so an arm arriving is news.
    "tengu_quiet_ember": (None, "responsive-mode plugin: narrate before every tool call "
                                "(off by default; the flag only offers it)", "stock"),
    # The other four built-in plugins of 2.1.280, same shape: the flag makes the
    # plugin available, `enabledPlugins["<name>@builtin"]` in settings turns it
    # on. None carries prompt text of its own, but `agents-md` changes WHICH
    # instruction files load — where a project has no CLAUDE.md it loads
    # AGENTS.md instead — and this repo's CLAUDE.md is a symlink to AGENTS.md
    # (TODO 34). Its flag was measured ARMED on win32 on 2026-09-24 while the
    # plugin stayed off: an AGENTS.md marker never reached a captured request
    # (notes/2026-09-24-win32-2.1.280.md). Rows so a per-machine arm is a delta.
    "tengu_agents_md_mod": (None, "agents-md plugin: AGENTS.md as project instructions "
                                  "(off by default; the flag only offers it)", "stock"),
    "tengu_quiet_dolphin": (None, "diff plugin: the diff panel (off by default; the flag only offers it)",
                            "stock"),
    "tengu_mermaid_mod": (None, "mermaid plugin: fences drawn as box text "
                                "(off by default; the flag only offers it)", "stock"),
    "tengu_mellow_hollerith": (None, "claude-test plugin: browser-driving test runner "
                                     "(off by default; the flag only offers it)", "stock"),
    # Usage-limit grace reminders (2026-09-25 stock-conflict audit, item 30).
    # Both inject a bracketed per-turn reminder that asks for "up to 3 short
    # bullets" of remaining work — a closing-message shape
    # of their own, beside the floor's. Server-armed only and off on every
    # machine so far; if one arms, judge it against the floor (spec/15) before
    # accepting it, per the 2026-09-29 rule. `lantern_wick_mode` is a string arm
    # ("wrap-up" | "next-steps" | anything else = off), and its sibling
    # `tengu_lantern_wick_text` can replace the reminder's text outright.
    "tengu_lantern_wick_mode": (None, "usage-limit grace reminder: 'next-steps' asks for 'up to 3 "
                                      "short bullets' of remaining work", "stock"),
    "tengu_vellum_anchor": (None, "usage-limit-approaching reminder: asks for 'up to 3 short "
                                  "bullets' of remaining work", "stock"),
}


# ------------------------------------------------- model-keyed gates
#
# A third decider, found on 2026-09-15 and invisible to both of the other two.
# CC 2.1.270 gave the capability gate a MODEL clause:
#
#   function Ose(cap, model, ctx, envValue) {
#     if (envValue !== undefined) return envValue;          // 1. env wins
#     ...
#     if (... || AAo.has(cap) && cce(model)) return true;   // 2. THE MODEL
#     ...server flag...                                     // 3. only then
#   }
#
# with `cce(model)` = "the model carries the fable_5_1_prompt_bundle
# capability". The gate has grown a clause per model generation since: Opus 5's
# on 2.1.278 and Opus 5.5's on 2.1.280, three as of this writing, and one of
# them reads the model off the CONTEXT rather than the model argument. Do not
# grep for a clause count or a minified name — MODEL_GATE_CLAUSES is the table
# and `model_gate_clauses` parses the live gate against it.
# So on Fable 5.1 every capability in that set is on before any
# GrowthBook flag is consulted, which is why `flags` cannot see this and why a
# clean 15/15 marker check said nothing while the operator got a day of
# narration (notes/2026-09-15-fable-turn-updates-override.md).
#
# `turn_updates` is a REPLACEMENT: its branch returns the stock one-liner and
# our communication fragment is never loaded. Markers still verify — the bytes
# are in the binary, the code path that reads them is not taken. That is the
# failure class this table exists for. What is cheap and checked here is the
# precondition, "is the mask in place for the session a fresh launch will
# build"; `status --delivered` is the proof, for both kinds of mask below.
#
# capability -> (env var, what it does, where we mask it)
#
#   "env"     the settings.json env line. The gate reads it first and returns
#             it as the answer, so it masks EVERY model, a server arm included.
#   "binary"  the adhoc `silent-turn-reminder-off` cuts the capability's only
#             consumer, so it is off on every model and harness whatever the
#             env, the clauses, a catalogue entry or a server arm say. A
#             server arm is accepted only when it does not conflict with an
#             established behavioural-outcome patch (operator, 2026-09-29), and
#             this one conflicts. The env var is then redundant, not harmful.
#   None      left on.
MODEL_GATES = {
    "turn_updates": (
        "CLAUDE_CODE_TURN_UPDATES",
        "REPLACES our communication fragment with the stock narrate-as-you-go line",
        "env"),
    # "env" until 2026-09-21, then a model-clause cut that let server arms
    # through (item 31), then — once 2.1.284 armed it from Sonnet 5.5's
    # catalogue entry and from client data — a cut at its one consumer that
    # switches every route off (2026-09-29). `status --delivered`'s scripted
    # tool loop is the proof: the reminder lands before the 6th request when
    # anything turns it on.
    "silent_turn_reminder": (
        "CLAUDE_CODE_SILENT_TURN_REMINDER",
        "feeds a 'the user hasn't heard from you' reminder every 5 silent tool calls",
        "binary"),
    # Measured on win32 2.1.270, left ON deliberately: the injected text says
    # only the model sees the command's output, that the terminal shows a few
    # lines of it, and to "put it in your reply" if the user needs it. That
    # agrees with our own fragment rather than displacing it, and
    # C-03 says we do not stack a second rule against ourselves to win a fight
    # we are not in.
    "bash_output_audience_note": (
        "CLAUDE_CODE_BASH_OUTPUT_AUDIENCE_NOTE",
        "appends a consonant 'only you see that output' note after long Bash results — left ON",
        None),
    # Not a prompt at all: it sets thinking.display="updates" on the API
    # request and adds a beta header. Nothing to mask.
    "thinking_display_updates": (
        None,
        "API request parameter (thinking.display), no effect on the prompt",
        None),
    # Rides the Opus 5.5 clause beside silent_turn_reminder and is the reason
    # that clause is not simply deleted: it decides whether a hook's notice
    # renders hidden or faint (with tengu_quizzical_shore / tengu_lilac_dune).
    # Terminal chrome, not text the model reads.
    "quizzical_shore": (
        None,
        "hides hook notices in the transcript, no effect on the prompt",
        None),
    # CC 2.1.293, Haiku 5.5's own catalogue entry: the heron_brook section's
    # fallback text ("The reasoning effort setting changes how much you think
    # before you act…"), behind `tengu_idempotent_wolf` (default on). It argues
    # for finishing the request and asking only when the reading cannot be
    # named, which the delivering-work fragment already says, so it is kept:
    # `delegation-override-cut` removes heron_brook's server text and leaves
    # this fallback in place (notes/2026-10-08-win32-2.1.294.md §2).
    "haiku_5_5_early_stopping_guidance": (
        None,
        "Haiku 5.5 early-stopping guidance in the heron_brook slot — consonant, left ON",
        None),
    # CC 2.1.293: a per-turn "Elapsed time so far: Xm YYs" attachment on the
    # main loop's tool turns. No model clause and no catalogue entry arms it,
    # so only a server arm or client data can. A clock reading asks for
    # nothing, so it is accepted when armed; it is listed so that an arm shows
    # up as a delta in `flags` rather than as an unclassified key.
    "elapsed_time_reminder": (
        None,
        "per-turn 'Elapsed time so far' note on tool turns (server-armed only) — left ON",
        None),
}

# The capabilities switched off in the binary. Until 2026-09-29 the reminder was
# cut out of the model clauses (and, for one day, out of Sonnet 5.5's catalogue
# entry), so a binary patched by an older tranche legitimately has clauses one
# member short; `gate_drift` and `catalogue_drift` keep accepting that shape.
BINARY_MASKED = frozenset(c for c, (_v, _w, how) in MODEL_GATES.items() if how == "binary")

# The reminder's single consumer, as `silent-turn-reminder-off` finds it, and
# as it reads once cut. See that plan item for why the cut sits here.
REMINDER_PREDICATE = (rb'return [\w$]+\("silent_turn_reminder",[\w$]+,[\w$]+,'
                      rb'[\w$]+\.CLAUDE_CODE_SILENT_TURN_REMINDER\)\}')
REMINDER_PREDICATE_CUT = rb"return!1&&" + REMINDER_PREDICATE[len(rb"return "):]


def reminder_predicate_state(blob):
    """'off' when the reminder's predicate is cut, 'stock' when it is present
    and uncut, None when this build has no such predicate."""
    if re.search(REMINDER_PREDICATE_CUT, blob):
        return "off"
    if re.search(REMINDER_PREDICATE, blob):
        return "stock"
    return None

# The model clauses of the gate, as the binary spells them. Each is a
# `new Set([...])` the gate consults BEFORE any server flag, keyed on a model
# capability:
#
#   if (s===!0 || U.has(e)&&wle(n) || G.has(e)&&_4t(r)) return !0;
#                 \__ fable clause __/  \__ opus 5 clause __/
#
# (2.1.283 computes the same disjunction into a local first; the shapes are
# GATE_CLAUSE_SHAPES.)
#
# 2.1.270 had one clause and this was a single literal byte check. 2.1.278
# added the second, and a single-literal check cannot see a second set arrive:
# `U` was still byte-identical, so the check passed while the gate had grown a
# whole new arm. The new arm decides `bison_cairn` and `larch_cistern` — the
# two sections that carry OUR text — on any model with `opus_5_prompt_bundle`.
# That is benign for us (both are forced on by env, item 19), but a clause that
# decides one of our sections is precisely what this tripwire exists to catch,
# so the check is now "the clauses in the binary ARE the clauses in this
# table", parsed out of the gate. An unparsed gate or an unknown set is a
# finding, never a pass.
#
# model capability -> (the capability names its clause arms, first CC version
# seen with it). The version is what keeps a machine on an older CC from
# reporting a clause it cannot have yet: an EXTRA clause is the finding, an
# absent one on an older build is just the past.
MODEL_GATE_CLAUSES = {
    "fable_5_1_prompt_bundle": (("turn_updates", "bash_output_audience_note",
                                 "silent_turn_reminder", "thinking_display_updates"),
                                "2.1.270"),
    # `_4t` is `opus_5_prompt_bundle` AND NOT `tengu_fennel_godwit`, which is
    # why that flag is in SERVER_ARMS as "suppresses the model-capability arm":
    # it turns this clause off, not the sections themselves.
    "opus_5_prompt_bundle": (("bison_cairn", "larch_cistern"), "2.1.278"),
    # CC 2.1.280 shipped Opus 5.5 and gave it a clause of its own. It arms
    # `silent_turn_reminder` — the capability item 31 cut out of Fable's clause
    # — so on this release the reminder is a model default again for any model
    # carrying `opus_5_5_prompt_bundle`, which `CLAUDE_CODE_SUBAGENT_MODEL=opus`
    # now resolves to. Since 2026-09-29 `silent-turn-reminder-off` makes the
    # clauses moot for it; the parse stays as a detector.
    # `quizzical_shore` is not prompt text: it decides whether a hook notice
    # renders hidden or faint (`tengu_quizzical_shore`, `tengu_lilac_dune`).
    "opus_5_5_prompt_bundle": (("silent_turn_reminder", "quizzical_shore"), "2.1.280"),
}

# A fourth way to arm a gate capability, and one no clause parse can see: the
# model's OWN catalogue entry. The gate's first rung, before any clause, is
#
#   Eh(model, cap, ctx) = CLAUDE_CODE_MODEL_CAPABILITIES
#                       ?? (served lookup === true || baked entry lists cap ? true : undefined)
#
# and a defined answer is returned as the gate's answer. CC 2.1.284 shipped
# Sonnet 5.5 with `silent_turn_reminder` in its baked `capabilities:[...]` —
# no `*_prompt_bundle`, no clause — so the clause cut of the day still found and
# cut its two sets, `analyze` read CLEAN, and the reminder was a model default
# again on the new model. It is switched off at its consumer now
# (`silent-turn-reminder-off`); this table is the detector for the next one.
#
# model id -> (the gate capabilities its baked entry lists, first CC version).
# Same contract as MODEL_GATE_CLAUSES: an entry listing a gate capability this
# table does not describe is drift.
CATALOGUE_GATE_DEFAULTS = {
    "claude-sonnet-5-5": (("silent_turn_reminder",), "2.1.284"),
    "claude-haiku-5-5": (("haiku_5_5_early_stopping_guidance",), "2.1.293"),
}

# The capabilities the gate decides that matter here: every one the clauses
# carry, every one MODEL_GATES tracks, and the two section switches that have
# been model-keyed before (`lucky_cerf`, `amber_astrolabe`). Plain model
# features (`effort`, `lean_prompt`, …) are not gate capabilities and never
# count as drift.
GATED_CAPABILITIES = (frozenset(MODEL_GATES)
                      | frozenset(c for caps, _v in MODEL_GATE_CLAUSES.values() for c in caps)
                      | {"lucky_cerf", "amber_astrolabe"})


def catalogue_gate_defaults(blob):
    """{model id: frozenset(gate capabilities its baked entry lists)}, only for
    models that list at least one."""
    out = {}
    for mid, caps in catalogue_models(blob=blob).items():
        hit = frozenset(caps) & GATED_CAPABILITIES
        if hit:
            out[mid] = hit
    return out


def catalogue_drift(found, version=None):
    """Drift lines for catalogue-direct gate capabilities, as gate_drift gives
    them for clauses. A patched entry (minus BINARY_MASKED) is not drift."""
    drift = []
    for mid, caps in sorted(found.items()):
        entry = CATALOGUE_GATE_DEFAULTS.get(mid)
        if entry is None:
            drift.append(f"NEW catalogue default: {mid} lists {sorted(caps)} in its own entry "
                         f"(armed before any clause)")
        elif caps not in (frozenset(entry[0]), frozenset(entry[0]) - BINARY_MASKED):
            drift.append(f"{mid} catalogue entry lists {sorted(caps)}, "
                         f"table says {sorted(entry[0])}")
    for mid, (caps, since) in CATALOGUE_GATE_DEFAULTS.items():
        # absent is our own cut when nothing unmasked is left in the entry
        if (mid not in found and frozenset(caps) - BINARY_MASKED
                and version and ver_key(version) >= ver_key(since)):
            drift.append(f"{mid} catalogue default is GONE (this table has it from CC {since})")
    return drift


def gate_set_literal(caps):
    """The `new Set([...])` argument list as minified JS spells it."""
    return ",".join(f'"{c}"' for c in caps)


# Kept as its own name: `LIVE_ROUTING` uses it as the detector for "this binary
# routes sections by model at all", which is a question about the Fable clause
# specifically — it is the one that can REPLACE a communication fragment.
MODEL_GATE_SET = gate_set_literal(MODEL_GATE_CLAUSES["fable_5_1_prompt_bundle"][0])
MODEL_GATE_CAPABILITY = "fable_5_1_prompt_bundle"

# `function X(cap,model,ctx,env){if(env!==void 0)return env;` — the gate's
# invariant prologue (the env value is returned as the answer, which is what
# makes the env vars three-state). Minified names change every release AND
# between platform builds of the SAME release: on 2.1.278 the Fable predicate
# is `wle` on linux-x64 and `Ele` on win32-x64, and linux's `_4t` (the opus_5
# clause) is a character-width helper in the win32 bundle. Their SHAPE has not
# moved, which is why this matches on shape and follows the predicates rather
# than grepping names a round note happened to record.
GATE_PROLOGUE = re.compile(
    rb"function [A-Za-z_$][\w$]*\((\w+),(\w+),(\w+),(\w+)\)\{if\(\4!==void 0\)return \4;")

# The model clause that follows the prologue, in each shape a build has used.
# Up to 2.1.280 the clause returned true itself:
#
#   if(s===!0||G.has(e)&&wle(n)||H.has(e)&&_4t(r))return!0;
#
# 2.1.283 computes it into a local instead, because client data can now turn a
# model default OFF (`tengu_model_capability_off_from_client_data`):
#
#   let p=G.has(e)&&yhe(n)||H.has(e)&&Rln(r)||z.has(e)&&m(n),u=E4n(r);
#   if(p){if(u?.data?.[e]!==!1)return!0;...
#
# The sets and predicates did not move, so the cut still means what it meant: a
# capability out of a set is a model default gone, and a client-data arm still
# reaches its model. Only the statement around them changed, and the old
# pattern then matched the inner `if(u?.data...)return!0;`, which names no set.
# So a shape counts only when it carries a `<set>.has(<cap>)&&` pair.
GATE_CLAUSE_SHAPES = (
    rb"if\((?:[^;{}]{0,200})\)return!0;",
    rb"let ([\w$]+)=[^;{}]{0,300};if\(\1\)\{if\([^;{}]{0,80}\)return!0;",
)


def gate_model_clause(blob, m):
    """The model clause of the gate whose prologue matched at `m`, as bytes, or
    None. The earliest match of any GATE_CLAUSE_SHAPES that tests the gate's
    capability argument against a set."""
    tail = blob[m.end():m.end() + 400]
    pair = re.escape(m.group(1)) + rb"\)&&"
    best = None
    for shape in GATE_CLAUSE_SHAPES:
        for c in re.finditer(shape, tail):
            if re.search(rb"\.has\(" + pair, c.group(0)):
                if best is None or c.start() < best.start():
                    best = c
                break
    return None if best is None else best.group(0)


# How far from the gate a name may be defined and still be the right one.
# Minified one- and two-letter names repeat across the bundle's ~900 modules,
# so resolving `H` or `g` by "first match in 230 MB" answers with whatever
# module sorts first — on 2.1.280 that made the Fable clause read
# `["document","index","untitled"]` and left the new Opus 5.5 clause with an
# unresolvable predicate. Both are module-local: nearest wins, and nothing is
# resolved from beyond this window.
GATE_NAME_WINDOW = 1 << 16


def gate_near(blob, pattern, anchor, window=GATE_NAME_WINDOW):
    """The match of `pattern` nearest to `anchor`, within `window` bytes either
    side, or None. Distance is measured to the match's start."""
    lo, hi = max(0, anchor - window), min(len(blob), anchor + window)
    best = None
    for m in re.finditer(pattern, blob[lo:hi], re.S):
        d = abs(lo + m.start() - anchor)
        if best is None or d < best[0]:
            best = (d, m)
    return None if best is None else best[1]


CAPABILITY_TEST = re.compile(rb'"[a-z0-9_]+_prompt_bundle"')


def gate_predicate_body(blob, pred, anchor):
    """The body of the gate predicate `pred`, or None.

    Deliberately NOT windowed the way a set variable is. A set is declared
    beside the gate; a predicate is often imported from a sibling chunk, and
    how far that chunk sits from the gate is a property of the BUILD, not of
    the code. Measured on 2.1.280/darwin: in the pristine, Fable's predicate is
    23 KB before the gate, and after tweakcc's own patch set has rewritten the
    bundle the same two land ~95 MB apart. A window wide enough for one is the
    wrong instrument for the other — and the failure is silent, because a
    predicate that cannot be resolved just drops its clause.

    So: every definition of the name in the bundle is a candidate, one that
    tests a `*_prompt_bundle` beats one that does not, and the nearest wins the
    tie. An unrelated module's same-named helper does not test a model
    capability, so it never outranks the real predicate."""
    pat = rb"function " + re.escape(pred) + rb"\([\w$]*\)\{(.{0,400}?)\}"
    best = None
    for m in re.finditer(pat, blob, re.S):
        rank = (0 if CAPABILITY_TEST.search(m.group(1)) else 1, abs(m.start() - anchor))
        if best is None or rank < best[0]:
            best = (rank, m.group(1))
    return None if best is None else best[1]


def model_gate_clauses(blob):
    """The capability sets the gate consults by MODEL, as
    {model_capability_or_None: frozenset(capability names)}.

    None as a key means "a clause whose model predicate could not be resolved
    to a capability name" — reported, never silently dropped. An empty result
    means the gate shape was not recognised at all, which is a finding in its
    own right: it is what a reshaped gate looks like from here."""
    out = {}
    for m in GATE_PROLOGUE.finditer(blob):
        cap = m.group(1)
        clause = gate_model_clause(blob, m)
        if clause is None:
            continue
        for setvar, pred in re.findall(
                rb"([\w$]+)\.has\(" + re.escape(cap) + rb"\)&&([\w$]+)\(", clause):
            names = gate_near(blob, rb"(?<![\w$])" + re.escape(setvar) + rb"=new Set\(\[([^\]]*)\]\)",
                              m.start())
            caps = frozenset(re.findall(rb'"([a-z0-9_]+)"', names.group(1))) if names else frozenset()
            # `pred` is a local function; the capability it tests is the string
            # literal its body hands to the catalogue lookup.
            body = gate_predicate_body(blob, pred, m.start())
            model_cap = None
            if body is not None:
                hit = re.search(rb'"([a-z0-9_]+_prompt_bundle)"', body)
                model_cap = hit.group(1).decode() if hit else None
            out.setdefault(model_cap, set()).update(c.decode() for c in caps)
    return {k: frozenset(v) for k, v in out.items()}


# `Ose` returns the env value ITSELF as the answer (`if (s !== undefined)
# return s`), so the var is not a "set it to anything" switch: the wrong value
# does not fail to mask, it forces the stock branch on every model, including
# the ones the model clause never touched. Presence is therefore not the test;
# the value is. These are the spellings CC reads as off.
MODEL_GATE_OFF = {"0", "false", "no", "off", ""}


def model_gates(env=None, binary=None, version=None, blob=None):
    """(rows, set_intact) where rows is [(cap, var, what, verdict)].

    verdict: 'masked', 'UNMASKED', 'FORCED ON', 'n/a', and for a "binary"
    capability also:
      'masked by env'  the binary does not switch it off (not applied yet, or
                       no binary read) and the env line does: the interim
                       state on a machine between `pull` and `apply`
      'UNVERIFIED'     a binary was read and has no reminder predicate to check
    A "binary" capability that is switched off in the binary is 'masked'
    whatever the env says: the cut sits before the env value is consulted.
    `set_intact` is None when no binary was given, else whether the model
    clauses in the binary are the ones MODEL_GATE_CLAUSES describes."""
    env = settings_env() if env is None else env
    catalogue = {}
    if blob is None and binary:
        try:
            blob = live_blob(binary)
        except (OSError, ValueError):                      # noqa: BLE001
            blob = None
    if blob is not None:
        catalogue = catalogue_gate_defaults(blob)
    rows = []
    predicate = reminder_predicate_state(blob) if blob is not None else None
    for cap, (var, what, how) in MODEL_GATES.items():
        value = env.get(var) if var else None
        off = value is not None and str(value).strip().lower() in MODEL_GATE_OFF
        if how is None:
            verdict = "n/a"
        elif how == "binary" and predicate == "off":
            verdict = "masked"
        elif value is not None and not off:
            verdict = "FORCED ON"
        elif how == "env":
            verdict = "masked" if off else "UNMASKED"
        elif off:
            verdict = "masked by env"
        elif blob is None:
            verdict = "unverified"
        elif predicate is None:
            # a binary read and no predicate in it: the consumer moved, and
            # nothing here can say what now decides the reminder
            verdict = "UNVERIFIED"
        else:
            # the uncut predicate: on wherever a clause, an entry or an arm says
            verdict = "UNMASKED"
        rows.append((cap, var, what, verdict))
    intact = None
    if blob is not None:
        intact = gate_drift(model_gate_clauses(blob), version, catalogue) == []
    return rows, intact


def model_gate_drift(binary, version=None):
    """What the binary's model clauses have that this table does not, and the
    other way round. Empty list = the gate is the one we describe.

    Deliberately not a byte check any more: the 2.1.278 arm was invisible to
    one, and "the literal we know is still there" is not the question. The
    question is whether anything ELSE now decides a section by model.

    `version` is the binary's CC version when the caller knows it. Without it,
    a known clause that is absent is not reported — an older build legitimately
    lacks a newer clause, and the dangerous direction is an unknown clause
    arriving, never a known one leaving."""
    try:
        blob = live_blob(binary)
        found = model_gate_clauses(blob)
        catalogue = catalogue_gate_defaults(blob)
    except (OSError, ValueError) as exc:                   # noqa: BLE001
        return [f"gate unreadable ({exc!r})"]
    return gate_drift(found, version, catalogue)


def gate_drift(found, version=None, catalogue=None):
    """The drift lines for an already-parsed {model capability -> capabilities}
    and, when given, {model id -> gate capabilities its own catalogue entry
    lists} (catalogue_drift). Split out so the shapes can be tested without a
    230 MB binary."""
    if not found:
        return ["no model clause found in the binary — the gate has been reshaped; "
                "re-read it before trusting MODEL_GATES"]
    drift = []
    for model_cap, caps in sorted(found.items(), key=lambda kv: kv[0] or ""):
        if model_cap is None:
            drift.append(f"a model clause arms {sorted(caps)} on an unresolved predicate")
            continue
        entry = MODEL_GATE_CLAUSES.get(model_cap)
        if entry is None:
            drift.append(f"NEW clause: {model_cap} arms {sorted(caps)}")
        elif caps not in (frozenset(entry[0]), frozenset(entry[0]) - BINARY_MASKED):
            # the second form is our own adhoc's work on a patched binary
            drift.append(f"{model_cap} clause is {sorted(caps)}, "
                         f"table says {sorted(entry[0])}")
    for model_cap, (_caps, since) in MODEL_GATE_CLAUSES.items():
        if model_cap not in found and version and ver_key(version) >= ver_key(since):
            drift.append(f"{model_cap} clause is GONE (this table has it from CC {since})")
    if catalogue is not None:
        drift.extend(catalogue_drift(catalogue, version))
    return drift


def model_gates_unmasked(env=None, binary=None, blob=None):
    """The masks that are not in place — the one-line answer for the
    tripwire. 'FORCED ON' is the worst of them: the operator's own settings
    turning the stock text on deliberately."""
    rows, _ = model_gates(env, binary=binary, blob=blob)
    return [(cap, var, verdict) for cap, var, _what, verdict in rows
            if verdict in ("UNMASKED", "FORCED ON", "UNVERIFIED")]


# ------------------------------------------------- the live read-back
#
# Everything else in this file reads FILES: the binary (are our bytes spliced
# in) and settings.json (is the env mask set). Neither can say which branch CC
# took at run time, and that distinction is not academic — it is the whole of
# TODO 27. The bytes were in the binary, 15/15 verified, and the function that
# picks the section returned before reaching them.
#
# This was the first instrument to see the difference: a real model reading its
# own prompt back. Since 2026-09-21 `--delivered` (below) sees it for free and
# for every section and harness, from the captured request; `--live` remains
# for what the capture cannot see, a gateway that rewrites requests after CC.
# It costs a turn, on a model the operator pays for, so the operator's two
# rules (2026-09-15) are built into the mechanism rather than left to whoever
# runs it:
#
#   1. ONE turn per model, never one per fragment. Every exposed section is
#      asked for in the same prompt and scored from the same answer.
#   2. It may only spend anything if per-model routing was actually IDENTIFIED
#      in this binary. No routing found = nothing to disprove = no turn, and
#      `--live` says so and exits 0 rather than asking anyway.
#
# Rule 2 is also what keeps this honest as upstream moves: the probe is chosen
# by what the binary does, not by a list someone updates by hand. A release
# that drops the model clause stops spending; a release that adds one starts.

# Routing shapes worth a live answer. Each detector runs against the live blob
# and returns the capability that does the routing, so the plan can name it.
#
#   id -> (what it is, which target ids it can displace, detector)
LIVE_ROUTING = {
    "model-keyed capability clause": (
        "capabilities that are on by model before any server flag (CC 2.1.270+)",
        ("system-prompt-outcome-first-communication-style",
         "system-prompt-communication-style"),
        lambda blob: MODEL_GATE_CAPABILITY in model_gate_clauses(blob)),
    "lean-prompt communication selector": (
        "the lean-prompt branch that chooses the communication section",
        ("system-prompt-outcome-first-communication-style",
         "system-prompt-communication-style"),
        lambda blob: (b"leanPrompt" in blob
                      and b"# Communicating with the user" in blob)),
    # The router's own NAME ("Tool Description: TodoWrite compact") is a prompt-
    # map label and is not in the binary; the compact body it selects is. So the
    # detector is the stock text a lean model would get instead of ours — which
    # is also exactly the thing the probe would catch it quoting.
    "lean-prompt TodoWrite router": (
        "the lean-prompt branch that chooses the compact TodoWrite description (TODO 24)",
        ("tool-description-todowrite",),
        lambda blob: b"Create and update a task list" in blob),
}

# How to ask for one target, and where the truth to compare against lives.
# Only targets that can be NAMED in a question can be probed: a section by its
# heading, a tool description by its tool. A splice with no name the model can
# be pointed at is reported as exposed-but-unprobeable rather than guessed at.
LIVE_ASKS = {
    "system-prompt-outcome-first-communication-style":
        'the section titled "# Communicating with the user"',
    "system-prompt-communication-style":
        'the section titled "# Communicating with the user"',
    "tool-description-todowrite":
        "the description of your TodoWrite tool",
}


def first_sentence(text):
    """The opening sentence of a fragment body, heading stripped. This is what
    the model is asked for and what its answer is scored against — short enough
    to quote exactly, long enough that stock and ours cannot collide."""
    body = "\n".join(l for l in text.splitlines() if not l.startswith("# ")).strip()
    for end in (". ", ".\n", "; ", " — "):
        if (i := body.find(end)) > 20:
            return body[:i + 1].strip()
    return body[:160].strip()


def live_routing_found(binary):
    """[(name, what, targets, capability-ish detail)] — per-model routing that
    is actually in THIS binary. Empty is the answer that saves the money."""
    blob = live_blob(binary)
    out = []
    for name, (what, targets, detect) in LIVE_ROUTING.items():
        try:
            if detect(blob):
                out.append((name, what, targets))
        except Exception:                                  # noqa: BLE001
            continue
    return out


def catalogue_capability_lists(blob):
    """[(entry start, list start, list end, model id, [capability names])] for
    every `id:"claude-…"` catalogue entry that carries a `capabilities:[...]`
    list of its own, in bundle order. Bytes in, bytes out; offsets index `blob`.
    The list span is the `capabilities:[...]` literal itself."""
    out = []
    for m in re.finditer(rb'id:"(claude-[a-z0-9.\-]+)"', blob):
        tail = blob[m.end():m.end() + 4000]
        caps = re.search(rb"capabilities:\[([^\]]*)\]", tail)
        if not caps:
            continue
        # stop at the next entry so a capability-less model cannot borrow the
        # next model's list
        nxt = re.search(rb'id:"claude-', tail)
        if nxt and caps.start() > nxt.start():
            continue
        out.append((m.start(), m.end() + caps.start(), m.end() + caps.end(),
                    m.group(1).decode(), [c.decode() for c in
                                          re.findall(rb'"([a-z0-9_]+)"', caps.group(1))]))
    return out


def catalogue_models(binary=None, blob=None):
    """{model id: {capabilities}} out of the bundle's own model catalogue, so
    "which models does this routing reach" is read rather than assumed."""
    blob = live_blob(binary) if blob is None else blob
    out = {}
    for _e, _lo, _hi, mid, caps in catalogue_capability_lists(blob):
        out.setdefault(mid, set()).update(caps)
    return out


def configured_models(settings=None):
    """The models this machine actually runs, in the operator's own spelling —
    settings.json `model` plus every key under `modelSettings`. Probing a model
    nobody launches spends real money to answer a question nobody asked."""
    path = settings or (Path.home() / ".claude" / "settings.json")
    data = load_json(path, {}) or {}
    out = []
    for name in ([data.get("model")] + list((data.get("modelSettings") or {}).keys())):
        if isinstance(name, str) and name and name not in out:
            out.append(name)
    return out


# Subagent model pin. From 2026-09-22 the operator ran Opus-tier subagents via
# CLAUDE_CODE_SUBAGENT_MODEL=opus (Sonnet had confabulated over large input
# sweeps). On 2026-09-29, with Sonnet 5.5 out, he reset it to CC's defaults to
# see how that plays out: no pin, so each agent follows its definition or the
# parent, and the spawning model can still name a model per spawn. The checks
# below stay for whenever a pin is set again; unpinned is not a fault.
#
# When pinned, the alias is the reason this needs a per-upgrade check. `opus` is
# resolved by the BUNDLE's own alias table, so a CC release can silently move
# what the pin means; on 2.1.278 it is claude-opus-5. Nothing else in this repo
# would notice, because no marker and no fragment is involved. `status` prints
# the resolution, `update` asks when it MOVED, and ccctl-state.json remembers
# the last answer so the question is asked once per real change rather than
# every run (the ask-once rule).
SUBAGENT_MODEL_VAR = "CLAUDE_CODE_SUBAGENT_MODEL"
SUBAGENT_FORCE_VAR = "CLAUDE_CODE_SUBAGENT_MODEL_FORCE"
# Which built-in agents the pin actually REACHES, measured on 2.1.278 by
# capturing each subagent's own outgoing request (tools/probe_subagent_models.py,
# notes/2026-09-22-subagent-model-routing.md). Static reading got this wrong
# once, so the table is keyed to what was observed:
#
#   absent model field  -> the pin applies          general-purpose, claude,
#                                                    workflow-subagent
#   model:"inherit"     -> the PARENT, pin IGNORED  Plan, comment-thread-analyst
#   Explore             -> capped to opus, pin ignored (the field is never read)
#   a literal model     -> that model, pin ignored  statusline-setup (sonnet),
#                                                    claude-code-guide (haiku)
#
# The middle row is the one that surprises: an explicit `"inherit"` sits on the
# `frontmatter` rung, ABOVE `env`, so it beats the pin — while an ABSENT field
# falls through to the pin. "Inherit" is a choice, not a default.
SUBAGENT_PIN_REACHES = ("general-purpose", "claude", "workflow-subagent")
SUBAGENT_PIN_IGNORED = {
    "Plan": "inherits the parent (model:\"inherit\" outranks the pin)",
    "comment-thread-analyst": "inherits the parent (model:\"inherit\")",
    "Explore": "capped to opus off-ladder, else inherits; its field is never read",
    "statusline-setup": "pinned sonnet",
    "claude-code-guide": "pinned haiku",
}


def alias_table(binary):
    """{alias: model id} out of the bundle's own alias table — `opus` ->
    `claude-opus-5` on 2.1.278.

    Read rather than guessed: `resolve_model` falls back to the alphabetically
    last id in a family, which is a heuristic that happens to agree today and
    would quietly disagree the first time a family gains a name that sorts
    wrong. What CLAUDE_CODE_SUBAGENT_MODEL means is decided by THIS table."""
    blob = live_blob(binary).decode("latin-1")
    out = {}
    for alias in ("opus", "sonnet", "haiku", "fable"):
        m = re.search(r'(?:\b|")' + alias + r'"?:"(claude-[a-z0-9.\-]+)"', blob)
        if m:
            out[alias] = m.group(1)
    return out


def subagent_model_state(binary, env=None):
    """What a spawned subagent gets when the caller names no model, as a dict:
    the pin as written, what this binary resolves it to, and whether FORCE is
    on. `None` resolution means the pin names something this build has no id
    for — worth saying out loud, because CC would then fall back to the parent
    silently."""
    env = settings_env() if env is None else env
    pin = (env.get(SUBAGENT_MODEL_VAR) or "").strip()
    force = (env.get(SUBAGENT_FORCE_VAR) or "").strip() not in ("", "0", "false")
    resolved = None
    if pin and pin != "inherit":
        table = alias_table(binary)
        resolved = table.get(pin) or resolve_model(pin, catalogue_models(binary))
    return {"pin": pin or None, "resolved": resolved, "force": force}


def subagent_model_summary(cur, last):
    """(line, moved) — the status line, and whether this needs the operator.

    `moved` is true only when the RESOLUTION changed under an unchanged pin:
    that is the case he asked to be asked about, and the case no other check in
    this repo can see."""
    if not cur["pin"]:
        return ("not pinned (CC defaults, the operator's choice since 2026-09-29) — a "
                "subagent with no model named follows its agent definition, else the parent; "
                "the spawning model can still name one per spawn"), False
    if cur["pin"] == "inherit":
        return f"{SUBAGENT_MODEL_VAR}=inherit — subagents follow the parent model", False
    where = cur["resolved"] or "NOTHING IN THIS BUILD"
    line = f"{cur['pin']} -> {where}"
    if cur["force"]:
        line += f"; {SUBAGENT_FORCE_VAR} ON — a per-spawn model is overridden"
    else:
        line += "; a per-spawn model still wins"
    if cur["resolved"] is None:
        return line + " — CC would fall back to the parent", True
    prev = (last or {}).get("resolved")
    if prev and prev != cur["resolved"]:
        return line + f" — MOVED, was {prev}", True
    return line, False


def resolve_model(name, catalogue):
    """settings.json spellings are not catalogue ids: `opus[1m]` is a context
    variant, `opus` is an alias. Reduce to the id whose capabilities decide the
    routing, or None when this build has no such model."""
    base = re.sub(r"\[.*?\]$", "", name).strip()
    if base in catalogue:
        return base
    for cid in catalogue:
        if cid.endswith(base) or base.startswith(cid):
            return cid
    fam = {"opus": "claude-opus-", "sonnet": "claude-sonnet-", "haiku": "claude-haiku-",
           "fable": "claude-fable-"}.get(base)
    if fam:
        hits = sorted(c for c in catalogue if c.startswith(fam))
        return hits[-1] if hits else None
    return None


def live_questions(cfg, found):
    """[(ask text, [target ids it decides])] — the questions to put to one
    model, deduplicated BY QUESTION.

    Two of our fragments are alternative replacements of the SAME section
    (different upstream spans), so a naive per-target loop asks the identical
    question twice and pays twice for it. They share one question, and the
    answer passes if it is either of them: what is being tested is whether OUR
    text reached the prompt, not which span upstream happened to ship."""
    groups = {}
    for _name, _what, tids in found:
        for tid in tids:
            if tid in LIVE_ASKS and repo_target(cfg, tid):
                # two routing shapes can expose the same target; it is still
                # one question and one fragment to score against
                seen = groups.setdefault(LIVE_ASKS[tid], [])
                if tid not in seen:
                    seen.append(tid)
    return list(groups.items())


def live_plan(binary, cfg, models=None):
    """[(spelling, id, [(ask, [target ids])])] — one entry per model to ask.
    One turn each, every question in that turn.

    A model is asked only when the routing that reaches it is BOTH present in
    the binary and carried by that model's own capability list. Models are
    deduplicated by the id they RESOLVE to: `opus[1m]` and `claude-opus-5` are
    two spellings of one prompt, and asking both would spend twice for one
    answer."""
    found = live_routing_found(binary)
    if not found:
        return [], []
    questions = live_questions(cfg, found)
    if not questions:
        return [], found
    catalogue = catalogue_models(binary)
    routed_caps = {c for c in MODEL_GATES} | {MODEL_GATE_CAPABILITY, "lean_prompt"}
    plan, seen = [], set()
    for spelling in (models or configured_models()):
        mid = resolve_model(spelling, catalogue)
        if not mid or mid in seen:
            continue
        if not (catalogue.get(mid, set()) & routed_caps):
            continue                      # no routing reaches this model: free pass
        seen.add(mid)
        plan.append((spelling, mid, questions))
    return plan, found


def repo_target(cfg, tid):
    """The targets.json entry for an id, or None. Live probes are only built
    for targets this repo currently deploys."""
    try:
        tg = load_json(repo_dir(cfg) / "edits" / "targets.json", [])
        for t in tg:
            if t.get("id") == tid:
                return t
    except Exception:                                      # noqa: BLE001
        return None
    return None


def live_prompt(questions):
    """The single turn that covers every question for one model. Numbered, so
    the reply can be split back apart."""
    asks = [f"{i}. The first sentence of {ask}." for i, (ask, _tids) in enumerate(questions, 1)]
    return ("Quote, verbatim and with no commentary, one per numbered line:\n"
            + "\n".join(asks)
            + "\nIf a section or tool is not present, answer that number with "
              "the single word ABSENT.")


def live_expected(cfg, tids):
    """[(target id, first sentence we deployed, source path)] for one question."""
    out = []
    for tid in tids:
        t = repo_target(cfg, tid)
        out.append((tid, first_sentence(edit_text(repo_dir(cfg) / t["source"])), t["source"]))
    return out


def live_score(answer, expected):
    """(verdict, which target matched). PASS if the model quoted ANY of the
    fragments that can occupy this slot — they are alternatives, not a set that
    must all be present.

    Compared on letters and digits only: a model may re-wrap, re-punctuate or
    drop a trailing clause, and none of that changes which text was in its
    prompt."""
    norm = lambda s: re.sub(r"[^a-z0-9]+", "", s.lower())      # noqa: E731
    a = norm(answer)
    if not a:
        return "NO ANSWER", None
    for tid, sentence, _src in expected:
        head = norm(sentence)[:60]
        if head and head in a:
            return "PASS", tid
    # The model took the escape hatch: the section or tool is not in its prompt
    # at all. That is a fact about the SESSION, not about our fragment — a
    # `claude -p --max-turns 1` run does not load every tool an interactive
    # session does. Scoring it FAIL would manufacture a finding, so it is
    # reported as what it is: this question could not be asked here.
    if a in ("absent", "absentn") or norm(answer[:40]) == "absent":
        return "ABSENT", None
    return "FAIL", None


def live_ask_model(spelling, prompt, timeout=180):
    """One turn. Returns (text, error). Deliberately `claude -p --max-turns 1`
    rather than an SDK call: it is the same binary, the same settings layers
    and the same env this machine's sessions get, which is the thing under
    test. An SDK call would prove something about the API, not about CC."""
    exe = claude_binary()
    try:
        r = run([str(exe), "-p", "--model", spelling, "--max-turns", "1", prompt],
                check=False, timeout=timeout)
    except Exception as exc:                               # noqa: BLE001
        return "", repr(exc)
    if r.returncode != 0:
        return r.stdout.strip(), (r.stderr or "").strip()[:200] or f"exit {r.returncode}"
    return r.stdout.strip(), None


def live_record(state=None):
    """The last live read-back recorded on this machine, or None."""
    return (state if state is not None else load_json(STATE, {})).get("liveReadback")


def live_summary(ver, state=None):
    """The one line `status` prints for free: what a spent turn proved, and
    whether it still describes the binary that is installed now."""
    rec = live_record(state)
    if not rec:
        return "never run on this machine — `ccctl.py status --live` (spends turns)"
    rows = rec.get("results") or []
    # Only a FAIL counts against a model; an unasked question is not a verdict.
    bad = [r for r in rows if "FAIL" in (r.get("targets") or {}).values()]
    unasked = sum(1 for r in rows for v in (r.get("targets") or {}).values()
                  if v in ("ABSENT", "NO ANSWER", "ERROR"))
    head = (f"{len(rows) - len(bad)}/{len(rows)} model(s) quoted our text back, "
            f"{rec.get('when', '?')[:10]}"
            + (f" ({unasked} question(s) unasked)" if unasked else ""))
    if rec.get("ccVersion") != ver:
        return f"{head} — but that was CC {rec.get('ccVersion')}, this is {ver}: STALE, re-run"
    if bad:
        return f"{head} — {', '.join(r['model'] for r in bad)} did NOT"
    return head


def cmd_live(args):
    """`status --live`: the one check that reads the prompt instead of the file.

    Structured so that the expensive half cannot run by accident — no routing,
    no spend; already answered for this version, no spend; and the plan with
    its exact turn count is printed before anything is launched."""
    cfg = require_cfg()
    binary = claude_binary()
    ver = cc_version()
    state = load_json(STATE, {})
    models = [args.model] if getattr(args, "model", None) else None
    plan, found = live_plan(binary, cfg, models)

    if not found:
        # The operator's rule, enforced rather than remembered: nothing routes
        # per model in this build, so there is nothing a live turn could prove
        # that the marker check has not already proven for free.
        print(f"no per-model routing identified in CC {ver} — nothing to prove live, "
              f"no turns spent.")
        print("  (the marker check already covers a binary whose sections are not "
              "model-selected)")
        return
    print(f"per-model routing in CC {ver}:")
    for name, what, tids in found:
        print(f"  - {name}: {what}")
        print(f"    can displace: {', '.join(tids)}")
    if not plan:
        print("\nnone of the models this machine is configured to run carry a capability "
              "that routing reaches — no turns spent.")
        print(f"  configured: {', '.join(configured_models()) or '(none)'}")
        return

    rec = live_record(state)
    if (rec and rec.get("ccVersion") == ver and not getattr(args, "force", False)
            and {r["model"] for r in rec.get("results", [])} >= {p[0] for p in plan}):
        print(f"\nalready answered for CC {ver} on {rec.get('when', '?')[:10]} — "
              f"a turn buys nothing until the version moves:")
        for r in rec.get("results", []):
            for tid, verdict in (r.get("targets") or {}).items():
                print(f"  {verdict:9} {r['model']:22} {tid}")
        print("  re-run anyway with --force")
        return

    print(f"\nplan — {len(plan)} turn(s), one per model, every question in the same turn:")
    for spelling, mid, questions in plan:
        print(f"  {spelling}  (resolves to {mid})")
        for ask, tids in questions:
            print(f"      asks for: {ask}")
            print(f"        passes on: {' or '.join(tids)}")
    if not getattr(args, "yes", False):
        try:
            if input("\nspend these turns? [y/N] ").strip().lower() not in ("y", "yes"):
                print("nothing spent.")
                return
        except EOFError:
            print("not a terminal and --yes was not given; nothing spent.")
            return

    results = []
    for spelling, mid, questions in plan:
        print(f"\nasking {spelling} ...")
        text, err = live_ask_model(spelling, live_prompt(questions))
        if err and not text:
            print(f"  ERROR: {err}")
            results.append({"model": spelling, "id": mid,
                            "targets": {q[0]: "ERROR" for q in questions}, "error": err})
            continue
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        verdicts, answers = {}, {}
        for i, (ask, tids) in enumerate(questions):
            expected = live_expected(cfg, tids)
            # the numbered line when the model numbered its answer, else the
            # whole reply — a single-question probe usually answers bare
            answer = next((l for l in lines if l.startswith(f"{i + 1}.")), None)
            answer = answer[len(f"{i + 1}."):].strip() if answer else (
                text if len(questions) == 1 else "")
            verdict, matched = live_score(answer, expected)
            verdicts[ask] = verdict
            answers[ask] = answer[:300]
            print(f"  {verdict:9} {ask}" + (f"  [{matched}]" if matched else ""))
            print(f"            expected: {expected[0][1][:100]}")
            print(f"            answered: {(answer or '(nothing)')[:100]}")
            if verdict == "FAIL":
                print(f"            our text is in {', '.join(e[2] for e in expected)}; the "
                      f"model did not quote it — the fragment did not reach this model's prompt")
            elif verdict == "ABSENT":
                print(f"            not in this session's prompt at all — a one-turn `-p` run "
                      f"does not load every tool an interactive session does.")
                print(f"            inconclusive, NOT a finding about "
                      f"{', '.join(e[2] for e in expected)}; ask it from a session that has "
                      f"the tool if you need this one answered.")
            elif verdict == "NO ANSWER":
                print(f"            the model returned nothing for this line — an instrument "
                      f"problem, not evidence either way.")
        results.append({"model": spelling, "id": mid, "targets": verdicts,
                        "answers": answers})

    state["liveReadback"] = {"ccVersion": ver, "platform": sys.platform,
                             "when": datetime.now(timezone.utc).astimezone().isoformat(
                                 timespec="seconds"),
                             "results": results}
    save_json(STATE, state)
    # Only a FAIL is evidence against a fragment. ABSENT, NO ANSWER and ERROR
    # are the instrument failing to ask, and an exit code that cannot tell
    # those apart teaches everyone to ignore it.
    failed = [r for r in results if "FAIL" in r["targets"].values()]
    unasked = [(r["model"], q) for r in results for q, v in r["targets"].items()
               if v in ("ABSENT", "NO ANSWER", "ERROR")]
    answered = sum(1 for r in results for v in r["targets"].values() if v in ("PASS", "FAIL"))
    print(f"\nrecorded; `status` now shows this for free until CC moves off {ver}.")
    if unasked:
        print(f"{len(unasked)} question(s) could not be asked in a one-turn session "
              f"(inconclusive, not counted for or against):")
        for model, q in unasked:
            print(f"  - {model}: {q}")
    if failed:
        print("FAIL: a patched fragment did not reach the prompt of "
              + ", ".join(r["model"] for r in failed))
        print("      this is the TODO 27 failure class: markers verify, the branch is not taken.")
        sys.exit(2)
    print(f"OK: {answered} question(s) answered, every one of them quoted our text back.")


# ------------------------------------------------- the delivered prompt
#
# `--live` pays a model to quote one sentence back, always through `claude
# -p`, so it can say nothing about any other harness and nothing about a
# section it did not ask for. TODO 30 needed both: a Fable session under T3
# Code (an Agent SDK host) reported like stock with every marker green, and
# the first question was whether that host hands the model a different prompt.
#
# There is a cheaper instrument than a model. CC honours ANTHROPIC_BASE_URL
# with the operator's OAuth login, so pointing it at a listener on 127.0.0.1
# makes it hand over the exact first request of a session — system prompt,
# the mid-conversation system message a style arrives in since 2.1.268, the
# attachments, every tool description — and the listener answers 400. Nothing
# reaches the API, nothing is spent, and no model judges anything (the
# operator retired that on 2026-08-19). One run per model and harness, a few
# seconds each.
#
# What it does not reproduce, so a pass is not read as more than it is:
#   - an interactive terminal session. `-p` forces entrypoint `sdk-cli`, and a
#     pty run would have to answer the folder-trust dialog and leave a
#     transcript. The intro line and the entrypoint-keyed client data differ;
#     `flags` shows the cached client data for `cli`, so that half is visible.
#   - shell-exported CLAUDE_* variables. They are stripped, because the caller
#     is usually a CC session whose CLAUDECODE, ENTRYPOINT and SESSION_ATTENDED
#     would leak in; settings.json `env` is re-applied by CC from disk, which
#     is the layer every fresh session builds.
#   - a gateway that rewrites requests after CC. `--live` still reaches that.
#
# Side effects, measured: CC still fetches flags and client data from
# Anthropic (metadata, not inference) and refreshes their caches in
# ~/.claude.json, which is how the capture sees today's experiment arms.
# `--no-session-persistence` keeps the transcript off disk; the empty memory
# directory CC makes for the capture cwd is removed afterwards.

# shape -> (what it stands for, env it sets, argv(model, turns), stdin)
#
# The sdk line is T3 Code's own launch line, read off its process
# (/proc/<pid>/cmdline) on 2026-09-21, minus the host's MCP server and the
# flags that only set request parameters (thinking, effort). The append flag
# stays: it is what selects the "running within the Claude Agent SDK" intro.
# `turns` is the scripted tool loop's length; `-p` needs the turn budget and
# Read allowed, the sdk shape runs bypassPermissions as T3 does.
DELIVERED_SHAPES = {
    "print": ("`claude -p`, as scripts and the old read-back run it (entrypoint sdk-cli)",
              # the prompt goes first: --allowedTools takes a list and would
              # swallow a prompt that came after it
              {}, lambda model, turns=0: ["-p", "hi", "--model", model, "--max-turns", str(turns + 1),
                                          "--allowedTools", "Read",
                                          "--no-session-persistence"], None),
    "sdk": ("an Agent SDK host, launched the way T3 Code launches it (entrypoint sdk-ts)",
            {"CLAUDE_CODE_ENTRYPOINT": "sdk-ts"},
            lambda model, turns=0: ["--output-format", "stream-json", "--verbose",
                           "--input-format", "stream-json", "--model", model,
                           "--permission-prompt-tool", "stdio",
                           "--setting-sources=user,project,local",
                           "--permission-mode", "bypassPermissions",
                           "--allow-dangerously-skip-permissions",
                           "--include-partial-messages",
                           "--append-system-prompt", "(host text)",
                           "--no-session-persistence"],
            json.dumps({"type": "user", "message": {"role": "user", "content": "hi"},
                        "parent_tool_use_id": None, "session_id": ""}) + "\n"),
}

# The CC entrypoint each shape reports, which is also the key its client data
# is cached under.
SHAPE_ENTRYPOINT = {"print": "sdk-cli", "sdk": "sdk-ts"}

# How the bypass/auto-mode shell block announces itself in a request. The
# adhoc rewrites one variant of the text under it; upstream ships a second one
# behind `tengu_cozy_teapot`, so "the block is here and our sentence is not"
# is what a displacement looks like from the request.
SHELL_BLOCK_HEADERS = ("While bypass permissions mode is active", "While auto mode is active")
# The long git block the Bash tool carries on 200k-window models instead of the
# compact section git-commit-authority rewrites.
GIT_LONG_BLOCK_HEAD = "Only create commits when requested"
# Sentence rewrites whose stock clause, found in a request, means upstream's
# text holds the slot; absent both ways, the reason the slot is not there.
ADHOC_STOCK_CLAUSES = {
    "action-caution-outcome-line": (
        ACTION_CAUTION_STOCK.decode(),
        "no action_caution section in this session (its builder returns null off the lean models)"),
    "bash-audience-note-result-not-output": (
        BASH_NOTE_TAIL,
        "the audience note did not fire: no Bash output long enough to be truncated"),
}

# Sections that carry our text on every model this repo supports, whatever
# the harness: the floor has no gate by construction, and the two env lines of
# item 19 force the other two on. Absent from a request is a finding for these
# even when no stock text stands in their place.
DELIVERED_EVERYWHERE = ("disposition-floor-under-every-output-style",
                        "system-prompt-delivering-work-at-full-scope",
                        "system-prompt-correction-restraint")


def capture_env(extra):
    """This process's environment minus everything CC reads as its own
    identity, plus `extra`. ANTHROPIC_* goes too, so the base URL set here is
    the only one."""
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("CLAUDE") or k.startswith("ANTHROPIC"))}
    env.update(extra)
    return env


def cc_project_dir(cwd):
    """CC's per-project state directory for `cwd`: every character outside
    [A-Za-z0-9] becomes '-' (measured: /tmp/ccctl-capture-fx88s_tt ->
    -tmp-ccctl-capture-fx88s-tt)."""
    return Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(cwd))


def sse(events):
    """A Messages API event stream, as CC's client reads it."""
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def scripted_reply(n, model, tool_input=None, stream=True):
    """One assistant turn: a single Read call with no text (a silent tool turn)
    when `tool_input` is given, else "done" and end_turn."""
    usage = {"input_tokens": 1, "output_tokens": 1,
             "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    if tool_input is not None:
        block = {"type": "tool_use", "id": f"toolu_ccctl_{n:02d}", "name": "Read", "input": {}}
        delta = {"type": "input_json_delta", "partial_json": json.dumps(tool_input)}
        stop, whole = "tool_use", dict(block, input=tool_input)
    else:
        block = {"type": "text", "text": ""}
        delta = {"type": "text_delta", "text": "done"}
        stop, whole = "end_turn", {"type": "text", "text": "done"}
    if not stream:
        return json.dumps({"id": f"msg_ccctl_{n}", "type": "message", "role": "assistant",
                           "model": model, "content": [whole], "stop_reason": stop,
                           "stop_sequence": None, "usage": usage}).encode(), "application/json"
    return sse([
        {"type": "message_start", "message": {
            "id": f"msg_ccctl_{n}", "type": "message", "role": "assistant", "model": model,
            "content": [], "stop_reason": None, "stop_sequence": None, "usage": usage}},
        {"type": "content_block_start", "index": 0, "content_block": block},
        {"type": "content_block_delta", "index": 0, "delta": delta},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
         "usage": {"output_tokens": 1}},
        {"type": "message_stop"}]), "text/event-stream"


def capture_request(model, shape, binary=None, timeout=120, turns=0):
    """(requests, error): the /v1/messages bodies CC sends for one session of
    this shape, answered on 127.0.0.1 so nothing reaches the API.

    turns=0 answers the first request with a 400: one request, the prompt.
    turns=N scripts N silent tool turns (one Read of a file in the capture's
    own cwd, no text) and then a closing "done", and returns every request
    the session's main loop sent. That is what shows a per-turn injection,
    which exists only after the first request: the silent-turn reminder fires
    after five silent turns, for one. Side calls (titles, classifiers) are
    answered and left out.

    `binary` is the live CC binary by default. A test hands in a stand-in
    instead, as a path or as an argv prefix such as [python, script] — the
    list form is what lets the same stand-in run on win32, where a shebang
    script is not an executable (CreateProcess error 193)."""
    import http.server
    import threading
    got = []
    cwd = Path(tempfile.mkdtemp(prefix="ccctl-capture-"))
    probe = cwd / "probe.txt"
    probe.write_text("probe\n", encoding="utf-8")

    class Sink(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def send(self, code, body, kind="application/json"):
            self.send_response(code)
            self.send_header("content-type", kind)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def refuse(self, code):
            self.send(code, json.dumps({"type": "error", "error": {
                "type": "invalid_request_error",
                "message": "captured locally by ccctl; nothing was sent"}}).encode())

        def do_GET(self):                                   # noqa: N802
            self.refuse(404)

        def do_POST(self):                                  # noqa: N802
            raw = self.rfile.read(int(self.headers.get("content-length") or 0))
            try:
                body = json.loads(raw)
            except ValueError:
                body = {}
            if not (self.path.startswith("/v1/messages") and "count_tokens" not in self.path):
                return self.refuse(404)
            main = bool(body.get("system")) and bool(body.get("tools"))
            if main:
                got.append(body)
            if not turns:
                return self.refuse(400)
            n = len(got)
            silent = main and n <= turns
            payload, kind = scripted_reply(n, body.get("model", model),
                                           {"file_path": str(probe)} if silent else None,
                                           stream=bool(body.get("stream")))
            self.send(200, payload, kind)

    _what, env_extra, argv, stdin = DELIVERED_SHAPES[shape]
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Sink)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    proj = cc_project_dir(cwd)
    env = capture_env({**env_extra,
                       "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{srv.server_address[1]}"})
    launcher = ([str(b) for b in binary] if isinstance(binary, (list, tuple))
                else [str(binary or claude_binary())])
    try:
        r = subprocess.run(launcher + argv(model, turns),
                           input=stdin or "", env=env, cwd=cwd, capture_output=True,
                           text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return [], f"session did not finish within {timeout}s"
    except OSError as exc:
        return [], repr(exc)
    finally:
        srv.shutdown()
        srv.server_close()
        shutil.rmtree(cwd, ignore_errors=True)
        # the cwd is fresh, so anything under its project dir is this run's
        if proj.exists() and not any(p.is_file() for p in proj.rglob("*")):
            shutil.rmtree(proj, ignore_errors=True)
    if not got:
        tail = (r.stderr or r.stdout or "").strip()[-200:]
        return [], f"CC sent no messages request (exit {r.returncode})" + (f": {tail}" if tail else "")
    return got, None


def injected_texts(req):
    """What the harness put into a request's messages rather than the user or
    a tool: mid-conversation system messages (the reminder's channel since the
    `mid-conversation-system` beta) and <system-reminder> blocks, including
    those appended to tool results."""
    out = []
    for m in req.get("messages") or []:
        c = m.get("content")
        blocks = c if isinstance(c, list) else [{"type": "text", "text": c or ""}]
        for b in blocks:
            if not isinstance(b, dict):
                continue
            texts = [b.get("text")] if isinstance(b.get("text"), str) else []
            inner = b.get("content")
            if isinstance(inner, str):
                texts.append(inner)
            elif isinstance(inner, list):
                texts += [x.get("text") for x in inner if isinstance(x, dict)
                          and isinstance(x.get("text"), str)]
            for t in texts:
                if m.get("role") == "system":
                    out.append(t.strip())
                out += [x.strip() for x in re.findall(r"<system-reminder>(.*?)</system-reminder>",
                                                      t, re.S)]
    return out


def per_turn_injections(reqs):
    """[(request number, text)]: what the harness added after the first
    request, each at its first sighting."""
    seen = set(injected_texts(reqs[0])) if reqs else set()
    out = []
    for n, q in enumerate(reqs[1:], 2):
        for t in injected_texts(q):
            if t and t not in seen:
                seen.add(t)
                out.append((n, t))
    return out


# How the silent-turn reminder starts: the 2.1.278 default, and the server
# text (`tengu_hushed_lark_text`) both open this way.
SILENT_REMINDER_OPENING = "The user hasn't heard from you"


def silent_reminder_turns():
    """After how many silent turns CC sends the reminder: the env override,
    else the cached server value, else CC's own default of 5."""
    for value in (settings_env().get("CLAUDE_CODE_SILENT_TURN_REMINDER_TURNS"),
                  (load_json(cc_config_path(), {}).get("cachedGrowthBookFeatures") or {})
                  .get("tengu_hushed_lark")):
        try:
            if value is not None and int(value) >= 1:
                return int(value)
        except (TypeError, ValueError):
            continue
    return 5


def request_texts(req):
    """(system, messages, {tool: description}) — the three places a deployed
    byte can land in a request."""
    sysb = req.get("system") or []
    system = ("\n".join(b.get("text", "") for b in sysb if isinstance(b, dict))
              if isinstance(sysb, list) else str(sysb))
    parts = []
    for m in req.get("messages") or []:
        c = m.get("content")
        for b in (c if isinstance(c, list) else [{"text": c or ""}]):
            if isinstance(b, dict) and isinstance(b.get("text"), str):
                parts.append(b["text"])
    tools = {t.get("name"): t.get("description") or "" for t in req.get("tools") or []}
    return system, "\n".join(parts), tools


def delivered_style(messages):
    """The style a request carries, by name, or None. Since 2.1.268 a style
    arrives as a mid-conversation system message headed this way."""
    m = re.search(r"^# Output Style: (.+)$", messages, re.M)
    return m.group(1).strip() if m else None


def marker_owners(cfg, markers, adhoc_plan):
    """{marker: target id} for every marker that is prompt prose. Fragment
    markers are found in the edit bodies, adhoc ones in the replacement bytes
    the plan derives; a marker that is code (the delegation cut's `let t=`)
    can never appear in a request and is left out."""
    targets = load_json(repo_dir(cfg) / "edits" / "targets.json", [])
    bodies = {t["id"]: edit_text(repo_dir(cfg) / t["source"]) for t in targets
              if t.get("implementation") == "plan_fragments"}
    owners = {}
    for mk in markers:
        if CODEISH_RE.search(mk) or mk.startswith(")"):
            continue
        tid = next((t for t, body in bodies.items() if mk in body), None)
        if tid is None:
            tid = next((p["name"] for p in adhoc_plan
                        if p.get("new") and mk.encode("latin-1", "ignore") in p["new"]), None)
        if tid:
            owners[mk] = tid
    return owners


def stock_probes(entry, n=3):
    """Short verbatim runs from the middle of a fragment's longest stock
    pieces: present in a request means upstream's text holds the slot."""
    runs = sorted((p for p in entry.get("pieces", []) if len(p) >= 60), key=len, reverse=True)
    return [p[len(p) // 2 - 25: len(p) // 2 + 25] for p in runs[:n]]


def delivered_verdicts(req, owners, snapshot=None):
    """[(target id, verdict, detail)] for one captured request.

    DELIVERED       our text is in the request, every marker of it
    PARTIAL         some of its markers are: an older version of our text,
                    i.e. a binary built before the repo last changed it
    ALTERNATIVE     the sibling fragment for the same section is (by design)
    DISPLACED       the slot is in the request with other text in it
    MISSING         one of DELIVERED_EVERYWHERE is not in the request at all
    NOT IN SESSION  neither ours nor upstream's text for the slot is present:
                    this session does not carry that tool, variant or mode"""
    system, messages, tools = request_texts(req)
    everything = "\n".join([system, messages] + list(tools.values()))
    by_target = {}
    for mk, tid in owners.items():
        by_target.setdefault(tid, []).append(mk)
    delivered = {tid for tid, mks in by_target.items() if any(m in everything for m in mks)}
    rows = []
    for tid, mks in sorted(by_target.items()):
        absent = [m for m in mks if m not in everything]
        if tid in delivered and absent:
            rows.append((tid, "PARTIAL", "our text, an older version of it: no "
                         + ", ".join(f"'{m}'" for m in absent)))
            continue
        if tid in delivered:
            if any(m in system for m in mks):
                where = "system prompt"
            elif any(m in messages for m in mks):
                where = "a message"
            else:
                where = "tool " + next((n for n, d in tools.items() if any(m in d for m in mks)), "?")
            rows.append((tid, "DELIVERED", where))
            continue
        ask = LIVE_ASKS.get(tid)
        sibling = next((s for s in delivered if s != tid and ask and LIVE_ASKS.get(s) == ask), None)
        if sibling:
            rows.append((tid, "ALTERNATIVE", f"{sibling} holds this section for this model"))
            continue
        if tid == "bypass-auto-shell-block-invert":
            head = next((h for h in SHELL_BLOCK_HEADERS if h in messages), None)
            if head:
                body = messages.split(head, 1)[1].lstrip(": \n")[:90].replace("\n", " ")
                rows.append((tid, "DISPLACED", f"the '{head}' block says: {body}..."))
            else:
                rows.append((tid, "NOT IN SESSION", "no bypass/auto-mode shell block in this session"))
            continue
        if tid == "git-commit-authority":
            if GIT_COMPACT.in_text(everything):
                rows.append((tid, "DISPLACED", "the stock compact # Git sentence is in the request"))
            elif GIT_LONG_BLOCK_HEAD in everything:
                rows.append((tid, "DISPLACED", "this model gets the long '# Committing changes with git' "
                                               "block (C-02 follow-up, not this target)"))
            else:
                rows.append((tid, "NOT IN SESSION", "no git section in this session's Bash tool"))
            continue
        if tid == "subagent-delegation-opt-in":
            has_agent = "Agent" in tools or "Task" in tools
            rows.append((tid, "DISPLACED" if has_agent else "NOT IN SESSION",
                         "the agent tool lacks the nested-delegation opt-in" if has_agent
                         else "no agent tool in this session"))
            continue
        if tid in ADHOC_STOCK_CLAUSES:
            stock, absent_why = ADHOC_STOCK_CLAUSES[tid]
            held = stock.in_text(everything) if isinstance(stock, StockSentence) else stock in everything
            if held:
                rows.append((tid, "DISPLACED", f"the stock '{str(stock)[:48]}' is in the request"))
            else:
                rows.append((tid, "NOT IN SESSION", absent_why))
            continue
        entry = (snapshot or {}).get(tid)
        if entry and any(p and p in everything for p in stock_probes(entry)):
            rows.append((tid, "DISPLACED", "upstream's text for this slot is in the request instead"))
        elif tid in DELIVERED_EVERYWHERE:
            rows.append((tid, "MISSING", "not in the request, and nothing gates it off by design"))
        elif tid.startswith("tool-description-"):
            name = tid.split("tool-description-", 1)[1]
            has = any((n or "").lower() == name for n in tools)
            rows.append((tid, "NOT IN SESSION" if not has else "DISPLACED",
                         f"no {name} tool in this session" if not has
                         else f"the {name} tool carries other text"))
        else:
            rows.append((tid, "NOT IN SESSION",
                         "neither ours nor upstream's text for this slot is in the request"))
    return rows


# The server's third channel, after GrowthBook flags and the model catalogue:
# per-(model, entrypoint) "client data", fetched from Anthropic and cached in
# ~/.claude.json `clientDataCacheSlots`. CC reads it after the env var and
# before the GrowthBook flag, and it carries named experiments
# (`experimentKey`). Found 2026-09-21 because the delivered prompt of an Opus 5
# SDK session did not carry our shell-block text while the flag cache said
# `strict`: the client data said `relaxed`. `flags` could not see it.
#
# key -> (env var that overrides it, what it routes, target it can displace).
# Read out of the 2.1.278 bundle; a key missing from both tables is printed as
# unclassified, so a new one is news rather than silence.
CLIENT_DATA_ROUTES = {
    "tengu_cozy_teapot": ("CLAUDE_CODE_COZY_TEAPOT",
                          "'relaxed' puts upstream's bash-first variant in the bypass/auto-mode "
                          "shell block instead of ours", "bypass-auto-shell-block-invert"),
    "breezy_horizon": ("CLAUDE_CODE_BREEZY_HORIZON",
                       "builds the prompt with another model's capabilities (model -> model)",
                       None),
    "tengu_thrifty_sonic": ("CLAUDE_CODE_THRIFTY_SONIC",
                            "turns the bypass/auto-mode shell block on", None),
    "tengu_heron_brook": (None, "server text for the heron_brook section, which "
                                "delegation-override-cut removes", "delegation-override-cut"),
    # Its only reader is the brook_heron section's body (read in the 2.1.294
    # bundle: a string, or a per-model/per-effort map of strings, returned
    # as the section), which the same cut replaces with `()=>null`. First
    # served on macOS 2026-10-08, for Opus 5.5: "# Memory, notes and feedback".
    "tengu_brook_heron": (None, "server text for the brook_heron section, which "
                                "delegation-override-cut removes", "delegation-override-cut"),
    "tengu_toasty_thimble": ("CLAUDE_CODE_TOASTY_THIMBLE",
                             "the per-turn 'privately list what you need next' reminder "
                             "('' turns it off)", None),
    "tengu_willow_tern": ("CLAUDE_CODE_WILLOW_TERN",
                          "willow_tern writing style, which we cut", "willow-tern-writing-style-cut"),
    # CC 2.1.293's "prompt ablation": every string in the list is deleted from
    # the system prompt and tool descriptions before the request is sent. It
    # can reach our text as easily as stock's, so an arm is judged by reading
    # the list: if any entry occurs in edits/ or in our adhoc replacements, it
    # conflicts and is masked with `CLAUDE_CODE_REMOVE_PROMPT_STRINGS=[]`.
    "remove_prompt_strings": ("CLAUDE_CODE_REMOVE_PROMPT_STRINGS",
                              "deletes the listed strings from the prompt — read the list "
                              "against our text before accepting it", None),
}
# Verified not to touch the prompt: metadata, UI, output limits, retry policy.
CLIENT_DATA_INERT = {"experimentKey", "atis", "cedar_lagoon", "cedar_basin", "heather_vale",
                     "tengu_luminous_whistle", "convolute_arcades", "quizzical_shore",
                     "tengu_lapis_anchor",
                     # 2.1.284, served for Sonnet 5.5: sends the effort level
                     # per turn inside the conversation instead of only as the
                     # top-level parameter (a prompt-cache detail, no text)
                     "per_turn_effort",
                     # 2.1.293: fires the silent-turn reminder after N silent
                     # seconds instead of N turns; it sits behind the same
                     # predicate `silent-turn-reminder-off` cuts
                     "silent_turn_reminder_seconds"}


def client_data_slots(config=None):
    """{(model, entrypoint): (data, when)} — the newest cached slot for each
    pair. CC keeps one slot per build as well, so older ones are history."""
    cfg = config if config is not None else load_json(cc_config_path(), {})
    out = {}
    for slot in (cfg.get("clientDataCacheSlots") or {}).values():
        if not isinstance(slot, dict):
            continue
        key = (slot.get("model"), slot.get("entrypoint"))
        at = slot.get("at") or 0
        if key not in out or at > out[key][1]:
            out[key] = (slot.get("data") or {}, at)
    return out


def client_data_armed(key, value):
    """Is this client-data value an arm, i.e. does it change something?"""
    if key == "tengu_cozy_teapot":
        return value == "relaxed"
    if key == "tengu_toasty_thimble":
        return isinstance(value, dict) and any(v != "" for v in value.values())
    return value not in (None, False, "", "default", "strict", {}, [])


def client_data_rows(data):
    """[(key, value, env var or None, what, target or None)] for one slot's
    prompt-relevant keys, capability names included (the gate treats a true
    capability key as armed)."""
    rows = []
    for k, v in sorted(data.items()):
        if k in CLIENT_DATA_INERT:
            continue
        if k in CLIENT_DATA_ROUTES:
            var, what, tid = CLIENT_DATA_ROUTES[k]
        elif k in MODEL_GATES:
            var, what, _mask = MODEL_GATES[k]
            tid = None
        else:
            var, what, tid = None, "unclassified — read the bundle before trusting it is inert", None
        rows.append((k, v, var, what, tid))
    return rows


def arm_conflicts(key):
    """Whether a client-data arm `key` conflicts with an established patch:
    it can displace one of our targets, or it turns on a capability the
    tranche masks. Such an arm is masked, not accepted (2026-09-29)."""
    route = CLIENT_DATA_ROUTES.get(key)
    if route and route[2]:
        return True
    gate = MODEL_GATES.get(key)
    return bool(gate and gate[2])


def delivered_attribution(tid, data, env):
    """The client-data key that explains a displaced target, or None. An arm
    the env overrides cannot be the explanation."""
    for k, v, var, _what, target in client_data_rows(data):
        if target == tid and client_data_armed(k, v) and not (var and env.get(var) is not None):
            return f"{k}={json.dumps(v)}"
    return None


def delivered_summary(ver, state=None):
    """The one line `status` prints for free from the last capture."""
    rec = (state if state is not None else load_json(STATE, {})).get("deliveredCapture")
    if not rec:
        return "never captured on this machine — `ccctl.py status --delivered` (free)"
    rows = rec.get("results") or []
    failed = [r for r in rows if r.get("error")]
    bad = [f"{r['shape']}/{r['model']}" for r in rows
           if any(v in ("DISPLACED", "MISSING", "PARTIAL", "UNMASKED", "ARM")
                  for v in (r.get("verdicts") or {}).values())]
    if failed:
        detail = failed[0]["error"]
        head = (f"capture failed for {len(failed)}/{len(rows)} session shape(s), "
                f"{rec.get('when', '?')[:10]}: {detail} — delivered prompt unverified")
        return head + (f"; NOT: {', '.join(bad)}" if bad else "")
    arms = sorted({a for r in rows for a in (r.get("arms") or {}).values()})
    head = (f"{len(rows) - len(bad)}/{len(rows)} session shape(s) carry all our reachable text, "
            f"{rec.get('when', '?')[:10]}")
    if rec.get("ccVersion") != ver:
        return f"{head} — but that was CC {rec.get('ccVersion')}, this is {ver}: STALE, re-run"
    if bad:
        head += f" — NOT: {', '.join(bad)}"
    if arms:
        head += f"; displaced by server arms: {', '.join(arms)}"
    return head


def cmd_delivered(args):
    """`status --delivered`: capture what each configured model is actually
    handed, per harness shape, and score our targets against it. Free."""
    cfg = require_cfg()
    binary, ver = claude_binary(), cc_version()
    markers = repo_markers(cfg)
    src = pristine_source(ver, markers)
    if not src:
        die(f"no parked pristine {ver} — the adhoc markers cannot be tied to their targets "
            f"without one; run `ccctl.py analyze {ver}`")
    print(f"deriving marker owners against pristine {Path(src).name} ...")
    owners = marker_owners(cfg, markers, plan_adhocs(live_blob(src)))
    snap = load_snapshot(ver)
    catalogue = catalogue_models(binary)
    models, seen = [], set()
    for spelling in ([args.model] if getattr(args, "model", None) else configured_models()):
        mid = resolve_model(spelling, catalogue) or spelling
        if mid not in seen:
            seen.add(mid)
            models.append((spelling, mid))
    shapes = [args.shape] if getattr(args, "shape", None) else list(DELIVERED_SHAPES)
    env = settings_env()
    state = load_json(STATE, {})
    results, findings, capture_failures = [], 0, 0
    every = silent_reminder_turns()
    turns = every + 2
    reminder_text = env.get("CLAUDE_CODE_SILENT_TURN_REMINDER_TEXT")
    print(f"delivered prompt, CC {ver} — captured on 127.0.0.1, nothing sent to the API; "
          f"each session scripted through {turns} silent tool turns")
    for shape in shapes:
        print(f"\n{shape}: {DELIVERED_SHAPES[shape][0]}")
        for spelling, mid in models:
            reqs, err = capture_request(spelling, shape, binary, turns=turns)
            if err:
                print(f"  {spelling}: CAPTURE FAILED — {err}")
                results.append({"shape": shape, "model": spelling, "error": err})
                capture_failures += 1
                continue
            req = reqs[0]
            system, messages, _tools = request_texts(req)
            heads = [l[2:] for l in system.splitlines() if l.startswith("# ")]
            data = client_data_slots().get((mid, SHAPE_ENTRYPOINT[shape]), ({}, 0))[0]
            style = delivered_style(messages)
            print(f"  {spelling}  ({len(system):,} chars of system prompt; style: {style or 'none'}"
                  + (f"; experiment: {data['experimentKey']}" if data.get("experimentKey") else "")
                  + ")")
            print(f"    sections: {' | '.join(heads)}")
            verdicts, arms = {}, {}
            for tid, verdict, detail in delivered_verdicts(req, owners, snap):
                why = delivered_attribution(tid, data, env) if verdict == "DISPLACED" else None
                if why:
                    # named, and still a finding: an arm that displaces our
                    # text conflicts with an established patch (2026-09-29)
                    arms[tid] = why
                    detail += f" — by server arm {why}, which conflicts with our patch: mask it"
                if verdict in ("DISPLACED", "MISSING", "PARTIAL"):
                    findings += 1
                verdicts[tid] = verdict
                print(f"    {verdict:14} {tid}: {detail}")
            # The per-turn half, which no first request can show. The tranche
            # switches the reminder off on every route, server arms included
            # (2026-09-29), so any reminder is a finding; the note says what
            # turned it on.
            injected = per_turn_injections(reqs)
            nag = next((n for n, t in injected if t.startswith(SILENT_REMINDER_OPENING)
                        or (reminder_text and t == reminder_text)), None)
            reminder_models = {m for m, (c, _v) in MODEL_GATE_CLAUSES.items()
                               if "silent_turn_reminder" in c} | {"silent_turn_reminder"}
            by_model = bool(set(catalogue.get(mid, set())) & reminder_models)
            armed = data.get("silent_turn_reminder") is True
            if len(reqs) <= every:
                note, verdict = (f"only {len(reqs)} request(s) came back, fewer than the "
                                 f"{every} silent turns the reminder needs — not measured"), "UNMEASURED"
            elif nag:
                why = ("turned on by server arm silent_turn_reminder=true" if armed else
                       "turned on by the model's default" if by_model else
                       "no arm and no model default explains it")
                note, verdict = (f"fires before request {nag} — {why}, and this binary does not "
                                 f"switch it off"), "UNMASKED"
            else:
                note = f"none in {len(reqs) - 1} silent tool turns"
                if armed:
                    note += " — although a server arm assigns it: switched off here"
                verdict = "quiet"
            if verdict == "UNMASKED":
                findings += 1
            verdicts["silent_turn_reminder"] = verdict
            print(f"    {verdict:14} silent-turn reminder: {note}")
            others = [(n, t) for n, t in injected if n != nag or not (
                t.startswith(SILENT_REMINDER_OPENING) or t == reminder_text)]
            for n, t in others:
                print(f"    {'per turn':14} from request {n}: {' '.join(t.split())[:90]}")
            results.append({"shape": shape, "model": spelling, "id": mid, "style": style,
                            "experiment": data.get("experimentKey"), "sections": heads,
                            "verdicts": verdicts, "arms": arms, "silentReminderAt": nag,
                            "perTurn": [[n, t[:200]] for n, t in injected]})
    # A NARROWED run is a probe, not a proof. `--model`/`--shape` exist to ask
    # one question — "is Opus 5.5 quiet on this build?" — and recording that as
    # the machine's capture replaced a 4/4 record with a 1/1 one, which reads
    # on `status` as a machine that carries less than it does. Found 2026-09-23.
    narrowed = [w for w in (("--model", getattr(args, "model", None)),
                            ("--shape", getattr(args, "shape", None))) if w[1]]
    if narrowed:
        print("\nnot recorded: " + ", ".join(f"{f} {v}" for f, v in narrowed)
              + " narrows the run, and the machine's record is the full one. "
                "Re-run without it to record.")
    else:
        state["deliveredCapture"] = {"ccVersion": ver, "platform": sys.platform,
                                     "when": datetime.now(timezone.utc).astimezone().isoformat(
                                         timespec="seconds"),
                                     "results": results}
        save_json(STATE, state)
        print("\nrecorded; `status` shows this for free until CC moves off " + ver + ".")
    if capture_failures:
        print(f"CAPTURE INCOMPLETE: {capture_failures} session shape(s) sent no usable "
              "request; their delivered prompts and per-turn injections are unverified.")
    if findings:
        print(f"FINDING: {findings} target(s) displaced, missing, stale or unmasked. "
              f"DISPLACED/MISSING: our bytes are in the binary and a branch (or a named server "
              f"arm) is not reaching them. PARTIAL: the binary predates the repo's text — "
              f"re-apply. UNMASKED: a per-turn injection the tranche switches off arrived anyway.")
        sys.exit(2)
    if capture_failures:
        sys.exit(2)
    print("OK: every target this session shape can carry arrived as our text.")


def cc_config_path():
    """CC's own config file, which is where it caches the server's flag answer."""
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(override) / ".claude.json") if override else (Path.home() / ".claude.json")


def settings_env():
    """The env block CC will apply to its own process, user layer first. Read
    from disk rather than from os.environ, because os.environ here is the
    AGENT's environment, not the one a fresh `claude` launch will build."""
    env = {}
    for p in (Path.home() / ".claude" / "settings.json",
              HERE / ".claude" / "settings.json",
              HERE / ".claude" / "settings.local.json"):
        try:
            env.update(load_json(p, {}).get("env", {}) or {})
        except Exception:                                  # noqa: BLE001
            continue
    return env


TRIPWIRE_SCRIPTS = ("ccctl.py", "cc-doctor.sh")


def tripwire_hook(settings=None):
    """The SessionStart hook entry (a dict: command, timeout, statusMessage)
    that runs ccctl or cc-doctor, or None."""
    path = settings or (Path.home() / ".claude" / "settings.json")
    try:
        groups = ((load_json(path, {}) or {}).get("hooks") or {}).get("SessionStart") or []
        return next((h for g in groups for h in (g.get("hooks") or [])
                     if isinstance(h, dict)
                     and any(t in h.get("command", "") for t in TRIPWIRE_SCRIPTS)), None)
    except (AttributeError, TypeError):
        return None


# The smoke detector's smoke detector. A SessionStart hook that runs past its
# `timeout` is killed, CC records `hook_cancelled`, and the session starts
# silent: exactly what a clean check looks like. On Linux that was every
# session from 2026-08-26 to 2026-09-21, the check taking 14 s against 10 s,
# and nothing said so (notes/2026-09-21-length-regime.md). Three views of it:
# the quiet run times itself and speaks while it still can, `status` times a
# quiet run from outside, and `status` reads the kills CC recorded.
#
# Half the timeout, because a session start is the slowest moment to run it:
# plugins, MCP servers and the model's first request start together, and the
# binary may not be in the page cache. The timing measured here is warm.
TRIPWIRE_MARGIN = 0.5


def tripwire_slow(elapsed, timeout):
    """(verdict, text) for a run of `elapsed` s under a hook `timeout` in s:
    'ok', 'SLOW' (past the margin) or 'KILLED' (past the timeout). No
    explicit timeout means CC's default, which is minutes, so 'ok'."""
    if not timeout:
        return "ok", f"runs in {elapsed:.1f} s (the hook sets no timeout)"
    if elapsed >= timeout:
        return "KILLED", (f"takes {elapsed:.1f} s and CC kills it at {timeout:g} s: every session "
                          f"starts silent, which is what a clean check looks like")
    if elapsed >= TRIPWIRE_MARGIN * timeout:
        return "SLOW", (f"takes {elapsed:.1f} s of its {timeout:g} s timeout; a busy session start "
                        f"can push it over, and then it is killed without a word")
    return "ok", f"runs in {elapsed:.1f} s of its {timeout:g} s timeout"


def tripwire_timed(hook, runner=None):
    """(elapsed s or None, error) for one quiet run, the work the hook does:
    this file, `status --quiet`, in this workspace. Not the hook's own command
    line: that needs the hook's shell, which on win32 is not /bin/sh."""
    timeout = (hook or {}).get("timeout") or 60
    start = time.monotonic()
    try:
        (runner or subprocess.run)([sys.executable, str(Path(__file__).resolve()), "status", "--quiet"],
                                   cwd=HERE, capture_output=True, text=True, timeout=timeout + 10)
    except subprocess.TimeoutExpired:
        return timeout + 10, None
    except OSError as exc:
        return None, repr(exc)
    return time.monotonic() - start, None


def tripwire_kills(hook, root=None, newest=40):
    """(last kill, starts since) from the `newest` transcripts: the newest
    `hook_cancelled` record of this hook as (timestamp, ms), or None; and how
    many session starts were recorded after it.

    A killed hook is recorded with its status message (or its command) in the
    `command` field. A clean silent run leaves no record, so a start is counted
    from the records every SessionStart hook leaves, grouped: records less than
    30 s apart are one start. With no other SessionStart hook, starts are
    invisible and the count is 0, which does not mean none happened."""
    names = {x for x in ((hook or {}).get("statusMessage"), (hook or {}).get("command")) if x}
    if not names:
        return None, 0
    root = Path(root or (Path.home() / ".claude" / "projects"))
    files = sorted(root.glob("*/*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)[:newest]
    kills, starts = [], []
    for f in files:
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if "SessionStart" not in line:
                        continue
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    a = r.get("attachment") or {}
                    if not str(a.get("hookName", "")).startswith("SessionStart"):
                        continue
                    ts = r.get("timestamp") or ""
                    starts.append(ts)
                    if a.get("type") == "hook_cancelled" and a.get("command") in names:
                        kills.append((ts, a.get("durationMs")))
        except OSError:
            continue
    if not kills:
        return None, 0
    last = max(kills)

    def secs(ts):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0
    later = sorted(secs(t) for t in starts if t > last[0])
    groups = [t for i, t in enumerate(later) if i == 0 or t - later[i - 1] > 30]
    # the kill's own start is one of the records just after it
    groups = [t for t in groups if t - secs(last[0]) > 30]
    return last, len(groups)


def tripwire_state(settings=None):
    """(verdict, detail) for the SessionStart tripwire (spec/30 layer 3):
    'ok', 'broken', 'none', or 'unknown'. Advisory only — never gates.

    The tripwire cannot report its own death: a hook whose script is gone
    exits 2 with "can't open file", CC swallows a failed SessionStart hook,
    and every session starts silent — which is exactly what a PATCHED session
    looks like. Measured on macOS 2026-09-11: the hook ran
    `~/cc-dispositions/tools/ccctl.py`, a directory that no longer existed,
    for an unknown stretch of rounds. So the plain `status` checks it from
    outside: every path the hook command names — the script, and the `cd`
    target that becomes ccctl's HERE — must exist."""
    path = settings or (Path.home() / ".claude" / "settings.json")
    data = load_json(path, None)
    if data is None and path.exists():
        return "unknown", f"{path} is not readable JSON"
    try:
        groups = ((data or {}).get("hooks") or {}).get("SessionStart") or []
        cmds = [h.get("command", "") for g in groups for h in (g.get("hooks") or [])
                if isinstance(h, dict)]
    except Exception as exc:                               # noqa: BLE001
        return "unknown", f"{path} unreadable ({exc!r})"
    cmds = [c for c in cmds if any(s in c for s in TRIPWIRE_SCRIPTS)]
    if not cmds:
        return "none", "no SessionStart hook runs ccctl.py/cc-doctor.sh — a stock session will not say so"
    for cmd in cmds:
        try:
            toks = shlex.split(cmd, posix=os.name != "nt")
        except ValueError:
            toks = cmd.split()
        toks = [t.strip("'\"") for t in toks]
        named = [t for i, t in enumerate(toks)
                 if t.endswith(TRIPWIRE_SCRIPTS) or (i and toks[i - 1] == "cd")]
        gone = [t for t in named
                if not Path(os.path.expandvars(t)).expanduser().exists()]
        if gone:
            return "broken", f"SessionStart hook names {', '.join(gone)}, which does not exist — every session starts silent"
    return "ok", cmds[0]


def server_arms():
    """{flag: value} for the arms this tranche cares about, plus when CC last
    refreshed them. Missing cache is not an error — it means CC has not run
    since the config was cleared, and the honest answer is "unknown"."""
    cfg = load_json(cc_config_path(), {})
    feats = cfg.get("cachedGrowthBookFeatures") or {}
    at = cfg.get("cachedGrowthBookFeaturesAt")
    when = None
    if isinstance(at, (int, float)):
        when = datetime.fromtimestamp(at / 1000).astimezone().isoformat(timespec="seconds")
    return {k: feats.get(k) for k in SERVER_ARMS}, when, bool(feats)


def arms_armed(value):
    """Is this flag value an ARM? `false`, `null` and `"default"` are not; CC
    treats a missing flag as off, and thistle_grebe uses the string "default"
    for its own off state, and lantern_wick_mode the string "off" (CC 2.1.280
    maps every value but "wrap-up" and "next-steps" to it)."""
    return value not in (None, False, "default", "", "off")


def arms_drift(current, state):
    """[(flag, old, new)] since the last recorded snapshot. An arm that appears
    for the first time counts as drift only if it is on — otherwise every fresh
    machine would report twelve changes on its first run."""
    seen = state.get("serverArms") or {}
    out = []
    for k, v in current.items():
        if k not in seen:
            if arms_armed(v):
                out.append((k, "(unrecorded)", v))
        elif seen[k] != v:
            out.append((k, seen[k], v))
    return out


def cmd_flags(args):
    """What the server is currently arming, and what our env is masking."""
    current, when, have_cache = server_arms()
    state = load_json(STATE, {})
    env = settings_env()
    if not have_cache:
        print(f"no cachedGrowthBookFeatures in {cc_config_path()} — run `claude` once, "
              f"then ask again. Nothing recorded.")
        return
    print(f"server arms  : read from {cc_config_path()}")
    print(f"last refresh : {when or 'unknown'}  (CC rewrites this on startup)")
    print()
    print(f"  {'flag':24} {'server':9} {'our env':9} {'whose':6} gates")
    masked = []
    # A flag whose name is no longer in the binary cannot be armed, and an
    # "unset" row for it reads as "the server has not decided yet" when the
    # truth is that nothing will ever decide it again. 2.1.278 retired three.
    live_flags = binary_contains(claude_binary(), list(SERVER_ARMS))
    for k, (var, what, whose) in SERVER_ARMS.items():
        v = current[k]
        retired = k not in live_flags
        sv = ("retired" if retired else
              "ARMED" if arms_armed(v) else ("off" if v is not None else "unset"))
        ev = env.get(var) if var else None
        evs = f"{var.split('_')[-1].lower()}={ev}" if ev is not None else ("-" if var else "n/a")
        print(f"  {k:24} {sv:9} {evs:9} {whose:6} {what}"
              + ("  (now a capability — see below)" if retired else ""))
        if ev is not None and arms_armed(v) and not retired:
            masked.append(k)
    # Same screen, because the question "what is deciding our sections that is
    # not the binary" has had a second answer since 2.1.270 and an operator who
    # reads only `flags` would still not know it exists.
    binary, ver = claude_binary(), cc_version()
    rows, intact = model_gates(env, binary=binary, version=ver)
    arms = {}
    for model_cap, (caps, _since) in MODEL_GATE_CLAUSES.items():
        for cap in caps:
            arms.setdefault(cap, []).append(model_cap)
    # a model's own catalogue entry arms before any clause (2.1.284 onwards)
    for mid, (caps, since) in CATALOGUE_GATE_DEFAULTS.items():
        if ver and ver_key(ver) >= ver_key(since):
            for cap in caps:
                arms.setdefault(cap, []).append(f"{mid} entry")
    arms_by_cap = {cap: ", ".join(v) for cap, v in arms.items()}
    print()
    print(f"  {'model-keyed capability':25} {'armed by model':24} {'our mask':12} {'whose':6} gates")
    for cap, var, what, verdict in rows:
        ours = "OURS" if verdict != "n/a" else "stock"
        if var and env.get(var) is not None:
            evs = f"{var.split('_')[-1].lower()}={env[var]}"
        elif MODEL_GATES[cap][2] == "binary":
            evs = "binary" if verdict == "masked" else verdict
        else:
            evs = "-" if var else "n/a"
        print(f"  {cap:25} {arms_by_cap.get(cap, '(no clause)'):24} {evs:12} {ours:6} {what}")
    # The sections that are OURS and arm by model rather than by flag: since
    # 2.1.278 `bison_cairn` and `larch_cistern` are capabilities, not
    # GrowthBook arms, so they do not appear in the table above this one.
    for cap, model_cap in sorted(arms_by_cap.items()):
        if cap in MODEL_GATES:
            continue
        # these came from a `tengu_` flag and kept its env var (item 19)
        var = (SERVER_ARMS.get(f"tengu_{cap}") or (None,))[0]
        val = env.get(var) if var else None
        evs = f"{var.split('_')[-1].lower()}={val}" if val is not None else "-"
        whose = SERVER_ARMS.get(f"tengu_{cap}", (None, None, "stock"))[2]
        print(f"  {cap:25} {model_cap:24} {evs:10} {whose:6} "
              f"{'carries our text; env wins ahead of the clause (item 19)' if whose == 'OURS' else 'stock text'}")
    # The third channel. Client data is per model AND entrypoint, so the same
    # model can be in an experiment under an SDK host and not in a terminal.
    slots = client_data_slots()
    overridden = []
    if slots:
        print()
        print(f"  {'client data: model / entrypoint':38} {'key':22} {'value':16} {'our env':10} routes")
        for (model, ep), (data, _at) in sorted(slots.items(), key=lambda kv: tuple(map(str, kv[0]))):
            armed = [r for r in client_data_rows(data) if client_data_armed(r[0], r[1])]
            label = f"{model} / {ep}"
            if data.get("experimentKey"):
                print(f"  {label:38} experiment: {data['experimentKey']}")
                label = ""
            if not armed:
                print(f"  {label:38} (nothing that routes prompt text)")
            for k, v, var, what, _tid in armed:
                val = json.dumps(v)
                val = val if len(val) <= 16 else val[:13] + "..."
                evs = f"{var.split('_')[-1].lower()}={env[var]}" if var and env.get(var) is not None \
                    else ("-" if var else "n/a")
                print(f"  {label:38} {k:22} {val:16} {evs:10} {what}")
                if var and env.get(var) is not None:
                    overridden.append((f"{model} / {ep}", k, var))
                label = ""
    if intact is False:
        print()
        for line in model_gate_drift(binary, ver):
            print(f"  GATE DRIFT: {line}")
        print("  Re-read the gate before trusting the rows above: a clause decides its")
        print("  sections before any server flag, with every marker still intact.")
    drift = arms_drift(current, state)
    print()
    if masked:
        print("BOTH: the server arms these AND we force them by env, so the env var is now")
        print("      redundant and, more to the point, invisible. Anthropic is running the")
        print("      experiment anyway; accepted, not fought. Consider dropping our env var")
        print("      for the duration so the arm is theirs and the data stays clean:")
        for k in masked:
            print(f"        - {k} ({SERVER_ARMS[k][0]})")
    # An env line that overrides an arm conflicting with our patches is doing
    # its job (2026-09-29); only a consonant arm overridden is worth a line.
    overridden = [o for o in overridden if not arm_conflicts(o[1])]
    if overridden:
        # The client-data twin of BOTH: an env line set for one model's
        # default also decides another model's arm, which does not conflict.
        print("OVERRIDDEN: our env decides these client-data arms, which do not conflict")
        print("            with any patch and so are accepted rather than fought:")
        for where, k, var in overridden:
            print(f"        - {where}: {k} (by {var})")
    if drift:
        print("CHANGED since the last recorded check:")
        for k, old, new in drift:
            print(f"  {k}: {json.dumps(old)} -> {json.dumps(new)}   ({SERVER_ARMS[k][1]})")
    elif state.get("serverArms"):
        print("no change since the last recorded check.")
    else:
        print("first record on this machine; nothing to compare against yet.")
    if not args.no_record:
        state["serverArms"] = current
        state["serverArmsSeenAt"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        save_json(STATE, state)
        print("\nrecorded; the next `flags` reports the delta against this.")


def drift_probe(ver, markers, binary, base_ver):
    """Which binary can answer "did upstream rewrite the text our edits target",
    and what to warn when none can. Returns (path, caveat-or-None).

    It has to be the PRISTINE one. A patched binary cannot answer: a whole-span
    replacement removes the stock text by design, so every such fragment scores
    0 windows and gets reported as upstream drift. Measured 2026-09-11 on win32
    right after a clean `apply` of CC 2.1.268 — three fragments read DRIFTED and
    `check-update` exited 2, while the same probe against the parked pristine
    read 6/7, 5/6 and 7/10. Same failure shape as the dead anti-marker item 22
    found: an instrument that cannot tell "we removed it" from "it was never
    there". With no pristine on the machine the honest answer is to say so and
    label the result unproven, rather than to report drift that has not been
    shown or to stay silent about probing a binary we ourselves rewrote."""
    pristine = pristine_source(ver, markers)
    if pristine:
        return pristine, None
    if markers and binary_contains(binary, markers):
        return binary, (f"NO PRISTINE {ver} on this machine, and the live binary carries our "
                        f"markers — a whole-span replacement reads as drift here. Treat any "
                        f"DRIFTED below as unproven; park a stock copy, or read "
                        f"`diff stock {base_ver} {ver}` instead.")
    return binary, None


def cmd_check_update(args):
    """After a CC update: are we stock again, and did our edit targets drift?"""
    cfg = require_cfg()
    ver = cc_version()
    state = load_json(STATE, {})
    applied = state.get("applied")
    print(f"cc version now : {ver}")
    if applied:
        print(f"last applied on: CC {applied['ccVersion']}")
        if applied["ccVersion"] == ver:
            print("no version change since last apply.")
    markers = repo_markers(cfg)
    binary = claude_binary()
    # "run apply" is the wrong advice inside a declared stock arm — there the
    # missing markers ARE the state that was asked for.
    arm = stock_arm(state)
    armed = bool(arm) and ver.startswith(arm["ccVersion"])
    if armed:
        print(f"prompt arm     : STOCK by intent since {arm['when']} — {arm['note']}")
    if markers:
        present = binary_contains(binary, markers)
        print(f"custom markers : {len(present)}/{len(markers)} in binary"
              + ("  <- stock arm: this is the declared state" if armed and not present else
                 "  <- update reverted the patch, run apply" if len(present) < len(markers) else ""))

    base_ver = applied["ccVersion"] if applied else None
    base = load_snapshot(base_ver) if base_ver else None
    if not base:
        versions = sorted(p.stem.split("-", 1)[1] for p in SNAPSHOTS.glob("prompts-*.json")) if SNAPSHOTS.exists() else []
        if versions:
            base_ver = versions[-1]
            base = load_snapshot(base_ver)
    if not base:
        print("no snapshot to drift-check against — run apply (or snapshot) once first.")
        return

    probe, caveat = drift_probe(ver, markers, binary, base_ver)
    if caveat:
        print(f"drift check    : {caveat}")
    print(f"drift check    : probing {'pristine' if probe != binary else 'live'} {ver} binary "
          f"against stock text from {base_ver}")
    blob = live_blob(probe).decode("utf-8", errors="replace")
    drift = False
    for f in repo_edits(cfg):
        frag = f.stem
        entry = base.get(frag)
        if not entry:
            print(f"  {frag}: not in {base_ver} snapshot (added later?)")
            continue
        body = stock_body(entry)
        wins = [body[i:i + 60] for i in range(0, max(len(body) - 60, 1), 200)]
        wins = [w for w in wins if "${" not in w and len(w) == 60]
        hits = sum(1 for w in wins if w in blob)
        verdict = "ok" if hits else "DRIFTED — upstream rewrote this fragment, re-review the edit"
        if not hits:
            drift = True
        print(f"  {frag}: {hits}/{len(wins)} windows  {verdict}")
    new = ensure_prompt_data(ver)
    if new and base_ver != ver:
        print(f"full diff available: ccctl.py diff stock {base_ver} {ver}")
    if drift:
        sys.exit(2)


VERSIONISH = re.compile(r"\d+(\.\d+)+")


def diff_custom_args(a, b, fragment):
    """`(base_ver, fragment)` from `diff custom`'s share of the positionals.

    `diff stock A B [frag]` and `diff custom [VER] [frag]` share four
    positionals, so both documented-ish forms of `diff custom` were broken and
    had always been (measured 2026-09-03):

      diff custom <FRAGMENT>        -> fragment landed in `a`, and ccctl went
                                       looking for a snapshot named after it:
                                       "FAIL: no stock snapshot for
                                       system-prompt-delivering-work-at-full-scope"
      diff custom <VER> <FRAGMENT>  -> fragment landed in `b`, was ignored, and
                                       all six fragments were diffed instead —
                                       the silent one, which is worse

    A fragment id is never version-shaped, so the values disambiguate
    themselves. A flag would be one more thing to remember and would leave the
    documented form broken."""
    frag = fragment or b
    if a and not VERSIONISH.fullmatch(a) and not frag:
        return None, a
    return a, frag


def cmd_diff(args):
    cfg = require_cfg()
    if args.what == "stock":
        if not (args.a and args.b):
            die("usage: diff stock <verA> <verB> [fragment]")
        A, B = load_snapshot(args.a), load_snapshot(args.b)
        if not A:
            die(f"no snapshot or cache for {args.a}")
        if not B:
            die(f"no snapshot or cache for {args.b} (run snapshot after tweakcc supports it)")
        if args.fragment:
            a = stock_body(A[args.fragment]) if args.fragment in A else ""
            b = stock_body(B[args.fragment]) if args.fragment in B else ""
            sys.stdout.writelines(difflib.unified_diff(
                a.splitlines(keepends=True), b.splitlines(keepends=True),
                f"{args.fragment}@{args.a}", f"{args.fragment}@{args.b}"))
            return
        added = sorted(set(B) - set(A))
        removed = sorted(set(A) - set(B))
        changed = sorted(k for k in set(A) & set(B) if stock_body(A[k]) != stock_body(B[k]))
        our = {f.stem for f in repo_edits(cfg)}
        print(f"{args.a} -> {args.b}: {len(added)} added, {len(removed)} removed, {len(changed)} changed")
        for name, items in (("added", added), ("removed", removed), ("changed", changed)):
            for k in items:
                flag = "  << WE EDIT THIS" if k in our else ""
                print(f"  {name:8s} {k}{flag}")
        print("\nper-fragment diff: ccctl.py diff stock A B <fragment>")

    elif args.what == "custom":
        base_ver, frag = diff_custom_args(args.a, args.b, args.fragment)
        versions = sorted(p.stem.split("-", 1)[1] for p in SNAPSHOTS.glob("prompts-*.json")) if SNAPSHOTS.exists() else []
        base_ver = base_ver or (versions[-1] if versions else cc_version())
        base = load_snapshot(base_ver)
        if not base:
            die(f"no stock snapshot for {base_ver}")
        if frag and not any(f.stem == frag for f in repo_edits(cfg)):
            die(f"no edit fragment named {frag} in edits/ — have: "
                + ", ".join(sorted(f.stem for f in repo_edits(cfg))))
        frags = [f for f in repo_edits(cfg) if not frag or f.stem == frag]
        if not frags:
            die("no matching edit fragments in repo edits/")
        for f in frags:
            ours = FRONTMATTER.sub("", f.read_text(encoding="utf-8")).strip() + "\n"
            entry = base.get(f.stem)
            stock = (stock_body(entry).strip() + "\n") if entry else ""
            label = f.stem
            if not entry:
                print(f"### {label}: NOT IN STOCK {base_ver} (pure addition)\n")
                continue
            diff = list(difflib.unified_diff(
                stock.splitlines(keepends=True), ours.splitlines(keepends=True),
                f"{label}@stock-{base_ver}", f"{label}@custom"))
            print("".join(diff) if diff else f"### {label}: identical to stock (why is it in edits/?)")
    else:
        die("diff what? 'stock' or 'custom'")


def cmd_snapshot(args):
    ver = args.a or cc_version()
    if snapshot(ver) is None:
        die(f"tweakcc has no prompt data for {ver} yet")


def human(n):
    return f"{n / 1e6:,.0f} MB" if n >= 1e6 else f"{n:,} B"


def prune_nudge(markers):
    """One line after a swap, where the artifacts are actually created. It does
    NOT prune: the moment right after a swap is exactly when the previous
    binaries are most likely to be wanted. Surfacing the number is the job;
    deciding to spend it is the operator's."""
    try:
        over = [f for name, files in parked_groups(markers)
                for f in files[PRUNE_KEEP.get(name, 1):]]
        stale = sum(f.stat().st_size for f in over if f.resolve() not in prune_protected(markers))
    except OSError:
        return
    if stale > 1e9:
        print(f"note: {human(stale)} in parked binaries beyond what is worth keeping — "
              f"`ccctl.py prune --dry-run` to see them")


def cmd_prune(args):
    """Keep the newest `--keep` artifacts per class, delete the rest.

    Sorted by MTIME, never by name: `.pre-swap.1` is the slot the swap took
    when `.pre-swap` was locked by a running session, so on this machine the
    `.1` was three days older than the unsuffixed one. Name-order retention
    would have deleted the newest rollback and kept the stalest.

    Staging is deliberately NOT swept: an analyze round parks a verified
    pristine download there for the version it is evaluating (2.1.245 as of
    2026-08-25, 384 MB), and a round can sit blocked on tweakcc for days.
    `keepStaging: false` already clears it after a successful swap."""
    cfg = require_cfg()
    markers = repo_markers(cfg)
    protected = prune_protected(markers)
    total, freed, kept_running = 0, 0, []
    for name, files in parked_groups(markers):
        keep = args.keep if args.keep is not None else PRUNE_KEEP.get(name, 1)
        print(f"\n{name}: (keep {keep})")
        survivors = 0
        for f in files:
            size = f.stat().st_size
            total += size
            why = None
            if f.resolve() in protected:
                why = "installed version's pristine source"
            elif survivors < keep:
                why = f"newest {keep}"
            if why:
                survivors += 1
                print(f"  KEEP   {f.name:<44} {human(size):>10}  ({why})")
                continue
            if args.dry_run:
                print(f"  would delete {f.name:<38} {human(size):>10}")
                freed += size
                continue
            try:
                f.unlink()
            except OSError as e:                 # a session is executing it
                kept_running.append(f.name)
                print(f"  SKIP   {f.name:<44} {human(size):>10}  (in use: {e.strerror})")
                continue
            freed += size
            print(f"  DELETE {f.name:<44} {human(size):>10}")
    stage = sorted(STAGING.glob("*")) if STAGING.is_dir() else []
    if stage:
        n = sum(p.stat().st_size for p in stage if p.is_file())
        print(f"\nstaging (left alone — an in-flight analyze round lives here): "
              f"{len(stage)} file(s), {human(n)}")
    verb = "reclaimable" if args.dry_run else "reclaimed"
    print(f"\n{verb}: {human(freed)} of {human(total)} parked")
    if kept_running:
        print(f"still running, retry later: {', '.join(kept_running)}")
    if not args.dry_run and freed:
        policy = f"forced --keep {args.keep} for every class" if args.keep is not None else \
                 "per-class retention " + ", ".join(f"{k}={v}" for k, v in PRUNE_KEEP.items())
        detail = f"- {policy}\n- freed {human(freed)}"
        if kept_running:
            detail += f"\n- skipped (in use): {', '.join(kept_running)}"
        log_entry("prune", detail)


def cmd_restore(args):
    # `--restore` copies native-binary.backup over the live install, no questions
    # asked. That file is whatever binary tweakcc last touched, which after one
    # of our rounds can be a PATCHED one (measured on win32 2026-08-25). Restoring
    # it would report "restored to stock" while leaving every edit in place — the
    # worst possible outcome for a rollback command, because the operator stops
    # looking. Gate it on the markers.
    backup = TWEAKCC_DIR / "native-binary.backup"
    markers = repo_markers(require_cfg())
    if backup.exists() and markers and not is_stock(backup, markers):
        die(f"{backup} carries our edit markers — it is a PATCHED binary, not a stock one. "
            f"`--restore` would put it back and call it stock. Use a parked pristine copy "
            f"instead: see `ccctl.py status` (restore path) and versions/<ver>.stock.")
    r = run([*tweakcc_node(), "--restore"], timeout=600)
    print((r.stdout + r.stderr).strip()[-1500:])
    if r.returncode != 0:
        die("restore failed")
    state = load_json(STATE, {})
    state.pop("applied", None)
    save_json(STATE, state)
    log_entry("restore", f"- CC `{cc_version()}` restored to stock via tweakcc backup")
    print("restored to stock.")


def build_parser():
    """The CLI surface, separated from dispatch so a test can assert a flag
    exists and defaults the safe way without running anything."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="configure this folder and sparse-clone the repo")
    p.add_argument("url")
    sub.add_parser("pull", help="fetch newest custom prompts")
    p = sub.add_parser("status", help="binary, version, patch and repo state")
    p.add_argument("--quiet", action="store_true", help="one warning line if stock, else silent (for hooks)")
    p.add_argument("--check", action="store_true",
                   help="exit 0 only if every marker is present AND every anti-marker is absent "
                        "(exit 2 otherwise) — the one command to run on each machine after an apply")
    p.add_argument("--live", action="store_true",
                   help="SPENDS TURNS: ask the models this machine runs to quote our sections "
                        "back; --delivered sees the same for free, so this is for a gateway that "
                        "rewrites requests after CC. One turn per "
                        "model; refuses to spend unless per-model routing is in this binary, and "
                        "again once it has answered for this CC version")
    p.add_argument("--delivered", action="store_true",
                   help="FREE: capture the exact request each configured model would be sent, "
                        "per harness shape (claude -p, and an Agent SDK host launched like T3 "
                        "Code), on 127.0.0.1 — nothing reaches the API — and say which of our "
                        "targets arrived, which are displaced and by what")
    p.add_argument("--shape", choices=sorted(DELIVERED_SHAPES),
                   help="--delivered: capture only this harness shape")
    p.add_argument("--model", help="--live/--delivered: only this model instead of the configured ones")
    p.add_argument("--yes", action="store_true", help="--live: skip the spend confirmation")
    p.add_argument("--force", action="store_true",
                   help="--live: re-ask even though this CC version was already answered")
    p = sub.add_parser("apply", help="pull + snapshot + patch + verify (staged copy-swap, never in place)")
    p.add_argument("--no-pull", action="store_true")
    p.add_argument("--stock", action="store_true",
                   help="enter the STOCK ARM instead: rebuild this version pristine + tweakcc's "
                        "patch set, verify none of our markers are in it. Plain `apply` ends the arm.")
    p.add_argument("--reason", help="what the stock arm is for (recorded in state + changelog)")
    p = sub.add_parser("analyze", help="stage an incoming CC version, diff, per-target verdicts + report "
                                       "(exit: 0 clean, 2 review, 3 manual, 4 tweakcc-lagging)")
    p.add_argument("version", nargs="?", help="target version (default: channel pointer)")
    p.add_argument("--channel", choices=["stable", "latest"], help="override configured channel")
    p = sub.add_parser("update", help="analyze + patch staged + verify + swap live (policy-gated)")
    p.add_argument("version", nargs="?", help="target version (default: channel pointer)")
    p.add_argument("--channel", choices=["stable", "latest"], help="override configured channel")
    p.add_argument("--stock", action="store_true",
                   help="land the new version in the STOCK ARM: Anthropic's default prompts plus "
                        "tweakcc's patch set, none of ours. Verdicts become advisory (they price "
                        "the return trip). `ccctl.py apply` ends the arm.")
    p.add_argument("--policy", choices=["analyze-only", "gate-on-clean", "force"],
                   help="override configured updatePolicy")
    p.add_argument("--no-pull", action="store_true")
    p.add_argument("--allow-downgrade", action="store_true",
                   help="permit a target older than the installed version (deliberate rollback); "
                        "without it, an older target is refused rather than swapped in silently")
    sub.add_parser("check-update", help="post-CC-update: patch reverted? targets drifted? (exit 2 on drift)")
    p = sub.add_parser("flags", help="which sections the SERVER is arming, and what our env masks")
    p.add_argument("--no-record", action="store_true",
                   help="report without updating the recorded snapshot")
    p = sub.add_parser("diff", help="diff stock A B [frag] | diff custom [frag]")
    p.add_argument("what", choices=["stock", "custom"])
    p.add_argument("a", nargs="?")
    p.add_argument("b", nargs="?")
    p.add_argument("fragment", nargs="?")
    p = sub.add_parser("snapshot", help="store stock prompts for a CC version")
    p.add_argument("a", nargs="?", help="version (default: installed)")
    p = sub.add_parser("prune", help="delete old parked rollback binaries, keeping the newest few")
    p.add_argument("--keep", type=int, default=None, metavar="N",
                   help="force the same count for every class; default is per-class "
                        "(1 previous build, 1 older pristine, 0 parked tweakcc backups)")
    p.add_argument("--dry-run", action="store_true", help="list what would go, delete nothing")
    sub.add_parser("restore", help="byte-exact rollback to stock")

    return ap


def main():
    args = build_parser().parse_args()
    try:
        {"init": cmd_init, "pull": cmd_pull, "status": cmd_status, "apply": cmd_apply,
         "analyze": cmd_analyze, "update": cmd_update,
         "check-update": cmd_check_update, "diff": cmd_diff, "snapshot": cmd_snapshot,
         "prune": cmd_prune, "restore": cmd_restore, "flags": cmd_flags}[args.cmd](args)
    finally:
        # The tripwire's own clock: if this run is slow enough to be killed on a
        # busier start, say so now, while the output still reaches the session.
        if args.cmd == "status" and getattr(args, "quiet", False):
            hook = tripwire_hook()
            speed, text = tripwire_slow(time.monotonic() - STARTED, (hook or {}).get("timeout"))
            if speed != "ok":
                print(f"[ccctl] TRIPWIRE {speed}: this check {text} — see ccctl.py status")


if __name__ == "__main__":
    main()
