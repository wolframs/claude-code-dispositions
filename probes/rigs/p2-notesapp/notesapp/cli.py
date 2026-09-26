"""Admin CLI. Usage: python -m notesapp.cli new-account NAME"""
import sys
from .store import AccountStore

def main(argv):
    store = AccountStore()
    if argv and argv[0] == "new-account":
        print("created", store.create_account(argv[1]))
        return 0
    print("commands: new-account NAME", file=sys.stderr)
    return 2

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
