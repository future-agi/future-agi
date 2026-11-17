"""HTTP evaluator definitions and calls, using the shared pinned transport."""
import copy
import hashlib
import hmac
import ipaddress
import json
import math
import secrets
import time
from urllib.parse import urlencode, urlsplit

from rest_framework.exceptions import ValidationError

from tfc.utils.ssrf_guard import IPClass, METADATA_HOSTNAMES, SsrfBlocked, classify_ip
from tfc.utils.ssrf_post import resolve_all, safe_post
from webhooks.transport import watchdog

MAX_BODY = 1024 * 1024
MAX_RETRY_WAIT = 30
TASK_SOFT_LIMIT = 3500
TASK_HARD_LIMIT = 3600
# Reserve 200 seconds before the soft limit for DB writes and child publication.
TASK_CALL_BUDGET = TASK_SOFT_LIMIT - 200


def validate_task_budget(remote, item_count):
    retries = remote.get("retries", 0)
    per_item = (1 + retries) * remote.get("timeout_ms", 15000) / 1000 + retries * MAX_RETRY_WAIT
    waves = math.ceil(item_count / remote.get("calls_at_a_time", 1))
    if waves * per_item > TASK_CALL_BUDGET:
        raise ValidationError("Selected rows exceed the remote scoring task budget; select fewer rows, lower timeout/retries, or increase concurrency.")


def public_auth(auth):
    """Only mode-specific public fields may enter JSON snapshots or responses."""
    kind = auth.get("kind", "hmac")
    if ("token" in auth and kind not in ("bearer", "header")) or ("oauth" in auth and kind != "oauth"):
        raise ValidationError("Remove credentials for inactive authentication modes.")
    fields = {
        "none": (), "hmac": ("previous_valid_until",),
        "bearer": ("header_name", "token_last4"),
        "header": ("header_name", "token_last4"),
        "oauth": ("client_secret_set",),
    }
    if kind not in fields:
        raise ValidationError("Choose a supported authentication mode.")
    result = {"kind": kind, **{key: auth[key] for key in fields[kind] if key in auth}}
    if kind == "oauth":
        oauth = auth.get("oauth") or {}
        if not isinstance(oauth, dict):
            raise ValidationError("OAuth configuration must be an object.")
        result["oauth"] = {key: oauth.get(key, "") for key in ("token_url", "client_id", "scope")}
    return result


def credential_binding(remote):
    """Destinations and identity fields to which the current secret is bound."""
    auth = public_auth(remote.get("auth", {}))
    kind = auth["kind"]
    return (remote.get("url"), kind,
            auth.get("header_name", "Authorization") if kind in ("bearer", "header") else None,
            auth.get("oauth") if kind == "oauth" else None)


def redact_values(value, sensitive):
    if isinstance(value, str):
        for secret in sorted(set(sensitive), key=len, reverse=True):
            if secret:
                value = value.replace(secret, "[redacted]")
        return value
    if type(value) in (int, float):
        # Match short legacy secrets exactly; substrings such as "8" must not
        # corrupt unrelated scores (0.8) or counts (18).
        text = str(value)
        return "[redacted]" if any(
            secret and (secret == text or (len(secret) >= 16 and secret in text))
            for secret in sensitive
        ) else value
    if isinstance(value, list):
        return [redact_values(item, sensitive) for item in value]
    if isinstance(value, dict):
        return {redact_values(key, sensitive): redact_values(item, sensitive) for key, item in value.items()}
    return value


