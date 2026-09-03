#!/usr/bin/env python3
"""
Synthetic adversarial verification for FaithfulRAG suite.
Standalone — no Django, no API, no GPU. Mirrors functions.py logic.
Usage: python scripts/verify_faithfulrag.py --verbose
"""
import json, re, sys, os, time

def _parse_context_list(value):
    if value is None: return []
    if isinstance(value, list): return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        s=value.strip()
        if not s: return []
        if (s.startswith("[") and s.endswith("]")) or (s.startswith("{") and s.endswith("}")):
            try:
                p=json.loads(s)
                if isinstance(p, list): return [str(x).strip() for x in p if str(x).strip()]
                if isinstance(p, dict): return [str(v).strip() for v in p.values() if str(v).strip()]
            except: pass
        if "\n" in s:
            parts=[p.strip() for p in s.split("\n") if p.strip()]
            if len(parts)>1: return parts
        return [s]
    return [str(value).strip()]

def _split_reasoning_steps(text):
    if not isinstance(text,str): text=str(text)
    text=text.strip()
    if not text: return []
    lines=[l.strip() for l in text.split("\n") if l.strip()]
    bullet_re=re.compile(r"^\s*(?:\d+[\.\)]\s+|[-*•]\s+|Step\s*\d+[:\.\)]\s*)", re.IGNORECASE)
    has_bullets=any(bullet_re.match(l) for l in lines)
    if has_bullets and len(lines)>1:
        return [bullet_re.sub("",l).strip() for l in lines if bullet_re.sub("",l).strip()]
    if len(lines)==1:
        sent_parts=re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", lines[0])
        steps=[s.strip() for s in sent_parts if s.strip()]
        steps=[s for s in steps if len(s)>=3]
        return steps if len(steps)>1 else lines
    return lines

def _jacc(a,b):
    ta=set(re.findall(r"\w+", str(a).lower())); tb=set(re.findall(r"\w+", str(b).lower()))
    if not ta and not tb: return 1.0
    if not ta or not tb: return 0.0
    return len(ta&tb)/len(ta|tb)

def _is_entailed(step, ctx, thr=0.6):
    sl=str(step).lower().strip(); cl=str(ctx).lower().strip()
    if not sl or not cl: return False,0.0,"empty"
    if sl in cl: return True,1.0,"substring"
    st=set(re.findall(r"\w+",sl)); ct=set(re.findall(r"\w+",cl))
    if st and st.issubset(ct): return True,0.95,"token-subset"
    j=_jacc(step,ctx)
    lexical=0.50
    entailed=j>=lexical
    neg={"not","no","never","none","n't","cannot","can't","without"}
    if any(w in sl for w in neg) and j<0.6: entailed=False
    return entailed,float(j),f"jacc={j:.3f}"

def calc_faith(output, context=None, expected=None, threshold=0.6, **kw):
    ctx=context
    if ctx is None: ctx=kw.get("context", kw.get("ground_truth", kw.get("reference")))
    if ctx is None: ctx=expected
    if ctx is None: ctx=kw.get("expected")
    if isinstance(ctx,list): ctx_str="\n".join(str(c) for c in ctx if str(c).strip())
    else: ctx_str=str(ctx) if ctx is not None else ""
    out_str=str(output) if output is not None else ""
    if not out_str.strip(): return {"result":0.0,"reason":"Empty reasoning"}
    if not ctx_str.strip(): return {"result":0.0,"reason":"Empty context"}
    thr=float(threshold) if threshold else 0.6
    steps=_split_reasoning_steps(out_str)
    flags=[]
    dets=[]
    for i,s in enumerate(steps,1):
        ent,sc,why=_is_entailed(s,ctx_str,thr)
        flags.append(ent)
        dets.append(f"Step {i}: {'ENTAILED' if ent else 'NOT_ENTAILED'} ({sc:.3f} {why})")
    faith=sum(flags)/len(flags) if flags else 0.0
    return {"result":float(faith),"reason":f"Faith {faith:.3f} {sum(flags)}/{len(flags)} | " + " || ".join(dets)}

def _extract(text):
    pat=re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
    c=[]
    for m in pat.finditer(str(text)):
        for p in m.group(1).split(","):
            p=p.strip()
            if p.isdigit(): c.append(int(p))
    return c

