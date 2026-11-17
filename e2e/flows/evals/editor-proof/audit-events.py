"""Verify persisted records without trusting a test's success exit."""
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
mode = sys.argv[2]
rows = []
files = sorted(root.rglob("*.jsonl"))
assert files, "no persisted guard records"
for path in files:
    for line in path.read_text().splitlines():
        row = json.loads(line)
        assert isinstance(row["hostname"], str)
        assert isinstance(row["port"], (str, int)) or row["port"] is None
        assert set(row) <= {"kind", "layer", "hostname", "port", "service", "pid", "time"}
        rows.append(row)
refused = [row for row in rows if row["kind"] == "refused"]
controls = [row for row in rows if row["kind"] == "refusal-control"]
# Old frontend bundles request these assets; record them without allowing traffic.
asset_hosts = {"fonts.googleapis.com", "api.iconify.design", "api.simplesvg.com", "api.unisvg.com"}
attributed = [row for row in refused if mode == "browser-pre-assets"
              and row["layer"] == "browser.route" and row["hostname"] in asset_hosts]
unexpected = [row for row in refused if row not in attributed]
report = {"files": len(files), "events": len(rows), "nonControlRefusals": len(refused),
          "attributedAssetRefusals": len(attributed), "unexpectedRefusals": len(unexpected),
          "zeroAttemptCertified": not refused,
          "controlRefusals": len(controls), "refusedDestinations": [
              {"hostname": row["hostname"], "port": row["port"]} for row in refused]}
print(json.dumps(report))
assert not unexpected, "unattributed non-control refusal: stop the lane"
if mode == "controls":
    assert len(controls) == 35, "refusal-control count mismatch"
    assert {row["layer"] for row in controls} == {
        "socket.connect", "socket.create_connection", "socket.getaddrinfo",
        "socket.sendto", "socket.sendmsg", "socket.gethostbyname", "socket.gethostbyname_ex",
        "socket.gethostbyaddr", "socket.getnameinfo",
        "net.Socket.connect", "dns.lookup", "dns.resolve4", "dns.resolve6", "undici.connector"}
else:
    assert not controls, "controls must run in a separate invocation"
    assert any(row["kind"] == "installed" for row in rows), "no guard installation witness"
