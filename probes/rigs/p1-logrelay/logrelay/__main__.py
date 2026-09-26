import sys
from .core import summarize, parse_line

def main(argv):
    if len(argv) >= 2 and argv[0] == "summary":
        with open(argv[1]) as fh:
            counts = summarize(fh)
        for svc, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"{svc}\t{n}")
        return 0
    print("usage: logrelay summary FILE | logrelay import FILE", file=sys.stderr)
    return 2

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
