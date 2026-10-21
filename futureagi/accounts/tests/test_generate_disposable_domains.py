"""
scripts/generate_disposable_domains.py and its consumer,
.github/workflows/refresh-disposable-domains.yml (TH-7620).

The upstream source list this feeds from is untrusted external input, so the
two things that matter here are: the generated module never turns a domain
string into executable code, and the added/removed counts the refresh PR
reports are correct on one-sided diffs (upstream removing domains without
adding any, or vice versa) rather than only on the common case.
"""

import ast
import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "generate_disposable_domains.py"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "refresh-disposable-domains.yml"


def _load_generator_module():
    spec = importlib.util.spec_from_file_location("generate_disposable_domains", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = _load_generator_module()


@pytest.fixture
def domains_file(tmp_path):
    def _write(domains):
        path = tmp_path / "upstream_domains.txt"
        path.write_text("\n".join(sorted(domains)) + "\n")
        return path

    return _write


@pytest.mark.unit
class TestCountingIsRobustToOneSidedDiffs:
    """The old shell implementation counted added/removed lines with
    `grep -c` under `set -e`, which exits 1 (and aborts the script) whenever
    one side of the diff is empty — i.e. every release that only adds or
    only removes domains. Counting is now plain Python set arithmetic."""

    def test_additions_only_is_reported_correctly(self, tmp_path, domains_file):
        out = tmp_path / "disposable_domains.py"
        gen.main(
            [
                "--domains",
                str(domains_file({"a.com", "b.com"})),
                "--version",
                "0.0.1",
                "--out",
                str(out),
            ]
        )
        gen.main(
            [
                "--domains",
                str(domains_file({"a.com", "b.com", "c.com"})),
                "--version",
                "0.0.2",
                "--out",
                str(out),
            ]
        )
        new_domains = gen.load_existing_domains(out)
        assert new_domains == {"a.com", "b.com", "c.com"}

    def test_removals_only_is_reported_correctly(self, tmp_path, domains_file):
        out = tmp_path / "disposable_domains.py"
        gen.main(
            [
                "--domains",
                str(domains_file({"a.com", "b.com", "c.com"})),
                "--version",
                "0.0.1",
                "--out",
                str(out),
            ]
        )
        github_output = tmp_path / "github_output"
        gen.main(
            [
                "--domains",
                str(domains_file({"a.com"})),
                "--version",
                "0.0.2",
                "--out",
                str(out),
                "--github-output",
                str(github_output),
            ]
        )
        result = dict(
            line.split("=", 1) for line in github_output.read_text().splitlines()
        )
        assert result == {"changed": "true", "added": "0", "removed": "2"}

    def test_additions_only_reports_zero_removed_via_github_output(
        self, tmp_path, domains_file
    ):
        out = tmp_path / "disposable_domains.py"
        gen.main(
            [
                "--domains",
                str(domains_file({"a.com"})),
                "--version",
                "0.0.1",
                "--out",
                str(out),
            ]
        )
        github_output = tmp_path / "github_output"
        gen.main(
            [
                "--domains",
                str(domains_file({"a.com", "b.com", "c.com"})),
                "--version",
                "0.0.2",
                "--out",
                str(out),
                "--github-output",
                str(github_output),
            ]
        )
        result = dict(
            line.split("=", 1) for line in github_output.read_text().splitlines()
        )
        assert result == {"changed": "true", "added": "2", "removed": "0"}

    def test_no_change_is_reported_as_unchanged(self, tmp_path, domains_file):
        out = tmp_path / "disposable_domains.py"
        for version in ("0.0.1", "0.0.2"):
            github_output = tmp_path / f"github_output_{version}"
            gen.main(
                [
                    "--domains",
                    str(domains_file({"a.com", "b.com"})),
                    "--version",
                    version,
                    "--out",
                    str(out),
                    "--github-output",
                    str(github_output),
                ]
            )
        result = dict(
            line.split("=", 1) for line in github_output.read_text().splitlines()
        )
        assert result == {"changed": "false", "added": "0", "removed": "0"}


@pytest.mark.unit
class TestGeneratedModuleIsSafeAgainstUntrustedDomains:
    """The upstream list is fetched from a third-party PyPI sdist and
    rendered straight into a Python module that later gets imported — a
    domain string built into that source via unescaped interpolation would
    let a malicious upstream release execute code on import."""

    MALICIOUS_DOMAIN = 'evil.example", __import__("os").system("touch /tmp/pwned"), "x'

    def test_malicious_domain_stays_a_string_literal(self, tmp_path, domains_file):
        out = tmp_path / "disposable_domains.py"
        gen.main(
            [
                "--domains",
                str(domains_file({"safe.com", self.MALICIOUS_DOMAIN})),
                "--version",
                "0.0.1",
                "--out",
                str(out),
            ]
        )

        tree = ast.parse(out.read_text())
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
        # The only call in the module should be the outer frozenset(...) —
        # anything else means a domain string escaped into executable code.
        assert len(calls) == 1

        domains = gen.load_existing_domains(out)
        assert self.MALICIOUS_DOMAIN in domains
        assert "safe.com" in domains

    def test_malicious_domain_does_not_execute_on_import(self, tmp_path, domains_file):
        out = tmp_path / "disposable_domains.py"
        gen.main(
            [
                "--domains",
                str(domains_file({self.MALICIOUS_DOMAIN})),
                "--version",
                "0.0.1",
                "--out",
                str(out),
            ]
        )

        spec = importlib.util.spec_from_file_location("generated_domains_under_test", out)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop("generated_domains_under_test", None)

        assert self.MALICIOUS_DOMAIN in module.DISPOSABLE_EMAIL_DOMAINS
        assert not Path("/tmp/pwned").exists()


@pytest.mark.unit
class TestGeneratedModuleIsSafeAgainstUntrustedVersion:
    """The version comes from the same untrusted PyPI JSON as the domain list,
    and lands inside the generated module's docstring. A value carrying a
    triple quote closes that docstring and turns whatever follows into code
    that runs on import."""

    MALICIOUS_VERSION = '0.0.1"""\nversion_was_executed = True\n"""'

    def test_malicious_version_is_rejected(self, tmp_path, domains_file):
        out = tmp_path / "disposable_domains.py"
        with pytest.raises(ValueError, match="untrusted upstream version"):
            gen.main(
                [
                    "--domains",
                    str(domains_file({"a.com"})),
                    "--version",
                    self.MALICIOUS_VERSION,
                    "--out",
                    str(out),
                ]
            )

    def test_rejected_version_does_not_truncate_the_existing_file(
        self, tmp_path, domains_file
    ):
        """Rendering happens before the output is opened for write, so a bad
        version leaves the previously vendored list intact rather than
        clobbering it with an empty file."""
        out = tmp_path / "disposable_domains.py"
        gen.main(
            [
                "--domains",
                str(domains_file({"a.com", "b.com"})),
                "--version",
                "0.0.1",
                "--out",
                str(out),
            ]
        )
        before = out.read_text()

        with pytest.raises(ValueError):
            gen.main(
                [
                    "--domains",
                    str(domains_file({"a.com"})),
                    "--version",
                    self.MALICIOUS_VERSION,
                    "--out",
                    str(out),
                ]
            )

        assert out.read_text() == before
        assert gen.load_existing_domains(out) == {"a.com", "b.com"}

    def test_malicious_version_does_not_execute_on_import(self, tmp_path):
        """Belt and braces: render() is the choke point, so drive it directly
        and confirm the payload cannot reach the module even if a future caller
        skips main()."""
        with pytest.raises(ValueError):
            gen.render({"a.com"}, self.MALICIOUS_VERSION)

    @pytest.mark.parametrize(
        "version",
        ["0.0.240", "1.2.3", "2024.1.1", "1.0.0rc1", "1.0.0.post1", "1!2.0"],
    )
    def test_real_upstream_versions_are_accepted(self, version, tmp_path, domains_file):
        out = tmp_path / "disposable_domains.py"
        gen.main(
            [
                "--domains",
                str(domains_file({"a.com"})),
                "--version",
                version,
                "--out",
                str(out),
            ]
        )
        assert f"disposable-email-domains {version}" in out.read_text()


@pytest.mark.unit
class TestRefreshWorkflowStructure:
    """Lightweight regression coverage for the two workflow-level bugs that
    aren't reachable from a Python unit test: a scheduled run computing its
    diff against the wrong branch, and a still-open refresh PR blocking every
    later run for that upstream version."""

    @pytest.fixture(autouse=True)
    def _load_workflow(self):
        self.workflow = yaml.safe_load(WORKFLOW_PATH.read_text())

    def _steps(self):
        return self.workflow["jobs"]["refresh"]["steps"]

    def _step_index(self, predicate):
        return next(i for i, s in enumerate(self._steps()) if predicate(s))

    @staticmethod
    def _is_checkout(step):
        return step.get("uses", "").startswith("actions/checkout@")

    def test_checkout_targets_dev_not_the_scheduled_default_branch(self):
        checkout_step = next(s for s in self._steps() if self._is_checkout(s))
        assert checkout_step.get("with", {}).get("ref") == "dev"

    def test_checkout_authenticates_with_the_app_token_not_the_job_token(self):
        """actions/checkout persists the credential it authenticated with, and
        the job's GITHUB_TOKEN is contents:read — so if checkout runs on the
        default token, the `git push` in "Open the PR" is rejected no matter
        what GH_TOKEN is set to on that step."""
        mint_index = self._step_index(lambda s: s.get("id") == "app-token")
        checkout_index = self._step_index(self._is_checkout)
        assert mint_index < checkout_index

        checkout_step = self._steps()[checkout_index]
        assert checkout_step["with"]["token"] == "${{ steps.app-token.outputs.token }}"

    def test_app_token_is_not_gated_on_a_later_step(self):
        """Minting moved ahead of checkout, so it can no longer be conditioned
        on the generate step's output — that would evaluate to false and leave
        checkout with no token at all."""
        mint_step = next(s for s in self._steps() if s.get("id") == "app-token")
        assert "if" not in mint_step

    def test_open_pr_step_updates_an_existing_branch_safely(self):
        open_pr_step = next(
            s
            for s in self.workflow["jobs"]["refresh"]["steps"]
            if s.get("name") == "Open the PR"
        )
        script = open_pr_step["run"]
        assert "checkout -B" in script
        assert "fetch origin" in script
        assert "--force-with-lease" in script
        # Must not attempt to open a second PR for a branch that's already open.
        assert "gh pr view" in script

    def test_generate_step_does_not_use_fragile_grep_counting(self):
        generate_step = next(
            s
            for s in self.workflow["jobs"]["refresh"]["steps"]
            if s.get("name") == "Regenerate the vendored file"
        )
        script = generate_step["run"]
        assert "grep -c" not in script
        assert "--github-output" in script
