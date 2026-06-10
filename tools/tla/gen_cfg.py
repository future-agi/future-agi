#!/usr/bin/env python3
"""
gen_cfg.py — auto-generate a TLC .cfg from a TLA+ spec.

Usage:
    python gen_cfg.py Spec.tla            # prints cfg to stdout
    python gen_cfg.py Spec.tla -o out.cfg # writes to file
    python gen_cfg.py Spec.tla --inplace  # writes Spec.cfg next to Spec.tla

Classification (the bug this tool prevents):
    PROPERTY  — body contains <>  ~>  WF_  SF_  ]_  or primed vars (x' = ...)
    INVARIANT — zero-arg operator, no temporal tokens, body looks boolean
    SKIP      — parameterised operators; Init/Next/Fairness/Spec/vars/helpers
"""

import argparse
import re
import sys
from pathlib import Path

# ── Token patterns ────────────────────────────────────────────────────────────

# Any token that indicates a temporal / action formula
_TEMPORAL = re.compile(
    r'<>'           # eventually
    r'|~>'          # leads-to
    r'|\bWF_'       # weak fairness
    r'|\bSF_'       # strong fairness
    r'|\]\s*_\s*\w' # action subscript  ]_vars  ]_<<x,y>>
    r'|\[\]'        # globally / always  [](P)  or  [][P]_v
)

# Primed variable in COMPARISON context (temporal property, not action assignment):
#   x' /= y   x' <= y   x' \in S   x' \subseteq S   x' \notin S
# Excludes the assignment form  x' = y  so that action bodies aren't pulled in.
_PRIMED_COMPARE = re.compile(
    r"\b\w+'\s*(?:/=|<=|>=|\\(?:in|notin|subseteq|union|inter)\b)"
)

# Actions are identified by UNCHANGED or bulk primed-assignments
_ACTION_BODY = re.compile(r'\bUNCHANGED\b')

# Operators that clearly open a boolean formula (safe to classify further)
_BOOL_OPENER = re.compile(r'^\s*(?:/\\|\\/|\\A\b|\\E\b|~\b|<>|\[\]|\\\s*lnot\b)')

# Bodies that define sets / ranges / functions — checked when body does NOT
# open with a boolean operator.  All patterns are start-anchored so they
# don't match \X inside a predicate like  pending \subseteq Modes \X Sites.
_SET_BODY = re.compile(
    r'^\s*\{'            # set literal   { ... }
    r'|^\s*\d+\s*\.\.'   # numeric range  1..N
    r'|^\s*\[\w'         # function def  [x \in S |-> ...]  (but NOT  [][P]_v )
    r'|^\s*CHOOSE\b'     # set choice
    r'|^\s*SUBSET\b'     # powerset
)

# Set-like body even when it starts with something else
# (e.g.  (Modes \X Sites) \union ...  or  Modes \X Sites)
_SET_EXPR = re.compile(r'\\X\b|\\union\b|\\inter\b')

# Names that must be skipped regardless
_SKIP_EXACT = frozenset({
    'Init', 'Next', 'Fairness', 'Spec', 'vars',
    'FullSpec', 'ExtractionSpec', 'ExtractionInit',
    'ExtractionNext', 'ExtractionFairness',
})
# Suffixes that tag structural/spec operators
_SKIP_SUFFIX = ('Spec', 'Fairness', 'Next', 'Init', 'Keys')


# ── TLA+ mini-parser ──────────────────────────────────────────────────────────

def _strip_comments(src: str) -> str:
    src = re.sub(r'\(\*.*?\*\)', ' ', src, flags=re.DOTALL)  # (* block *)
    src = re.sub(r'\\\*[^\n]*', ' ', src)                    # \* line comment
    return src


def _parse(src: str) -> dict:
    clean = _strip_comments(src)

    # Module name
    m = re.search(r'^-{4,}\s+MODULE\s+(\w+)\s+-{4,}', clean, re.MULTILINE)
    module_name = m.group(1) if m else Path(sys.argv[1]).stem

    # CONSTANTS block — split on comma/newline, grab valid identifiers only
    # We look for each CONSTANTS block (specs can have more than one).
    constants: list[str] = []
    for cm in re.finditer(
        r'\bCONSTANTS?\b(.*?)(?=\n[A-Z]|\n\n|\bASSUME\b|\bVARIABLES?\b)',
        clean, re.DOTALL,
    ):
        for token in re.split(r'[\s,\n]+', cm.group(1)):
            # Must be an identifier that starts with a capital letter
            # (constants in our specs are capitalized; skip helper words)
            if re.match(r'^[A-Z][A-Za-z0-9_]*$', token) and token not in constants:
                constants.append(token)

    # ASSUME statements (for type inference)
    assumes = re.findall(r'\bASSUME\b(.*?)(?=\n(?=\S))', clean, re.DOTALL)

    # Operator definitions — two forms:
    #   WithParams(a, b) == body
    #   NoParams         == body
    # We only care about no-param, uppercase-starting operators.
    _op_re = re.compile(
        r'^([A-Z][A-Za-z0-9_]*)'  # name
        r'(\s*\([^)]*\))?\s*==',   # optional params then ==
        re.MULTILINE,
    )

    operators = []
    matches = list(_op_re.finditer(clean))
    for i, m in enumerate(matches):
        name = m.group(1)
        has_params = bool(m.group(2) and m.group(2).strip())
        if has_params:
            operators.append({'name': name, 'params': True, 'body': ''})
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(clean)
        body = clean[start:end].strip()
        operators.append({'name': name, 'params': False, 'body': body})

    return {
        'name': module_name,
        'constants': constants,
        'assumes': assumes,
        'operators': operators,
    }


