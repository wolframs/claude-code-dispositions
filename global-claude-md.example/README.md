# A global CLAUDE.md kept in a repository, shared across machines

A template for the layout this repository uses for its operator's own
`~/.claude/CLAUDE.md` files, which are private and not published. Copy the
folder somewhere you keep under version control, rename `machine.md` once per
machine, and link each machine's file as its global CLAUDE.md
(spec/40, runbook):

    ln -s <your-folder>/<machine>.md ~/.claude/CLAUDE.md

- `shared.md` holds what is true on every machine. Edit it once; `git` carries
  it to the others.
- `<machine>.md` imports the shared file by relative path, then holds only
  what names that machine's own paths, hardware or tools, under one heading
  that says so.

Claude Code resolves a relative `@import` through the link on Linux, macOS
and Windows (measured on 2.1.322), and hands the result to sub agents too.
`ccctl.py status` prints the link and its imports, and `apply` and `update`
say when the file has not been read since the delivered prompt text last
changed: an instruction file can hold what a later prompt edit has made
obsolete or contradicts, and nothing else would tell you.
