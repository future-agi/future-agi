"""Public client IDs for the Microsoft exclusion, never client credentials.

The configured clients file mixes public IDs and secrets. Do not use the
credential loader (or json.load) here: scan its bounded, flat JSON schema and
skip secret string values without accumulating, decoding or returning them.
Only keys and client IDs are materialized. Invalid/ambiguous metadata fails
closed. Reader errors are consumed here, never attached to a refusal traceback.
"""
import json
import os

_LIMIT = 1 << 20


class _MetadataError(ValueError):
    """A fixed diagnostic only: never a JSON document or credential value."""


class _IDsReader:
    def __init__(self, handle):
        self.handle = handle
        self.size = 0
        self.char = ""
        self.advance()

    def advance(self):
        self.position = self.handle.tell()
        self.char = self.handle.read(1)
        self.size += len(self.char.encode("utf-8"))
        if self.size > _LIMIT:
            raise _MetadataError(f"is larger than {_LIMIT} bytes")

    def whitespace(self):
        while self.char and self.char in " \r\n\t":
            self.advance()

    def take(self, expected):
        self.whitespace()
        if self.char != expected:
            raise ValueError
        self.advance()

    def string(self, *, keep):
        self.take('"')
        text = ['"'] if keep else None
        while self.char != '"':
            if not self.char or ord(self.char) < 32:
                raise ValueError
            if keep:
                text.append(self.char)
            if self.char == "\\":
                self.advance()
                if not self.char or self.char not in '"\\/bfnrtu':
                    raise ValueError
                if keep:
                    text.append(self.char)
                if self.char == "u":
                    for _ in range(4):
                        self.advance()
                        if not self.char or self.char not in "0123456789abcdefABCDEF":
                            raise ValueError
                        if keep:
                            text.append(self.char)
            self.advance()
        self.advance()
        if keep:
            text.append('"')
            return json.loads("".join(text))
        return None

    def shape_error(self, reason):
        # Retain the legacy nesting diagnostic without decoding nested values.
        depth = 0
        while self.char:
            if self.char == '"':
                self.string(keep=False)
                continue
            if self.char in "[{":
                depth += 1
                if depth > 512:
                    raise _MetadataError("nests too deeply")
            elif self.char in "]}":
                depth -= 1
            self.advance()
        raise _MetadataError(reason)

    def entry(self, group):
        from agentcc.services.connector_oauth_clients import _value_problem, MAX_CLIENT_ID_CHARS

        shape = f"entry {group!r} must hold exactly client_id and client_secret"
        self.whitespace()
        if self.char != "{":
            self.shape_error(shape)
        self.take("{")
        fields = set()
        client_id = None
        secret_position = None
        self.whitespace()
        while self.char != "}":
            key = self.string(keep=True)
            if key in fields:
                raise _MetadataError(f"has the key {key!r} twice")
            if key not in {"client_id", "client_secret"}:
                self.shape_error(shape)
            fields.add(key)
            self.take(":")
            self.whitespace()
            if self.char != '"':
                self.shape_error("client_id and client_secret must be a non-empty string")
            if key == "client_id":
                client_id = self.string(keep=True)
                if problem := _value_problem(f"entry {group!r} client_id", client_id, MAX_CLIENT_ID_CHARS):
                    raise _MetadataError(problem)
            else:
                secret_position = self.position
                self.string(keep=False)
            self.whitespace()
            if self.char == "}":
                break
            self.take(",")
            self.whitespace()
            if self.char == "}":
                raise ValueError
        self.take("}")
        if fields != {"client_id", "client_secret"}:
            raise _MetadataError(shape)
        return client_id, secret_position

    def entries(self):
        from agentcc.services.connector_oauth_clients import CATALOG_NAME

        self.whitespace()
        if self.char != "{":
            self.shape_error("must hold one JSON object")
        self.take("{")
        groups = {}
        self.whitespace()
        while self.char != "}":
            group = self.string(keep=True)
            if not CATALOG_NAME.fullmatch(group):
                raise _MetadataError("has a key that is not a client group")
            if group in groups:
                raise _MetadataError(f"has the key {group!r} twice")
            self.take(":")
            groups[group] = self.entry(group)
            self.whitespace()
            if self.char == "}":
                break
            self.take(",")
            self.whitespace()
            if self.char == "}":
                raise ValueError
        self.take("}")
        self.whitespace()
        if self.char:
            raise ValueError
        return groups

    def microsoft_ids(self):
        return {client_id for group, (client_id, _) in self.entries().items()
                if group == "microsoft"}


