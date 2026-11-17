package bundle

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// digestJS is how a JavaScript consumer, such as Omega pinning the bundle,
// recomputes its sha256 from the file alone (the package doc).
const digestJS = `
const fs = require("fs"), crypto = require("crypto");
const b = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const sort = (v) => Array.isArray(v) ? v.map(sort)
  : v && typeof v === "object" ? Object.fromEntries(Object.keys(v).sort().map((k) => [k, sort(v[k])])) : v;
const text = JSON.stringify(sort({ apps: b.apps, providers: b.providers, platforms: b.platforms }));
process.stdout.write(crypto.createHash("sha256").update(text, "utf8").digest("hex") + " " + b.sha256);
`

// TestDigestMatchesJavaScript runs only when BUNDLE_NODE names a Node.js
// binary: BUNDLE_NODE=$(command -v node) go test ./internal/connectorcatalog/bundle/ -run TestDigestMatchesJavaScript
func TestDigestMatchesJavaScript(t *testing.T) {
	node := os.Getenv("BUNDLE_NODE")
	if node == "" {
		t.Skip("BUNDLE_NODE is not set")
	}
	fsys := fixture(t)
	// Text JSON.stringify must agree on: escapes, non-ASCII, a key order
	// that differs between UTF-8 and UTF-16, and numbers.
	edit(t, fsys, "apps/close/app.yaml", "name: Close\n", "name: \"Clos\\u00e9 \\\"CRM\\\" \\\\ \\u2028 \\U0001F600\"\n")
	b := build(t, fsys, nil)
	data, err := b.Encode()
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	path := filepath.Join(dir, "catalog.json")
	if err := os.WriteFile(path, data, 0o644); err != nil {
		t.Fatal(err)
	}
	script := filepath.Join(dir, "digest.js")
	if err := os.WriteFile(script, []byte(digestJS), 0o644); err != nil {
		t.Fatal(err)
	}
	out, err := exec.Command(node, script, path).Output()
	if err != nil {
		t.Fatal(err)
	}
	got, stated, _ := strings.Cut(string(out), " ")
	if got != b.SHA256 || stated != b.SHA256 {
		t.Fatalf("JavaScript computes %s; the bundle states %s; Go computed %s", got, stated, b.SHA256)
	}
}
