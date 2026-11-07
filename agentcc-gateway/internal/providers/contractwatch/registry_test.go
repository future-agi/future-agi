package contractwatch

import (
	"bytes"
	"os"
	"strconv"
	"strings"
	"testing"
)

func loadCommitted(t *testing.T) (*Registry, []byte) {
	t.Helper()
	raw, err := os.ReadFile(RegistryFile)
	if err != nil {
		t.Fatal(err)
	}
	reg, err := ParseRegistry(raw)
	if err != nil {
		t.Fatalf("%s: %v", RegistryFile, err)
	}
	return reg, raw
}

// TestCommittedRegistry: the committed registry is valid, in canonical form
// (so -update-registry and -fmt only ever produce the diff they mean to),
// and has no untriaged placeholder reasons.
func TestCommittedRegistry(t *testing.T) {
	reg, raw := loadCommitted(t)
	if got := reg.Encode(); !bytes.Equal(got, raw) {
		t.Errorf("%s is not in canonical form; run `go run ./cmd/model-watch -fmt` from agentcc-gateway/\n%s",
			RegistryFile, firstDiffLine(string(raw), string(got)))
	}
	if ph := reg.Placeholders(); len(ph) > 0 {
		t.Errorf("entries still carry -update-registry's placeholder reason; give each a real reason or record it:\n  %s",
			strings.Join(ph, "\n  "))
	}
	for _, name := range SpecNames() {
		if reg.Providers[name] == nil {
			t.Errorf("model-watch knows provider %q, but the registry has no section for it", name)
		}
	}
}

func firstDiffLine(a, b string) string {
	al, bl := strings.Split(a, "\n"), strings.Split(b, "\n")
	for i := 0; i < len(al) && i < len(bl); i++ {
		if al[i] != bl[i] {
			return "line " + strconv.Itoa(i+1) + ":\n  file:      " + al[i] + "\n  canonical: " + bl[i]
		}
	}
	return "lengths differ"
}

const validRegistry = `{
  "schema_version": 1,
  "about": "test",
  "providers": {
    "openai": {
      "list": "api",
      "ignore_rules": [{"match": "ft:*", "reason": "fine-tunes"}],
      "models": [
        {"id": "gpt-6-sol", "status": "covered"},
        {"id": "gpt-6", "status": "alias", "alias_of": "gpt-6-sol"},
        {"id": "o1-pro", "status": "ignored", "reason": "responses only"}
      ]
    }
  }
}`

func TestParseRegistryValid(t *testing.T) {
	reg, err := ParseRegistry([]byte(validRegistry))
	if err != nil {
		t.Fatal(err)
	}
	s := reg.Providers["openai"]
	if got := s.Covered(); len(got) != 1 || got[0] != "gpt-6-sol" {
		t.Errorf("Covered() = %v", got)
	}
	if r, ok := s.MatchRule("ft:gpt-6-sol:acme::abc"); !ok || r.Reason != "fine-tunes" {
		t.Errorf("rule did not match a fine-tune")
	}
	// Encode then parse gives the same registry back.
	again, err := ParseRegistry(reg.Encode())
	if err != nil {
		t.Fatalf("re-parsing the encoded registry: %v\n%s", err, reg.Encode())
	}
	if !bytes.Equal(again.Encode(), reg.Encode()) {
		t.Error("Encode is not stable across a round trip")
	}
}

