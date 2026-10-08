#!/usr/bin/env python3
"""Measure runs of consecutive stock-prompt words in a tree about to be published.

The public copy carries no run of eight or more consecutive words of
Anthropic's prompt text outside the replacement bodies in edits/*.md (site-prep,
2026-09-25). `master` does not hold that line, so each merge into `site-prep-2`
can bring new runs: a locator that spells out the sentence it locates is the
usual one. Run this on the mirror's stage and on the current public tree; a run
in the first and not in the second is one to trim (see ccctl.StockSentence).

    python3 tools/stock_runs.py <tree> baseline/<ver>/prompts.json [more maps...]

Prints `<runs> <longest> <file>` per file and writes <tree>.runs.json with the
runs themselves. The prompt maps are the only stock text it knows: text the
bundle carries outside them (per-turn reminders, plugin text) is not seen.
"""
import json,re,sys,os
N=8
W=re.compile(r"[A-Za-z0-9']+")
def words(s): return [w.lower() for w in W.findall(s)]
stock=set()
def feed(o):
    if isinstance(o,str):
        w=words(o)
        for i in range(len(w)-N+1): stock.add(tuple(w[i:i+N]))
    elif isinstance(o,dict):
        for v in o.values(): feed(v)
    elif isinstance(o,list):
        for v in o: feed(v)
for m in sys.argv[2:]: feed(json.load(open(m,encoding='utf-8')))
root=sys.argv[1]; out={}
for d,_,fs in os.walk(root):
    if '.git' in d: continue
    for f in fs:
        p=os.path.join(d,f); rel=os.path.relpath(p,root)
        try: t=open(p,encoding='utf-8').read()
        except: continue
        w=words(t); runs=[]; i=0
        while i<=len(w)-N:
            if tuple(w[i:i+N]) in stock:
                j=i+N
                while j<len(w) and tuple(w[j-N+1:j+1]) in stock: j+=1
                runs.append(' '.join(w[i:j])); i=j
            else: i+=1
        if runs: out[rel]=runs
for k in sorted(out): 
    print(len(out[k]),max(len(r.split()) for r in out[k]),k)
json.dump(out,open(sys.argv[1].rstrip('/')+'.runs.json','w'))
