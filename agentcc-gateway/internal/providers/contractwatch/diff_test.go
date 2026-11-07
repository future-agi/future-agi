package contractwatch

import (
	"strings"
	"testing"
	"time"
)

func mustParse(t *testing.T, raw string) *Registry {
	t.Helper()
	reg, err := ParseRegistry([]byte(raw))
	if err != nil {
		t.Fatal(err)
	}
	return reg
}

const diffRegistry = `{
  "schema_version": 1, "about": "t",
  "providers": {
    "openai": {"list": "api",
      "ignore_rules": [
        {"match": "*embedding*", "reason": "embeddings"},
        {"match": "sora-*", "reason": "video"}
      ],
      "models": [
        {"id": "gpt-4.1-2025-04-14", "status": "covered"},
        {"id": "gpt-4.1", "status": "alias", "alias_of": "gpt-4.1-2025-04-14"},
        {"id": "gpt-6-sol", "status": "covered"},
        {"id": "gpt-5-retired", "status": "covered"},
        {"id": "o1-pro", "status": "ignored", "reason": "responses only"},
        {"id": "o0-gone", "status": "ignored", "reason": "old"}
      ]}
  }
}`

func at(day int) *time.Time {
	t := time.Date(2026, 10, day, 0, 0, 0, 0, time.UTC)
	return &t
}

func TestDiffSection(t *testing.T) {
	reg := mustParse(t, diffRegistry)
	listed := []Model{
		{ID: "gpt-4.1-2025-04-14", Retires: "2027-01-14"},
		{ID: "gpt-4.1"},
		{ID: "gpt-6-sol"},
		{ID: "o1-pro"},
		{ID: "text-embedding-3-large"},
		{ID: "gpt-4o", AutoIgnore: "shutdown announced"},
		{ID: "gpt-6.1-sol", Created: at(20)},
		{ID: "gpt-6.1-terra", Created: at(27)},
		{ID: "gpt-7"},
		{ID: "gpt-6.1-sol", Created: at(20)}, // listed twice
		{ID: "bad id; rm -rf /"},
	}
	r := DiffSection("openai", reg.Providers["openai"], listed)
	if got := ids(r.New); got != "gpt-6.1-terra,gpt-6.1-sol,gpt-7" {
		t.Errorf("New = %s (newest created first, then undated by version)", got)
	}
	if r.Listed != 9 || r.Covered != 2 || r.Aliases != 1 || r.Ignored != 1 || r.RuleIgnored != 1 || r.AutoIgnored != 1 {
		t.Errorf("counts: %+v", r)
	}
	if len(r.Retired) != 1 || r.Retired[0].Model != "gpt-5-retired" {
		t.Errorf("Retired = %+v", r.Retired)
	}
	if len(r.Gone) != 1 || r.Gone[0].Model != "o0-gone" {
		t.Errorf("Gone = %+v", r.Gone)
	}
	if len(r.Deprecating) != 1 || r.Deprecating[0].Model != "gpt-4.1-2025-04-14" || !strings.Contains(r.Deprecating[0].Detail, "2027-01-14") {
		t.Errorf("Deprecating = %+v", r.Deprecating)
	}
	if strings.Join(r.UnusedRules, ",") != "sora-*" {
		t.Errorf("UnusedRules = %v", r.UnusedRules)
	}
	if len(r.Invalid) != 1 {
		t.Errorf("Invalid = %v", r.Invalid)
	}
	if r.Meta["gpt-6.1-sol"] == nil {
		t.Error("metadata of listed models must be kept for the selector")
	}
}

func TestSortNewest(t *testing.T) {
	ms := []Model{{ID: "gemini-3.9-flash"}, {ID: "gemini-4.1-argon"}, {ID: "gemini-4-argon"}, {ID: "gemini-2.5-pro", Created: at(1)}}
	SortNewest("gemini", ms)
	if got := ids(ms); got != "gemini-2.5-pro,gemini-4.1-argon,gemini-4-argon,gemini-3.9-flash" {
		t.Errorf("got %s", got)
	}
}

func TestDiffFoldsBedrockGeographies(t *testing.T) {
	reg := mustParse(t, `{
  "schema_version": 1, "about": "t",
  "providers": {
    "bedrock": {"list": "api", "models": [
      {"id": "global.anthropic.claude-sonnet-4-6", "status": "covered"},
      {"id": "us.anthropic.claude-haiku-9", "status": "ignored", "reason": "x"}
    ]}
  }
}`)
	listed := []Model{
		{ID: "global.anthropic.claude-sonnet-4-6"},
		{ID: "us.anthropic.claude-sonnet-4-6", Lifecycle: "LEGACY"}, // a geography of a covered model
		{ID: "eu.anthropic.claude-haiku-9"},                         // a geography of an ignored one
		{ID: "apac.anthropic.claude-sonnet-4-6"},                    // the adapter sends apac. to InvokeModel: not the same contract
		{ID: "us.anthropic.claude-opus-6"},
		{ID: "global.anthropic.claude-opus-6"},
		{ID: "eu.anthropic.claude-opus-6"},
		{ID: "anthropic.claude-opus-6"}, // a base ID is InvokeModel: its own entry
	}
	r := DiffSection("bedrock", reg.Providers["bedrock"], listed)
	if got := ids(r.New); got != "anthropic.claude-opus-6,global.anthropic.claude-opus-6,apac.anthropic.claude-sonnet-4-6" {
		t.Fatalf("New = %s", got)
	}
	for _, m := range r.New {
		if m.ID == "global.anthropic.claude-opus-6" && strings.Join(m.Variants, ",") != "eu.anthropic.claude-opus-6,us.anthropic.claude-opus-6" {
			t.Errorf("variants = %v", m.Variants)
		}
	}
	if r.GeoAliases != 2 {
		t.Errorf("GeoAliases = %d, want 2", r.GeoAliases)
	}
	if len(r.Deprecating) != 1 || r.Deprecating[0].Model != "us.anthropic.claude-sonnet-4-6" {
		t.Errorf("a deprecated geography of a covered model is reported: %+v", r.Deprecating)
	}
	if GeoKey("openai", "us.x.y") != "" || GeoKey("bedrock", "anthropic.claude-x") != "" {
		t.Error("GeoKey applies to Bedrock profiles only")
	}
}
