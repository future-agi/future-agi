"""Pure check contracts, boundaries, and credential-safe diagnostics."""

import json

import pytest

from model_hub.services.skills_hub import checks, limits

DESCRIPTION = "Use when a support request needs a clear and verifiable next step."


def skill(header=None, body="Follow the approved policy.\n"):
    header = (
        header if header is not None else f"name: help-desk\ndescription: {DESCRIPTION}"
    )
    return {"path": "SKILL.md", "content": f"---\n{header}\n---\n{body}"}


def codes(files, **kwargs):
    return {item["code"] for item in checks.run_checks(files, **kwargs)["items"]}


# Severities are specified independently from the implementation, per SPEC §6.
CASES = [
    ("root_missing", "error", []),
    (
        "frontmatter_missing",
        "error",
        [{"path": "SKILL.md", "content": "# Instructions"}],
    ),
    ("frontmatter_invalid", "error", [skill("name: [")]),
    ("name_missing", "error", [skill(f"description: {DESCRIPTION}")]),
    ("name_invalid", "error", [skill(f"name: Bad_Name\ndescription: {DESCRIPTION}")]),
    (
        "name_mismatch",
        "error",
        [skill(f"name: other-skill\ndescription: {DESCRIPTION}")],
    ),
    ("description_missing", "error", [skill("name: help-desk")]),
    (
        "description_too_long",
        "error",
        [skill(f"name: help-desk\ndescription: {'x' * 1025}")],
    ),
    (
        "compatibility_too_long",
        "error",
        [
            skill(
                f"name: help-desk\ndescription: {DESCRIPTION}\ncompatibility: {'x' * 501}"
            )
        ],
    ),
    (
        "metadata_invalid",
        "error",
        [skill(f"name: help-desk\ndescription: {DESCRIPTION}\nmetadata: [wrong]")],
    ),
    ("path_invalid", "error", [skill(), {"path": "../escape.txt", "content": "hello"}]),
    ("path_duplicate", "error", [skill(), {"path": "skill.MD", "content": "hello"}]),
    (
        "file_too_large",
        "error",
        [skill(), {"path": "large.txt", "content": "x" * (limits.MAX_FILE_BYTES + 1)}],
    ),
    (
        "too_many_files",
        "error",
        [skill()] + [{"path": f"{index}.txt", "content": "x"} for index in range(200)],
    ),
    (
        "bundle_too_large",
        "error",
        [skill()]
        + [
            {"path": f"{index}.txt", "content": "x" * limits.MAX_FILE_BYTES}
            for index in range(4)
        ],
    ),
    ("file_not_text", "error", [skill(), {"path": "bad.txt", "content": "a\x00b"}]),
    ("secret_suspected", "warning", [skill(body="AWS key: " + "AKIA" + "A" * 16)]),
    ("link_broken", "warning", [skill(body="Read [policy](references/missing.md).")]),
    ("skill_md_long", "warning", [skill(body="line\n" * 501)]),
    ("skill_md_tokens", "warning", [skill(body="x" * 20001)]),
    (
        "description_short",
        "warning",
        [skill("name: help-desk\ndescription: Use when asked.")],
    ),
    (
        "frontmatter_unknown_key",
        "warning",
        [skill(f"name: help-desk\ndescription: {DESCRIPTION}\nunknown: true")],
    ),
    ("file_empty", "warning", [skill(), {"path": "empty.txt", "content": "\t\n "}]),
    (
        "host_extension",
        "info",
        [skill(f"name: help-desk\ndescription: {DESCRIPTION}\nuser-invocable: true")],
    ),
    (
        "allowed_tools_note",
        "info",
        [skill(f"name: help-desk\ndescription: {DESCRIPTION}\nallowed-tools: Read")],
    ),
    (
        "executable_content",
        "info",
        [skill(), {"path": "scripts/inspect.sh", "content": "printf 'hello\\n'"}],
    ),
    (
        "description_trigger_hint",
        "info",
        [
            skill(
                "name: help-desk\ndescription: A clear and comprehensive support policy for customer questions."
            )
        ],
    ),
]


