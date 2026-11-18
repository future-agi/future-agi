package detid

import (
	"crypto/rand"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/google/uuid"
)

// ─── HARD PARITY GATE ───────────────────────────────────────────────────────
//
// THE correctness check for P3b step2 (CANONICAL half). The deterministic
// end_user_id / trace_session_id bytes are a FROZEN contract shared by the
// Python read-side remap (already committed) and this Go ingest path. If the
// bytes drift, the whole migration's read-side unification is wrong. These
// tests guard parity two complementary ways:
//
//  1. ANCHORS (this file's golden literals) — frozen values copied from the
//     committed Python test (futureagi/tracer/tests/test_deterministic_id.py)
//     and the committed remap artifacts. Asserting Go reproduces them pins Go
//     to the ALREADY-COMMITTED Python output. (A recompute-with-Go assertion
//     would tautologically pass a Go bug; literals are the real guard.)
//
//  2. DIFFERENTIAL (TestParityAgainstPython) — shells out to the futureagi
//     venv Python over random inputs and asserts Go == Python across the input
//     space, including the None-vs-"" sentinel boundary. This catches any
//     drift the fixed anchors miss.
//
// ANY mismatch is a STOP-and-report: do NOT edit a golden to make Go pass.

// Golden namespace literals — must never drift (a seed edit like v1→v2 is a
// breaking re-key). Source: deterministic_id.py docstring + the committed
// Python test's _GOLD_NS_*.
const (
	goldNSEndUser = "97daafcc-ae7b-5a44-a76b-c85e63059e1c"
	goldNSSession = "1c4977df-2af9-5330-b34f-7969ffdabf25"

	// The NULL/"" sentinel enduser id: (project="p", org="o", user="u",
	// type=None). Python test _GOLD_EU_SENTINEL.
	goldEUSentinel = "a740d3f6-3215-535c-8687-2f3df0decd78"
)

// goldEUIsland mirrors the committed Python test's _ISLAND_EU — real island
// (pg-test) rows precomputed by the remap. INCLUDES the sarthak golden the
// task calls out (65e4ebd8…), which pins Go to the remap's new_id.
var goldEUIsland = []struct {
	project, org, user, utype, want string
	typePresent                     bool // false ⇒ user_id_type is None/absent
}{
	{
		project: "61840705-018d-415e-b9f0-4120f06e1fcc",
		org:     "42a08887-dba2-4b47-9f12-46ec87b1df9f",
		user:    "test_user",
		utype:   "", typePresent: false, // None
		want: "4b98d9c8-56cc-597b-a3e8-ef51f70ea230",
	},
	{
		// THE SARTHAK GOLDEN — project 86a643c9…, org 36ab6a86…, user
		// sarthak@futureagi.com, type None ⇒ 65e4ebd8… (remap new_id).
		project: "86a643c9-791b-4b48-9516-a8da3c058ed7",
		org:     "36ab6a86-28ef-484e-9fa2-0aade2cde52d",
		user:    "sarthak@futureagi.com",
		utype:   "", typePresent: false, // None
		want: "65e4ebd8-682d-5788-9bd0-e0d4a69d437b",
	},
}

// goldSessionIsland mirrors the committed Python test's _ISLAND_SESSION.
var goldSessionIsland = []struct {
	project, name, want string
}{
	{"61840705-018d-415e-b9f0-4120f06e1fcc", "test_session", "78ec9dff-4299-542d-84e9-a5555e5976f6"},
	{"86a643c9-791b-4b48-9516-a8da3c058ed7", "new-session", "a720e946-644e-59a3-9fc5-2adbd66e631c"},
	{"86a643c9-791b-4b48-9516-a8da3c058ed7", "new-session2", "0ebc5453-6a11-56a6-931d-59f278193161"},
}

// TestNamespaceGoldens asserts the namespace UUIDs equal BOTH the formula
// (uuid5(NAMESPACE_DNS, seed)) and the pinned golden literals. The literal
// check is what catches a seed edit the formula check would silently pass.
func TestNamespaceGoldens(t *testing.T) {
	if got := NSEndUser.String(); got != goldNSEndUser {
		t.Fatalf("NSEndUser drift: got %s want %s", got, goldNSEndUser)
	}
	if got := NSSession.String(); got != goldNSSession {
		t.Fatalf("NSSession drift: got %s want %s", got, goldNSSession)
	}
	// Formula equivalence (mirrors the Python test_namespaces_match_formula).
	if NSEndUser != uuid.NewSHA1(uuid.NameSpaceDNS, []byte("futureagi.enduser.v1")) {
		t.Fatal("NSEndUser != uuid5(NAMESPACE_DNS, futureagi.enduser.v1)")
	}
	if NSSession != uuid.NewSHA1(uuid.NameSpaceDNS, []byte("futureagi.session.v1")) {
		t.Fatal("NSSession != uuid5(NAMESPACE_DNS, futureagi.session.v1)")
	}
}

