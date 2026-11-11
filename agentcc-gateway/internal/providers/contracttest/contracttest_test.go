package contracttest

import (
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/common"
)

// fakeTB records what the harness reports, so its failure paths can be
// tested. Fatal ends the goroutine the way testing.T does.
type fakeTB struct {
	testing.TB // nil: calling anything not overridden panics

	mu       sync.Mutex
	errors   []string
	fatal    bool
	cleanups []func()
}

func (f *fakeTB) Helper()                        {}
func (f *fakeTB) Logf(string, ...any)            {}
func (f *fakeTB) Cleanup(fn func())              { f.cleanups = append(f.cleanups, fn) }
func (f *fakeTB) Errorf(format string, a ...any) { f.record(fmt.Sprintf(format, a...)) }
func (f *fakeTB) Fatalf(format string, a ...any) {
	f.record(fmt.Sprintf(format, a...))
	f.mu.Lock()
	f.fatal = true
	f.mu.Unlock()
	runtime.Goexit()
}

func (f *fakeTB) record(msg string) {
	f.mu.Lock()
	f.errors = append(f.errors, msg)
	f.mu.Unlock()
}

// runFake runs fn, then the cleanups it registered, each on its own goroutine
// so a Fatal ends only that goroutine.
func runFake(fn func(tb *fakeTB)) *fakeTB {
	ft := &fakeTB{}
	run := func(f func()) {
		done := make(chan struct{})
		go func() { defer close(done); f() }()
		<-done
	}
	run(func() { fn(ft) })
	for i := len(ft.cleanups) - 1; i >= 0; i-- {
		run(ft.cleanups[i])
	}
	return ft
}

// setUpdate sets the package's -update flag to v and restores it when the test
// ends. The harness's own tests pin the flag so they behave the same whether or
// not the package runs with -update (go test ./internal/providers/... -update).
func setUpdate(t *testing.T, v bool) {
	t.Helper()
	old := *update
	*update = v
	t.Cleanup(func() { *update = old })
}

