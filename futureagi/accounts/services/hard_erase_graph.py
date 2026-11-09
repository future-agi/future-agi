"""Bounded, batched ownership resolution for one locked deletion closure.

No framework imports: the caller supplies an unfiltered, projected batch reader.
Forward references describe ownership of the row being changed, not permission
to erase the referenced object. In particular User.organization is NOT an effect
of deleting User. Cycles are resolved by monotone propagation, never per-row DFS.
"""

from collections import defaultdict, deque

MAX_NODES = 100_000
MAX_READS = 1024
MAX_ROUNDS = 64


class OwnershipUnresolved(ValueError):
    pass


def label(obj):
    return obj._meta.label_lower


def projection(model):
    return [
        f.attname
        for f in model._meta.concrete_fields
        if f.primary_key
        or f.is_relation
        or f.name
        in {
            "org",
            "org_id",
            "organization",
            "organization_id",
            "workspace",
            "workspace_id",
            "user",
            "user_id",
            "person",
            "person_id",
        }
    ]


def edges(obj):
    if label(obj) in {"accounts.user", "accounts.organization"}:
        return
    for field in obj._meta.concrete_fields:
        if field.is_relation and (field.many_to_one or field.one_to_one):
            target = field.remote_field.model
            if label(target) == "accounts.user":
                continue
            value = getattr(obj, field.attname)
            if value is not None:
                yield target, field.target_field.attname, value
        elif not field.is_relation and field.name in {"workspace", "workspace_id"}:
            value = getattr(obj, field.attname)
            if value is not None:
                # Reader resolves this label without importing the application here.
                yield "accounts.workspace", "pk", value


def direct_scopes(obj):
    if label(obj) == "accounts.organization":
        return {str(obj.pk)}
    if label(obj) == "accounts.user":
        return set()
    return {
        str(getattr(obj, f.attname))
        for f in obj._meta.concrete_fields
        if not f.is_relation
        and f.name in {"org", "org_id", "organization", "organization_id"}
        and getattr(obj, f.attname) is not None
    }


class OwnershipGraph:
    def __init__(self, rows, reader):
        self.rows = {}
        self.bindings = {}
        self.values = {}
        self.parents = defaultdict(set)
        self.nodes_read = 0
        self.reads = 0
        frontier = self._add(rows)
        for _ in range(MAX_ROUNDS):
            requests = defaultdict(set)
            pending = []
            for obj in frontier:
                key = (label(obj), str(obj.pk))
                for target, field, value in edges(obj):
                    target_label = target if isinstance(target, str) else label(target)
                    binding = (target_label, field, str(value))
                    pending.append((key, binding))
                    if binding not in self.bindings:
                        requests[target, field].add(value)
            frontier = []
            for (target, field), values in requests.items():
                self.reads += 1
                if self.reads > MAX_READS:
                    raise OwnershipUnresolved("ownership read budget exceeded")
                related = list(reader(target, field, values))
                for obj in related:
                    self.bindings[label(obj), field, str(getattr(obj, field))] = (
                        label(obj),
                        str(obj.pk),
                    )
                frontier.extend(self._add(related))
            for key, binding in pending:
                target_key = self.bindings.get(binding)
                if target_key is None:
                    raise OwnershipUnresolved(
                        "an ownership reference cannot be resolved"
                    )
                self.parents[target_key].add(key)
            if not frontier:
                break
        else:
            raise OwnershipUnresolved("ownership depth budget exceeded")
        queue = deque(key for key, values in self.values.items() if values)
        while queue:
            key = queue.popleft()
            for parent in self.parents[key]:
                extra = self.values[key] - self.values[parent]
                if extra:
                    self.values[parent].update(extra)
                    queue.append(parent)

    def _add(self, rows):
        added = []
        for obj in rows:
            key = (label(obj), str(obj.pk))
            if key in self.rows:
                continue
            self.rows[key] = obj
            self.nodes_read += 1
            if self.nodes_read > MAX_NODES:
                raise OwnershipUnresolved("ownership node budget exceeded")
            self.bindings[label(obj), "pk", str(obj.pk)] = key
            pk = getattr(obj._meta, "pk", None)
            if pk is not None:
                self.bindings[label(obj), pk.attname, str(obj.pk)] = key
            self.values[key] = direct_scopes(obj)
            added.append(obj)
        return added

    def scopes(self, obj):
        return self.values[label(obj), str(obj.pk)]
