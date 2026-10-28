package contractwatch

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func samplePlan() []PlanEntry {
	yes := true
	return []PlanEntry{
		{
			Provider: "gemini", NewModel: "gemini-4.1-argon", TemplateModel: "gemini-4-argon",
			Reason: "family gemini; same tier argon; nearest version 4 → 4.1", Record: true, InModelDB: false,
			Recorder: &RecorderArgs{Provider: "gemini", From: "gemini-4-argon", As: "gemini-4.1-argon", Cases: []string{"text_basic"}},
			Metadata: &Model{ID: "gemini-4.1-argon", Thinking: &yes, ContextWindow: 1048576, Capabilities: []string{"generateContent"}},
		},
		{Provider: "cohere", NewModel: "command-b-01-2027", Reason: "no covered cohere model to clone cases from"},
	}
}

func TestReportMarkdown(t *testing.T) {
	rep := &Report{
		Sections: []SectionResult{
			{Name: "gemini", State: StateListed, Listed: 3, New: []Model{{ID: "gemini-4.1-argon"}}, Covered: 2,
				Retired: []Finding{{Section: "gemini", Model: "gemini-3.1-flash-lite", Status: Covered}}},
			{Name: "anthropic", State: StateSkipped, Detail: "ANTHROPIC_API_KEY not set"},
		},
		Plan:  samplePlan(),
		Stale: []Stale{{Kind: StaleNoEntry, Detail: "x | y"}},
	}
	md := rep.Markdown()
	for _, want := range []string{
		"| gemini | listed | 3 | 1 | 2 |",
		"| anthropic | skipped: ANTHROPIC_API_KEY not set |",
		"| gemini | `gemini-4.1-argon` | `gemini-4-argon` |",
		"| **missing** |",
		"| cohere | `command-b-01-2027` | none |",
		"## Retired", "`gemini-3.1-flash-lite`",
		"x \\| y", // pipes escaped inside a table
	} {
		if !strings.Contains(md, want) {
			t.Errorf("summary lacks %q:\n%s", want, md)
		}
	}
}

func writeLogs(t *testing.T) (string, []Result) {
	t.Helper()
	dir := t.TempDir()
	logs := map[string]string{
		"logs/new-1-test.log": "=== RUN   TestContract\n=== RUN   TestContract/gemini-4.1-argon/text_basic\n" +
			"    contract_test.go:120: upstream response has fields the adapter's structs do not represent (content would be dropped):\n" +
			"          $.candidates[].thoughtSignatureV2\n" +
			"    contract_test.go:121: ```injected fence\n" +
			"    golden.go:52: contracttest: live mode, no golden at x; skipping comparison\n" +
			"--- FAIL: TestContract (0.01s)\n    --- FAIL: TestContract/gemini-4.1-argon/text_basic (0.00s)\nFAIL\n",
		"logs/new-1-record.log":    "gemini gemini-4.1-argon/text_basic: recorded\ndone: 1 recorded, 0 failed\n",
		"logs/newest-1-test.log":   "ok  \tgithub.com/x\t0.1s\n",
		"logs/newest-1-record.log": "done\n",
	}
	for name, body := range logs {
		p := filepath.Join(dir, filepath.FromSlash(name))
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	results := []Result{
		{Kind: "new", Provider: "gemini", Model: "gemini-4.1-argon", Template: "gemini-4-argon", Recorded: 1, TestExit: 1,
			RecordLog: "logs/new-1-record.log", TestLog: "logs/new-1-test.log", Dir: "recordings/new/1"},
		{Kind: "newest", Provider: "anthropic", Model: "claude-opus-5-5", Recorded: 4, RecordLog: "logs/newest-1-record.log", TestLog: "logs/newest-1-test.log"},
	}
	lines := []string{}
	for _, r := range results {
		lines = append(lines, string(mustJSON(t, r)))
	}
	if err := os.WriteFile(filepath.Join(dir, "results.jsonl"), []byte(strings.Join(lines, "\n")+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	return dir, results
}

func TestRenderResultsAndIssues(t *testing.T) {
	dir, _ := writeLogs(t)
	results, err := ReadResults(filepath.Join(dir, "results.jsonl"))
	if err != nil || len(results) != 2 {
		t.Fatalf("ReadResults: %v %v", results, err)
	}
	md := RenderResults(results, dir)
	for _, want := range []string{
		"## New models recorded from their templates (1)",
		"| gemini | `gemini-4.1-argon` | `gemini-4-argon` | 1 | ok | DRIFT |",
		"## Newest models re-recorded (1)",
		"| anthropic | `claude-opus-5-5` |  | 4 | ok | passed |",
		"$.candidates[].thoughtSignatureV2",
		"'''injected fence", // a fence in a log cannot close the summary's code block
	} {
		if !strings.Contains(md, want) {
			t.Errorf("results summary lacks %q:\n%s", want, md)
		}
	}
	if strings.Contains(md, "=== RUN") || strings.Contains(md, "live mode, no golden") {
		t.Errorf("bookkeeping lines leaked into the drift summary:\n%s", md)
	}

	issues := RenderIssues(samplePlan(), results, dir, IssueOptions{RunURL: "https://github.com/o/r/actions/runs/1", Artifact: "model-watch-1", Date: "2026-10-29"})
	if len(issues) != 2 {
		t.Fatalf("got %d issues", len(issues))
	}
	if issues[0].Title != "[model-watch] New gemini model not under contract: gemini-4.1-argon" {
		t.Errorf("title %q", issues[0].Title)
	}
	body := issues[0].Body
	for _, want := range []string{
		"go run ./cmd/contract-recorder -provider gemini -from 'gemini-4-argon' -as 'gemini-4.1-argon'",
		"go test ./internal/providers/gemini/ -run Contract -update",
		`{"id": "gemini-4.1-argon", "status": "covered"}`,
		"live contract test **DRIFT**",
		"$.candidates[].thoughtSignatureV2",
		"no entry in internal/modeldb/litellm.json",
		"thinking true",
		"Closes #",
	} {
		if !strings.Contains(body, want) {
			t.Errorf("issue body lacks %q:\n%s", want, body)
		}
	}
	if b := issues[1].Body; !strings.Contains(b, "Write fixtures for it by hand") || !strings.Contains(b, "not recorded this run") {
		t.Errorf("no-template issue body:\n%s", b)
	}
}

func TestResultOutcome(t *testing.T) {
	for _, tc := range []struct {
		r    Result
		want string
	}{
		{Result{Recorded: 0}, "not recorded"},
		{Result{Recorded: 0, RecordExit: 1, TestExit: -1}, "not recorded"},
		{Result{Recorded: 0, RecordExit: RecorderExitNothing, TestExit: -1}, "nothing to record"},
		{Result{Recorded: 2}, "passed"},
		{Result{Recorded: 2, RecordExit: 1}, "passed (some cases failed to record)"},
		{Result{Recorded: 2, TestExit: 1}, "DRIFT"},
		{Result{Recorded: 2, TestExit: -1}, "not tested"},
	} {
		if got := tc.r.Outcome(); got != tc.want {
			t.Errorf("%+v: %q, want %q", tc.r, got, tc.want)
		}
	}
	if rs, err := ReadResults(filepath.Join(t.TempDir(), "none.jsonl")); err != nil || rs != nil {
		t.Errorf("a missing results file is no results: %v %v", rs, err)
	}
}

func mustJSON(t *testing.T, v any) []byte {
	t.Helper()
	raw, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}
