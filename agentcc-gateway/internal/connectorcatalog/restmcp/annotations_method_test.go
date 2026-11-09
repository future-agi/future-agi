package restmcp

import (
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

// TestCompileRefusesAWriteMarkedReadOnly (IR-CN4b-2, defence in depth behind
// CN4's generator): the runtime itself refuses a tool whose annotations
// contradict its HTTP method, so a hand-edited tools.generated.json that
// slips past generate.py --check is not served. readOnlyHint true on a PUT,
// PATCH or DELETE (each writes) and destructiveHint false on a DELETE (it
// always destroys) are refused, naming the tool; a POST may be read-only
// (a reviewer states it, as Calendar's find_free_busy), and a GET, a POST
// and a destructive DELETE still compile.
func TestCompileRefusesAWriteMarkedReadOnly(t *testing.T) {
	refused := []struct {
		name, method, want    string
		readOnly, destructive bool
	}{
		{"read-only PUT", "PUT", "readOnlyHint is true on a PUT", true, false},
		{"read-only PATCH", "PATCH", "readOnlyHint is true on a PATCH", true, false},
		{"read-only DELETE", "DELETE", "readOnlyHint is true on a DELETE", true, true},
		{"non-destructive DELETE", "DELETE", "destructiveHint is false on a DELETE", false, false},
	}
	for _, c := range refused {
		t.Run(c.name, func(t *testing.T) {
			app, p := gmailCopy(t)
			tool := toolNamed(t, p, "get_profile") // no body input, so any method may carry it
			tool.Operation.Method = c.method
			tool.Annotations = manifest.Annotations{ReadOnlyHint: c.readOnly, DestructiveHint: c.destructive}
			_, err := Compile(&manifest.Catalog{Apps: []*manifest.App{app}})
			if err == nil || !strings.Contains(err.Error(), c.want) {
				t.Fatalf("Compile error = %v, want one containing %q", err, c.want)
			}
			if !strings.Contains(err.Error(), "get_profile") {
				t.Errorf("the refusal does not name the tool: %v", err)
			}
		})
	}
	served := []struct {
		name, method          string
		readOnly, destructive bool
	}{
		{"read-only GET", "GET", true, false},
		{"read-only POST, as a reviewer may state", "POST", true, false},
		{"destructive DELETE", "DELETE", false, true},
		{"writing PATCH", "PATCH", false, false},
	}
	for _, c := range served {
		t.Run(c.name, func(t *testing.T) {
			app, p := gmailCopy(t)
			tool := toolNamed(t, p, "get_profile")
			tool.Operation.Method = c.method
			tool.Annotations = manifest.Annotations{ReadOnlyHint: c.readOnly, DestructiveHint: c.destructive}
			if _, err := Compile(&manifest.Catalog{Apps: []*manifest.App{app}}); err != nil {
				t.Fatalf("Compile refused a consistent %s: %v", c.method, err)
			}
		})
	}
}
