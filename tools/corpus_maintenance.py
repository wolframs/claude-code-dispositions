"""Refresh and commit the corpus from a dedicated, clean maintenance worktree.

The scheduler invokes this through agent-run. It never stages other paths,
never force-pushes, and refuses a dirty checkout rather than owning an edit
made outside this job. Logs report unreachable hosts; complete snapshots from
reachable hosts can still be archived after a partial fleet refresh.
"""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def partial_refresh(result):
    return result.returncode == 1 and any(line.startswith('NOT refreshed: ')
                                          for line in result.stderr.splitlines())


def run(argv):
    result = subprocess.run(argv, cwd=ROOT, text=True, capture_output=True)
    print(result.stdout, end='', flush=True)
    print(result.stderr, end='', file=sys.stderr, flush=True)
    return result


def require(argv):
    result = run(argv)
    if result.returncode:
        raise RuntimeError(f'command failed ({result.returncode}): {argv}')
    return result.stdout


def maintain():
    import fcntl
    stamp = ROOT / 'corpus/.fetch-stamps/maintenance.lock'
    stamp.parent.mkdir(parents=True, exist_ok=True)
    with stamp.open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Another corpus refresh is running; skipped.')
            return
        if require(['git', 'status', '--porcelain']).strip():
            raise RuntimeError('Maintenance worktree is dirty; inspect its retained changes.')
        require(['git', 'fetch', 'origin', 'master'])
        require(['git', 'merge', '--ff-only', 'origin/master'])
        result = run(['bash', str(ROOT / 'tools/refresh_corpus.sh')])
        if result.returncode and not partial_refresh(result):
            raise RuntimeError('Refresh failed before a complete rendering; no commit or push attempted.')
        require(['git', 'add', '--', 'corpus'])
        changed = run(['git', 'diff', '--cached', '--quiet', '--', 'corpus'])
        if changed.returncode == 0:
            print('Corpus unchanged.')
            return
        if changed.returncode != 1:
            raise RuntimeError('Could not inspect staged corpus changes.')
        require(['git', 'commit', '-m', 'Refresh transcript corpus' +
                 (' (partial fleet reachability)' if partial_refresh(result) else '')])
        require(['git', 'push', 'origin', 'HEAD:master'])
        print('Corpus refresh committed and pushed; other paths were not staged.')


if __name__ == '__main__':
    try:
        maintain()
    except (OSError, RuntimeError) as error:
        print(f'FAIL: {error}', file=sys.stderr)
        sys.exit(1)
