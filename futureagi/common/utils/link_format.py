"""Pattern-only link checks, also embedded in the self-contained system templates.

Keep the YAML helpers identical to these functions; acceptance tests enforce parity.
"""


def _links(text):
    import re

    # Explicit schemes retain malformed/private candidates for refusal rather
    # than treating them as link-free prose: any "<scheme>://" (RFC 3986
    # scheme characters, so ftp://, ws:// and typos such as htp:// too) and
    # scheme-relative "//host". A "//host" starts a word or follows "=" or
    # ":" (an unquoted attribute such as src=//host, or a typo such as
    # http:://host), and names a dotted domain, an IPv4 address or a
    # bracketed IPv6 address, followed by the end, a port, "/", "?" or "#",
    # so code comments and build labels such as "//TODO", "//go:build" or
    # "//pkg:target" are not links. A "<scheme>://" link is matched whole
    # by the first alternative, so its "//" is never a second link. Quotes
    # include typographic ones, and the last domain label may be punycode.
    # Bare domains need alphabetic TLDs.
    pattern = (
        r"(?<![\w@.+-])[A-Za-z][A-Za-z0-9+.-]*://[^\s<>\"']*|"
        r"(?<![^\s\"'`(\[{<=:\u201c\u2018\u00ab])//(?:[^\s/?#@<>\"'\[\]]*@)?"
        r"(?:(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+"
        r"(?:[A-Za-z]{2,}|xn--[A-Za-z0-9-]+)\.?|"
        r"[0-9]{1,3}(?:\.[0-9]{1,3}){3}|\[[0-9A-Fa-f.]*:[0-9A-Za-z:.%~_-]*\])"
        r"(?::[0-9]+)?(?=[/?#\s<>\"'`)\]}\u201d\u2019\u00bb]|"
        r"[.,;!?:]+(?:[\s<>\"'`)\]}\u201d\u2019\u00bb]|$)|$)"
        r"(?:[/?#][^\s<>\"']*)?|"
        r"(?<![\w@.+/:-])(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+"
        r"[A-Za-z]{2,}(?![\w-]|\.[A-Za-z0-9])(?::[0-9]+)?(?:[/?#][^\s<>\"']*)?"
    )
    text = str(text)
    result = []
    checked = 0
    # Whitespace-separated words, read once in step with the matches, so
    # the email check below stays linear in the length of the text.
    words = re.finditer(r"\S+", text)
    word_end = 0
    in_email = False
    for match in re.finditer(pattern, text, re.IGNORECASE):
        candidate = match.group().rstrip(".,;!?:")
        # Drop unbalanced closing brackets at the end, counted once.
        for close, opening in ((")", "("), ("]", "[")):
            surplus = candidate.count(close) - candidate.count(opening)
            trailing = len(candidate) - len(candidate.rstrip(close))
            candidate = candidate[: len(candidate) - max(0, min(surplus, trailing))]
        explicit = re.match(r"[a-z][a-z0-9+.-]*://|//", candidate, re.IGNORECASE)
        if not explicit:
            # A suffix after punctuation in an email is still part of the email.
            while word_end <= match.start():
                word = next(words)
                word_end = word.end()
                authority = re.split(r"[/?#]", word.group(), maxsplit=1)[0]
                in_email = "@" in authority
            if in_email:
                continue
        if not explicit or explicit.group() == "//":
            # Common filenames are prose, not links ("//package.json" is a code
            # comment). An explicit scheme or www prefix disambiguates a real
            # domain with one of these endings.
            authority = re.split(r"[/?#]", candidate.removeprefix("//"), maxsplit=1)[0]
            host = authority.rsplit("@", 1)[-1].split(":")[0]
            if not host.lower().startswith("www.") and host.rsplit(".", 1)[
                -1
            ].lower() in {
                "txt",
                "pdf",
                "json",
                "yaml",
                "yml",
                "csv",
                "png",
                "jpg",
                "jpeg",
                "gif",
                "svg",
                "zip",
                "py",
                "js",
                "ts",
                "log",
                "html",
                "css",
            }:
                continue
        if not explicit:
            candidate = "http://" + candidate
        result.append(candidate)
        # Only http(s) links count toward the limit; the others are never valid.
        checked += _web_link(candidate)
        if checked > 20:
            raise ValueError("Too many links to validate")
    return result


def _web_link(url):
    return url.lower().startswith(("http://", "https://"))


def _valid_link(url):
    """Check URL syntax only; never resolve a hostname or contact a destination."""
    import ipaddress
    import re
    from urllib.parse import urlsplit

    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url):
        return False
    if any(char in url for char in '\\<>"{}|^`') or re.search(
        r"%(?![0-9a-fA-F]{2})", url
    ):
        return False
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        if parsed.username is not None or parsed.password is not None:
            return False
        # urlsplit accepts an empty port and junk after a bracketed IP. Neither
        # is a well-formed authority. Port validity is syntactic, not a service policy.
        if not re.fullmatch(
            r"(?:\[[0-9a-fA-F:.]+\]|[^:\[\]]+)(?::[0-9]+)?", parsed.netloc
        ):
            return False
        if parsed.port is not None and not 0 < parsed.port < 65536:
            return False
        host = parsed.hostname.removesuffix(".").encode("idna").decode("ascii").lower()
        if not host or len(host) > 253 or "%" in host:
            return False
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            labels = host.split(".")
            if len(labels) < 2 or not all(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in labels
            ):
                return False
            # Require a TLD, not an integer, abbreviated IP or single-label host.
            if not re.fullmatch(r"[a-z]{2,}|xn--[a-z0-9-]+", labels[-1]):
                return False
            host.encode("ascii").decode("idna")
            return True
        # Literal addresses must have public-address form. This classifies only
        # the supplied literal: domain names are never resolved or geo-located.
        return (
            address.is_global
            and not address.is_reserved
            and not address.is_multicast
            and not (
                address.version == 6
                and (
                    address.is_site_local
                    or address.ipv4_mapped
                    or address.sixtofour
                    or address.teredo
                )
            )
        )
    except (ValueError, UnicodeError):
        return False


def _evaluate_links(text, require_all):
    try:
        links = _links(text)
        if require_all:
            if not links:
                return {
                    "score": 1.0,
                    "reason": "No links found in output, so none are invalid",
                }
            if not all(_web_link(link) for link in links):
                return {
                    "score": 0.0,
                    "reason": "Found invalid links (not http or https)",
                }
            if all(_valid_link(link) for link in links):
                return {"score": 1.0, "reason": f"All {len(links)} link(s) are valid"}
            return {"score": 0.0, "reason": "Found links with invalid format"}
        web_links = [link for link in links if _web_link(link)]
        if not web_links:
            if links:
                return {
                    "score": 0.0,
                    "reason": "No valid link found (not http or https)",
                }
            return {"score": 0.0, "reason": "No link found in output"}
        if any(_valid_link(link) for link in web_links):
            return {"score": 1.0, "reason": "Link found with valid format"}
        return {"score": 0.0, "reason": "No link with valid format found"}
    except ValueError:
        return {"score": 0.0, "reason": "Too many links to validate"}
    except Exception:  # noqa: BLE001 -- evaluator boundary returns a fixed, URL-free reason
        # Fixed explanations never include submitted URLs or parser diagnostics.
        return {"score": 0.0, "reason": "Link could not be validated"}
