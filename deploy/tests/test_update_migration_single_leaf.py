"""Every Django app ships exactly one migration leaf, checked without Django.

Two leaves in one app make `migrate` refuse to run ("Conflicting migrations
detected; multiple leaf nodes"), and an update that ships them cannot apply.
That happens when a branch adds a migration on top of a parent that the base
branch has since moved past: each side is linear, the merge is not. The
pending-scope inventory pins which migrations ship; it does not catch this.

The graph is read from the migration files' AST (``dependencies``,
``replaces``, ``run_before``); no migration module is imported.
"""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / 'futureagi'


def _literal_nodes(value):
    """(app_label, name) pairs from a dependencies/replaces/run_before list.

    Non-literal entries such as ``swappable_dependency(...)`` point at another
    app's migration and never make a node of the declaring app a non-leaf.
    """
    if not isinstance(value, (ast.List, ast.Tuple)):
        return []
    nodes = []
    for element in value.elts:
        try:
            node = ast.literal_eval(element)
        except ValueError:
            continue
        if isinstance(node, tuple) and len(node) == 2 and all(isinstance(part, str) for part in node):
            nodes.append(node)
    return nodes


def _migration_attributes(path):
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    for statement in tree.body:
        if isinstance(statement, ast.ClassDef) and statement.name == 'Migration':
            attributes = {}
            for item in statement.body:
                if isinstance(item, ast.Assign) and len(item.targets) == 1 and isinstance(item.targets[0], ast.Name):
                    attributes[item.targets[0].id] = item.value
            return attributes
    return None


def app_label(app_dir):
    """The AppConfig ``label`` declared in ``apps.py``, else the directory name.

    ``agentcc`` is labelled ``prism`` and ``ee/licensing`` ``ee_licensing``, so
    the directory name alone would split one app into two.
    """
    apps_py = app_dir / 'apps.py'
    if apps_py.exists():
        for node in ast.walk(ast.parse(apps_py.read_text(encoding='utf-8'), filename=str(apps_py))):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'label'
                    and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
                return node.value.value
    return app_dir.name


def load_graph(backend=BACKEND):
    """Return ({(app_label, name)}, [(child, parent)]) for every migration under backend."""
    nodes, edges, replaced, labels = set(), [], set(), {}
    for path in sorted(backend.glob('**/migrations/[0-9]*.py')):
        relative = path.relative_to(backend).parts
        # Only first-party apps: a local virtualenv (futureagi/.venv) carries
        # third-party migrations whose graphs are not ours to check.
        if any(part.startswith('.') or part in ('node_modules', 'site-packages') for part in relative):
            continue
        attributes = _migration_attributes(path)
        if attributes is None:
            continue
        app_dir = path.parent.parent
        if app_dir not in labels:
            labels[app_dir] = app_label(app_dir)
        node = (labels[app_dir], path.stem)
        nodes.add(node)
        for parent in _literal_nodes(attributes.get('dependencies')):
            edges.append((node, parent))
        for child in _literal_nodes(attributes.get('run_before')):
            edges.append((child, node))
        replaced.update(_literal_nodes(attributes.get('replaces')))
    # Django drops replaced nodes when their squash is present; so do we.
    nodes -= replaced
    return nodes, edges


def leaves_by_app(nodes, edges):
    has_child = {parent for child, parent in edges if child in nodes and child[0] == parent[0]}
    leaves = {}
    for node in nodes:
        if node not in has_child:
            leaves.setdefault(node[0], set()).add(node[1])
    return leaves


class SingleMigrationLeafTests(unittest.TestCase):
    maxDiff = None

    def test_every_app_has_exactly_one_leaf(self):
        nodes, edges = load_graph()
        self.assertIn(('tracer', '0001_initial'), nodes)
        leaves = leaves_by_app(nodes, edges)
        conflicts = {app: sorted(names) for app, names in leaves.items() if len(names) != 1}
        self.assertEqual(conflicts, {}, 'multiple migration leaves; add a merge migration or re-point the newer one')

    def test_dependencies_on_repository_apps_resolve(self):
        # Guards the label mapping: a parent whose label or name is not on disk
        # would leave its real parent looking like a stray leaf.
        nodes, edges = load_graph()
        apps = {app for app, _ in nodes}
        self.assertIn('prism', apps)
        self.assertNotIn('agentcc', apps)
        self.assertNotIn('token_blacklist', apps)
        missing = sorted({parent for child, parent in edges
                          if parent[0] in apps and parent[1] not in ('__first__', '__latest__') and parent not in nodes})
        self.assertEqual(missing, [])

    def test_two_children_of_one_parent_are_two_leaves(self):
        # The #3941 shape: dev adds 0132_merge on 0131, the branch adds 0132..0134 on 0131.
        nodes = {('tracer', name) for name in ('0131_merge', '0132_merge_dev', '0132_branch', '0134_branch_tip')}
        edges = [
            (('tracer', '0132_merge_dev'), ('tracer', '0131_merge')),
            (('tracer', '0132_branch'), ('tracer', '0131_merge')),
            (('tracer', '0134_branch_tip'), ('tracer', '0132_branch')),
        ]
        self.assertEqual(leaves_by_app(nodes, edges), {'tracer': {'0132_merge_dev', '0134_branch_tip'}})
        # Re-pointing the branch's first migration at dev's leaf linearises it.
        edges[1] = (('tracer', '0132_branch'), ('tracer', '0132_merge_dev'))
        self.assertEqual(leaves_by_app(nodes, edges), {'tracer': {'0134_branch_tip'}})

    def test_cross_app_and_swappable_dependencies_do_not_hide_leaves(self):
        source = ast.parse(
            "deps = [('accounts', '0031_x'), migrations.swappable_dependency(settings.AUTH_USER_MODEL), ('tracer', '0131_y')]"
        ).body[0].value
        self.assertEqual(_literal_nodes(source), [('accounts', '0031_x'), ('tracer', '0131_y')])
        nodes = {('tracer', '0131_y'), ('tracer', '0132_z'), ('accounts', '0031_x')}
        edges = [(('tracer', '0132_z'), ('tracer', '0131_y')), (('tracer', '0132_z'), ('accounts', '0031_x'))]
        self.assertEqual(leaves_by_app(nodes, edges), {'tracer': {'0132_z'}, 'accounts': {'0031_x'}})


if __name__ == '__main__':
    unittest.main()