func writeFile(t *testing.T, path, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

const syntheticMeta = `{
  "provider": "acme",
  "model": "acme-1",
  "source": "synthetic",
  "recorded_at": null,
  "notes": "test",
  "allowed_unknown_fields": [{"path": "$.extra", "reason": "known gap: test"}],
  "allowed_unknown_shapes": [{"kind": "unknown_block", "value": "x", "reason": "known gap: test"}]
}`

// fixtureTree writes acme-1 with a JSON case and a stream case.
func fixtureTree(t *testing.T, meta string) string {
	t.Helper()
	root := t.TempDir()
	writeFile(t, filepath.Join(root, "acme-1", MetaFile), meta)
	writeFile(t, filepath.Join(root, "acme-1", "basic", RequestFile), `{"model":"acme-1","messages":[{"role":"user","content":"hi"}],"thinking":{"budget":1}}`)
	writeFile(t, filepath.Join(root, "acme-1", "basic", ResponseJSONFile), `{"id":"r1"}`)
	writeFile(t, filepath.Join(root, "acme-1", "stream", RequestFile), `{"model":"acme-1","stream":true,"messages":[{"role":"user","content":"hi"}]}`)
	writeFile(t, filepath.Join(root, "acme-1", "stream", ResponseSSEFile), "data: {}\n\n")
	return root
}

func TestDiscover(t *testing.T) {
	root := fixtureTree(t, syntheticMeta)
	var cases []Case
	ft := runFake(func(tb *fakeTB) { cases = discover(tb, "acme", root, "", false) })
	if len(ft.errors) != 0 {
		t.Fatalf("unexpected errors: %q", ft.errors)
	}
	if len(cases) != 2 {
		t.Fatalf("got %d cases, want 2", len(cases))
	}
	basic, stream := cases[0], cases[1]
	if basic.ID() != "acme-1/basic" || basic.IsStream || string(basic.Body) != `{"id":"r1"}` {
		t.Errorf("basic case = %+v", basic)
	}
	if _, ok := basic.Request.Extra["thinking"]; !ok {
		t.Errorf("request extras not captured: %+v", basic.Request.Extra)
	}
	if !stream.IsStream || !stream.Request.Stream || stream.ContentType != "text/event-stream" || basic.ContentType != "application/json" {
		t.Errorf("stream case = %+v", stream)
	}
	if basic.Meta != stream.Meta {
		t.Errorf("cases of one model must share a Meta")
	}
	if basic.GoldenPath() != filepath.Join(root, "acme-1", "basic", GoldenFile) {
		t.Errorf("GoldenPath = %s", basic.GoldenPath())
	}
}

func TestDiscover_RejectsBadFixtures(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(t *testing.T, root string)
		want   string
	}{
		{"stream flag disagrees with response file", func(t *testing.T, root string) {
			writeFile(t, filepath.Join(root, "acme-1", "basic", RequestFile), `{"model":"acme-1","stream":true,"messages":[]}`)
		}, "stream=true"},
		{"two response files", func(t *testing.T, root string) {
			writeFile(t, filepath.Join(root, "acme-1", "basic", ResponseSSEFile), "data: {}\n\n")
		}, "more than one response file"},
		{"no response file", func(t *testing.T, root string) {
			if err := os.Remove(filepath.Join(root, "acme-1", "basic", ResponseJSONFile)); err != nil {
				t.Fatal(err)
			}
		}, "no response file"},
		{"invalid JSON response", func(t *testing.T, root string) {
			writeFile(t, filepath.Join(root, "acme-1", "basic", ResponseJSONFile), `{`)
		}, "not valid JSON"},
		{"meta model differs from directory", func(t *testing.T, root string) {
			writeFile(t, filepath.Join(root, "acme-1", MetaFile), strings.Replace(syntheticMeta, `"acme-1"`, `"acme-2"`, 1))
		}, "directory name"},
		{"meta has an unknown key", func(t *testing.T, root string) {
			writeFile(t, filepath.Join(root, "acme-1", MetaFile), strings.Replace(syntheticMeta, `"notes"`, `"note"`, 1))
		}, "unknown field"},
		{"recorded without a date", func(t *testing.T, root string) {
			writeFile(t, filepath.Join(root, "acme-1", MetaFile), strings.Replace(syntheticMeta, `"synthetic"`, `"recorded"`, 1))
		}, "recorded_at"},
		{"allowlisted shape kind that does not exist", func(t *testing.T, root string) {
			writeFile(t, filepath.Join(root, "acme-1", MetaFile), strings.Replace(syntheticMeta, `"kind": "unknown_block"`, `"kind": "unknown_blok"`, 1))
		}, "not a common.ShapeKind"},
		{"allowlist entry without a reason", func(t *testing.T, root string) {
			writeFile(t, filepath.Join(root, "acme-1", MetaFile), strings.Replace(syntheticMeta, `"reason": "known gap: test"}],
  "allowed_unknown_shapes"`, `"reason": ""}],
  "allowed_unknown_shapes"`, 1))
		}, "no reason"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			root := fixtureTree(t, syntheticMeta)
			tc.mutate(t, root)
			ft := runFake(func(tb *fakeTB) { discover(tb, "acme", root, "", false) })
			if !ft.fatal || len(ft.errors) == 0 || !strings.Contains(ft.errors[0], tc.want) {
				t.Errorf("want a fatal mentioning %q, got fatal=%v %q", tc.want, ft.fatal, ft.errors)
			}
		})
	}
}

func TestDiscover_StaleAllowlist(t *testing.T) {
	root := fixtureTree(t, syntheticMeta)
	ft := runFake(func(tb *fakeTB) {
		for _, c := range discover(tb, "acme", root, "", false) {
			AssertKnownPaths(tb, c, nil)
			AssertNoUnknownShapes(tb, c, nil)
		}
	})
	if len(ft.errors) != 2 || !strings.Contains(ft.errors[0], "$.extra") || !strings.Contains(ft.errors[1], "unknown_block:x") {
		t.Errorf("want stale-entry errors for $.extra and unknown_block:x, got %q", ft.errors)
	}
}

