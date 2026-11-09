package eligibility

import (
	"strings"
	"testing"
)

func TestKeyModelDeniedNamesTheRestriction(t *testing.T) {
	code, reason := CheckKeyACL(&Input{Model: "o3-mini", Key: KeyACL{AllowedModels: "gpt-4o, gpt-4o-mini"}})
	want := "API key does not have access to model: o3-mini. This key is restricted to its allowed models (gpt-4o, gpt-4o-mini); call one of them, or use a key without this restriction."
	if code != CodeKeyModelDenied || reason != want {
		t.Fatalf("%s %q, want %s %q", code, reason, CodeKeyModelDenied, want)
	}

	var many []string
	for i := 0; i < maxNamedAllowedModels+3; i++ {
		many = append(many, "m"+string(rune('a'+i)))
	}
	_, reason = CheckKeyACL(&Input{Model: "other", Key: KeyACL{AllowedModels: strings.Join(many, ",")}})
	if !strings.Contains(reason, "(ma, mb, mc, md, me, mf, mg, mh, mi, mj and 3 more)") {
		t.Fatalf("reason %q does not cap the list", reason)
	}
}