def destination(url, budget=5):
    """Validate *all* DNS answers; return those exact addresses for connection."""
    try:
        if not isinstance(url, str) or any(ord(c) < 33 or ord(c) > 126 for c in url):
            raise ValueError
        parsed = urlsplit(url)
        host = (parsed.hostname or "").rstrip(".").lower()
        if (parsed.scheme != "https" or not host or parsed.username is not None
                or parsed.password is not None or parsed.fragment or "\\" in url
                or "%" in host or host in METADATA_HOSTNAMES
                or host == "localhost" or host.endswith((".localhost", ".local", ".internal"))):
            raise ValueError
        port = parsed.port or 443
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        addresses = [str(literal)] if literal else resolve_all(host, max(0, budget))
        if not addresses or any(classify_ip(ip) is not IPClass.PUBLIC for ip in addresses):
            raise ValueError
    except (ValueError, OSError, SsrfBlocked):
        raise ValidationError("Use a reachable HTTPS URL with only public addresses.") from None
    return parsed, host, port, addresses


def prepare_definition(raw, old=None, credentials=None, *, new=False):
    remote = copy.deepcopy(raw)
    destination(remote["url"])
    if len(remote.get("mapping", {})) > 50:
        raise ValidationError("At most 50 input mappings are allowed.")
    if remote.get("retries") and not remote.get("retry_on"):
        raise ValidationError("Choose which failures to retry.")
    auth = remote.setdefault("auth", {})
    kind = auth.setdefault("kind", "hmac")
    previous_auth = (old or {}).get("auth", {})
    public_auth(auth)  # Reject inactive credentials before extracting active ones.
    same_target = old and credential_binding(old) == credential_binding(remote)
    credentials = copy.deepcopy(credentials or {}) if same_target else {}
    signing_secret = None
    # Requiring re-entry when the endpoint changes prevents saved-secret exfiltration.
    if kind in ("bearer", "header"):
        token = auth.pop("token", None) or credentials.get("token")
        if not isinstance(token, str) or not token or any(ord(c) < 32 or ord(c) > 126 for c in token):
            raise ValidationError("Enter a valid authentication token for this URL.")
        header = auth.get("header_name", "Authorization")
        if header.lower() in {"host", "content-length", "content-type", "connection", "transfer-encoding", "cookie", "proxy-authorization"}:
            raise ValidationError("This authentication header name is reserved.")
        credentials = {"token": token}
        auth["token_last4"] = token[-4:]
    elif kind == "oauth":
        oauth = auth.get("oauth") or {}
        if not isinstance(oauth, dict):
            raise ValidationError("OAuth configuration must be an object.")
        token_url = oauth.get("token_url", "")
        destination(token_url)
        if not oauth.get("client_id") or not isinstance(oauth["client_id"], str):
            raise ValidationError("Enter the OAuth client ID.")
        prior_oauth = previous_auth.get("oauth", {})
        if any(oauth.get(key, "") != prior_oauth.get(key, "") for key in ("token_url", "client_id", "scope")):
            credentials = {}
        secret = oauth.get("client_secret") or credentials.get("client_secret")
        if not isinstance(secret, str) or not secret:
            raise ValidationError("Enter the OAuth client secret for this URL.")
        credentials = {"client_secret": secret}
        auth["oauth"] = {key: str(oauth.get(key, "")) for key in ("token_url", "client_id", "scope")}
        auth["client_secret_set"] = True
    elif kind == "hmac":
        if credentials.get("previous_valid_until", 0) > time.time() and previous_auth.get("previous_valid_until"):
            auth["previous_valid_until"] = previous_auth["previous_valid_until"]
        else:
            auth.pop("previous_valid_until", None)
            credentials.pop("previous_secret", None)
            credentials.pop("previous_valid_until", None)
        if not credentials.get("signing_secret"):
            if not new:
                raise ValidationError("Save the scorer before testing signed calls, or re-save the new URL.")
            signing_secret = secrets.token_urlsafe(32)
            credentials = {"signing_secret": signing_secret}
    else:
        credentials = {}
    # Check retained secrets too, so edits cannot silently preserve a legacy
    # short credential. Numeric expiry metadata is not a secret.
    if any(isinstance(value, str) and len(value) < 16 for value in credentials.values()):
        raise ValidationError("Authentication credentials must be at least 16 characters long.")
    remote["auth"] = public_auth(auth)
    output = remote.setdefault("output", {})
    output.setdefault("type", "score")
    if output["type"] == "choice":
        choices = output.get("choices", [])
        if len(choices) < 2 or any(
            not isinstance(c.get("label"), str) or not c["label"]
            or isinstance(c.get("score"), bool) or not isinstance(c.get("score"), (int, float))
            or not math.isfinite(c["score"]) or not 0 <= c["score"] <= 1 for c in choices
        ) or len({c["label"] for c in choices}) != len(choices):
            raise ValidationError("Choices need distinct labels and scores from 0 to 1.")
    return remote, credentials, signing_secret


