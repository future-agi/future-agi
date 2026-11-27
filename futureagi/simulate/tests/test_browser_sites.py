"""The sites a browser environment's browser may open: read strictly, refused when wrong."""

from __future__ import annotations

import pytest

from simulate.services.browser_sites import (
    MAX_BROWSER_SITES,
    browser_sites,
    browser_sites_at_pin,
    refuse_unpaired_browser_agent,
)
from simulate.services.hosted_harness import HostedHarnessError


def _refusal(value) -> HostedHarnessError:
    with pytest.raises(HostedHarnessError) as caught:
        browser_sites(value)
    assert caught.value.status_code == 400
    return caught.value


def test_host_names_are_kept_in_one_form_sorted_without_repeats():
    assert browser_sites(
        [" Shop.Example.com ", "docs.example.org", "shop.example.com."]
    ) == [
        "docs.example.org",
        "shop.example.com",
    ]


def test_a_trailing_dot_names_the_same_host():
    assert browser_sites(["example.com."]) == ["example.com"]


def test_a_name_outside_ascii_is_kept_in_its_ascii_form():
    # The form a browser sends, so the list matches the host the page is on.
    assert browser_sites(["Exämple.com"]) == ["xn--exmple-cua.com"]
    assert browser_sites(["exämple.com", "xn--exmple-cua.com"]) == [
        "xn--exmple-cua.com"
    ]


@pytest.mark.parametrize(
    ("site", "form"),
    [
        # IDNA 2003 would read these as strasse.de and fass.de: other names, maybe others' sites.
        ("straße.de", "xn--strae-oqa.de"),
        ("faß.de", "xn--fa-hia.de"),
        ("*.straße.de", "*.xn--strae-oqa.de"),
        # Already in that form: kept as it is, in lower case.
        ("xn--strae-oqa.de", "xn--strae-oqa.de"),
        ("Shop.XN--Strae-OQA.de", "shop.xn--strae-oqa.de"),
    ],
)
def test_a_name_is_kept_in_the_form_a_browser_asks_for(site, form, monkeypatch):
    monkeypatch.setattr("simulate.services.browser_sites.TAKES_WILDCARDS", True)
    assert browser_sites([site]) == [form]


def test_a_kept_list_reads_back_as_itself():
    # A stored list is read again at every pin (browser_sites_at_pin), so its one form must be
    # a fixed point: reading it back keeps every site and changes none.
    kept = browser_sites(
        [
            "Exämple.com",
            "straße.de",
            "xn--nxasmq6b.com",
            "Shop.Example.com.",
            "docs.example.org",
        ]
    )
    assert browser_sites(kept) == kept


@pytest.mark.parametrize(
    "site", ["*.example.com", "*.example.com.", "*.exämple.com", "*.docs.example.org"]
)
def test_a_wildcard_is_refused_until_the_kit_reads_it(site):
    # Not yet: the merged kit reads *. more widely than the sandbox does.
    refusal = _refusal(["example.com", site])
    assert refusal.code == "browser_sites_invalid"
    assert refusal.details == {"positions": [2]}


def test_a_wildcard_names_every_host_under_a_name_once_the_kit_reads_it(monkeypatch):
    monkeypatch.setattr("simulate.services.browser_sites.TAKES_WILDCARDS", True)
    assert browser_sites(["*.Example.com.", "*.exämple.com"]) == [
        "*.example.com",
        "*.xn--exmple-cua.com",
    ]
    # One wildcard, in front, before a name with a dot.
    for wrong in ("*.com", "*.*.example.com", "a.*.example.com", "*example.com"):
        assert _refusal(["example.com", wrong]).details == {"positions": [2]}
    # Kept as written: a host a wildcard covers stays in the list.
    assert browser_sites(["*.example.com", "shop.example.com"]) == [
        "*.example.com",
        "shop.example.com",
    ]


def test_a_browser_environment_names_at_least_one_site():
    assert _refusal([]).code == "browser_sites_required"


@pytest.mark.parametrize("value", [None, "example.com", {"site": "example.com"}])
def test_anything_but_a_list_is_refused(value):
    assert _refusal(value).code == "browser_sites_invalid"


