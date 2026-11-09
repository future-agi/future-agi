"""Exact r3a13 lens A/B source fixtures; source-only complete-audit inputs."""

A_SENDS = (
    (
        "getter_walrus_base",
        "import builtins\nfrom types import SimpleNamespace\nholder = SimpleNamespace()\nother = holder\nclass Box:\n    pass\nbox = Box()\nsetattr(self, 'saved', self.session)\n(b := builtins).getattr(builtins, 'setattr')(box, 'ref', self)\nother.pipe = box.ref.saved\nemit = holder.pipe.get\nemit('offline')",
    ),
    (
        "nested_getter_walrus_base",
        "import builtins\nfrom types import SimpleNamespace\nholder = SimpleNamespace()\nother = holder\nclass Box:\n    pass\nbox = Box()\nsetattr(self, 'saved', self.session)\n(outer := (b := builtins)).getattr(builtins, 'setattr')(box, 'ref', self)\nother.pipe = box.ref.saved\nemit = holder.pipe.get\nemit('offline')",
    ),
    (
        "getter_walrus_callee_and_base",
        "import builtins\nfrom types import SimpleNamespace\nholder = SimpleNamespace()\nother = holder\nclass Box:\n    pass\nbox = Box()\nsetattr(self, 'saved', self.session)\n(lookup := (b := builtins).getattr)(builtins, 'setattr')(box, 'ref', self)\nother.pipe = box.ref.saved\nemit = holder.pipe.get\nemit('offline')",
    ),
    (
        "getter_statement_alias",
        "import builtins\nfrom types import SimpleNamespace\nholder = SimpleNamespace()\nother = holder\nclass Box:\n    pass\nbox = Box()\nsetattr(self, 'saved', self.session)\nlookup = (b := builtins).getattr\nlookup(builtins, 'setattr')(box, 'ref', self)\nother.pipe = box.ref.saved\nemit = holder.pipe.get\nemit('offline')",
    ),
    (
        "getter_object_setter",
        "import builtins\nfrom types import SimpleNamespace\nholder = SimpleNamespace()\nother = holder\nclass Box:\n    pass\nbox = Box()\nsetattr(self, 'saved', self.session)\n(b := builtins).getattr(object, '__setattr__')(box, 'ref', self)\nother.pipe = box.ref.saved\nemit = holder.pipe.get\nemit('offline')",
    ),
    (
        "qualified_setter_alias",
        "import builtins\nfrom types import SimpleNamespace\nholder = SimpleNamespace()\nother = holder\nclass Box:\n    pass\nbox = Box()\nsetattr(self, 'saved', self.session)\nsetter = (b := builtins).setattr\nsetter(box, 'ref', self)\nother.pipe = box.ref.saved\nemit = holder.pipe.get\nemit('offline')",
    ),
    (
        "object_setter_alias",
        "import builtins\nfrom types import SimpleNamespace\nholder = SimpleNamespace()\nother = holder\nclass Box:\n    pass\nbox = Box()\nsetattr(self, 'saved', self.session)\nsetter = (base := object).__setattr__\nsetter(box, 'ref', self)\nother.pipe = box.ref.saved\nemit = holder.pipe.get\nemit('offline')",
    ),
    (
        "nested_object_setter_alias",
        "import builtins\nfrom types import SimpleNamespace\nholder = SimpleNamespace()\nother = holder\nclass Box:\n    pass\nbox = Box()\nsetattr(self, 'saved', self.session)\nsetter = (b := builtins).object.__setattr__\nsetter(box, 'ref', self)\nother.pipe = box.ref.saved\nemit = holder.pipe.get\nemit('offline')",
    ),
)