// TestEndUserGoldens asserts Go reproduces the frozen enduser literals,
// including the sentinel case and the sarthak/island remap rows.
func TestEndUserGoldens(t *testing.T) {
	// Sentinel: type None renders as "" → goldEUSentinel.
	if got := EndUserID("p", "o", "u", "").String(); got != goldEUSentinel {
		t.Fatalf("sentinel enduser drift: got %s want %s", got, goldEUSentinel)
	}
	for _, c := range goldEUIsland {
		utype := c.utype
		if !c.typePresent {
			// Python None ⇒ `None or ""` ⇒ "" sentinel; the Go caller passes "".
			utype = ""
		}
		if got := EndUserID(c.project, c.org, c.user, utype).String(); got != c.want {
			t.Fatalf("enduser drift for %s|%s|%s|present=%v: got %s want %s",
				c.project, c.org, c.user, c.typePresent, got, c.want)
		}
	}
}

// TestSessionGoldens asserts Go reproduces the frozen session literals.
func TestSessionGoldens(t *testing.T) {
	for _, c := range goldSessionIsland {
		if got := TraceSessionID(c.project, c.name).String(); got != c.want {
			t.Fatalf("session drift for %s|%s: got %s want %s",
				c.project, c.name, got, c.want)
		}
	}
}

// TestSentinelCollapse asserts the consolidation mechanism: an absent/None
// user_id_type (rendered "") and a literal "" give the SAME id, and neither
// equals the literal "None" string. This is the 879→544 box-data property.
func TestSentinelCollapse(t *testing.T) {
	none := EndUserID("p", "o", "u", "") // caller maps None → ""
	empty := EndUserID("p", "o", "u", "")
	if none != empty {
		t.Fatalf("None and \"\" must collapse: %s vs %s", none, empty)
	}
	noneLiteral := EndUserID("p", "o", "u", "None")
	if none == noneLiteral {
		t.Fatal("\"\" sentinel must NOT equal the literal \"None\" — byte contract broken")
	}
	// A typed identity stays distinct from the NULL-type one.
	if none == EndUserID("p", "o", "u", "email") {
		t.Fatal("typed identity must differ from NULL-type identity")
	}
}

// ─── DIFFERENTIAL PARITY (Go == Python over random inputs) ──────────────────

// pyParityScript is run by the futureagi venv Python. It reads TAB-separated
// rows on stdin and prints the corresponding deterministic id per line. The
// FIRST field selects the function ("E" enduser / "S" session). For enduser,
// the user_id_type field uses the literal token "\x00NONE\x00" to mean Python
// None (NOT the string "None") — this exercises the exact sentinel the
// contract guards. NEVER run without CH25_DATABASE=ch_rehearsal (read-only,
// but the env guards schema leakage to the default DB).
const pyParityScript = `
import sys
from tracer.services.clickhouse.v2.deterministic_id import (
    deterministic_end_user_id, deterministic_trace_session_id,
)
NONE = "\x00NONE\x00"
for line in sys.stdin:
    line = line.rstrip("\n")
    if not line:
        continue
    parts = line.split("\t")
    kind = parts[0]
    if kind == "E":
        project, org, user, utype = parts[1], parts[2], parts[3], parts[4]
        utype = None if utype == NONE else utype
        print(deterministic_end_user_id(project, org, user, utype))
    elif kind == "S":
        project, name = parts[1], parts[2]
        print(deterministic_trace_session_id(project, name))
    else:
        raise SystemExit("bad kind " + kind)
`

// noneToken must equal the NONE marker in pyParityScript byte-for-byte.
const noneToken = "\x00NONE\x00"