func TestDiscover_AllowlistInUseIsNotStale(t *testing.T) {
	root := fixtureTree(t, syntheticMeta)
	ft := runFake(func(tb *fakeTB) {
		for i, c := range discover(tb, "acme", root, "", false) {
			var paths []string
			var shapes []common.UnknownShape
			if i == 0 { // used by one case of the model is enough
				paths = []string{"$.extra"}
				shapes = []common.UnknownShape{{Provider: "acme", Kind: common.ShapeUnknownBlock, Value: "x"}}
			}
			AssertKnownPaths(tb, c, paths)
			AssertNoUnknownShapes(tb, c, shapes)
		}
	})
	if len(ft.errors) != 0 {
		t.Errorf("unexpected errors: %q", ft.errors)
	}
}

func TestDiscover_StaleCheckNeedsEveryCaseAudited(t *testing.T) {
	root := fixtureTree(t, syntheticMeta)
	ft := runFake(func(tb *fakeTB) {
		cases := discover(tb, "acme", root, "", false)
		AssertKnownPaths(tb, cases[0], nil) // as if -run selected one case
		AssertNoUnknownShapes(tb, cases[0], nil)
	})
	if len(ft.errors) != 0 {
		t.Errorf("a partial run must not judge the allowlist, got %q", ft.errors)
	}
}

func TestAssertKnownPathsAndShapes_ReportUnexpected(t *testing.T) {
	root := fixtureTree(t, syntheticMeta)
	ft := runFake(func(tb *fakeTB) {
		c := discover(tb, "acme", root, "", false)[0]
		AssertKnownPaths(tb, c, []string{"$.extra", "$.surprise", "$.surprise"})
		AssertNoUnknownShapes(tb, c, []common.UnknownShape{
			{Provider: "acme", Kind: common.ShapeUnknownBlock, Value: "x"},
			{Provider: "acme", Kind: common.ShapeUnknownDelta, Value: "new_delta"},
		})
	})
	if len(ft.errors) != 2 || !strings.Contains(ft.errors[0], "$.surprise") || !strings.Contains(ft.errors[1], "unknown_delta:new_delta") {
		t.Errorf("got %q", ft.errors)
	}
}

func TestDiscover_LiveFallsBackToRepoMetaAndRequest(t *testing.T) {
	repo := fixtureTree(t, syntheticMeta)
	live := t.TempDir()
	// The recorder wrote only a response for a case the repo has, and a case
	// for a model the repo has never seen, with its own request.
	writeFile(t, filepath.Join(live, "acme-1", "basic", ResponseJSONFile), `{"id":"live"}`)
	writeFile(t, filepath.Join(live, "acme-9", "fresh", RequestFile), `{"model":"acme-9","messages":[]}`)
	writeFile(t, filepath.Join(live, "acme-9", "fresh", ResponseJSONFile), `{"id":"live9"}`)

	var cases []Case
	ft := runFake(func(tb *fakeTB) {
		cases = discover(tb, "acme", live, repo, true)
		for _, c := range cases {
			AssertKnownPaths(tb, c, nil) // stale entries are not judged live
		}
	})
	if len(ft.errors) != 0 {
		t.Fatalf("unexpected errors: %q", ft.errors)
	}
	if len(cases) != 2 {
		t.Fatalf("got %d cases, want 2", len(cases))
	}
	if c := cases[0]; !c.Live || c.Request.Model != "acme-1" || len(c.Meta.AllowedUnknownFields) != 1 || c.Dir != filepath.Join(live, "acme-1", "basic") {
		t.Errorf("acme-1 case did not fall back to the repo's request and meta: %+v", c)
	}
	if got, want := cases[0].UpstreamGoldenPath(), filepath.Join(repo, "acme-1", "basic", UpstreamGoldenFile); got != want {
		t.Errorf("live UpstreamGoldenPath = %s, want the repo's %s (it pins the repo request)", got, want)
	}
	if got, want := cases[0].GoldenPath(), filepath.Join(live, "acme-1", "basic", GoldenFile); got != want {
		t.Errorf("live GoldenPath = %s, want %s", got, want)
	}
	if got, want := cases[1].UpstreamGoldenPath(), filepath.Join(live, "acme-9", "fresh", UpstreamGoldenFile); got != want {
		t.Errorf("live UpstreamGoldenPath for a recorded request = %s, want %s", got, want)
	}
	if c := cases[1]; c.Meta.Model != "acme-9" || c.Meta.Source != SourceRecorded {
		t.Errorf("acme-9 meta = %+v, want a default recorded meta", c.Meta)
	}
}