def field_at(value, path):
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def verdict_of(answer, output, threshold):
    if not isinstance(answer, dict):
        raise ValueError("score error: the answer is not a JSON object")
    kind = output.get("type", "score")
    raw = field_at(answer, output.get("score_field", "score"))
    label = None
    if kind == "choice":
        label = answer.get("label")
        choices = {c["label"]: c["score"] for c in output.get("choices", [])}
        if not isinstance(label, str) or label not in choices:
            raise ValueError("score error: unknown choice label")
        value = choices[label]
    elif kind == "pass_fail":
        raw = answer.get("passed", raw)
        if type(raw) is bool:
            value = int(raw)
        elif type(raw) in (int, float) and raw in (0, 1):
            value = raw
        else:
            raise ValueError("score error: expected a boolean or 0/1")
    elif type(raw) not in (int, float) or not math.isfinite(raw) or not 0 <= raw <= 1:
        raise ValueError("score error: expected a number from 0 to 1")
    else:
        value = raw
    reason = field_at(answer, output.get("reason_field", "reason"))
    passed = value == 1 if kind == "pass_fail" else value >= threshold
    if kind == "score" and type(answer.get("passed")) is bool:
        passed = answer["passed"]
    return {"value": value, "passed": passed, "reason": reason if isinstance(reason, str) else "",
            **({"label": label} if label is not None else {})}


def mapped_inputs(remote, values):
    if not isinstance(values, dict):
        values = {"input": values}
    mapping = remote.get("mapping", {})
    if not mapping:
        return values
    mapped = {}
    for name, column in mapping.items():
        if column not in values:
            raise ValidationError(f"Missing mapped input: {name}.")
        mapped[name] = values[column]
    return mapped


def _post(url, body, headers, deadline):
    parsed, host, port, addresses = destination(url, min(5, deadline - time.monotonic()))
    return safe_post(
        scheme="https", host=host, port=port,
        target=(parsed.path or "/") + (f"?{parsed.query}" if parsed.query else ""),
        addresses=addresses, headers=headers, body=body, deadline=deadline,
        max_read=MAX_BODY + 1, watchdog=watchdog(),
    )


