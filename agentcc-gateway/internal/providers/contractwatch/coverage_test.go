package contractwatch

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

// TestRegistryMatchesFixtures is the offline consistency check: every
// covered model in known_models.json has its fixture directory, and every
// fixture directory under internal/providers/*/testdata/contracts has a
// covered entry. Add or rename fixtures and this fails until the registry
// agrees.
func TestRegistryMatchesFixtures(t *testing.T) {
	reg, _ := loadCommitted(t)
	for _, s := range CheckCoverage(reg, "..") {
		t.Errorf("%s", s)
	}
	fixtures, errs := FindFixtures("..")
	if len(errs) > 0 {
		t.Fatalf("reading fixtures: %v", errs)
	}
	if len(fixtures) == 0 {
		t.Fatal("found no fixture directories under ..; the test must run from the package directory")
	}
}

// TestLayoutMatchesContracttest pins this package's copy of the fixture
// layout to contracttest's (imported only here: contracttest is test
// support that registers -update on the global flag set).
func TestLayoutMatchesContracttest(t *testing.T) {
	if ContractsRoot != contracttest.DefaultRoot {
		t.Errorf("ContractsRoot %q != contracttest.DefaultRoot %q", ContractsRoot, contracttest.DefaultRoot)
	}
	if metaFile != contracttest.MetaFile || requestFile != contracttest.RequestFile {
		t.Errorf("file names drifted from contracttest's")
	}
	if caseFile != contracttest.CaseFile {
		t.Errorf("caseFile %q != contracttest.CaseFile %q", caseFile, contracttest.CaseFile)
	}
	for _, id := range []string{
		"gpt-6-sol", "us.amazon.nova-2-lite-v1:0", "groq@openai/gpt-oss-120b", "perplexity@sonar",
		"claude-x@20250101", "a b", "ü", "~x_y.z", "ft:gpt-4o:acme::abc", "%41",
	} {
		if got, want := ModelDir(id), contracttest.ModelDir(id); got != want {
			t.Errorf("ModelDir(%q) = %q, contracttest.ModelDir = %q", id, got, want)
		}
	}
}

// fakeTree builds providers/<p>/testdata/contracts/<dir>/meta.json for each
// "p/dir" given, plus a request.json case per listed case name.
func fakeTree(t *testing.T, dirs map[string][]string) string {
	t.Helper()
	root := t.TempDir()
	for pd, cases := range dirs {
		p, d, _ := strings.Cut(pd, "/")
		md := filepath.Join(root, p, ContractsRoot, d)
		if err := os.MkdirAll(md, 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(md, metaFile), []byte(`{}`), 0o644); err != nil {
			t.Fatal(err)
		}
		for _, c := range cases {
			name, spec, _ := strings.Cut(c, "=")
			cd := filepath.Join(md, name)
			if err := os.MkdirAll(cd, 0o755); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(cd, requestFile), []byte(`{"model":"x"}`), 0o644); err != nil {
				t.Fatal(err)
			}
			if spec != "" {
				if err := os.WriteFile(filepath.Join(cd, caseFile), []byte(spec), 0o644); err != nil {
					t.Fatal(err)
				}
			}
		}
	}
	return root
}

func TestCheckCoverageFindsStale(t *testing.T) {
	reg, err := ParseRegistry([]byte(`{
  "schema_version": 1, "about": "t",
  "providers": {
    "openai": {"list": "api", "models": [
      {"id": "gpt-6-sol", "status": "covered"},
      {"id": "gpt-7-sol", "status": "covered"}
    ]},
    "groq": {"list": "api", "fixtures": {"provider": "openai", "prefix": "groq@"}, "models": [
      {"id": "openai/gpt-oss-120b", "status": "covered"}
    ]}
  }
}`))
	if err != nil {
		t.Fatal(err)
	}
	root := fakeTree(t, map[string][]string{
		"openai/gpt-6-sol":                    nil,
		"openai/groq%40openai%2Fgpt-oss-120b": nil, // claimed through the groq section's prefix
		"openai/gpt-5.5":                      nil, // no entry
		"anthropic/claude-x":                  nil, // a provider with no section at all
		"bedrock/us.amazon.nova:0":            nil, // a literal ":" is not ModelDir's spelling
	})
	// A file at the contracts root is not a model directory.
	if err := os.WriteFile(filepath.Join(root, "openai", ContractsRoot, "notes.json"), []byte("{}"), 0o644); err != nil {
		t.Fatal(err)
	}
	got := map[string]StaleKind{}
	for _, s := range CheckCoverage(reg, root) {
		key := s.Model
		if s.Kind == StaleBadDir {
			key = "bad"
		}
		got[key] = s.Kind
	}
	want := map[string]StaleKind{
		"gpt-7-sol": StaleNoFixtures,
		"gpt-5.5":   StaleNoEntry,
		"claude-x":  StaleNoEntry,
		"bad":       StaleBadDir,
	}
	if len(got) != len(want) {
		t.Errorf("got %v, want %v", got, want)
	}
	for k, v := range want {
		if got[k] != v {
			t.Errorf("%s: got %q, want %q", k, got[k], v)
		}
	}
}

func TestTemplateCases(t *testing.T) {
	root := fakeTree(t, map[string][]string{
		"anthropic/claude-haiku-4-5-20251001": {
			"text_basic", "stream_text", "tool_use",
			`error_rate_limit={"status": 429, "recordable": false, "notes": "cannot be induced"}`,
			`error_invalid_request={"status": 400}`,
			`count_tokens={"endpoint": "anthropic_count_tokens"}`,
		},
	})
	cases, err := TemplateCases(root, "anthropic", "claude-haiku-4-5-20251001")
	if err != nil {
		t.Fatal(err)
	}
	var names []string
	for _, c := range cases {
		names = append(names, c.Name)
	}
	if strings.Join(names, ",") != "count_tokens,error_invalid_request,stream_text,text_basic,tool_use" {
		t.Errorf("TemplateCases = %v (a recordable:false case must be dropped)", names)
	}
	if got := PickCases(cases, 0); strings.Join(got, ",") != "text_basic,stream_text,tool_use,count_tokens,error_invalid_request" {
		t.Errorf("PickCases(all) = %v: plain cases by priority, then secondary ones", got)
	}
	if got := PickCases(cases, 2); strings.Join(got, ",") != "text_basic,stream_text" {
		t.Errorf("PickCases(2) = %v", got)
	}
	if _, err := TemplateCases(root, "anthropic", "nope"); err == nil {
		t.Error("a missing model directory must be an error")
	}
}
