package contracttest

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// AssertGolden compares got, normalized, with the golden file at path. With
// -update it writes the file instead.
//
// Normalization marshals got (honouring its MarshalJSON, so the golden is the
// wire form a client sees), then re-encodes it with sorted keys, two-space
// indentation and no HTML escaping, after scrubbing values that differ run to
// run:
//
//   - a non-zero number under a "created" key becomes "<nonzero>" (a zero
//     stays 0, so a missing timestamp still shows);
//   - an "id" string of the form <prefix>-<16+ digits>, the gateway's
//     time-based IDs (gen-…, bedrock-…, cohere-…), becomes "<prefix>-<generated>";
//   - the value under any key named in scrubKeys becomes "<scrubbed>".
//
// In live mode a missing golden is skipped rather than failed: a live
// response's text differs every run, so only structural checks apply.
func AssertGolden(t testing.TB, path string, got any, scrubKeys ...string) {
	t.Helper()
	norm, err := Normalize(got, scrubKeys...)
	if err != nil {
		t.Fatalf("contracttest: normalizing golden %s: %v", path, err)
	}

	if *update {
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatalf("contracttest: %v", err)
		}
		if err := os.WriteFile(path, norm, 0o644); err != nil {
			t.Fatalf("contracttest: writing golden: %v", err)
		}
		return
	}

	want, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		if isLive() {
			t.Logf("contracttest: live mode, no golden at %s; skipping comparison", path)
			return
		}
		t.Errorf("contracttest: golden %s does not exist; create it with -update", path)
		return
	}
	if err != nil {
		t.Fatalf("contracttest: reading golden: %v", err)
	}
	if !bytes.Equal(want, norm) {
		t.Errorf("contracttest: output differs from golden %s (rerun with -update if the change is intended):\n%s",
			path, firstDiff(string(want), string(norm)))
	}
}

func isLive() bool { return os.Getenv(LiveEnv) != "" }

// Normalize returns the deterministic encoding AssertGolden compares. got may
// be a value to marshal, or JSON already encoded as []byte or
// json.RawMessage.
func Normalize(got any, scrubKeys ...string) ([]byte, error) {
	var raw []byte
	switch g := got.(type) {
	case []byte:
		raw = g
	case json.RawMessage:
		raw = g
	default:
		b, err := json.Marshal(got)
		if err != nil {
			return nil, err
		}
		raw = b
	}

	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		return nil, err
	}

	extra := make(map[string]bool, len(scrubKeys))
	for _, k := range scrubKeys {
		extra[k] = true
	}
	v = scrub(v, "", extra)

	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", "  ")
	if err := enc.Encode(v); err != nil { // maps encode with sorted keys
		return nil, err
	}
	return buf.Bytes(), nil
}

var generatedIDRe = regexp.MustCompile(`^([A-Za-z][A-Za-z0-9_]*)-\d{16,}$`)

func scrub(v any, key string, extra map[string]bool) any {
	if extra[key] {
		return "<scrubbed>"
	}
	switch val := v.(type) {
	case map[string]any:
		for k, child := range val {
			val[k] = scrub(child, k, extra)
		}
		return val
	case []any:
		for i, child := range val {
			val[i] = scrub(child, key, extra)
		}
		return val
	case json.Number:
		if key == "created" && val.String() != "0" {
			return "<nonzero>"
		}
		return val
	case string:
		if key == "id" {
			if m := generatedIDRe.FindStringSubmatch(val); m != nil {
				return m[1] + "-<generated>"
			}
		}
		return val
	default:
		return v
	}
}

// firstDiff renders the first differing line of two texts with context.
func firstDiff(want, got string) string {
	w := strings.Split(want, "\n")
	g := strings.Split(got, "\n")
	i := 0
	for i < len(w) && i < len(g) && w[i] == g[i] {
		i++
	}
	lo := max(i-3, 0)
	var b strings.Builder
	fmt.Fprintf(&b, "first difference at line %d\n--- golden\n", i+1)
	for j := lo; j < min(i+4, len(w)); j++ {
		fmt.Fprintf(&b, "%s %s\n", marker(j == i), w[j])
	}
	b.WriteString("+++ got\n")
	for j := lo; j < min(i+4, len(g)); j++ {
		fmt.Fprintf(&b, "%s %s\n", marker(j == i), g[j])
	}
	return b.String()
}

func marker(here bool) string {
	if here {
		return ">"
	}
	return " "
}
