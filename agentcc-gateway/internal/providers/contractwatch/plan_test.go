package contractwatch

import (
	"encoding/json"
	"strings"
	"testing"
)

const planRegistry = `{
  "schema_version": 1, "about": "t",
  "providers": {
    "gemini": {"list": "api", "models": [
      {"id": "gemini-3.8-flash", "status": "covered"},
      {"id": "gemini-4-argon", "status": "covered"}
    ]},
    "groq": {"list": "api", "fixtures": {"provider": "openai", "prefix": "groq@"}, "models": [
      {"id": "openai/gpt-oss-120b", "status": "covered"}
    ]},
    "cohere": {"list": "api", "models": []},
    "anthropic": {"list": "api", "models": [
      {"id": "claude-haiku-4-5-20251001", "status": "covered"},
      {"id": "claude-opus-5-5", "status": "covered"},
      {"id": "claude-opus-4-8", "status": "covered"}
    ]}
  }
}`

func planTree(t *testing.T) string {
	return fakeTree(t, map[string][]string{
		"gemini/gemini-3.8-flash":             {"text_basic", "thinking", "grounding"},
		"gemini/gemini-4-argon":               {"text_basic", "stream_text", "tool_call", "stream_tool_call", "thinking", "continuation"},
		"openai/groq%40openai%2Fgpt-oss-120b": {"reasoning", "tool_call", "stream_reasoning"},
		"anthropic/claude-haiku-4-5-20251001": {"text_basic"},
		"anthropic/claude-opus-5-5":           {"text_basic", "refusal"},
		"anthropic/claude-opus-4-8":           {"text_basic"},
	})
}

func TestBuildPlan(t *testing.T) {
	reg := mustParse(t, planRegistry)
	root := planTree(t)
	results := []SectionResult{
		DiffSection("gemini", reg.Providers["gemini"], []Model{
			{ID: "gemini-4-argon"}, {ID: "gemma-5-27b-it", Created: at(28)}, {ID: "gemini-4.1-argon", Created: at(25)}, {ID: "gemini-3.9-flash-lite", Created: at(20)},
		}),
		DiffSection("groq", reg.Providers["groq"], []Model{{ID: "openai/gpt-oss-120b"}, {ID: "openai/gpt-oss-20b"}}),
		DiffSection("cohere", reg.Providers["cohere"], []Model{{ID: "command-b-01-2027"}}),
	}
	plan := BuildPlan(reg, results, PlanOptions{ProvidersDir: root, MaxRecord: 2, MaxCases: 4})
	if len(plan) != 5 {
		t.Fatalf("got %d entries: %+v", len(plan), plan)
	}
	byNew := map[string]PlanEntry{}
	for _, e := range plan {
		byNew[e.NewModel] = e
	}
	argon := byNew["gemini-4.1-argon"]
	if argon.TemplateModel != "gemini-4-argon" || argon.Recorder == nil ||
		argon.Recorder.Provider != "gemini" || argon.Recorder.From != "gemini-4-argon" || argon.Recorder.As != "gemini-4.1-argon" ||
		strings.Join(argon.Recorder.Cases, ",") != "text_basic,stream_text,tool_call,stream_tool_call" {
		t.Errorf("argon entry: %+v %+v", argon, argon.Recorder)
	}
	if byNew["gemini-3.9-flash-lite"].TemplateModel != "gemini-3.8-flash" {
		t.Errorf("flash-lite template: %+v", byNew["gemini-3.9-flash-lite"])
	}
	oss := byNew["openai/gpt-oss-20b"]
	if oss.Recorder == nil || oss.Recorder.Provider != "openai" || oss.Recorder.From != "groq@openai/gpt-oss-120b" || oss.Recorder.As != "groq@openai/gpt-oss-20b" {
		t.Errorf("groq entry must record through the openai suite with the preset prefix: %+v", oss.Recorder)
	}
	if !oss.InModelDB {
		t.Error("groq/openai/gpt-oss-20b is in the bundled litellm.json")
	}
	cmd := byNew["command-b-01-2027"]
	if cmd.TemplateModel != "" || cmd.Recorder != nil || cmd.Record || !strings.Contains(cmd.Reason, "no covered cohere chat model") {
		t.Errorf("no-template entry: %+v", cmd)
	}
	// Successors of covered families come first: the newer gemma (no
	// covered gemma) goes after both gemini models.
	var order []string
	for _, e := range plan {
		if e.Provider == "gemini" {
			order = append(order, e.NewModel)
		}
	}
	if strings.Join(order, ",") != "gemini-4.1-argon,gemini-3.9-flash-lite,gemma-5-27b-it" {
		t.Errorf("gemini order = %v", order)
	}
	// The cap of 2 goes round-robin: gemini's first, then groq's, not
	// gemini's two.
	var recorded []string
	for _, e := range plan {
		if e.Record {
			recorded = append(recorded, e.NewModel)
		}
	}
	if strings.Join(recorded, ",") != "gemini-4.1-argon,openai/gpt-oss-20b" {
		t.Errorf("recorded = %v", recorded)
	}

	// The plan file is a JSON list of {provider, new_model, template_model,
	// reason, ...}.
	raw, _ := json.Marshal(plan)
	var generic []map[string]any
	if err := json.Unmarshal(raw, &generic); err != nil {
		t.Fatal(err)
	}
	for _, k := range []string{"provider", "new_model", "template_model", "reason", "record", "in_modeldb"} {
		if _, ok := generic[0][k]; !ok {
			t.Errorf("plan entries lack %q: %s", k, raw)
		}
	}
}

