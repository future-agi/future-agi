"""Independent ordinary walrus spellings (no production normalizer)."""

# Exact seven minimal bodies and their hand-written ordinary spellings.
PREFIX_PAIRS = (
    (
        "walrus-rhs-holder-alias",
        'alias = (ref := holder)\nholder.lookup=getattr\nreturn alias.lookup(obj,"setattr")',
        'ref=holder\nalias=holder\nholder.lookup=getattr\nreturn alias.lookup(obj,"setattr")',
    ),
    (
        "walrus-target-holder-alias",
        '(alias := holder).lookup=getattr\nreturn alias.lookup(obj,"setattr")',
        'alias=holder\nholder.lookup=getattr\nreturn alias.lookup(obj,"setattr")',
    ),
    (
        "standalone-walrus-holder-alias",
        '(alias := holder)\nholder.lookup=getattr\nreturn alias.lookup(obj,"setattr")',
        'alias=holder\nholder.lookup=getattr\nreturn alias.lookup(obj,"setattr")',
    ),
    (
        "walrus-getter-attribute-value",
        'alias=holder\nholder.lookup=(getter := getattr)\nreturn alias.lookup(obj,"setattr")',
        'alias=holder\ngetter=getattr\nholder.lookup=getattr\nreturn alias.lookup(obj,"setattr")',
    ),
    (
        "nested-walrus-holder-alias",
        'alias=(first := (second := holder))\nholder.lookup=getattr\nreturn alias.lookup(obj,"setattr")',
        'second=holder\nfirst=holder\nalias=holder\nholder.lookup=getattr\nreturn alias.lookup(obj,"setattr")',
    ),
    (
        "walrus-tuple-holder-alias",
        'alias,unused=(ref := holder),None\nholder.lookup=getattr\nreturn alias.lookup(obj,"setattr")',
        'ref=holder\nalias,unused=holder,None\nholder.lookup=getattr\nreturn alias.lookup(obj,"setattr")',
    ),
    (
        "walrus-annotated-holder-alias",
        'alias:object=(ref := holder)\nholder.lookup=getattr\nreturn alias.lookup(obj,"setattr")',
        'ref=holder\nalias:object=holder\nholder.lookup=getattr\nreturn alias.lookup(obj,"setattr")',
    ),
)

SEND_PREFIX = (
    "import builtins\nfrom types import SimpleNamespace\nholder=SimpleNamespace()\n"
    "other=holder\nclass Box:\n    pass\nbox=Box()\ngetters=SimpleNamespace()\n"
    "setattr(self,'saved',self.session)\n"
)
SEND_SUFFIX = "\nother.pipe=box.ref.saved\nemit=holder.pipe.get\nemit('offline')"


def send_fixture(body):
    return (
        SEND_PREFIX
        + body.replace("holder", "getters").replace(
            'return alias.lookup(obj,"setattr")',
            "alias.lookup(builtins,'setattr')(box,'ref',self)",
        )
        + SEND_SUFFIX
    )


def ordinary_reviewer_fixture(source):
    """Hand-written expression controls; these side targets are never read later.

    Deliberately textual and independent of the scanner: a future broken AST
    normalizer cannot change the expected control. Full scope-sensitive controls
    for the 21 expression-position fixtures are separately written below.
    """
    for before, after in (
        ("(b := builtins)", "builtins"),
        ("(base := object)", "object"),
        ("(base := type)", "type"),
        ("(outer := builtins)", "builtins"),
        ("(lookup := builtins.getattr)", "builtins.getattr"),
        ("(lookup := getattr)", "getattr"),
        ("(lookup := read_attr)", "read_attr"),
        ("(outer := getattr)", "getattr"),
    ):
        source = source.replace(before, after)
    for value in (
        "builtins.setattr",
        "builtins.delattr",
        "object.__setattr__",
        "type.__setattr__",
        "builtins.object.__setattr__",
        "builtins.type.__setattr__",
        "builtins.getattr(builtins, 'setattr')",
        "builtins.getattr(object, '__setattr__')",
    ):
        source = source.replace(f"(setter := {value})", value)
    if ":=" in source:
        raise AssertionError("missing hand-written reviewer control: " + source)
    return source


# In the same order as test_falcon_transport_walrus_offline.POSITIONS. Omitted
# side stores have no reads; live side stores stay in their actual lexical scope.
ORDINARY_POSITIONS = (
    "consume([object.__setattr__ for item in items])",
    "consume({object.__setattr__ for item in items})",
    "consume({item: object.__setattr__ for item in items})",
    "consume((object.__setattr__ for item in items))",
    "save = lambda arg=object.__setattr__: None",
    "save = lambda: object.__setattr__",
    "@decorate(object.__setattr__)\ndef save():\n    pass",
    "consume(box[object.__setattr__])",
    'consume(f"{object.__setattr__}")',
    'consume(f"{value:{object.__setattr__}}")',
    "lookup = getattr\nconsume([getattr for item in items])\nlookup(obj, 'setattr')",
    "lookup = getattr\nconsume([[getattr for j in items] for i in items])\nlookup(obj, 'setattr')",
    "lookup = getattr\nsave = lambda arg=getattr: None\nlookup(obj, 'setattr')",
    "lookup = getattr\n@decorate(getattr)\ndef save():\n    pass\nlookup(obj, 'setattr')",
    "lookup = getattr\nconsume(box[getattr])\nlookup(obj, 'setattr')",
    "lookup = getattr\nconsume(f\"{getattr}\")\nlookup(obj, 'setattr')",
    "consume([getattr(obj, 'setattr') for item in items])",
    "for base in [builtins]:\n    lookup = base.getattr\nlookup(obj, 'setattr')",
    "class Box:\n    getter = getattr\n    lookup = getattr\n    lookup(obj, 'setattr')",
    "global lookup\nlookup = getattr\nlookup(obj, 'setattr')",
    "lookup = None\ndef save():\n    nonlocal lookup\n    lookup = getattr\n    lookup(obj, 'setattr')",
)


def deep_pair(depth):
    prefix = "def acquire(obj):\n    "
    tail = "\n    return lookup(obj, 'setattr')\n"
    return (
        prefix + "not " * depth + "(lookup := getattr)" + tail,
        prefix + "lookup = getattr\n    " + "not " * depth + "lookup" + tail,
    )