def file_metadata(path):
    """Public IDs/offsets and a value-free diagnostic; never sealed secrets."""
    if not os.path.isabs(path):
        return None, "must be an absolute path"
    try:
        with open(path, encoding="utf-8") as handle:
            return _IDsReader(handle).entries(), None
    except _MetadataError as error:
        return None, str(error)
    except (ValueError, UnicodeError):
        return None, "is not UTF-8 JSON"
    except OSError as error:
        return None, f"cannot be read ({type(error).__name__})"


def excluded_ids():
    """Both configured sources, not their precedence; None means unreadable.

    No environment secret is accessed, no client-secret object is created, and
    no metadata is cached across rotation. The code-only lift lives in policy.
    """
    client_id = os.environ.get("AGENTCC_CONNECTOR_CLIENT_MICROSOFT_ID")
    ids = {client_id} if client_id else set()
    path = os.environ.get("AGENTCC_CONNECTOR_CLIENTS_FILE")
    if not path:
        return ids
    entries, problem = file_metadata(path)
    if problem is not None:
        return None
    ids.update(client_id for group, (client_id, _) in entries.items() if group == "microsoft")
    return ids


def microsoft_origin(value):
    """Conservative issuer/resource provenance for ambiguous legacy grants."""
    from urllib.parse import urlsplit

    if not isinstance(value, str):
        return False
    try:
        host = (urlsplit(value).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    domains = ("microsoft.com", "microsoftonline.com", "microsoftonline.us",
               "cloud.microsoft", "sharepoint.com", "office.com", "office365.com",
               "windows.net", "microsoftonline.cn")
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def unproven_microsoft_grant(client_id, origins):
    """A grant with no recorded issuing client from a Microsoft issuer or
    resource host: nothing proves which client issued it. Stored fields only."""
    return not client_id and any(microsoft_origin(origin) for origin in origins)


def deny_if_excluded(client_id, *, origins=(), additional_ids=()):
    """The one identity check, independent of routing labels and connect flag.

    Call BEFORE encrypted-field access, decrypt, secret loading or delivery.
    Additional IDs may strengthen the exclusion with a just-opened file's
    metadata; they cannot replace or weaken the configured identity set.
    Missing provenance fails closed for known Microsoft issuer/resource hosts.
    """
    from agentcc.services import microsoft_connect_gate as policy
    from agentcc.services.connector_oauth_clients import OrgClientRequired

    if policy.MICROSOFT_PLATFORM_CLIENT_LIFTED is True:
        return
    ids = excluded_ids()
    if (ids is None or (isinstance(client_id, str) and client_id in (ids | set(additional_ids)))
            or unproven_microsoft_grant(client_id, origins)):
        raise OrgClientRequired(policy.ORG_CLIENT_MESSAGE)


def file_client_id(path, group):
    """Preflight a requested platform group without decoding any secret."""
    from agentcc.services.connector_oauth_clients import CLIENTS_FILE_ENV, _misconfigured

    entries, problem = file_metadata(path)
    if problem is not None:
        raise _misconfigured(group, f"{CLIENTS_FILE_ENV} {problem}", path=path)
    client_id = entries.get(group, (None, None))[0]
    deny_if_excluded(client_id, additional_ids={value[0] for name, value in entries.items()
                                             if name == "microsoft"})
    return client_id


def platform_file(path, seal):
    """Load permitted file entries only; excluded secrets are never decoded.

    First scan all IDs and secret offsets (including secret-before-ID JSON).
    Check all identities before loading any secret. Use the same open descriptor
    for the secret reads so an atomic file rotation cannot swap the document.
    Error frames containing secret material never escape this function.
    """
    from agentcc.services.connector_oauth_clients import OrgClientRequired

    try:
        with open(path, encoding="utf-8") as handle:
            initial = os.fstat(handle.fileno())
            entries = _IDsReader(handle).entries()
            file_ids = {value[0] for name, value in entries.items() if name == "microsoft"}
            allowed = {}
            for group, (client_id, position) in entries.items():
                try:
                    deny_if_excluded(client_id, additional_ids=file_ids)
                except OrgClientRequired:
                    continue
                allowed[group] = (client_id, position)
            document = {}
            for group, (client_id, position) in allowed.items():
                # The scanner skipped secret strings; only an allowed identity
                # can seek back and decode its own string into a sealed holder.
                deny_if_excluded(client_id, additional_ids=file_ids)
                handle.seek(position)
                value = _IDsReader(handle).string(keep=True)
                document[group] = {"client_id": client_id, "client_secret": seal(value)}
            final = os.fstat(handle.fileno())
            if (initial.st_size, initial.st_mtime_ns, initial.st_ctime_ns) != (
                    final.st_size, final.st_mtime_ns, final.st_ctime_ns):
                return None, "changed while reading"
            return document, None
    except _MetadataError as error:
        return None, str(error)
    except (ValueError, UnicodeError):
        return None, "is not UTF-8 JSON"
    except Exception as error:
        return None, f"cannot be read ({type(error).__name__})"