A_MINIMAL = (
    (
        "qualified_setattr",
        "import builtins\ndef acquire():\n    return (b := builtins).setattr\n",
    ),
    (
        "qualified_delattr",
        "import builtins\ndef acquire():\n    return (b := builtins).delattr\n",
    ),
    (
        "object_setattr",
        "import builtins\ndef acquire():\n    return (base := object).__setattr__\n",
    ),
    (
        "type_setattr",
        "import builtins\ndef acquire():\n    return (base := type).__setattr__\n",
    ),
    (
        "nested_object_setattr",
        "import builtins\ndef acquire():\n    return (b := builtins).object.__setattr__\n",
    ),
    (
        "nested_type_setattr",
        "import builtins\ndef acquire():\n    return (b := builtins).type.__setattr__\n",
    ),
    (
        "getter_prefix",
        "import builtins\ndef acquire():\n    return (b := builtins).getattr(builtins, 'setattr')\n",
    ),
    (
        "nested_getter_prefix",
        "import builtins\ndef acquire():\n    return (outer := (b := builtins)).getattr(builtins, 'setattr')\n",
    ),
    (
        "getter_callee_and_prefix",
        "import builtins\ndef acquire():\n    return (lookup := (b := builtins).getattr)(builtins, 'setattr')\n",
    ),
    (
        "getter_object_acquisition",
        "import builtins\ndef acquire():\n    return (b := builtins).getattr(object, '__setattr__')\n",
    ),
)