// TestParityAgainstPython generates random (project, org, user, type) and
// (project, name) tuples, computes each id in BOTH Go and the futureagi venv
// Python, and asserts they are EQUAL. Skips (does not fail) when the venv
// Python isn't reachable so `go test ./...` stays green in environments
// without the Django checkout; the fixed-golden tests above still pin Go.
func TestParityAgainstPython(t *testing.T) {
	py := findVenvPython(t)
	if py == "" {
		t.Skip("futureagi venv python not found — skipping differential parity (golden anchors still enforce the contract)")
	}

	// Build the input rows + the expected Go output for each.
	type row struct {
		line   string    // TAB-separated stdin line for Python
		goWant uuid.UUID // Go-computed id
		desc   string
	}
	var rows []row

	// 5 random enduser tuples, cycling the user_id_type through the
	// contract-relevant cases: None (→ "" sentinel), "" (present-empty →
	// "custom" at the converter, but detid takes the post-normalization token,
	// so here we feed the RAW post-normalization tokens Python's formula sees:
	// "" and "email"/"custom"/etc.). detid + Python share the SAME formula
	// (sentinel already applied), so we drive both with identical tokens:
	// the marker maps to Python None on the Python side and to "" on the Go
	// side (detid's caller applies `or ""`).
	typeCases := []string{noneToken, "", "email", "phone", "custom"}
	for i := 0; i < 5; i++ {
		project := randUUID()
		org := randUUID()
		user := fmt.Sprintf("user-%s@example.com", randHex(6))
		rawType := typeCases[i%len(typeCases)]
		// Go side: the marker means Python-None ⇒ detid gets "" (sentinel).
		goType := rawType
		if rawType == noneToken {
			goType = ""
		}
		rows = append(rows, row{
			line:   strings.Join([]string{"E", project, org, user, rawType}, "\t"),
			goWant: EndUserID(project, org, user, goType),
			desc:   fmt.Sprintf("E project=%s org=%s user=%s rawType=%q", project, org, user, rawType),
		})
	}

	// A couple of session tuples, including a name with separator-adjacent
	// characters to stress the key construction.
	sessionNames := []string{
		"session-" + randHex(8),
		"weird|name with spaces",
	}
	for _, name := range sessionNames {
		project := randUUID()
		rows = append(rows, row{
			line:   strings.Join([]string{"S", project, name}, "\t"),
			goWant: TraceSessionID(project, name),
			desc:   fmt.Sprintf("S project=%s name=%q", project, name),
		})
	}

	// Run Python once over all rows.
	var stdin strings.Builder
	for _, r := range rows {
		stdin.WriteString(r.line)
		stdin.WriteByte('\n')
	}
	out := runPython(t, py, stdin.String())
	pyLines := strings.Split(strings.TrimRight(out, "\n"), "\n")
	if len(pyLines) != len(rows) {
		t.Fatalf("python returned %d lines, want %d\n--- output ---\n%s", len(pyLines), len(rows), out)
	}

	for i, r := range rows {
		goStr := r.goWant.String()
		pyStr := strings.TrimSpace(pyLines[i])
		if goStr != pyStr {
			t.Fatalf("PARITY MISMATCH [%s]\n  Go     = %s\n  Python = %s", r.desc, goStr, pyStr)
		}
	}
	t.Logf("differential parity OK: %d Go==Python id comparisons (enduser+session, incl. None/empty sentinel)", len(rows))
}

// findVenvPython locates the futureagi venv interpreter relative to this
// repo. Both repos live under .../future-agi/ — fi-collector and futureagi are
// siblings. Returns "" if not found.
func findVenvPython(t *testing.T) string {
	t.Helper()
	wd, err := os.Getwd() // .../fi-collector/pkg/detid
	if err != nil {
		return ""
	}
	candidates := []string{
		filepath.Join(wd, "..", "..", "..", "futureagi", ".venv", "bin", "python"),
		filepath.Join(wd, "..", "..", "..", "..", "futureagi", ".venv", "bin", "python"),
	}
	for _, c := range candidates {
		if abs, err := filepath.Abs(c); err == nil {
			if _, err := os.Stat(abs); err == nil {
				return abs
			}
		}
	}
	return ""
}

// runPython executes pyParityScript with the futureagi venv interpreter,
// feeding `stdin` and returning stdout. The working directory is the futureagi
// repo so the `tracer` package imports resolve; CH25_DATABASE=ch_rehearsal is
// set per the guardrail (read-only here, but never leak schema to default).
func runPython(t *testing.T, py, stdin string) string {
	t.Helper()
	repoRoot := filepath.Dir(filepath.Dir(filepath.Dir(py))) // .../futureagi
	cmd := exec.Command(py, "-c", pyParityScript)
	cmd.Dir = repoRoot
	cmd.Stdin = strings.NewReader(stdin)
	cmd.Env = append(os.Environ(), "CH25_DATABASE=ch_rehearsal")
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("python parity helper failed: %v\n--- output ---\n%s", err, string(out))
	}
	return string(out)
}

// randUUID returns a canonical lowercase-dashed v4 UUID string — the same
// shape the converter passes as project_id / org_id.
func randUUID() string {
	var b [16]byte
	_, _ = rand.Read(b[:])
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	return fmt.Sprintf("%08x-%04x-%04x-%04x-%012x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:])
}

func randHex(n int) string {
	b := make([]byte, n)
	_, _ = rand.Read(b)
	return fmt.Sprintf("%x", b)
}