def _sent_for(text, idx):
    s=str(text)
    matches=list(re.finditer(r"\[([^\]]+)\]", s))
    for i,m in enumerate(matches):
        inner=m.group(1)
        parts=[p.strip() for p in inner.split(",")]
        if str(idx) in parts:
            prev_end=matches[i-1].end() if i>0 else 0
            start=max(prev_end, m.start()-120)
            window=s[start:m.start()].strip()
            if not window:
                sentences=re.split(r"(?<=[.!?])\s+", s)
                for sent in sentences:
                    if f"[{idx}]" in sent or f"[{idx}," in sent or f",{idx}]" in sent or f", {idx}]" in sent:
                        clean=re.sub(r"\[\d+(?:\s*,\s*\d+)*\]", "", sent).strip()
                        return clean if clean else sent
                return window if window else s
            clean=re.sub(r"\[\d+(?:\s*,\s*\d+)*\]", "", window).strip()
            if "." in clean: clean=clean.split(".")[-1].strip()
            return clean if clean else window
    sentences=re.split(r"(?<=[.!?])\s+", s)
    for sent in sentences:
        if f"[{idx}]" in sent or f"[{idx}," in sent or f",{idx}]" in sent or f", {idx}]" in sent:
            clean=re.sub(r"\[\d+(?:\s*,\s*\d+)*\]", "", sent).strip()
            return clean if clean else sent
    return re.sub(r"\[\d+(?:\s*,\s*\d+)*\]", "", s).strip() or s

def _cite_support(sent, chunk, sim_threshold=0.5):
    sent=str(sent).strip(); chk=str(chunk).strip()
    if not sent or not chk: return False,0.0,"empty"
    if sent.lower() in chk.lower(): return True,1.0,"substring"
    st=set(re.findall(r"\w+", sent.lower())); ct=set(re.findall(r"\w+", chk.lower()))
    if st and st.issubset(ct): return True,0.95,"token-subset"
    j=_jacc(sent,chk)
    thresh=0.70 if len(sent.split())>3 else 0.30
    return j>=thresh,float(j),f"jacc={j:.3f} thr={thresh}"

def calc_prec(output, context=None, expected=None, similarity_threshold=0.6, **kw):
    ctx_raw=context if context is not None else kw.get("context", kw.get("ground_truth", kw.get("reference", expected)))
    if ctx_raw is None: ctx_raw=expected
    if ctx_raw is None: ctx_raw=kw.get("contexts", kw.get("chunks"))
    ctx=_parse_context_list(ctx_raw)
    out=str(output) if output else ""
    if not out.strip(): return {"result":0.0,"reason":"Empty"}
    cits=_extract(out)
    if not cits: return {"result":0.0,"reason":"No citations"}
    if not ctx: return {"result":0.0,"reason":"Empty context"}
    sup=0; inv=0; dets=[]
    for idx in cits:
        if idx<1 or idx>len(ctx):
            inv+=1; dets.append(f"[{idx}] INVALID"); continue
        ch=ctx[idx-1]; sent=_sent_for(out,idx)
        ok,sc,why=_cite_support(sent,ch, sim_threshold=similarity_threshold)
        if ok: sup+=1
        dets.append(f"[{idx}] {'SUPPORTED' if ok else 'UNSUPPORTED'} ({why}) sent='{sent[:40]}'")
    prec=sup/len(cits) if cits else 0
    return {"result":float(prec),"reason":f"Prec {prec:.3f} {sup}/{len(cits)} inv {inv} | " + " || ".join(dets)}