A_POSITIONS = (
    (
        "qualified_setattr/assignment",
        "import builtins\nsetter = (b := builtins).setattr",
    ),
    (
        "qualified_setattr/annotated",
        "import builtins\nsetter: object = (b := builtins).setattr",
    ),
    (
        "qualified_setattr/walrus",
        "import builtins\n(setter := (b := builtins).setattr)",
    ),
    ("qualified_setattr/argument", "import builtins\nconsume((b := builtins).setattr)"),
    (
        "qualified_setattr/keyword_argument",
        "import builtins\nconsume(callback=(b := builtins).setattr)",
    ),
    (
        "qualified_setattr/collection",
        "import builtins\nconsume([(b := builtins).setattr])",
    ),
    ("qualified_setattr/return", "import builtins\nreturn (b := builtins).setattr"),
    ("qualified_setattr/yield", "import builtins\nyield (b := builtins).setattr"),
    (
        "qualified_setattr/default",
        "import builtins\ndef save(setter=(b := builtins).setattr):\n    pass",
    ),
    (
        "qualified_setattr/class_body",
        "import builtins\nclass Stored:\n    setter = (b := builtins).setattr",
    ),
    (
        "qualified_setattr/attribute_store",
        "import builtins\nbox.setter = (b := builtins).setattr",
    ),
    (
        "qualified_setattr/global_store",
        "import builtins\nglobal setter\nsetter = (b := builtins).setattr",
    ),
    (
        "qualified_delattr/assignment",
        "import builtins\nsetter = (b := builtins).delattr",
    ),
    (
        "qualified_delattr/annotated",
        "import builtins\nsetter: object = (b := builtins).delattr",
    ),
    (
        "qualified_delattr/walrus",
        "import builtins\n(setter := (b := builtins).delattr)",
    ),
    ("qualified_delattr/argument", "import builtins\nconsume((b := builtins).delattr)"),
    (
        "qualified_delattr/keyword_argument",
        "import builtins\nconsume(callback=(b := builtins).delattr)",
    ),
    (
        "qualified_delattr/collection",
        "import builtins\nconsume([(b := builtins).delattr])",
    ),
    ("qualified_delattr/return", "import builtins\nreturn (b := builtins).delattr"),
    ("qualified_delattr/yield", "import builtins\nyield (b := builtins).delattr"),
    (
        "qualified_delattr/default",
        "import builtins\ndef save(setter=(b := builtins).delattr):\n    pass",
    ),
    (
        "qualified_delattr/class_body",
        "import builtins\nclass Stored:\n    setter = (b := builtins).delattr",
    ),
    (
        "qualified_delattr/attribute_store",
        "import builtins\nbox.setter = (b := builtins).delattr",
    ),
    (
        "qualified_delattr/global_store",
        "import builtins\nglobal setter\nsetter = (b := builtins).delattr",
    ),
    (
        "object_setattr/assignment",
        "import builtins\nsetter = (base := object).__setattr__",
    ),
    (
        "object_setattr/annotated",
        "import builtins\nsetter: object = (base := object).__setattr__",
    ),
    (
        "object_setattr/walrus",
        "import builtins\n(setter := (base := object).__setattr__)",
    ),
    (
        "object_setattr/argument",
        "import builtins\nconsume((base := object).__setattr__)",
    ),
    (
        "object_setattr/keyword_argument",
        "import builtins\nconsume(callback=(base := object).__setattr__)",
    ),
    (
        "object_setattr/collection",
        "import builtins\nconsume([(base := object).__setattr__])",
    ),
    ("object_setattr/return", "import builtins\nreturn (base := object).__setattr__"),
    ("object_setattr/yield", "import builtins\nyield (base := object).__setattr__"),
    (
        "object_setattr/default",
        "import builtins\ndef save(setter=(base := object).__setattr__):\n    pass",
    ),
    (
        "object_setattr/class_body",
        "import builtins\nclass Stored:\n    setter = (base := object).__setattr__",
    ),
    (
        "object_setattr/attribute_store",
        "import builtins\nbox.setter = (base := object).__setattr__",
    ),
    (
        "object_setattr/global_store",
        "import builtins\nglobal setter\nsetter = (base := object).__setattr__",
    ),
    ("type_setattr/assignment", "import builtins\nsetter = (base := type).__setattr__"),
    (
        "type_setattr/annotated",
        "import builtins\nsetter: object = (base := type).__setattr__",
    ),
    ("type_setattr/walrus", "import builtins\n(setter := (base := type).__setattr__)"),
    ("type_setattr/argument", "import builtins\nconsume((base := type).__setattr__)"),
    (
        "type_setattr/keyword_argument",
        "import builtins\nconsume(callback=(base := type).__setattr__)",
    ),
    (
        "type_setattr/collection",
        "import builtins\nconsume([(base := type).__setattr__])",
    ),
    ("type_setattr/return", "import builtins\nreturn (base := type).__setattr__"),
    ("type_setattr/yield", "import builtins\nyield (base := type).__setattr__"),
    (
        "type_setattr/default",
        "import builtins\ndef save(setter=(base := type).__setattr__):\n    pass",
    ),
    (
        "type_setattr/class_body",
        "import builtins\nclass Stored:\n    setter = (base := type).__setattr__",
    ),
    (
        "type_setattr/attribute_store",
        "import builtins\nbox.setter = (base := type).__setattr__",
    ),
    (
        "type_setattr/global_store",
        "import builtins\nglobal setter\nsetter = (base := type).__setattr__",
    ),
    (
        "nested_object_setattr/assignment",
        "import builtins\nsetter = (b := builtins).object.__setattr__",
    ),
    (
        "nested_object_setattr/annotated",
        "import builtins\nsetter: object = (b := builtins).object.__setattr__",
    ),
    (
        "nested_object_setattr/walrus",
        "import builtins\n(setter := (b := builtins).object.__setattr__)",
    ),
    (
        "nested_object_setattr/argument",
        "import builtins\nconsume((b := builtins).object.__setattr__)",
    ),
    (
        "nested_object_setattr/keyword_argument",
        "import builtins\nconsume(callback=(b := builtins).object.__setattr__)",
    ),
    (
        "nested_object_setattr/collection",
        "import builtins\nconsume([(b := builtins).object.__setattr__])",
    ),
    (
        "nested_object_setattr/return",
        "import builtins\nreturn (b := builtins).object.__setattr__",
    ),
    (
        "nested_object_setattr/yield",
        "import builtins\nyield (b := builtins).object.__setattr__",
    ),
    (
        "nested_object_setattr/default",
        "import builtins\ndef save(setter=(b := builtins).object.__setattr__):\n    pass",
    ),
    (
        "nested_object_setattr/class_body",
        "import builtins\nclass Stored:\n    setter = (b := builtins).object.__setattr__",
    ),
    (
        "nested_object_setattr/attribute_store",
        "import builtins\nbox.setter = (b := builtins).object.__setattr__",
    ),
    (
        "nested_object_setattr/global_store",
        "import builtins\nglobal setter\nsetter = (b := builtins).object.__setattr__",
    ),
    (
        "nested_type_setattr/assignment",
        "import builtins\nsetter = (b := builtins).type.__setattr__",
    ),
    (
        "nested_type_setattr/annotated",
        "import builtins\nsetter: object = (b := builtins).type.__setattr__",
    ),
    (
        "nested_type_setattr/walrus",
        "import builtins\n(setter := (b := builtins).type.__setattr__)",
    ),
    (
        "nested_type_setattr/argument",
        "import builtins\nconsume((b := builtins).type.__setattr__)",
    ),
    (
        "nested_type_setattr/keyword_argument",
        "import builtins\nconsume(callback=(b := builtins).type.__setattr__)",
    ),
    (
        "nested_type_setattr/collection",
        "import builtins\nconsume([(b := builtins).type.__setattr__])",
    ),
    (
        "nested_type_setattr/return",
        "import builtins\nreturn (b := builtins).type.__setattr__",
    ),
    (
        "nested_type_setattr/yield",
        "import builtins\nyield (b := builtins).type.__setattr__",
    ),
    (
        "nested_type_setattr/default",
        "import builtins\ndef save(setter=(b := builtins).type.__setattr__):\n    pass",
    ),
    (
        "nested_type_setattr/class_body",
        "import builtins\nclass Stored:\n    setter = (b := builtins).type.__setattr__",
    ),
    (
        "nested_type_setattr/attribute_store",
        "import builtins\nbox.setter = (b := builtins).type.__setattr__",
    ),
    (
        "nested_type_setattr/global_store",
        "import builtins\nglobal setter\nsetter = (b := builtins).type.__setattr__",
    ),
    (
        "getter_prefix/assignment",
        "import builtins\nsetter = (b := builtins).getattr(builtins, 'setattr')",
    ),
    (
        "getter_prefix/annotated",
        "import builtins\nsetter: object = (b := builtins).getattr(builtins, 'setattr')",
    ),
    (
        "getter_prefix/walrus",
        "import builtins\n(setter := (b := builtins).getattr(builtins, 'setattr'))",
    ),
    (
        "getter_prefix/argument",
        "import builtins\nconsume((b := builtins).getattr(builtins, 'setattr'))",
    ),
    (
        "getter_prefix/keyword_argument",
        "import builtins\nconsume(callback=(b := builtins).getattr(builtins, 'setattr'))",
    ),
    (
        "getter_prefix/collection",
        "import builtins\nconsume([(b := builtins).getattr(builtins, 'setattr')])",
    ),
    (
        "getter_prefix/return",
        "import builtins\nreturn (b := builtins).getattr(builtins, 'setattr')",
    ),
    (
        "getter_prefix/yield",
        "import builtins\nyield (b := builtins).getattr(builtins, 'setattr')",
    ),
    (
        "getter_prefix/default",
        "import builtins\ndef save(setter=(b := builtins).getattr(builtins, 'setattr')):\n    pass",
    ),
    (
        "getter_prefix/class_body",
        "import builtins\nclass Stored:\n    setter = (b := builtins).getattr(builtins, 'setattr')",
    ),
    (
        "getter_prefix/attribute_store",
        "import builtins\nbox.setter = (b := builtins).getattr(builtins, 'setattr')",
    ),
    (
        "getter_prefix/global_store",
        "import builtins\nglobal setter\nsetter = (b := builtins).getattr(builtins, 'setattr')",
    ),
    (
        "nested_getter_prefix/assignment",
        "import builtins\nsetter = (outer := (b := builtins)).getattr(builtins, 'setattr')",
    ),
    (
        "nested_getter_prefix/annotated",
        "import builtins\nsetter: object = (outer := (b := builtins)).getattr(builtins, 'setattr')",
    ),
    (
        "nested_getter_prefix/walrus",
        "import builtins\n(setter := (outer := (b := builtins)).getattr(builtins, 'setattr'))",
    ),
    (
        "nested_getter_prefix/argument",
        "import builtins\nconsume((outer := (b := builtins)).getattr(builtins, 'setattr'))",
    ),
    (
        "nested_getter_prefix/keyword_argument",
        "import builtins\nconsume(callback=(outer := (b := builtins)).getattr(builtins, 'setattr'))",
    ),
    (
        "nested_getter_prefix/collection",
        "import builtins\nconsume([(outer := (b := builtins)).getattr(builtins, 'setattr')])",
    ),
    (
        "nested_getter_prefix/return",
        "import builtins\nreturn (outer := (b := builtins)).getattr(builtins, 'setattr')",
    ),
    (
        "nested_getter_prefix/yield",
        "import builtins\nyield (outer := (b := builtins)).getattr(builtins, 'setattr')",
    ),
    (
        "nested_getter_prefix/default",
        "import builtins\ndef save(setter=(outer := (b := builtins)).getattr(builtins, 'setattr')):\n    pass",
    ),
    (
        "nested_getter_prefix/class_body",
        "import builtins\nclass Stored:\n    setter = (outer := (b := builtins)).getattr(builtins, 'setattr')",
    ),
    (
        "nested_getter_prefix/attribute_store",
        "import builtins\nbox.setter = (outer := (b := builtins)).getattr(builtins, 'setattr')",
    ),
    (
        "nested_getter_prefix/global_store",
        "import builtins\nglobal setter\nsetter = (outer := (b := builtins)).getattr(builtins, 'setattr')",
    ),
    (
        "getter_callee_and_prefix/assignment",
        "import builtins\nsetter = (lookup := (b := builtins).getattr)(builtins, 'setattr')",
    ),
    (
        "getter_callee_and_prefix/annotated",
        "import builtins\nsetter: object = (lookup := (b := builtins).getattr)(builtins, 'setattr')",
    ),
    (
        "getter_callee_and_prefix/walrus",
        "import builtins\n(setter := (lookup := (b := builtins).getattr)(builtins, 'setattr'))",
    ),
    (
        "getter_callee_and_prefix/argument",
        "import builtins\nconsume((lookup := (b := builtins).getattr)(builtins, 'setattr'))",
    ),
    (
        "getter_callee_and_prefix/keyword_argument",
        "import builtins\nconsume(callback=(lookup := (b := builtins).getattr)(builtins, 'setattr'))",
    ),
    (
        "getter_callee_and_prefix/collection",
        "import builtins\nconsume([(lookup := (b := builtins).getattr)(builtins, 'setattr')])",
    ),
    (
        "getter_callee_and_prefix/return",
        "import builtins\nreturn (lookup := (b := builtins).getattr)(builtins, 'setattr')",
    ),
    (
        "getter_callee_and_prefix/yield",
        "import builtins\nyield (lookup := (b := builtins).getattr)(builtins, 'setattr')",
    ),
    (
        "getter_callee_and_prefix/default",
        "import builtins\ndef save(setter=(lookup := (b := builtins).getattr)(builtins, 'setattr')):\n    pass",
    ),
    (
        "getter_callee_and_prefix/class_body",
        "import builtins\nclass Stored:\n    setter = (lookup := (b := builtins).getattr)(builtins, 'setattr')",
    ),
    (
        "getter_callee_and_prefix/attribute_store",
        "import builtins\nbox.setter = (lookup := (b := builtins).getattr)(builtins, 'setattr')",
    ),
    (
        "getter_callee_and_prefix/global_store",
        "import builtins\nglobal setter\nsetter = (lookup := (b := builtins).getattr)(builtins, 'setattr')",
    ),
    (
        "getter_object_acquisition/assignment",
        "import builtins\nsetter = (b := builtins).getattr(object, '__setattr__')",
    ),
    (
        "getter_object_acquisition/annotated",
        "import builtins\nsetter: object = (b := builtins).getattr(object, '__setattr__')",
    ),
    (
        "getter_object_acquisition/walrus",
        "import builtins\n(setter := (b := builtins).getattr(object, '__setattr__'))",
    ),
    (
        "getter_object_acquisition/argument",
        "import builtins\nconsume((b := builtins).getattr(object, '__setattr__'))",
    ),
    (
        "getter_object_acquisition/keyword_argument",
        "import builtins\nconsume(callback=(b := builtins).getattr(object, '__setattr__'))",
    ),
    (
        "getter_object_acquisition/collection",
        "import builtins\nconsume([(b := builtins).getattr(object, '__setattr__')])",
    ),
    (
        "getter_object_acquisition/return",
        "import builtins\nreturn (b := builtins).getattr(object, '__setattr__')",
    ),
    (
        "getter_object_acquisition/yield",
        "import builtins\nyield (b := builtins).getattr(object, '__setattr__')",
    ),
    (
        "getter_object_acquisition/default",
        "import builtins\ndef save(setter=(b := builtins).getattr(object, '__setattr__')):\n    pass",
    ),
    (
        "getter_object_acquisition/class_body",
        "import builtins\nclass Stored:\n    setter = (b := builtins).getattr(object, '__setattr__')",
    ),
    (
        "getter_object_acquisition/attribute_store",
        "import builtins\nbox.setter = (b := builtins).getattr(object, '__setattr__')",
    ),
    (
        "getter_object_acquisition/global_store",
        "import builtins\nglobal setter\nsetter = (b := builtins).getattr(object, '__setattr__')",
    ),
)

