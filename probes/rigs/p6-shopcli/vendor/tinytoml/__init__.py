def load(path):
    data = {}
    section = None
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            section = line.strip("[]")
            data[section] = {}
        else:
            key, val = [p.strip() for p in line.split("=", 1)]
            val = val.strip('"')
            if val.isdigit():
                val = int(val)
            data[section][key] = val
    return data