@pytest.mark.parametrize(
    "code,expected_severity,files", CASES, ids=[case[0] for case in CASES]
)
def test_every_fixed_code_has_a_positive_and_negative_case(
    code, expected_severity, files
):
    result = checks.run_checks(files, expected_name="help-desk")
    findings = [item for item in result["items"] if item["code"] == code]
    assert findings and all(item["severity"] == expected_severity for item in findings)
    assert code not in codes([skill()], expected_name="help-desk")
    for key, severity in (
        ("errors", "error"),
        ("warnings", "warning"),
        ("infos", "info"),
    ):
        assert result["summary"][key] == sum(
            item["severity"] == severity for item in result["items"]
        )


def test_clean_unicode_crlf_and_exact_token_counts():
    root = skill(body="Read [café](references/caf%C3%A9.md).\r\n")
    root["content"] = root["content"].replace("\n", "\r\n")
    reference = {"path": "references/café.md", "content": "日本語\t🙂\r\n"}
    result = checks.run_checks([root, reference], expected_name="help-desk")
    assert result["summary"] == {"errors": 0, "warnings": 0, "infos": 0}
    assert result["frontmatter"] == {"name": "help-desk", "description": DESCRIPTION}
    assert result["tokens"] == {
        "metadata": checks.estimate_tokens("help-desk")
        + checks.estimate_tokens(DESCRIPTION),
        "skill_md": checks.estimate_tokens(root["content"]),
        "total": sum(
            checks.estimate_tokens(file["content"]) for file in (root, reference)
        ),
    }
    assert [
        checks.estimate_tokens(text) for text in ("", "a", "abcd", "abcde", "日本語🙂")
    ] == [0, 1, 1, 2, 1]


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/absolute",
        "C:/absolute",
        "a\\b",
        "a//b",
        "./file",
        "a/../b",
        "a/./b",
        "a/",
        "a. /b",
        "a./b",
        "a\x00b",
        "a\x7fb",
        "a\u0085b",
        "a\ud800b",
        "x" * 256,
        "/".join(["a"] * 9),
    ],
)
def test_invalid_paths(path):
    assert "path_invalid" in codes([skill(), {"path": path, "content": "text"}])


@pytest.mark.parametrize(
    "path", [".hidden", "references/é.md", "x" * 255, "/".join(["a"] * 8)]
)
def test_valid_paths_at_limits(path):
    assert "path_invalid" not in codes([skill(), {"path": path, "content": "text"}])


def test_unicode_casefold_duplicates():
    assert "path_duplicate" in codes(
        [
            skill(),
            {"path": "Straße.md", "content": "a"},
            {"path": "STRASSE.md", "content": "b"},
        ]
    )


@pytest.mark.parametrize(
    "content",
    [b"\xff", "\ud800", "NUL\0", "bell\x07", "escape\x1b", "delete\x7f", "next\u0085"],
)
def test_non_text(content):
    assert "file_not_text" in codes([skill(), {"path": "bad.txt", "content": content}])


@pytest.mark.parametrize(
    "header",
    [
        "[]",
        "null",
        "name: [",
        "!!python/object/apply:os.system ['false']",
        "x: &x [*x]",
        "date: 2026-11-06",
        "x: .nan",
        "1: value",
    ],
)
def test_frontmatter_is_bounded_safe_json_mapping(header):
    parsed, body, items = checks.parse_frontmatter(skill(header, "body")["content"])
    assert parsed is None
    assert body == "body"
    assert items[0]["code"] == "frontmatter_invalid"


def test_missing_frontmatter_closing_delimiter():
    assert (
        checks.parse_frontmatter("---\nname: help-desk\n")[2][0]["code"]
        == "frontmatter_invalid"
    )


def test_frontmatter_byte_guard_runs_before_yaml_load(monkeypatch):
    def unexpected_load(_):
        pytest.fail("Oversized YAML must never be loaded")

    monkeypatch.setattr(checks.yaml, "safe_load", unexpected_load)
    assert (
        checks.parse_frontmatter("---\nx: " + "é" * 8192 + "\n---\n")[2][0]["code"]
        == "frontmatter_invalid"
    )


def test_frontmatter_exact_size_and_one_byte_over():
    block = "x: " + "a" * (limits.MAX_FRONTMATTER_BYTES - 4) + "\n"
    assert checks.parse_frontmatter(f"---\n{block}---\n")[2] == []
    assert (
        checks.parse_frontmatter(f"---\n{block[:-1]}a\n---\n")[2][0]["code"]
        == "frontmatter_invalid"
    )