B_SENDS = (
    (
        "assign_walrus",
        "import builtins\nfrom types import SimpleNamespace\nholder=SimpleNamespace()\nother=holder\nclass Box:\n    pass\nbox=Box()\nsetattr(self,'saved',self.session)\ngetter = (lookup := getattr)\ngetter(builtins,'setattr')(box,'ref',self)\nother.pipe=box.ref.saved\nemit=holder.pipe.get\nemit('offline')",
    ),
    (
        "annotated_walrus",
        "import builtins\nfrom types import SimpleNamespace\nholder=SimpleNamespace()\nother=holder\nclass Box:\n    pass\nbox=Box()\nsetattr(self,'saved',self.session)\ngetter: object = (lookup := getattr)\ngetter(builtins,'setattr')(box,'ref',self)\nother.pipe=box.ref.saved\nemit=holder.pipe.get\nemit('offline')",
    ),
    (
        "tuple_walrus",
        "import builtins\nfrom types import SimpleNamespace\nholder=SimpleNamespace()\nother=holder\nclass Box:\n    pass\nbox=Box()\nsetattr(self,'saved',self.session)\ngetter, unused = (lookup := getattr), None\ngetter(builtins,'setattr')(box,'ref',self)\nother.pipe=box.ref.saved\nemit=holder.pipe.get\nemit('offline')",
    ),
    (
        "nested_assign_walrus",
        "import builtins\nfrom types import SimpleNamespace\nholder=SimpleNamespace()\nother=holder\nclass Box:\n    pass\nbox=Box()\nsetattr(self,'saved',self.session)\ngetter = (outer := (lookup := getattr))\ngetter(builtins,'setattr')(box,'ref',self)\nother.pipe=box.ref.saved\nemit=holder.pipe.get\nemit('offline')",
    ),
    (
        "qualified_assign_walrus",
        "import builtins\nfrom types import SimpleNamespace\nholder=SimpleNamespace()\nother=holder\nclass Box:\n    pass\nbox=Box()\nsetattr(self,'saved',self.session)\ngetter = (lookup := builtins.getattr)\ngetter(builtins,'setattr')(box,'ref',self)\nother.pipe=box.ref.saved\nemit=holder.pipe.get\nemit('offline')",
    ),
    (
        "imported_assign_walrus",
        "import builtins\nfrom types import SimpleNamespace\nholder=SimpleNamespace()\nother=holder\nclass Box:\n    pass\nbox=Box()\nsetattr(self,'saved',self.session)\nfrom builtins import getattr as read_attr\ngetter = (lookup := read_attr)\ngetter(builtins,'setattr')(box,'ref',self)\nother.pipe=box.ref.saved\nemit=holder.pipe.get\nemit('offline')",
    ),
)