@pytest.mark.parametrize(
    "site",
    [
        "https://example.com",
        "example.com/path",
        "example.com:8443",
        "user@example.com",
        "example",  # no dot: not a site on the internet
        "*.com",  # a whole top-level domain
        "*.com.",
        "example..com",
        "-example.com",
        "exa mple.com",
        "ex_ample.com",
        "ä" * 64 + ".com",  # a label too long once in ASCII form
        "exämple.com/path",
        "10.0.0.1",
        "127.0.0.1",
        "0x7f.0.0.1",
        "1.2.3.0x7f",
        "[::1]",
        "::1",
        "localhost",
        "app.localhost",
        "host.docker.internal",
        "metadata.internal",
        "printer.local",
        "router.home.arpa",
        "box.lan",
        "box.localdomain",
        "kubernetes.default.svc",
        "shop.test",
        "shop.invalid",
        "shop.example",
        "straße\u200d.de",  # a joiner: no form a browser asks for
        # An ASCII label that claims to be the ASCII form of another name, and is not one.
        "xn--zz.com",
        "shop.xn--zz.com",
        "XN--ZZ.com",
        "xn--a.com",
        # Special-use names that are never a site on the public internet (RFC 7686, RFC 9476).
        "hidden.onion",
        "shop.alt",
        "192.168.1.10",
        "169.254.169.254",
        "*.",
        "",
        "   ",
        "a" * 64 + ".com",
        ".".join(["a" * 63] * 4) + ".com",  # longer than 253
        7,
        None,
    ],
)
def test_a_value_that_is_not_a_public_host_name_is_refused(site):
    refusal = _refusal(["example.com", site])
    assert refusal.code == "browser_sites_invalid"
    # Positions only, counted from one: a wrong value may be anything, a URL with a password among them.
    assert refusal.details == {"positions": [2]}


def test_a_refused_value_is_never_echoed():
    # A wrong value may be anything, a URL with a password among them: the refusal names its
    # position and nothing of the value.
    url = "https://alice-e2e:pw-e2e-secret@private-shop-e2e.org/cart"
    refusal = _refusal(["example.com", url])
    assert refusal.code == "browser_sites_invalid"
    assert refusal.details == {"positions": [2]}
    for part in (url, "pw-e2e-secret", "alice-e2e", "private-shop-e2e"):
        assert part not in refusal.message
        assert part not in repr(refusal.details)


def test_a_site_the_egress_rules_refuse_is_refused(monkeypatch):
    # Every site joins the egress list, so the egress list's own rules apply first.
    def egress_rules(domains):
        if "refused.example.com" in domains:
            raise HostedHarnessError("egress_domain_private", "no", status_code=400)

    monkeypatch.setattr(
        "simulate.services.hosted_harness_gateway._validate_egress_domains",
        egress_rules,
    )
    refusal = _refusal(["shop.example.com", "Refused.Example.com."])
    assert refusal.code == "browser_sites_invalid"
    assert refusal.details == {"positions": [2]}


def test_the_list_is_capped():
    sites = sorted(f"site{index}.example.com" for index in range(MAX_BROWSER_SITES))
    assert browser_sites(sites) == sites
    refusal = _refusal([*sites, "one-more.example.com"])
    assert refusal.code == "browser_sites_limit_exceeded"
    assert refusal.details == {
        "limit": MAX_BROWSER_SITES,
        "count": MAX_BROWSER_SITES + 1,
    }


def test_repeats_do_not_count_against_the_cap():
    sites = sorted(f"site{index}.example.com" for index in range(MAX_BROWSER_SITES))
    assert browser_sites([*sites, sites[0].upper()]) == sites


def test_the_cap_is_ten():
    assert MAX_BROWSER_SITES == 10


# The sites at a job's pin: the one reading every caller uses.


class _Version:
    def __init__(self, sites):
        self.browser_allowed_sites = sites


def _payload(sites):
    return {"browser": {"allowed_sites": sites}}


