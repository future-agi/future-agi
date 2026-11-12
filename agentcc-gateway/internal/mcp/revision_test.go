package mcp

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"regexp"
	"testing"
)

var sha256Hex = regexp.MustCompile(`^[0-9a-f]{64}$`)

// TestCanonicalRevisionStable: a tool's revision is the sha256 of its
// canonical definition, so key order, whitespace and fields outside the
// definition (icons, _meta, execution) never change it.
func TestCanonicalRevisionStable(t *testing.T) {
	a := json.RawMessage(`{"name":"search","title":"Search","description":"Finds issues.","inputSchema":{"type":"object","properties":{"q":{"type":"string","maxLength":1.50}}},"annotations":{"readOnlyHint":true}}`)
	b := json.RawMessage("{\n \"annotations\": {\"readOnlyHint\": true},\n \"inputSchema\": {\"properties\": {\"q\": {\"maxLength\": 1.50, \"type\": \"string\"}}, \"type\": \"object\"},\n" +
		" \"description\": \"Finds issues.\", \"title\": \"Search\", \"name\": \"search\", \"icons\": [{\"src\": \"x\"}], \"_meta\": {\"k\": 1}, \"execution\": {\"taskSupport\": \"optional\"}}")
	ra, err := ToolRevision(a)
	if err != nil {
		t.Fatal(err)
	}
	rb, err := ToolRevision(b)
	if err != nil {
		t.Fatal(err)
	}
	if ra != rb {
		t.Fatalf("the same definition has two revisions: %s and %s", ra, rb)
	}
	want := sha256.Sum256([]byte(`{"annotations":{"readOnlyHint":true},"description":"Finds issues.",` +
		`"inputSchema":{"properties":{"q":{"maxLength":1.50,"type":"string"}},"type":"object"},"name":"search","title":"Search"}`))
	if ra != hex.EncodeToString(want[:]) {
		t.Fatalf("revision %s is not the sha256 of the canonical definition", ra)
	}
	// One tool's revision and a one-tool listing's ToolsHash hash the same
	// canonical form.
	h, err := ToolsHash([]json.RawMessage{a})
	if err != nil {
		t.Fatal(err)
	}
	list := sha256.Sum256([]byte(`[{"annotations":{"readOnlyHint":true},"description":"Finds issues.",` +
		`"inputSchema":{"properties":{"q":{"maxLength":1.50,"type":"string"}},"type":"object"},"name":"search","title":"Search"}]`))
	if h != hex.EncodeToString(list[:]) {
		t.Fatalf("ToolsHash %s no longer hashes the canonical form", h)
	}
}

// TestRevisionChangesWithTheDefinition: a change to a tool's name,
// title, description, input or output schema or annotations is a new
// revision.
func TestRevisionChangesWithTheDefinition(t *testing.T) {
	base := `{"name":"search","description":"Finds issues.","inputSchema":{"type":"object"}}`
	rb, err := ToolRevision(json.RawMessage(base))
	if err != nil {
		t.Fatal(err)
	}
	for field, changed := range map[string]string{
		"name":         `{"name":"search2","description":"Finds issues.","inputSchema":{"type":"object"}}`,
		"title":        `{"name":"search","title":"S","description":"Finds issues.","inputSchema":{"type":"object"}}`,
		"description":  `{"name":"search","description":"Finds issues, second wording.","inputSchema":{"type":"object"}}`,
		"inputSchema":  `{"name":"search","description":"Finds issues.","inputSchema":{"type":"object","properties":{"extra":{"type":"string"}}}}`,
		"outputSchema": `{"name":"search","description":"Finds issues.","inputSchema":{"type":"object"},"outputSchema":{"type":"object"}}`,
		"annotations":  `{"name":"search","description":"Finds issues.","inputSchema":{"type":"object"},"annotations":{"destructiveHint":true}}`,
	} {
		rc, err := ToolRevision(json.RawMessage(changed))
		if err != nil {
			t.Fatalf("%s: %v", field, err)
		}
		if rc == rb {
			t.Errorf("a change to %s kept the revision", field)
		}
	}
	if _, err := ToolRevision(json.RawMessage(`[]`)); err == nil {
		t.Error("a definition that is not an object has a revision")
	}
}

// TestToolRevisionFromTheUpstreamBytes: the gateway takes a listed tool's
// revision from the bytes the upstream sent, and from its fields when it has
// none; both agree for the same definition.
func TestToolRevisionFromTheUpstreamBytes(t *testing.T) {
	raw := json.RawMessage(`{"name":"echo","description":"Echoes.","inputSchema":{"type":"object"},"_meta":{"x":1}}`)
	want, err := ToolRevision(raw)
	if err != nil {
		t.Fatal(err)
	}
	fromRaw, canon, ok := toolRevision(Tool{Name: "other", Raw: raw})
	if !ok || fromRaw != want {
		t.Fatalf("revision from Raw = %q (ok %v), want %q", fromRaw, ok, want)
	}
	if string(canon) != `{"description":"Echoes.","inputSchema":{"type":"object"},"name":"echo"}` {
		t.Fatalf("canonical definition %s", canon)
	}
	fromFields, _, ok := toolRevision(Tool{Name: "echo", Description: "Echoes.", InputSchema: json.RawMessage(`{"type":"object"}`)})
	if !ok || fromFields != want {
		t.Fatalf("revision from fields = %q, want %q", fromFields, want)
	}
	if got, ok := knownDefinition(want); !ok || string(got) != string(canon) {
		t.Fatalf("the definition of a revision just seen is not known: %s %v", got, ok)
	}
}

// TestProbeReportsEachToolsRevision : an ok probe reports each
// listed tool's revision, the one pinning compares, so an add can pin the
// tools exactly as it listed them.
func TestProbeReportsEachToolsRevision(t *testing.T) {
	srv, m := upstream(t, "dual", nil)
	s := probeServer(t, hostOf(t, srv.URL))
	code, raw, res := probe(t, s, probeBody(map[string]any{"org_id": "org-a", "url": srv.URL + "/mcp"}))
	if code != http.StatusOK || !res.OK {
		t.Fatalf("status %d, body %s; want ok", code, raw)
	}
	defs := map[string]json.RawMessage{}
	for _, tool := range m.State().Tools() {
		defs[tool.Name] = tool.Def
	}
	if len(res.Tools) != len(defs) || len(res.Tools) == 0 {
		t.Fatalf("probe listed %d tools, the mock has %d", len(res.Tools), len(defs))
	}
	for _, pt := range res.Tools {
		want, err := ToolRevision(defs[pt.Name])
		if err != nil {
			t.Fatal(err)
		}
		if !sha256Hex.MatchString(pt.Revision) || pt.Revision != want {
			t.Errorf("%s: revision %q, want %q", pt.Name, pt.Revision, want)
		}
	}
}