func TestValidateRejects(t *testing.T) {
	tests := []struct {
		name, edit, want string
	}{
		{"duplicate ID", `{"id": "gpt-6-sol", "status": "ignored", "reason": "x"}`, "listed twice"},
		{"ignored without reason", `{"id": "gpt-5", "status": "ignored"}`, "needs a reason"},
		{"alias of a non-covered model", `{"id": "o1-pro-2025", "status": "alias", "alias_of": "o1-pro"}`, "must point at a covered model"},
		{"alias of nothing", `{"id": "gpt-7", "status": "alias", "alias_of": "gpt-7-sol"}`, "has no entry"},
		{"alias without target", `{"id": "gpt-7", "status": "alias"}`, "needs alias_of"},
		{"entry that also matches a rule", `{"id": "ft:gpt-6-sol:acme::1", "status": "covered"}`, "also matches ignore rule"},
		{"unknown status", `{"id": "gpt-7", "status": "maybe"}`, `status "maybe"`},
		{"invalid ID", `{"id": "gpt 7; rm -rf /", "status": "covered"}`, "invalid model ID"},
		{"covered with alias_of", `{"id": "gpt-7", "status": "covered", "alias_of": "gpt-6-sol"}`, "alias_of is only for"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			raw := strings.Replace(validRegistry, `{"id": "o1-pro"`, tc.edit+",\n        "+`{"id": "o1-pro"`, 1)
			_, err := ParseRegistry([]byte(raw))
			if err == nil || !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("got %v, want an error containing %q", err, tc.want)
			}
		})
	}

	sections := []struct {
		name, section, want string
	}{
		{"unknown provider", `"deepinfra": {"list": "api", "models": []}`, "unknown provider"},
		{"api without a lister", `"azure": {"list": "api", "models": []}`, "has no lister"},
		{"bad list kind", `"anthropic": {"list": "sometimes", "models": []}`, `list is "sometimes"`},
		{"none without a note", `"azure": {"list": "none", "models": []}`, "needs a note"},
		{"static without a list", `"perplexity": {"list": "static", "note": "n", "models": []}`, "needs a static block"},
		{"static with a bad date", `"perplexity": {"list": "static", "note": "n", "static": {"checked": "Oct 28", "source": "https://x", "models": ["sonar"]}, "models": []}`, "static.checked"},
		{"covered but not in the static list", `"perplexity": {"list": "static", "note": "n", "static": {"checked": "2026-10-28", "source": "https://x", "models": ["sonar"]}, "models": [{"id": "sonar-pro", "status": "covered"}]}`, "not in the static list"},
		{"rule matching everything", `"anthropic": {"list": "api", "ignore_rules": [{"match": "*", "reason": "all"}], "models": []}`, "would ignore everything"},
		{"rule without reason", `"anthropic": {"list": "api", "ignore_rules": [{"match": "claude-2*"}], "models": []}`, "needs a reason"},
		{"bad fixture prefix", `"groq": {"list": "api", "fixtures": {"provider": "openai", "prefix": "groq/"}, "models": []}`, "preset prefix"},
	}
	for _, tc := range sections {
		t.Run(tc.name, func(t *testing.T) {
			raw := strings.Replace(validRegistry, `"providers": {`, `"providers": {`+"\n    "+tc.section+",", 1)
			_, err := ParseRegistry([]byte(raw))
			if err == nil || !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("got %v, want an error containing %q", err, tc.want)
			}
		})
	}

	if _, err := ParseRegistry([]byte(strings.Replace(validRegistry, `"about"`, `"abuot"`, 1))); err == nil {
		t.Error("an unknown key must be an error")
	}
	if _, err := ParseRegistry([]byte(strings.Replace(validRegistry, `"schema_version": 1`, `"schema_version": 2`, 1))); err == nil {
		t.Error("an unknown schema version must be an error")
	}
}

func TestAddIgnoredAndPlaceholders(t *testing.T) {
	reg, err := ParseRegistry([]byte(validRegistry))
	if err != nil {
		t.Fatal(err)
	}
	if err := reg.AddIgnored("openai", "gpt-7", PlaceholderReason+": look at it"); err != nil {
		t.Fatal(err)
	}
	if err := reg.AddIgnored("openai", "gpt-7", "again"); err == nil {
		t.Error("adding an existing ID must fail")
	}
	if err := reg.AddIgnored("nope", "x", "r"); err == nil {
		t.Error("adding to a missing section must fail")
	}
	if got := reg.Placeholders(); len(got) != 1 || got[0] != "openai/gpt-7" {
		t.Errorf("Placeholders() = %v", got)
	}
	// The placeholder is still a valid registry (so -check keeps working
	// during triage); only the coverage test refuses it.
	if _, err := ParseRegistry(reg.Encode()); err != nil {
		t.Errorf("a registry with a placeholder must still parse: %v", err)
	}
	ids := []string{}
	for _, e := range reg.Providers["openai"].Models {
		ids = append(ids, e.ID)
	}
	if strings.Join(ids, ",") != "gpt-6,gpt-6-sol,gpt-7,o1-pro" {
		t.Errorf("models not kept sorted: %v", ids)
	}
}

func TestGlobMatch(t *testing.T) {
	tests := []struct {
		pattern, s string
		want       bool
	}{
		{"ft:*", "ft:gpt-4o:acme::abc", true},
		{"*embedding*", "text-embedding-3-large", true},
		{"*embedding*", "gpt-6-sol", false},
		{"canopylabs/*", "canopylabs/orpheus-v1-english", true}, // * crosses "/"
		{"*guard*", "meta-llama/llama-guard-4-12b", true},
		{"amazon.*", "us.amazon.nova-2-lite-v1:0", false}, // anchored
		{"amazon.*", "amazon.nova-pro-v1:0", true},
		{"*-latest", "gemini-flash-latest", true},
		{"*-latest", "gemini-flash-latest-2", false},
		{"gpt-?", "gpt-5", true},
		{"gpt-?", "gpt-55", false},
		{"*nova*sonic*", "us.amazon.nova-2-sonic-v1:0", true},
		{"a*b*c", "abc", true},
		{"a*b*c", "acb", false},
		{"", "", true},
	}
	for _, tc := range tests {
		if got := globMatch(tc.pattern, tc.s); got != tc.want {
			t.Errorf("globMatch(%q, %q) = %v, want %v", tc.pattern, tc.s, got, tc.want)
		}
	}
}

func TestValidID(t *testing.T) {
	for _, id := range []string{"gpt-6-sol", "us.amazon.nova-2-lite-v1:0", "groq@openai/gpt-oss-120b", "ft:gpt-4o:acme::x1", "claude-x@20250101"} {
		if !ValidID(id) {
			t.Errorf("ValidID(%q) = false", id)
		}
	}
	for _, id := range []string{"", "-x", "gpt 6", "gpt-6`id`", "a$(b)", "x\ny", strings.Repeat("a", 201), "../../etc"} {
		if ValidID(id) {
			t.Errorf("ValidID(%q) = true", id)
		}
	}
}
