"""Small synthetic witnesses executed only inside a disposable product fixture.

These cover service health, an actual account login and persisted store state.
They are not a cross-version database-migration or backup/restore qualification.
"""

PROGRAM = r"""
import base64, hashlib, json, os, sys, urllib.parse, urllib.request
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
django.setup()
from django.db import connection
import boto3
mode, token = sys.argv[1:3]
assert mode in ("seed", "check") and token.isalnum()

def request(url, data=None):
    req = urllib.request.Request(url, data=data,
        headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=30) as response:
        assert response.status == 200
        return response.read()

for url in ("http://futureagi-backend:8000/health/", "http://futureagi-frontend/",
            "http://futureagi-fi-collector:9464/healthz", "http://futureagi-agentcc-gateway:8080/healthz"):
    request(url)
login = json.loads(request("http://futureagi-backend:8000/accounts/token/", json.dumps({
    "email": "gitops-admin@example.com", "password": "GitOps-Smoke-2026!x"}).encode()))
assert isinstance(login.get("access"), str) and login["access"]
# Do not print the token or any environment credentials.
with connection.cursor() as cursor:
    if mode == "seed":
        cursor.execute("CREATE TABLE phase3_gitops_witness (id text PRIMARY KEY, payload text NOT NULL)")
        cursor.execute("INSERT INTO phase3_gitops_witness VALUES (%s, %s)", [token, "persistent-" + token])
    cursor.execute("SELECT id, payload FROM phase3_gitops_witness ORDER BY id")
    rows = cursor.fetchall()
    assert rows == [(token, "persistent-" + token)], rows
    cursor.execute("SELECT app, name FROM django_migrations ORDER BY app, name")
    migrations = cursor.fetchall()

def clickhouse(sql):
    url = "http://" + os.environ["CH_HOST"] + ":" + os.environ["CH_HTTP_PORT"] + "/"
    req = urllib.request.Request(url, data=sql.encode())
    credentials = os.environ.get("CH_USERNAME", "default") + ":" + os.environ["CH_PASSWORD"]
    req.add_header("Authorization", "Basic " + base64.b64encode(credentials.encode()).decode())
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read()

if mode == "seed":
    clickhouse("CREATE TABLE default.phase3_gitops_witness (id String, payload String) ENGINE=MergeTree ORDER BY id")
    clickhouse("INSERT INTO default.phase3_gitops_witness VALUES ('" + token + "', 'persistent-" + token + "')")
ch_rows = clickhouse("SELECT id, payload FROM default.phase3_gitops_witness ORDER BY id FORMAT JSONEachRow")
assert [json.loads(line) for line in ch_rows.splitlines()] == [{"id": token, "payload": "persistent-" + token}]

s3 = boto3.client("s3", endpoint_url=os.environ["S3_ENDPOINT_URL"],
    aws_access_key_id=os.environ["S3_ACCESS_KEY"], aws_secret_access_key=os.environ["S3_SECRET_KEY"],
    region_name=os.environ.get("S3_REGION", "us-east-1"))
bucket = "phase3-witness-" + token
key = "retained-object"
payload = ("gitops-object-" + token).encode()
if mode == "seed":
    s3.create_bucket(Bucket=bucket)
    s3.put_object(Bucket=bucket, Key=key, Body=payload)
response = s3.get_object(Bucket=bucket, Key=key)
with response["Body"] as stream:
    actual = stream.read()
assert actual == payload
print("GITOPS_WITNESS=" + json.dumps({
    "health_services": 4, "account_login": True, "postgres_rows": len(rows),
    "postgres_sha256": hashlib.sha256(json.dumps(rows).encode()).hexdigest(),
    "object_sha256": hashlib.sha256(actual).hexdigest(),
    "clickhouse_sha256": hashlib.sha256(ch_rows).hexdigest(),
    "migration_count": len(migrations),
    "migration_sha256": hashlib.sha256(json.dumps(migrations).encode()).hexdigest(),
}, sort_keys=True))
"""