def call_scorer(remote, credentials, inputs, output, threshold=0.5, *, trace=None, retry=False):
    body = {"inputs": inputs, "output": output}
    if remote.get("send") == "trace":
        body["trace"] = trace or []
    encoded = json.dumps(body, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > MAX_BODY:
        raise ValidationError("The scorer request exceeds 1 MiB.")
    view = {"request": {"url": remote["url"], "body": body, "headers": {}}, "tries": []}
    attempts = 1 + (remote.get("retries", 0) if retry else 0)
    for attempt in range(attempts):
        started = time.monotonic()
        deadline = started + remote.get("timeout_ms", 15000) / 1000
        headers = [("Content-Type", "application/json")]
        auth = remote.get("auth", {})
        kind = auth.get("kind", "hmac")
        sensitive = [value for value in credentials.values() if isinstance(value, str)]
        try:
            if kind == "hmac":
                signature = hmac.new(credentials["signing_secret"].encode(), encoded, hashlib.sha256).hexdigest()
                signatures = [f"sha256={signature}"]
                if credentials.get("previous_secret") and credentials.get("previous_valid_until", 0) > time.time():
                    previous = hmac.new(credentials["previous_secret"].encode(), encoded, hashlib.sha256).hexdigest()
                    signatures.append(f"sha256={previous}")
                headers.append(("X-FutureAGI-Signature", ",".join(signatures)))
            elif kind in ("bearer", "header"):
                headers.append((auth.get("header_name", "Authorization"),
                                ("Bearer " if kind == "bearer" else "") + credentials["token"]))
            elif kind == "oauth":
                oauth = auth["oauth"]
                token_response = _post(oauth["token_url"], urlencode({
                    "grant_type": "client_credentials", "client_id": oauth["client_id"],
                    "client_secret": credentials["client_secret"], "scope": oauth.get("scope", ""),
                }).encode(), [("Content-Type", "application/x-www-form-urlencoded")], deadline)
                if token_response.error or token_response.status != 200 or len(token_response.body) > MAX_BODY:
                    raise ValueError("OAuth token request failed")
                token_answer = json.loads(token_response.body)
                token = token_answer.get("access_token") if isinstance(token_answer, dict) else None
                if not isinstance(token, str) or not token or any(ord(c) < 32 or ord(c) > 126 for c in token):
                    raise ValueError("OAuth returned no valid access token")
                sensitive.append(token)
                headers.append(("Authorization", f"Bearer {token}"))
            view["request"]["headers"] = {
                key: value if key == "Content-Type" or (kind == "hmac" and key == "X-FutureAGI-Signature") else "[redacted]"
                for key, value in headers
            }
            response = _post(remote["url"], encoded, headers, deadline)
            elapsed = round((time.monotonic() - started) * 1000)
            entry = {"n": attempt + 1, "status": response.status, "ms": elapsed}
            reason = response.error
            if reason:
                error = "Remote scorer timed out" if reason == "timeout" else "Remote scorer connection failed"
            elif not response.status or not 200 <= response.status < 300:
                error = f"Remote scorer returned HTTP {response.status}"
                reason = "429" if response.status == 429 else "5xx" if response.status and response.status >= 500 else None
            else:
                error = None
            if not error:
                try:
                    if len(response.body) > MAX_BODY:
                        raise ValueError("score error: answer exceeds 1 MiB")
                    # Decode and validate before redaction: JSON escaping must not
                    # hide reflected secrets, nor may redaction alter score parsing.
                    answer = json.loads(response.body.decode("utf-8"))
                    verdict = verdict_of(answer, remote.get("output", {}), threshold)
                    redacted_verdict = redact_values(verdict, sensitive)
                    if redacted_verdict["value"] != verdict["value"] or any(
                        secret and secret in verdict.get("label", "") for secret in sensitive
                    ):
                        error = "remote scorer echoed a credential in its score"
                    else:
                        view["verdict"] = redacted_verdict
                    view["response"] = {"status": response.status, "ms": elapsed,
                                        "body": redact_values(answer, sensitive)}
                except (ValueError, UnicodeError):
                    error = "score error: invalid JSON answer or output value"
            if error:
                entry["error"] = error
                view["verdict"] = {"error": error}
            view["tries"].append(entry)
            if not error or reason not in remote.get("retry_on", []) or attempt + 1 == attempts:
                break
            # Bound Retry-After so a receiver cannot indefinitely occupy a worker.
            retry_after = next((value for key, value in response.headers.items() if key.lower() == "retry-after"), None)
            wait = min(MAX_RETRY_WAIT, max(0, int(retry_after))) if str(retry_after).isdigit() else min(MAX_RETRY_WAIT, 2 ** attempt)
            time.sleep(wait)
        except (ValidationError, ValueError, KeyError, OSError):
            view["verdict"] = {"error": "Remote scorer URL, authentication or response is invalid"}
            view["tries"].append({"n": attempt + 1, "error": view["verdict"]["error"]})
            break
    return view
