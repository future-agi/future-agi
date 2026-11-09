"""Static chart-source security contracts: no Helm, cluster or values files.

This deliberately does not claim to render Go templates. The release gate
must also inspect the default Helm render at the fixed commit.
"""

import json
import re
import unittest
from pathlib import Path

CHART = Path(__file__).resolve().parents[1] / "helm/futureagi"
SECURITY_KEYS = r"privileged|hostPID|hostNetwork|hostIPC"
SECURITY_MAPPING = re.compile(rf"(?<![\w.-])({SECURITY_KEYS})\s*:\s*")
QUOTED_SECURITY_KEY = re.compile(rf'''(["'`])({SECURITY_KEYS})\1''')
DICT_SECURITY_KEY = re.compile(rf"\bdict\s+({SECURITY_KEYS})\b")
LITERAL_FALSE = re.compile(r"false\s*(?:#.*)?$")


def unsafe_literals(text):
    # Fail closed on dynamic security values. Only an unquoted literal false
    # is safe; Helm expressions, aliases, non-booleans and quoted/dict keys
    # need review rather than inference from values files or Go templates.
    hits = []
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        for match in SECURITY_MAPPING.finditer(line):
            remainder = line[match.end():]
            value = re.split(r"[,}]", remainder, maxsplit=1)[0].strip()
            if "{{" in remainder or not LITERAL_FALSE.fullmatch(value):
                hits.append((number, match.group(1)))
        hits.extend((number, match.group(2)) for match in QUOTED_SECURITY_KEY.finditer(line))
        hits.extend((number, match.group(1)) for match in DICT_SECURITY_KEY.finditer(line))
    return hits


class HelmTemplateSecurityTests(unittest.TestCase):
    def test_all_yaml_templates_exclude_privileged_and_host_namespace_literals(self):
        paths = sorted(path for path in (CHART / "templates").rglob("*")
                       if path.is_file() and path.name != "NOTES.txt")
        self.assertTrue(paths, "template scan must not pass with an empty population")
        self.assertIn(CHART / "templates/code-executor/deployment.yaml", paths)
        for path in paths:
            with self.subTest(template=str(path.relative_to(CHART))):
                self.assertEqual(unsafe_literals(path.read_text()), [])

    def test_literal_scanner_covers_each_forbidden_flag_and_inline_maps(self):
        for key in ("privileged", "hostPID", "hostNetwork", "hostIPC"):
            with self.subTest(key=key):
                self.assertTrue(unsafe_literals(f"  {key}: true\n"))
                self.assertTrue(unsafe_literals(f'securityContext: {{"{key}": true}}'))
                self.assertFalse(unsafe_literals(f"  {key}: false\n# {key}: true\n"))

    def test_notes_describe_nonprivileged_sandbox_and_real_policy_constraints(self):
        notes = (CHART / "templates/NOTES.txt").read_text()
        self.assertNotRegex(notes, r"(?i)runs\s+privileged|needs\s+privileged\s+pods")
        for fact in ("privileged: false", "65534", "NET_ADMIN", "allowPrivilegeEscalation", "unprivileged user namespace"):
            with self.subTest(fact=fact):
                self.assertIn(fact, notes)
        self.assertIn("Restricted", notes)

    def test_schema_does_not_require_a_privileged_sandbox(self):
        schema = json.loads((CHART / "values.schema.json").read_text())
        description = schema["properties"]["codeExecutor"]["properties"]["enabled"]["description"]
        self.assertNotIn("privileged: true", description)
        for fact in ("privileged: false", "65534", "NET_ADMIN", "allowPrivilegeEscalation"):
            with self.subTest(fact=fact):
                self.assertIn(fact, description)

    def test_executor_template_binds_the_documented_security_context(self):
        text = (CHART / "templates/code-executor/deployment.yaml").read_text()
        for key, value in (
            ("privileged", "false"), ("runAsNonRoot", "true"),
            ("runAsUser", "65534"), ("runAsGroup", "65534"),
            ("readOnlyRootFilesystem", "true"), ("allowPrivilegeEscalation", "true"),
        ):
            with self.subTest(key=key):
                self.assertRegex(text, rf"(?m)^\s+{key}: {value}\s*$")
        self.assertIn("drop: [ALL]", text)
        self.assertIn("add: [NET_ADMIN]", text)
        self.assertIn("type: Unconfined", text)
        self.assertIn("container.apparmor.security.beta.kubernetes.io/code-executor: unconfined", text)


if __name__ == "__main__":
    unittest.main()
