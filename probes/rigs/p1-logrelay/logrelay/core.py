def parse_line(line):
    """'<ts> <service> <LEVEL> <message>' -> dict."""
    ts, service, level, msg = line.split(" ", 3)
    return {"ts": ts, "service": service, "level": level, "msg": msg.rstrip("\n")}

def summarize(lines):
    counts = {}
    for line in lines:
        rec = parse_line(line)
        counts[rec["service"]] = counts.get(rec["service"], 0) + 1
    return counts
