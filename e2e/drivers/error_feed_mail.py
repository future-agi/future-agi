"""Private local mail sink: normal Django MIME messages, no outbound delivery."""

import os
import re
import sys
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
from pathlib import Path

from django.core.mail.backends.filebased import EmailBackend as FileEmailBackend


class EmailBackend(FileEmailBackend):
    def __init__(self, *args, **kwargs):
        if (
            os.getenv("E2E_ERROR_FEED_PROFILE") != "th8613"
            or os.getenv("ENV_TYPE") != "local"
        ):
            raise RuntimeError(
                "Local Error Feed mail sink requires its dedicated test profile"
            )
        kwargs["file_path"] = "/tmp/th8613-mail"
        super().__init__(*args, **kwargs)


def invitation(email):
    if not re.fullmatch(r"e2e-h1-[a-zA-Z0-9-]+-(member|viewer)@futureagi.com", email):
        raise ValueError("Refusing an invitation outside the test-owned identities")
    for path in sorted(Path("/tmp/th8613-mail").glob("*.log"), reverse=True)[:256]:
        message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
        if email not in [
            address for _name, address in getaddresses(message.get_all("To", []))
        ]:
            continue
        for part in message.walk():
            if part.get_content_type() not in {"text/plain", "text/html"}:
                continue
            link = re.search(
                r"https?://localhost:3113/auth/jwt/invitation/accept/[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+",
                part.get_content(),
            )
            if link:
                return link.group(0)
    raise LookupError("Invitation has not reached the local sink")


if __name__ == "__main__":
    if os.getenv("E2E_ERROR_FEED_PROFILE") != "th8613":
        raise SystemExit("Mail reads require the dedicated test profile")
    print(invitation(sys.argv[1]))
