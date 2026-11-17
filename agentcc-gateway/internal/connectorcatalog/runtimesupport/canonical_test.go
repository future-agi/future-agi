package runtimesupport

import (
	"strings"
	"testing"
)

func TestDeclarationRevisionPythonVector(t *testing.T) {
	// Expected digest generated independently with Python json.dumps(record,
	// sort_keys=True, separators=(',', ':'), ensure_ascii=False), not Go/JCS.
	const raw = `{"app":"sample-app","id":"native-rest","version":"1.2.3","kind":"rest","note":"café <>& /  \n\t\\\"","nested":{"𐀀":1,"":2},"array":[null,true,false,0,-1,9007199254740993]}`
	const want = "1.2.3+sha256:4f8e03a5c16a84a86df27a2c3eb972a4a13e4322dd6c1230d1ffb1819c849cb4"
	got, err := DeclarationRevision([]byte(raw))
	if err != nil || got != want {
		t.Fatalf("%s, %v; want %s", got, err, want)
	}
	again, err := DeclarationRevision([]byte(" \n" + raw + " \n"))
	if err != nil || again != got {
		t.Fatal("whitespace changed identity")
	}
	changed, err := DeclarationRevision([]byte(strings.Replace(raw, "native-rest", "vendor-mcp", 1)))
	if err != nil || changed == got {
		t.Fatal("metadata change did not change revision")
	}
}

func TestDeclarationRevisionRefusesAmbiguousInput(t *testing.T) {
	for _, raw := range []string{
		`{"version":"1.2.3","version":"1.2.3"}`, `{"version":"1.2.3","x":{"a":1,"a":2}}`,
		`{"version":"1.2.3","n":1.0}`, `{"version":"1.2.3","n":1e3}`, `{"version":"1.2.3","n":NaN}`,
		`{"version":"1.2.3","bad":"\ud800"}`, `{"version":"1.2.3","bad":"\udc00"}`,
		`{"version":null}`, `{"version":"01.2.3"}`, `{"version":"1.2.3"} {}`, string([]byte{0xff}),
	} {
		if _, err := DeclarationRevision([]byte(raw)); err == nil {
			t.Errorf("accepted %q", raw)
		}
	}
}
