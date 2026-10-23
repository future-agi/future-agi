"""Generate a short-lived local-only E2E license using the development keyring.

Fails when official bundled roots exist. Never modifies production verification
or ships a signing key; the private key lives only in this process's memory.
"""

import argparse
import os
import time
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

parser = argparse.ArgumentParser()
parser.add_argument("--output", required=True)
options = parser.parse_args()
output = Path(options.output).resolve()
if not str(output).startswith(("/private/tmp/", "/tmp/")):
    raise SystemExit("Local test license output must be in the temporary directory")
source = Path(__file__).resolve().parents[2] / "futureagi/ee/licensing/keyring.py"
if "_BUNDLED_KEYS: tuple[PublicKeyEntry, ...] = ()" not in source.read_text():
    raise SystemExit("Official bundled roots present: use an issued E2E license")
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
public = (
    key.public_key()
    .public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    .decode()
    .strip()
    .replace("\n", "\\n")
)
now = int(time.time())
token = jwt.encode(
    {
        "typ": "futureagi-enterprise-license",
        "schema_version": 1,
        "license_id": "local-th8613-e2e",
        "customer_id": "local-e2e",
        "issued_to": "Local Error Feed E2E only",
        "iss": "https://licenses.futureagi.com",
        "aud": "futureagi-self-hosted",
        "iat": now,
        "nbf": now - 60,
        "exp": now + 86400,
        "license_type": "production",
        "band": "business",
        "features": ["error_feed"],
        "limits": {},
        "max_instances": 20,
        "grace_days": 0,
    },
    key,
    algorithm="RS256",
    headers={"kid": "default"},
)
fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as stream:
    stream.write(f"EE_LICENSE_KEY={token}\nEE_LICENSE_PUBLIC_KEY='{public}'\n")
print(f"Generated short-lived local test license: {output}")