func TestModelDir(t *testing.T) {
	tests := []struct{ model, dir string }{
		{"claude-opus-5-5", "claude-opus-5-5"},
		{"global.anthropic.claude-sonnet-4-6", "global.anthropic.claude-sonnet-4-6"},
		{"us.amazon.nova-2-lite-v1:0", "us.amazon.nova-2-lite-v1%3A0"},
		{"org/model@v1 x%", "org%2Fmodel%40v1%20x%25"},
	}
	for _, tc := range tests {
		if got := ModelDir(tc.model); got != tc.dir {
			t.Errorf("ModelDir(%q) = %q, want %q", tc.model, got, tc.dir)
		}
		if got, err := modelFromDir(tc.dir); err != nil || got != tc.model {
			t.Errorf("modelFromDir(%q) = %q, %v; want %q", tc.dir, got, err, tc.model)
		}
	}
}

// An encoded directory name holds the real model ID: meta.json and Case.Model
// carry it decoded, and live-mode fallbacks find the repo's encoded directory.
func TestDiscover_EncodedModelDir(t *testing.T) {
	const model, dir = "us.acme-1:0", "us.acme-1%3A0"
	meta := strings.Replace(syntheticMeta, `"acme-1"`, `"`+model+`"`, 1)
	repo := t.TempDir()
	writeFile(t, filepath.Join(repo, dir, MetaFile), meta)
	writeFile(t, filepath.Join(repo, dir, "basic", RequestFile), `{"model":"`+model+`","messages":[]}`)
	writeFile(t, filepath.Join(repo, dir, "basic", ResponseJSONFile), `{"id":"r1"}`)
	live := t.TempDir()
	writeFile(t, filepath.Join(live, dir, "basic", ResponseJSONFile), `{"id":"live"}`)

	for _, mode := range []struct {
		name, root, fallback string
		live                 bool
	}{{"repo", repo, "", false}, {"live", live, repo, true}} {
		t.Run(mode.name, func(t *testing.T) {
			var cases []Case
			ft := runFake(func(tb *fakeTB) { cases = discover(tb, "acme", mode.root, mode.fallback, mode.live) })
			if len(ft.errors) != 0 {
				t.Fatalf("unexpected errors: %q", ft.errors)
			}
			c := cases[0]
			if c.Model != model || c.Meta.Model != model || c.ID() != model+"/basic" || c.Request.Model != model {
				t.Errorf("case = %+v, want model %q throughout", c, model)
			}
			if got, want := c.UpstreamGoldenPath(), filepath.Join(repo, dir, "basic", UpstreamGoldenFile); got != want {
				t.Errorf("UpstreamGoldenPath = %s, want %s", got, want)
			}
		})
	}
}

func TestDiscover_RejectsNonCanonicalModelDir(t *testing.T) {
	tests := []struct{ dir, want string }{
		{"us.acme-1:0", `named "us.acme-1%3A0"`},   // literal ':'
		{"us.acme-1%3a0", `named "us.acme-1%3A0"`}, // lower-case hex
		{"acme%2", "not valid percent-encoding"},
	}
	for _, tc := range tests {
		t.Run(tc.dir, func(t *testing.T) {
			root := t.TempDir()
			writeFile(t, filepath.Join(root, tc.dir, MetaFile), syntheticMeta)
			ft := runFake(func(tb *fakeTB) { discover(tb, "acme", root, "", false) })
			if !ft.fatal || len(ft.errors) == 0 || !strings.Contains(ft.errors[0], tc.want) {
				t.Errorf("want a fatal mentioning %q, got fatal=%v %q", tc.want, ft.fatal, ft.errors)
			}
		})
	}
}