def test_yaml_merge_expansion_is_checked_before_safe_load(monkeypatch):
    header = "a: &a {key: value}\n"
    for previous, current in zip("abcdef", "bcdefg", strict=True):
        header += (
            f"{current}: &{current} {{<<: [" + ", ".join([f"*{previous}"] * 10) + "]}\n"
        )
    monkeypatch.setattr(
        checks.yaml,
        "safe_load",
        lambda _: pytest.fail("Merge expansion must be bounded before construction"),
    )
    assert (
        checks.parse_frontmatter(f"---\n{header}---\n")[2][0]["code"]
        == "frontmatter_invalid"
    )


def test_small_yaml_aliases_and_merges_remain_supported():
    parsed, _, items = checks.parse_frontmatter(
        "---\nmetadata: &m {owner: team}\ncustom: {<<: *m}\n---\n"
    )
    assert items == []
    assert parsed == {"metadata": {"owner": "team"}, "custom": {"owner": "team"}}


def test_alias_expansion_is_bounded():
    header = "a: &a [x, x, x, x, x, x, x, x, x, x]\n"
    for previous, current in zip("abcdef", "bcdefg", strict=True):
        header += f"{current}: &{current} [" + ", ".join([f"*{previous}"] * 10) + "]\n"
    assert (
        checks.parse_frontmatter(f"---\n{header}---\n")[2][0]["code"]
        == "frontmatter_invalid"
    )


@pytest.mark.parametrize(
    "header,code",
    [
        ("name: 123", "name_invalid"),
        ("name: a--b", "name_invalid"),
        ("name: -abc", "name_invalid"),
        ("name: " + "a" * 65, "name_invalid"),
        ("description: null", "description_missing"),
        ("description: ' '", "description_missing"),
        ("metadata: {number: 2}", "metadata_invalid"),
        ("metadata: null", "metadata_invalid"),
        ("metadata: {1: value}", "metadata_invalid"),
    ],
)
def test_metadata_invalid_values(header, code):
    assert code in codes([skill(header)])


def test_metadata_name_description_and_compatibility_at_limits():
    file = skill(
        f"name: {'a' * 64}\ndescription: {'x' * 1024}\ncompatibility: {'x' * 500}\nmetadata: {{author: sample}}"
    )
    assert checks.run_checks([file])["summary"]["errors"] == 0
    assert "name_mismatch" not in codes([skill()])
    assert "description_short" not in codes(
        [skill("name: help-desk\ndescription: " + "x" * 40)]
    )


