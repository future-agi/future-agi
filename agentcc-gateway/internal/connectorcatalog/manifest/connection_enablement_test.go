package manifest

import "testing"

// schemaVersion is pinned to 1 by the Go shape check as well as by the JSON
// Schema (TestSchemaParity holds the two equal), so an unknown version never
// loads as a declaration the gateway would then have to reinterpret.
func TestConnectionEnablementSchemaVersionIsPinned(t *testing.T) {
	declaration := func(version float64, operations ...string) map[string]any {
		ops := make([]any, len(operations))
		for i, op := range operations {
			ops[i] = op
		}
		return map[string]any{"schemaVersion": version, "enabled": true, "operations": ops}
	}
	got, errs := decodeShaped[ConnectionEnablementDeclaration](declaration(1, "connect", "list_tools", "invoke"))
	if len(errs) != 0 || got == nil || got.SchemaVersion != 1 || !got.Enabled || len(got.Operations) != 3 {
		t.Fatalf("schemaVersion 1 was refused or misread: %+v %v", got, errs)
	}
	for _, version := range []float64{0, 2, 1.5} {
		if _, errs := decodeShaped[ConnectionEnablementDeclaration](declaration(version, "connect")); len(errs) == 0 {
			t.Errorf("schemaVersion %v was accepted", version)
		}
	}
	if _, errs := decodeShaped[ConnectionEnablementDeclaration](declaration(1, "connect", "list_tools", "invoke", "extra")); len(errs) == 0 {
		t.Error("four operations were accepted")
	}
}