func TestNormalize(t *testing.T) {
	got, err := Normalize(map[string]any{
		"z":       1,
		"created": 1730000000,
		"id":      "gen-1730000000123456789",
		"choices": []any{map[string]any{"created": 0, "id": "msg_01ABC", "text": "<b>&</b>", "secret": "s"}},
	}, "secret")
	if err != nil {
		t.Fatal(err)
	}
	want := `{
  "choices": [
    {
      "created": 0,
      "id": "msg_01ABC",
      "secret": "<scrubbed>",
      "text": "<b>&</b>"
    }
  ],
  "created": "<nonzero>",
  "id": "gen-<generated>",
  "z": 1
}
`
	if string(got) != want {
		t.Errorf("Normalize =\n%s\nwant\n%s", got, want)
	}
}

func TestAssertGolden(t *testing.T) {
	path := filepath.Join(t.TempDir(), "sub", GoldenFile)
	value := map[string]any{"a": 1}

	setUpdate(t, false)
	ft := runFake(func(tb *fakeTB) { AssertGolden(tb, path, value) })
	if len(ft.errors) != 1 || !strings.Contains(ft.errors[0], "-update") {
		t.Fatalf("missing golden: got %q", ft.errors)
	}

	setUpdate(t, true)
	ft = runFake(func(tb *fakeTB) { AssertGolden(tb, path, value) })
	setUpdate(t, false)
	if len(ft.errors) != 0 {
		t.Fatalf("update: %q", ft.errors)
	}

	ft = runFake(func(tb *fakeTB) { AssertGolden(tb, path, value) })
	if len(ft.errors) != 0 {
		t.Fatalf("match: %q", ft.errors)
	}

	ft = runFake(func(tb *fakeTB) { AssertGolden(tb, path, map[string]any{"a": 2}) })
	if len(ft.errors) != 1 || !strings.Contains(ft.errors[0], `"a": 2`) {
		t.Fatalf("mismatch: %q", ft.errors)
	}

	t.Setenv(LiveEnv, t.TempDir())
	ft = runFake(func(tb *fakeTB) { AssertGolden(tb, filepath.Join(t.TempDir(), GoldenFile), value) })
	if len(ft.errors) != 0 {
		t.Fatalf("live mode with no golden must skip, got %q", ft.errors)
	}
}

func strp(s string) *string { return &s }

func TestFromStream(t *testing.T) {
	stop := "tool_calls"
	chunks := []models.StreamChunk{
		{Choices: []models.StreamChoice{{Delta: models.Delta{Role: "assistant"}}}},
		{Choices: []models.StreamChoice{{Delta: models.Delta{Content: strp("Hel")}}}},
		{Choices: []models.StreamChoice{{Delta: models.Delta{Content: strp("lo")}}}},
		{Choices: []models.StreamChoice{{Delta: models.Delta{ToolCalls: []models.ToolCallDelta{
			{Index: 1, ID: "b", Type: "function", Function: &models.FunctionCall{Name: "second"}},
		}}}}},
		{Choices: []models.StreamChoice{{Delta: models.Delta{ToolCalls: []models.ToolCallDelta{
			{Index: 0, ID: "a", Type: "function", Function: &models.FunctionCall{Name: "first"}},
		}}}}},
		{Choices: []models.StreamChoice{{Delta: models.Delta{ToolCalls: []models.ToolCallDelta{
			{Index: 0, Function: &models.FunctionCall{Arguments: `{"x":`}},
		}}}}},
		{Choices: []models.StreamChoice{{Delta: models.Delta{ToolCalls: []models.ToolCallDelta{
			{Index: 0, Function: &models.FunctionCall{Arguments: `1}`}},
		}}}}},
		{Choices: []models.StreamChoice{{FinishReason: &stop}}, Usage: &models.Usage{PromptTokens: 3, CompletionTokens: 2, TotalTokens: 5}},
	}
	c := FromStream(chunks)
	if c.Role != "assistant" || c.Content != "Hello" || c.FinishReason != "tool_calls" || c.Usage.TotalTokens != 5 {
		t.Errorf("assembled = %+v", c)
	}
	if len(c.ToolCalls) != 2 || c.ToolCalls[0].Function.Name != "first" || c.ToolCalls[0].Function.Arguments != `{"x":1}` || c.ToolCalls[1].ID != "b" {
		t.Errorf("tool calls = %+v", c.ToolCalls)
	}
}