def test_file_and_bundle_size_byte_boundaries():
    root = skill()
    root_bytes = len(root["content"].encode("utf-8"))
    files = [root] + [
        {"path": f"{i}.txt", "content": "é" * (limits.MAX_FILE_BYTES // 2)}
        for i in range(3)
    ]
    files.append(
        {"path": "last.txt", "content": "x" * (limits.MAX_FILE_BYTES - root_bytes)}
    )
    assert not {"file_too_large", "bundle_too_large"} & codes(files)
    files[-1]["content"] += "x"
    assert "bundle_too_large" in codes(files)
    assert "file_too_large" in codes(
        [
            root,
            {"path": "large.txt", "content": "é" * (limits.MAX_FILE_BYTES // 2) + "x"},
        ]
    )


def test_exact_file_count_line_and_token_limits():
    files = [skill()] + [
        {"path": f"{index}.txt", "content": "x"} for index in range(199)
    ]
    assert "too_many_files" not in codes(files)
    root = skill(body="")
    header_lines = len(root["content"].splitlines())
    root["content"] += "line\n" * (500 - header_lines)
    assert "skill_md_long" not in codes([root])
    root["content"] += "line\n"
    assert "skill_md_long" in codes([root])
    root = skill(body="")
    root["content"] += "x" * (20000 - len(root["content"]))
    assert "skill_md_tokens" not in codes([root])
    root["content"] += "x"
    assert "skill_md_tokens" in codes([root])


# Synthetic values are assembled rather than storing credential-looking literals.
@pytest.mark.parametrize(
    "prefix,suffix",
    [
        ("AKIA", "A" * 16),
        ("ASIA", "B" * 16),
        *((f"gh{kind}_", "a" * 36) for kind in "pousr"),
        ("sk-", "a" * 32),
        ("sk-proj-", "a" * 40),
        ("sk-ant-", "a" * 40),
        *((f"xox{kind}-", "1234567890-abcdefghijkl") for kind in "abprs"),
        ("AIza", "b" * 35),
        ("sk_live_", "a" * 24),
        ("rk_live_", "b" * 24),
        ("eyJ", "abc.defghi.jklmno"),
        ("-----BEGIN PRIVATE KEY-----\n", "synthetic\n-----END PRIVATE KEY-----"),
        ("api_key: ", "AbCd0123EfGh4567IjKl89Mn"),
        ("password = '", "AbCd0123EfGh4567IjKl89Mn"),
    ],
)
def test_each_secret_rule_is_redacted_with_line_and_no_leak(prefix, suffix):
    secret = prefix + suffix
    result = checks.run_checks([skill(body="Safe line\n" + secret)])
    findings = [item for item in result["items"] if item["code"] == "secret_suspected"]
    assert len(findings) == 1
    assert findings[0]["line"] == 6
    serialized = json.dumps(result)
    assert secret not in serialized
    assert suffix not in serialized
    assert "redacted" in findings[0]["detail"]["redacted"]


def test_generic_secret_in_yaml_and_filename_never_escapes_diagnostics():
    value = "".join(("AbCd0123", "EfGh4567", "IjKl89Mn"))
    header = f"name: help-desk\ndescription: {DESCRIPTION}\nmetadata:\n  api_key: '{value}'\n  copy: '{value}'"
    result = checks.run_checks(
        [skill(header), {"path": f"{value}.txt", "content": "text"}]
    )
    assert "secret_suspected" in {item["code"] for item in result["items"]}
    assert value not in json.dumps(result)
    assert result["frontmatter"]["metadata"]["api_key"].startswith("[redacted")


def test_yaml_parser_errors_do_not_echo_credentials():
    value = "ghp_" + "z" * 36
    result = checks.run_checks([skill("name: [" + value)])
    assert value not in json.dumps(result)
    assert {"frontmatter_invalid", "secret_suspected"} <= {
        item["code"] for item in result["items"]
    }


def test_provider_secret_in_filename_is_redacted():
    value = "AKIA" + "Z" * 16
    result = checks.run_checks([skill(), {"path": f"../{value}", "content": "text"}])
    assert value not in json.dumps(result)


def test_overlapping_rules_are_one_finding_and_low_entropy_is_not_a_secret():
    result = checks.run_checks([skill(body="api_key: " + "sk-proj-" + "AbCd1234" * 5)])
    assert sum(item["code"] == "secret_suspected" for item in result["items"]) == 1
    assert "secret_suspected" not in codes(
        [skill(body="password: " + "a" * 40 + "\napi_key: ${KEY_FROM_ENV}\n")]
    )


@pytest.mark.parametrize(
    "body",
    [
        "[missing](references/absent.md)",
        "`scripts/absent.sh`",
        "`assets/absent.txt`",
        "[missing][p]\n\n[p]: references/absent.md",
        "![image](assets/missing.svg)",
    ],
)
def test_missing_relative_links(body):
    assert "link_broken" in codes([skill(body=body)])


def test_long_unclosed_markdown_labels_do_not_trigger_quadratic_scanning():
    assert "link_broken" not in codes([skill(body="[" * 200000)])


def test_relative_links_and_external_links():
    files = [
        skill(
            body="[ref](references/policy.md#rules) `references/policy.md`\n[web](https://example.invalid/x) [mail](mailto:help@example.invalid) [anchor](#start)"
        ),
        {
            "path": "references/policy.md",
            "content": "[root](../SKILL.md)\n[space](<file name.md>)\n",
        },
        {"path": "references/file name.md", "content": "text"},
    ]
    assert "link_broken" not in codes(files)


@pytest.mark.parametrize("key", sorted(checks._HOST_KEYS))
def test_host_extensions_are_info_not_unknown_warnings(key):
    result = checks.run_checks(
        [skill(f"name: help-desk\ndescription: {DESCRIPTION}\n{key}: true")]
    )
    assert result["summary"] == {"errors": 0, "warnings": 0, "infos": 1}
    assert result["items"][0]["code"] == "host_extension"


@pytest.mark.parametrize(
    "path",
    ["scripts/readme.txt", "run.sh", "run.py", "run.js", "run.ts", "run.rb", "run.ps1"],
)
def test_executable_content_is_only_information(path):
    result = checks.run_checks([skill(), {"path": path, "content": "text"}])
    assert result["summary"] == {"errors": 0, "warnings": 0, "infos": 1}
    assert result["items"][0]["code"] == "executable_content"