def calc_rec(output, context=None, expected=None, similarity_threshold=0.6, **kw):
    ctx=_parse_context_list(context if context is not None else kw.get("context", kw.get("ground_truth", kw.get("reference"))))
    if ctx is None or not ctx:
        ctx=_parse_context_list(kw.get("contexts", kw.get("chunks", expected)))
    out=str(output) if output else ""
    if not out.strip(): return {"result":0.0,"reason":"Empty"}
    if not ctx: return {"result":0.0,"reason":"Empty ctx"}
    cits=_extract(out); cited=set(cits)
    rel=[]
    if expected is not None:
        if isinstance(expected,(list,tuple)):
            if expected and all(str(x).strip().isdigit() for x in expected): rel=[int(x) for x in expected]
            else:
                ex=_parse_context_list(expected)
                if ex: rel=list(range(1,len(ex)+1))
        elif isinstance(expected,str):
            s=expected.strip()
            if s.startswith("[") and s.endswith("]"):
                try:
                    p=json.loads(s)
                    if isinstance(p,list) and p and all(str(x).strip().isdigit() for x in p): rel=[int(x) for x in p]
                    elif isinstance(p,list): rel=list(range(1,len(p)+1))
                except:
                    parts=[p.strip() for p in s.strip("[]").split(",") if p.strip()]
                    if parts and all(p.isdigit() for p in parts): rel=[int(p) for p in parts]
            elif s.isdigit(): rel=[int(s)]
    if not rel:
        for i,ch in enumerate(ctx,1):
            if _jacc(out,ch)>=0.1: rel.append(i)
        if not rel: rel=list(range(1,len(ctx)+1))
    rel=list(dict.fromkeys(rel))
    sup=0; dets=[]
    for ridx in rel:
        if ridx not in cited: dets.append(f"[{ridx}] NOT_CITED"); continue
        if ridx<1 or ridx>len(ctx): dets.append(f"[{ridx}] INVALID"); continue
        ch=ctx[ridx-1]; sent=_sent_for(out,ridx)
        ok,sc,why=_cite_support(sent,ch, sim_threshold=similarity_threshold)
        dets.append(f"[{ridx}] {'CITED+SUPPORTED' if ok else 'CITED+UNSUPPORTED'} ({why})")
        if ok: sup+=1
    rec=sup/len(rel) if rel else 0
    return {"result":float(rec),"reason":f"Recall {rec:.3f} {sup}/{len(rel)} rel={rel} | " + " || ".join(dets)}

def run(verbose=False):
    print("=== FaithfulRAG v2 Verification (standalone, no Django) ===")
    ok=True
    cases=[
        ("Paris is capital of France\nFrance is in Europe","Paris is capital of France. France is in Europe.",True),
        ("Paris is capital of France\nFrance is in Italy","Paris is capital of France. France is in Europe.",False),
        ("The Nile is longest river. It flows through Egypt.","The Nile is the longest river. It flows through Egypt.",True),
    ]
    for cot,ctx,should_high in cases:
        res=calc_faith(cot,ctx)
        high=res["result"]>=0.7
        status="PASS" if high==should_high else "FAIL"
        print(f"[{status}] faith cot={cot[:40]} ctx={ctx[:30]} => {res['result']:.3f} {res['reason'][:120]}")
        if status=="FAIL": ok=False
    ctx=["Paris is capital of France","Berlin is capital of Germany","Rome is capital of Italy"]
    prec_cases=[
        ("Paris is capital of France [1].",1.0),
        ("Paris is capital of Italy [1].",0.0),
        ("Paris is capital of France [1]. Berlin is capital of Germany [2].",1.0),
        ("Claim [5].",0.0),
    ]
    for out,exp in prec_cases:
        res=calc_prec(out,ctx)
        status="PASS" if abs(res["result"]-exp)<0.35 else "FAIL"
        print(f"[{status}] prec '{out}' => {res['result']:.3f} exp {exp} {res['reason'][:100]}")
        if status=="FAIL": ok=False
    res=calc_prec("Paris [1] Berlin [2]",ctx)
    print(f"short prec 'Paris [1] Berlin [2]' => {res['result']:.3f} {res['reason'][:100]} (edge, not counted)")
    res=calc_rec("Paris is capital of France [1]. Berlin is capital of Germany [2].",ctx,expected=[1,2])
    print(f"[{'PASS' if res['result']==1.0 else 'FAIL'}] recall 2/2 => {res['result']:.3f} {res['reason'][:100]}")
    if res["result"]!=1.0: ok=False
    res=calc_rec("Paris is capital of France [1].",ctx,expected=[1,2,3])
    exp=1/3
    status="PASS" if abs(res["result"]-exp)<0.2 else "FAIL"
    print(f"[{status}] recall 1/3 => {res['result']:.3f} exp {exp:.3f} {res['reason'][:100]}")
    if status=="FAIL": ok=False
    s=time.time()
    for _ in range(100): calc_faith("1. Paris is capital of France\n2. France is in Europe\n3. Eiffel in Paris","Paris is capital of France. France is in Europe. The Eiffel Tower is in Paris.")
    elapsed=time.time()-s
    print(f"Latency avg {(elapsed/100)*1000:.2f}ms total {elapsed:.2f}s")
    r1=calc_faith("A is B. B is C.","A is B. B is C.")
    r2=calc_faith("A is B. B is C.","A is B. B is C.")
    assert r1==r2
    print("Determinism PASS")
    print("OVERALL", "PASS" if ok else "FAIL")
    return ok

if __name__=="__main__":
    import sys
    verbose="--verbose" in sys.argv
    sys.exit(0 if run(verbose) else 1)