func TestAssertSane(t *testing.T) {
	usage := &models.Usage{PromptTokens: 3, CompletionTokens: 2, TotalTokens: 5}
	tests := []struct {
		name         string
		c            Completion
		expectOutput bool
		wantErrors   int
	}{
		{"text", Completion{Content: "hi", FinishReason: "stop", Usage: usage}, true, 0},
		{"empty output allowed when upstream had none", Completion{FinishReason: "stop", Usage: usage}, false, 0},
		{"dropped output", Completion{FinishReason: "stop", Usage: usage}, true, 1},
		{"non-canonical finish", Completion{Content: "hi", FinishReason: "end_turn", Usage: usage}, true, 1},
		{"tool_calls without calls", Completion{Content: "hi", FinishReason: "tool_calls", Usage: usage}, true, 1},
		{"corrupt arguments", Completion{ToolCalls: []models.ToolCall{{ID: "a", Function: models.FunctionCall{Name: "f", Arguments: `{"q"`}}}, FinishReason: "tool_calls", Usage: usage}, true, 1},
		{"no usage", Completion{Content: "hi", FinishReason: "stop"}, true, 1},
		{"bad total", Completion{Content: "hi", FinishReason: "stop", Usage: &models.Usage{PromptTokens: 3, CompletionTokens: 2, TotalTokens: 3}}, true, 1},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			ft := runFake(func(tb *fakeTB) { AssertSane(tb, tc.c, tc.expectOutput) })
			if len(ft.errors) != tc.wantErrors {
				t.Errorf("got %d errors %q, want %d", len(ft.errors), ft.errors, tc.wantErrors)
			}
		})
	}
}

func TestStreamResultGoldenForm(t *testing.T) {
	got, err := Normalize(StreamResult{Err: fmt.Errorf("boom")})
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{`"chunks": []`, `"error": "boom"`, `"assembled"`} {
		if !strings.Contains(string(got), want) {
			t.Errorf("golden form %s lacks %s", got, want)
		}
	}
}

func TestReplayServer(t *testing.T) {
	c := Case{Body: []byte(`{"ok":true}`), ContentType: "application/json"}
	r := ReplayServer(t, c)

	req, err := http.NewRequest(http.MethodPost, r.URL+"/v1beta/models/m:generateContent?key=secret&alt=sse",
		strings.NewReader(`{"model":"m","n":1.50}`))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer secret")
	req.Header.Set("X-Amz-Date", "20261028T000000Z")
	req.Header.Set("Anthropic-Version", "2023-06-01")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(resp.Body)
	_ = resp.Body.Close()
	if string(body) != `{"ok":true}` || resp.Header.Get("Content-Type") != "application/json" {
		t.Errorf("replayed %q with %q", body, resp.Header.Get("Content-Type"))
	}

	got, err := Normalize(r.Request(t))
	if err != nil {
		t.Fatal(err)
	}
	want := `{
  "body": {
    "model": "m",
    "n": 1.50
  },
  "headers": {
    "anthropic-version": "2023-06-01",
    "authorization": "REDACTED",
    "x-amz-date": "VOLATILE"
  },
  "method": "POST",
  "path": "/v1beta/models/m:generateContent",
  "query": "alt=sse&key=REDACTED"
}
`
	if string(got) != want {
		t.Errorf("recorded request =\n%s\nwant\n%s", got, want)
	}
}