def test_a_payload_alone_answers_in_the_one_form():
    assert browser_sites_at_pin(
        _payload(["Shop.Example.com", "docs.example.org."])
    ) == [
        "docs.example.org",
        "shop.example.com",
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        None,
        {"browser": None},
        {"browser": {}},
        {"browser": {"allowed_sites": None}},
        {"browser": {"allowed_sites": []}},
    ],
)
def test_a_job_with_no_browser_has_no_sites(payload):
    assert browser_sites_at_pin(payload) == []


def test_the_pinned_version_s_sites_win_over_the_create_input():
    version = _Version(["pinned.example.com"])
    assert browser_sites_at_pin(_payload(["created.example.com"]), version) == [
        "pinned.example.com"
    ]


def test_a_version_that_recorded_no_sites_has_none():
    assert browser_sites_at_pin(_payload(["created.example.com"]), _Version([])) == []


def test_a_version_made_before_sites_were_recorded_reads_the_create_input():
    assert browser_sites_at_pin(_payload(["created.example.com"]), _Version(None)) == [
        "created.example.com"
    ]


def test_no_pin_reads_the_create_input():
    assert browser_sites_at_pin(_payload(["created.example.com"]), None) == [
        "created.example.com"
    ]


def test_a_wrong_stored_list_is_refused_rather_than_cut():
    # Nothing after the save drops a site: a list the save would refuse is refused here too.
    with pytest.raises(HostedHarnessError) as caught:
        browser_sites_at_pin(_payload(["shop.example.com", "10.0.0.1"]))
    assert caught.value.code == "browser_sites_invalid"


def test_the_answer_is_a_copy():
    version = _Version(["pinned.example.com"])
    browser_sites_at_pin({}, version).append("other.example.com")
    assert version.browser_allowed_sites == ["pinned.example.com"]


def test_an_environment_version_has_a_column_for_its_sites_apart_from_its_hash():
    from simulate.models.harness_versioning import HarnessEnvironmentVersion

    field = HarnessEnvironmentVersion._meta.get_field("browser_allowed_sites")
    # Null: made before sites were recorded, so the create input answers.
    assert field.null is True
    assert field.default is None or field.get_default() is None


# The pair: a browser agent is paired only with an environment that names sites.

BROWSER_CONTRACT = {"modality": "chat", "environment_needs": ["browser"]}


def _check(contract, sites, monkeypatch):
    monkeypatch.setattr(
        "simulate.services.agent_contracts.stored_version_contract",
        lambda _agent_version_id: contract,
    )
    refuse_unpaired_browser_agent("version-1", sites)


def test_a_browser_agent_with_no_sites_is_refused_with_its_own_code(monkeypatch):
    with pytest.raises(HostedHarnessError) as caught:
        _check(BROWSER_CONTRACT, [], monkeypatch)
    assert caught.value.code == "browser_environment_required"
    assert caught.value.status_code == 422


def test_a_browser_agent_with_sites_is_paired(monkeypatch):
    _check(BROWSER_CONTRACT, ["shop.example.com"], monkeypatch)


@pytest.mark.parametrize(
    "contract",
    [
        {"modality": "chat"},
        {"modality": "voice"},
        # Stored before the need existed: behaves as it does today.
        {"modality": "browser"},
        {"modality": "chat", "environment_needs": []},
        {"modality": "chat", "environment_needs": "browser"},
        # A version not read yet, or whose contract can't be read: the build decides.
        None,
    ],
)
def test_only_a_contract_that_needs_a_browser_is_refused(contract, monkeypatch):
    _check(contract, [], monkeypatch)


def test_sites_with_an_agent_read_as_chat_are_not_refused(monkeypatch):
    # The other way round is not refused: the run says no browser was started.
    _check({"modality": "chat"}, ["shop.example.com"], monkeypatch)


def test_no_version_needs_no_read(monkeypatch):
    def unread(_agent_version_id):
        raise AssertionError("read")

    monkeypatch.setattr(
        "simulate.services.agent_contracts.stored_version_contract", unread
    )
    refuse_unpaired_browser_agent(None, [])
    refuse_unpaired_browser_agent("version-1", ["shop.example.com"])
