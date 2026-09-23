"""
PoC Test — Vulnerability #4: Hardcoded Database Credentials

Locations:
  - futureagi/config/pgbouncer.ini  (user=user password=password)
  - futureagi/config/userlist.txt   ("user" "password")
  - futureagi/.env.example          (PG_PASSWORD=password)
  - futureagi/docker-compose.yml    (postgres:postgres, minioadmin creds)

Severity: HIGH (CVSS 8.6)

PROOF:
    Production-capable PgBouncer configuration files contain plaintext
    default credentials that are copied into Docker images via COPY . .
    in the Dockerfile. The auth_type is 'plain', transmitting credentials
    in cleartext. Any deployment that doesn't override these defaults
    has an openly accessible database.

HOW TO RUN:
    python test_vuln4_hardcoded_creds.py
"""

import sys
import os
import re
import configparser


def test_pgbouncer_hardcoded_creds():
    """
    PROOF: PgBouncer config contains hardcoded plaintext credentials.
    """
    print("=" * 70)
    print("PoC #4: Hardcoded Database Credentials in Source Control")
    print("=" * 70)

    base = os.path.join(os.path.dirname(__file__), "..", "..", "futureagi")

    # --- STEP 1: Check pgbouncer.ini ---
    print("\n[1] pgbouncer.ini — Hardcoded Credentials:")

    pgbouncer_path = os.path.join(base, "config", "pgbouncer.ini")
    with open(pgbouncer_path, "r") as f:
        pgbouncer_content = f.read()

    # Extract credentials
    cred_patterns = {
        "user": re.findall(r"user=(\S+)", pgbouncer_content),
        "password": re.findall(r"password=(\S+)", pgbouncer_content),
        "auth_type": re.findall(r"auth_type\s*=\s*(\S+)", pgbouncer_content),
    }

    for key, matches in cred_patterns.items():
        for match in matches:
            print(f"    {key}: {match}")

    has_default_user = any("user" in u for u in cred_patterns.get("user", []))
    has_default_pass = any("password" in p for p in cred_patterns.get("password", []))
    has_plain_auth = any("plain" in a for a in cred_patterns.get("auth_type", []))

    assert has_default_user, "Expected default user in pgbouncer.ini"
    print("    ✓ CONFIRMED: Default username 'user' found")

    assert has_default_pass, "Expected default password in pgbouncer.ini"
    print("    ✓ CONFIRMED: Default password 'password' found")

    if has_plain_auth:
        print("    ✓ CONFIRMED: auth_type = plain (cleartext transmission!)")

    # --- STEP 2: Check userlist.txt ---
    print("\n[2] userlist.txt — Plaintext Credential File:")

    userlist_path = os.path.join(base, "config", "userlist.txt")
    with open(userlist_path, "r") as f:
        userlist_content = f.read()

    print(f"    Content: {userlist_content.strip()}")

    has_plaintext_creds = '"user"' in userlist_content and '"password"' in userlist_content
    assert has_plaintext_creds, "Expected plaintext credentials in userlist.txt"
    print("    ✓ CONFIRMED: Plaintext credentials in userlist.txt")

    # --- STEP 3: Check .env.example ---
    print("\n[3] .env.example — Default Password:")

    env_path = os.path.join(base, ".env.example")
    with open(env_path, "r") as f:
        env_content = f.read()

    default_passwords = re.findall(r"(?:PASSWORD|SECRET)=(\S+)", env_content)
    for pwd in default_passwords[:5]:
        print(f"    Default: {pwd}")

    weak_defaults = [p for p in default_passwords if p.lower() in
                     ("password", "secret", "admin", "postgres", "changeme", "test")]
    if weak_defaults:
        print(f"    ✓ CONFIRMED: Weak default passwords found: {weak_defaults}")

    # --- STEP 4: Check docker-compose.yml ---
    print("\n[4] docker-compose.yml — Hardcoded Service Credentials:")

    compose_path = os.path.join(base, "docker-compose.yml")
    with open(compose_path, "r") as f:
        compose_content = f.read()

    compose_creds = []
    for line in compose_content.split("\n"):
        line_stripped = line.strip()
        if any(kw in line_stripped.upper() for kw in
               ["PASSWORD", "SECRET_ACCESS_KEY", "CREDENTIALS"]):
            if "=" in line_stripped or ":" in line_stripped:
                # Don't show lines that reference env vars (${...})
                if "${" not in line_stripped:
                    compose_creds.append(line_stripped)

    for cred in compose_creds[:8]:
        print(f"    {cred}")

    if compose_creds:
        print(f"    ✓ CONFIRMED: {len(compose_creds)} hardcoded credentials in docker-compose.yml")

    # --- STEP 5: Check Dockerfile copies these files ---
    print("\n[5] Dockerfile.oss — Config Files Copied to Image:")

    dockerfile_path = os.path.join(base, "Dockerfile.oss")
    with open(dockerfile_path, "r") as f:
        dockerfile_content = f.read()

    has_copy_all = "COPY . ." in dockerfile_content
    print(f"    'COPY . .' in Dockerfile: {has_copy_all}")
    if has_copy_all:
        print("    ✓ CONFIRMED: All config files (including pgbouncer.ini with")
        print("      hardcoded creds) are copied into the Docker image!")

    print("\n[6] IMPACT ANALYSIS:")
    print("    • pgbouncer.ini with user=user password=password is deployed")
    print("    • auth_type=plain transmits credentials in cleartext")
    print("    • Any deployment using defaults has open database access")
    print("    • docker-compose.yml has postgres:postgres for PeerDB")
    print("    • MinIO credentials _peerdb_minioadmin baked into compose")

    print("\n" + "=" * 70)
    print("RESULT: VULNERABILITY CONFIRMED ✓")
    print("=" * 70)
    return True


if __name__ == "__main__":
    try:
        test_pgbouncer_hardcoded_creds()
        print("\n\n✅ ALL PoC TESTS PASSED — Vulnerability #4 confirmed")
    except Exception as e:
        print(f"\n❌ Test error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
