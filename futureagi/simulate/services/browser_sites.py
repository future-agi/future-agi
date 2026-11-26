"""The sites a browser environment's browser may open.

An environment for an agent that drives a web browser names the sites that browser may open. The
list is the environment's own, apart from its additional egress domains: the browser is held to
these sites alone, while the sandbox as a whole reaches the wider egress list.

Each site is a host name, such as ``example.com``: that host only, with no scheme, port, path or
address. ``*.example.com`` names every host under it (not ``example.com`` itself), as the additional
egress domains do; it is taken only once the kit reads it the same way (``TAKES_WILDCARDS``).

Each site joins the sandbox's egress list as written, so a page on a host the list doesn't name is
blocked there even where the browser's own bound would let it through. A site must pass the egress
list's own rules first, so a list saved here is never refused when the run starts.

Sites are kept in one form: lower case, no trailing dot, a name outside ASCII in the ASCII form a
browser asks for (IDNA 2008, UTS 46 non-transitional), without repeats, sorted.
``browser_sites_at_pin`` is the one reading of a job's sites.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import idna

# Every site also joins the sandbox's egress list, which is finite on some providers. Ten leaves
# room for the hosts every run needs; the provider's own limit is checked on the whole list.
MAX_BROWSER_SITES = 10

# Whether a site may be ``*.`` and a host name. Off until the kit on the stack reads ``*.example.com``
# as the hosts under it and a bare name as that host only, as the sandbox's firewall does.
TAKES_WILDCARDS = False

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
# ASCII only: with IGNORECASE alone, [a-z] also takes letters a browser reads as others.
_HOST = re.compile(rf"(?:\*\.)?{_LABEL}(?:\.{_LABEL})+", re.ASCII)
# A host whose last label is a number (decimal, octal or 0x hex) is read as an IPv4 address.
_NUMBER_LABEL = re.compile(r"[0-9]+|0x[0-9a-f]*", re.ASCII)
# Names that only ever mean a machine on a private network, or no machine at all, never a site on
# the internet: the special-use names of RFC 6761 and RFC 8375 (home.arpa), and the names home,
# office and cluster networks use for themselves.
_LOCAL_SUFFIXES = (
    "localhost",
    "internal",
    "local",
    "lan",
    "localdomain",
    "svc",
    "arpa",
    "test",
    "invalid",
    "example",
)
_LONGEST_HOST = 253


def _one_form(value: Any) -> str | None:
    """The site in its one form, or ``None`` when it is not a public host name."""
    from simulate.services.hosted_harness import HostedHarnessError
    from simulate.services.hosted_harness_gateway import _validate_egress_domains

    if not isinstance(value, str):
        return None
    site = value.strip().lower()
    if site.endswith(".") and not site.endswith(".."):
        site = site[:-1]
    if not site.isascii():
        wildcard = site.startswith("*.")
        try:
            # The form a browser asks for (IDNA 2008 as UTS 46 reads it, non-transitional), so
            # the list names the host a page is on: straße.de stays itself, never strasse.de.
            ascii_form = idna.encode(
                site[2:] if wildcard else site, uts46=True, transitional=False
            ).decode("ascii")
        except UnicodeError:
            return None
        site = f"*.{ascii_form}" if wildcard else ascii_form
    # The egress list's own rules first: every site joins it.
    try:
        _validate_egress_domains([site])
    except HostedHarnessError:
        return None
    if len(site) > _LONGEST_HOST or _HOST.fullmatch(site) is None:
        return None
    if site.startswith("*.") and not TAKES_WILDCARDS:
        return None
    last = site.rsplit(".", 1)[-1]
    if _NUMBER_LABEL.fullmatch(last) is not None or last in _LOCAL_SUFFIXES:
        return None
    return site


def _in_one_form(value: Any) -> list[str]:
    """Every site in its one form, sorted, without repeats; ``HostedHarnessError`` when wrong.

    A refusal names the wrong sites by position, counted from one, never by value: a value that is
    not a host name may be anything, a URL with a password among them.
    """
    from simulate.services.hosted_harness import HostedHarnessError

    if not isinstance(value, list):
        raise HostedHarnessError(
            "browser_sites_invalid",
            "Allowed sites must be a list of host names, such as example.com.",
            status_code=400,
        )
    forms = [_one_form(site) for site in value]
    wrong = [index for index, form in enumerate(forms, start=1) if form is None]
    if wrong:
        named = ", ".join(str(index) for index in wrong)
        raise HostedHarnessError(
            "browser_sites_invalid",
            "Allowed sites must be public host names, such as example.com, "
            "with no scheme, port, path or address. "
            f"Check {'site' if len(wrong) == 1 else 'sites'} {named}.",
            status_code=400,
            details={"positions": wrong},
        )
    # Every form is a site here: a wrong one was refused above.
    sites = sorted({form for form in forms if form is not None})
    if len(sites) > MAX_BROWSER_SITES:
        raise HostedHarnessError(
            "browser_sites_limit_exceeded",
            f"A browser environment allows at most {MAX_BROWSER_SITES} sites; "
            f"this one names {len(sites)}.",
            status_code=400,
            details={"limit": MAX_BROWSER_SITES, "count": len(sites)},
        )
    return sites


def browser_sites(value: Any) -> list[str]:
    """A browser environment's sites as given at create: at least one, in their one form."""
    from simulate.services.hosted_harness import HostedHarnessError

    if isinstance(value, list) and not value:
        raise HostedHarnessError(
            "browser_sites_required",
            "A browser environment needs at least one allowed site, such as example.com.",
            status_code=400,
        )
    return _in_one_form(value)


def browser_sites_at_pin(
    payload: Mapping[str, Any] | None, environment_version: Any = None
) -> list[str]:
    """The sites a job's browser may open at its pin; none for a job without a browser.

    The pinned environment version's own record wins. A job with no pin, or pinned to a version
    made before sites were recorded, reads its create input (``browser.allowed_sites``), so the
    payload alone answers. A stored list the save would refuse is refused here too, never cut.
    """
    recorded = getattr(environment_version, "browser_allowed_sites", None)
    if isinstance(recorded, list):
        return list(recorded)
    browser = (payload or {}).get("browser")
    sites = browser.get("allowed_sites") if isinstance(browser, Mapping) else None
    if sites is None or sites == []:
        return []
    return _in_one_form(sites)


def needs_browser(contract: Any) -> bool:
    """Whether an agent's contract says it needs a browser from its environment."""
    needs = contract.get("environment_needs") if isinstance(contract, dict) else None
    return isinstance(needs, list) and "browser" in needs


BROWSER_ENVIRONMENT_REQUIRED = (
    "This agent drives a web browser, so it runs only in a browser environment. "
    "Build a browser environment for it and list the sites its browser may open."
)


def refuse_unpaired_browser_agent(agent_version_id: Any, sites: Any) -> None:
    """Refuse pairing an agent version that needs a browser with an environment that names no sites.

    Only the need its reading stored refuses: a version not read yet, one whose contract can't be
    read, or one stored before the need existed is left to the build, which refuses it there too.
    The other way round, sites with an agent read as chat, is not refused.
    """
    if agent_version_id is None or sites:
        return
    from simulate.services.agent_contracts import stored_version_contract
    from simulate.services.hosted_harness import HostedHarnessError

    if needs_browser(stored_version_contract(agent_version_id)):
        raise HostedHarnessError(
            "browser_environment_required",
            BROWSER_ENVIRONMENT_REQUIRED,
            status_code=422,
        )