func TestBuildNewest(t *testing.T) {
	reg := mustParse(t, planRegistry)
	root := planTree(t)
	all := BuildNewest(reg, []string{"anthropic", "gemini", "groq"}, PlanOptions{ProvidersDir: root, MaxCases: 1})
	var got []string
	for _, e := range all {
		got = append(got, e.Model)
		if !e.Record || len(e.Recorder.Cases) != 1 || e.Recorder.Model == "" {
			t.Errorf("without a cap every entry records one case: %+v", e)
		}
	}
	// opus-5-5 is newer than opus-4-8 in the same family; haiku is its own
	// family; sections interleave round-robin.
	if strings.Join(got, ",") != "claude-haiku-4-5-20251001,gemini-4-argon,openai/gpt-oss-120b,claude-opus-5-5,gemini-3.8-flash" {
		t.Errorf("newest = %v", got)
	}
	for _, e := range all {
		if e.Model == "openai/gpt-oss-120b" && (e.Recorder.Provider != "openai" || e.Recorder.Model != "groq@openai/gpt-oss-120b") {
			t.Errorf("groq re-record goes through the openai suite: %+v", e.Recorder)
		}
		if e.Model == "claude-opus-5-5" && !strings.Contains(e.Reason, "claude-opus-4-8") {
			t.Errorf("reason should name the family: %s", e.Reason)
		}
	}

	// A cap of 2 rotates through all five over three runs.
	seen := map[string]int{}
	for day := 0; day < 3; day++ {
		for _, e := range BuildNewest(reg, []string{"anthropic", "gemini", "groq"}, PlanOptions{ProvidersDir: root, MaxRecord: 2, Rotate: day}) {
			if e.Record {
				seen[e.Model]++
			}
		}
	}
	if len(seen) != 5 {
		t.Errorf("rotation reached %v, want all 5 families", seen)
	}
}

func TestRoundRobin(t *testing.T) {
	sizes := []int{3, 0, 1}
	got := roundRobin(3, func(i int) int { return sizes[i] })
	want := [][2]int{{0, 0}, {2, 0}, {0, 1}, {0, 2}}
	if len(got) != len(want) {
		t.Fatalf("got %v", got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("got %v, want %v", got, want)
		}
	}
}
