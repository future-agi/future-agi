#!/usr/bin/env python3
"""Apply or restore one proof run's spec perturbations in a SHADOW e2e copy.

Never point SHADOW at the worktree's e2e/. Usage (run with python3 -I):
  e2e_proof_mutate.py apply   MUTATIONS.json SHADOW_E2E PRISTINE_E2E RUN_ID OUT_DIFF
  e2e_proof_mutate.py restore MUTATIONS.json SHADOW_E2E PRISTINE_E2E
  e2e_proof_mutate.py verify  MUTATIONS.json DIR_E2E          (spec hashes == pinned)
"""
import difflib
import hashlib
import json
import os
import shutil
import sys


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def load(path):
    with open(path) as f:
        return json.load(f)


def verify(spec, root):
    bad = []
    for rel, want in spec["spec_sha256"].items():
        got = sha(os.path.join(root, rel))
        if got != want:
            bad.append(f"{rel}: {got} != pinned {want}")
    if bad:
        sys.exit("hash mismatch:\n  " + "\n  ".join(bad))
    print(f"verified pinned spec hashes in {root}")


def restore(spec, shadow, pristine):
    if os.path.realpath(shadow) == os.path.realpath(pristine):
        sys.exit("refusing: shadow and pristine are the same directory")
    for rel in spec["spec_sha256"]:
        shutil.copyfile(os.path.join(pristine, rel), os.path.join(shadow, rel))
    verify(spec, shadow)


def apply(spec, shadow, pristine, run_id, out_diff):
    if os.path.realpath(shadow) == os.path.realpath(pristine):
        sys.exit("refusing: shadow and pristine are the same directory")
    restore(spec, shadow, pristine)  # always start from pristine
    muts = [m for m in spec["mutations"] if m["run"] == run_id]
    if not muts:
        sys.exit(f"no mutations for run {run_id}")
    by_file = {}
    for m in muts:
        by_file.setdefault(m["file"], []).append(m)
    diffs = []
    for rel, ms in by_file.items():
        path = os.path.join(shadow, rel)
        with open(path) as f:
            lines = f.read().split("\n")
        before = list(lines)
        for m in ms:
            text = lines[m["line"] - 1]
            if text.count(m["find"]) != 1:
                sys.exit(f"{m['id']}: {rel}:{m['line']} must contain {m['find']!r} exactly once; line is {text!r}")
            lines[m["line"] - 1] = text.replace(m["find"], m["replace"])
        changed = [i for i, (a, b) in enumerate(zip(before, lines)) if a != b]
        if len(lines) != len(before) or len(changed) != len(ms):
            sys.exit(f"{rel}: expected exactly {len(ms)} changed line(s), got {len(changed)}")
        with open(path, "w") as f:
            f.write("\n".join(lines))
        diffs.extend(difflib.unified_diff(
            [l + "\n" for l in before], [l + "\n" for l in lines],
            fromfile=f"pristine/{rel}", tofile=f"{run_id}/{rel}", n=0))
        for m in ms:
            print(f"{m['id']} {m['flow']} {rel}:{m['line']} -> expect failure at line {m['fail_line']}")
    with open(out_diff, "w") as f:
        f.writelines(diffs)
    print(f"wrote {out_diff} ({len(muts)} mutation(s))")


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    cmd = argv[1]
    if cmd == "apply" and len(argv) == 7:
        apply(load(argv[2]), argv[3], argv[4], argv[5], argv[6])
    elif cmd == "restore" and len(argv) == 5:
        restore(load(argv[2]), argv[3], argv[4])
    elif cmd == "verify" and len(argv) == 4:
        verify(load(argv[2]), argv[3])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