# ── Classifier ────────────────────────────────────────────────────────────────

def _classify(op: dict) -> str:
    """Return 'spec' | 'property' | 'invariant' | 'skip'."""
    name, body = op['name'], op['body']

    if op['params']:
        return 'skip'
    if name in _SKIP_EXACT or any(name.endswith(s) for s in _SKIP_SUFFIX):
        return 'skip'

    # Spec: body contains  Init /\ [][  pattern
    if re.search(r'\bInit\b.*\[\]\[', body, re.DOTALL):
        return 'skip'  # SPECIFICATION is listed separately

    # Set/range/function body → not a boolean predicate
    if _SET_BODY.search(body):
        return 'skip'
    # Operators that are set expressions but don't open with {/[/digit
    # (e.g.  ViolationKeys == (Modes \X Sites) \union ...)
    if not _BOOL_OPENER.search(body) and _SET_EXPR.search(body):
        return 'skip'

    # Actions (UNCHANGED, or multiple primed assignments) → skip
    if _ACTION_BODY.search(body):
        return 'skip'

    # Temporal / action tokens → property
    if _TEMPORAL.search(body):
        return 'property'
    if _PRIMED_COMPARE.search(body):
        return 'property'

    # Zero-arg, boolean-looking body → invariant
    return 'invariant'


def _find_spec(operators: list[dict]) -> str:
    """Return the name of the Spec operator, or 'Spec'."""
    for op in operators:
        if op['name'] in ('Spec', 'FullSpec') or op['name'].endswith('Spec'):
            if not op['params'] and re.search(r'\bInit\b', op.get('body', '')):
                return op['name']
    return 'Spec'


# ── Constant value inference ──────────────────────────────────────────────────

def _infer_value(name: str, assumes: list[str]) -> str:
    text = ' '.join(assumes)

    if re.search(rf'\b{name}\b\s*\\in\s*BOOLEAN', text):
        return 'TRUE'

    nat = re.search(rf'\b{name}\b\s*\\in\s*Nat', text)
    if nat:
        ge = re.search(rf'\b{name}\b\s*>=\s*(\d+)', text)
        base = int(ge.group(1)) if ge else 1
        return str(min(base + 2, 6))

    finite = re.search(rf'IsFiniteSet\s*\(\s*{name}\b', text)
    if finite:
        prefix = name[0].upper()
        return '{' + f'{prefix}1, {prefix}2' + '}'

    # Boolean constant used as flag (e.g. FailureModel = TRUE in spec)
    if name.endswith('Model') or name.endswith('Flag') or name.endswith('Mode'):
        return 'TRUE'

    # Default: model value (TLC treats bare word as uninterpreted)
    return name


# ── Generator ─────────────────────────────────────────────────────────────────

def generate(parsed: dict) -> str:
    lines: list[str] = []
    name = parsed['name']
    constants = parsed['constants']
    assumes = parsed['assumes']
    operators = parsed['operators']

    lines += [
        f'\\* Auto-generated by tools/tla/gen_cfg.py — review before use',
        f'\\* Module: {name}',
        '',
    ]

    # SPECIFICATION
    spec = _find_spec(operators)
    lines += [f'SPECIFICATION {spec}', '']

    # CONSTANTS
    if constants:
        lines.append('CONSTANTS')
        for c in constants:
            val = _infer_value(c, assumes)
            lines.append(f'    {c} = {val}')
        lines.append('')

    # Classify all operators
    invariants = [op['name'] for op in operators if _classify(op) == 'invariant']
    properties = [op['name'] for op in operators if _classify(op) == 'property']

    if invariants:
        lines.append('INVARIANTS')
        for inv in invariants:
            lines.append(f'    {inv}')
        lines.append('')

    if properties:
        lines.append('PROPERTIES')
        for prop in properties:
            lines.append(f'    {prop}')
        lines.append('')

    return '\n'.join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('spec', help='path to the .tla file')
    parser.add_argument('-o', '--output', help='write to this file instead of stdout')
    parser.add_argument('--inplace', action='store_true',
                        help='write <spec>.cfg next to the .tla file')
    args = parser.parse_args(argv)

    src = Path(args.spec).read_text()
    parsed = _parse(src)
    cfg = generate(parsed)

    if args.inplace:
        out = Path(args.spec).with_suffix('.cfg')
        out.write_text(cfg)
        print(f'Wrote {out}', file=sys.stderr)
    elif args.output:
        Path(args.output).write_text(cfg)
        print(f'Wrote {args.output}', file=sys.stderr)
    else:
        print(cfg)


if __name__ == '__main__':
    main()
