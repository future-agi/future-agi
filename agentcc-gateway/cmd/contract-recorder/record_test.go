package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

const testKey = "sk-ant-test-0123456789"

// fakeAnthropic is a minimal Messages API. Model "claude-broken" gets a 400
// whose message echoes the API key, to check the recorder redacts it.
type fakeAnthropic struct {
	*httptest.Server
	mu     sync.Mutex
	bodies []string
}

func newFakeAnthropic(t *testing.T) *fakeAnthropic {
	f := &fakeAnthropic{}
	f.Server = httptest.NewTLSServer(http.HandlerFunc(f.serve))
	t.Cleanup(f.Close)
	return f
}

func (f *fakeAnthropic) requests() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]string(nil), f.bodies...)
}

func anthropicJSON(model string) string {
	return `{"id":"msg_01","type":"message","role":"assistant","model":"` + model + `","content":[{"type":"text","text":"Hi."}],` +
		`"stop_reason":"end_turn","stop_sequence":null,"usage":{"input_tokens":5,"output_tokens":2}}`
}

func anthropicSSE(model string) string {
	events := []string{
		`message_start`, `{"type":"message_start","message":{"id":"msg_01","type":"message","role":"assistant","model":"` + model + `","content":[],"stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":5,"output_tokens":1}}}`,
		`content_block_start`, `{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}`,
		`content_block_delta`, `{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hi."}}`,
		`content_block_stop`, `{"type":"content_block_stop","index":0}`,
		`message_delta`, `{"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":2}}`,
		`message_stop`, `{"type":"message_stop"}`,
	}
	var b strings.Builder
	for i := 0; i < len(events); i += 2 {
		b.WriteString("event: " + events[i] + "\ndata: " + events[i+1] + "\n\n")
	}
	return b.String()
}

func (f *fakeAnthropic) serve(w http.ResponseWriter, r *http.Request) {
	raw, _ := io.ReadAll(r.Body)
	f.mu.Lock()
	f.bodies = append(f.bodies, string(raw))
	f.mu.Unlock()
	if r.URL.Path != "/v1/messages" || r.Header.Get("X-Api-Key") != testKey {
		http.Error(w, `{"type":"error","error":{"type":"authentication_error","message":"bad key or path"}}`, http.StatusUnauthorized)
		return
	}
	var req struct {
		Model  string `json:"model"`
		Stream bool   `json:"stream"`
	}
	_ = json.Unmarshal(raw, &req)
	switch {
	case req.Model == "claude-broken":
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		_, _ = io.WriteString(w, `{"type":"error","error":{"type":"invalid_request_error","message":"nope for key `+testKey+`"}}`)
	case req.Stream:
		w.Header().Set("Content-Type", "text/event-stream; charset=utf-8")
		_, _ = io.WriteString(w, anthropicSSE(req.Model))
	default:
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, anthropicJSON(req.Model))
	}
}

var fixedNow = func() time.Time { return time.Date(2026, 10, 28, 23, 30, 0, 0, time.UTC) }

func runRecorder(t *testing.T, srv *fakeAnthropic, env map[string]string, args ...string) (int, string) {
	t.Helper()
	var stdout, stderr bytes.Buffer
	opts, err := parseFlags(args, &stderr)
	if err != nil {
		t.Fatalf("parseFlags(%q): %v %s", args, err, stderr.String())
	}
	r := &recorder{
		opts:   opts,
		out:    &stdout,
		env:    func(k string) string { return env[k] },
		now:    fixedNow,
		client: srv.Client(),
	}
	code := r.run(context.Background())
	return code, stdout.String()
}

var withKey = map[string]string{"ANTHROPIC_API_KEY": testKey}

func TestRecordInPlace(t *testing.T) {
	srv := newFakeAnthropic(t)
	f := newFixtureTree(t)
	f.model("anthropic", "claude-a", "stream_text", "text_basic")

	code, out := runRecorder(t, srv, withKey, "-providers-dir", f.root, "-provider", "anthropic", "-base-url", srv.URL)
	if code != 0 {
		t.Fatalf("exit %d:\n%s", code, out)
	}
	dir := "anthropic/testdata/contracts/claude-a/"
	if got := f.read(dir + "text_basic/response.json"); got != anthropicJSON("claude-a") {
		t.Errorf("response.json = %s", got)
	}
	if got := f.read(dir + "stream_text/response.sse"); got != anthropicSSE("claude-a") {
		t.Errorf("response.sse = %q", got)
	}
	meta := f.read(dir + "meta.json")
	if !strings.Contains(meta, `"source": "recorded"`) || !strings.Contains(meta, `"recorded_at": "2026-10-28"`) {
		t.Errorf("meta.json not marked recorded:\n%s", meta)
	}
	if strings.Contains(out, "warning") {
		t.Errorf("a clean recording printed warnings:\n%s", out)
	}
	// The request sent is the gateway's translation of request.json.
	for _, body := range srv.requests() {
		if !strings.Contains(body, `"model":"claude-a"`) || !strings.Contains(body, `"max_tokens":64`) {
			t.Errorf("upstream got %s", body)
		}
	}
}

func TestRecordPartialLeavesMeta(t *testing.T) {
	srv := newFakeAnthropic(t)
	f := newFixtureTree(t)
	f.model("anthropic", "claude-a", "stream_text", "text_basic")
	before := f.read("anthropic/testdata/contracts/claude-a/meta.json")

	code, out := runRecorder(t, srv, withKey, "-providers-dir", f.root, "-case", "text_basic", "-base-url", srv.URL, "-provider", "anthropic")
	if code != 0 {
		t.Fatalf("exit %d:\n%s", code, out)
	}
	if f.read("anthropic/testdata/contracts/claude-a/meta.json") != before {
		t.Error("meta.json changed although only 1 of 2 cases was recorded")
	}
	if !strings.Contains(out, "1 of its 2 cases were recorded") {
		t.Errorf("output does not explain the unchanged meta:\n%s", out)
	}
}

func TestRecordCloneToOutIsDiscoverable(t *testing.T) {
	const newModel = "claude-b:1"
	srv := newFakeAnthropic(t)
	f := newFixtureTree(t)
	f.model("anthropic", "claude-a", "stream_text", "text_basic")
	out := t.TempDir()

	code, log := runRecorder(t, srv, withKey, "-providers-dir", f.root, "-provider", "anthropic",
		"-from", "claude-a", "-as", newModel, "-out", out, "-base-url", srv.URL)
	if code != 0 {
		t.Fatalf("exit %d:\n%s", code, log)
	}
	for _, body := range srv.requests() {
		if !strings.Contains(body, `"model":"claude-b:1"`) {
			t.Errorf("upstream got %s, want the -as model", body)
		}
	}

	// What it wrote is exactly what live mode reads.
	t.Setenv(contracttest.LiveEnv, out)
	cases := contracttest.Discover(t, "anthropic")
	if len(cases) != 2 {
		t.Fatalf("live Discover found %d cases, want 2", len(cases))
	}
	for _, c := range cases {
		if c.Model != newModel || c.Meta.Model != newModel || c.Request.Model != newModel || c.Meta.Source != "recorded" {
			t.Errorf("%s: model %q / meta %q / request %q / source %q", c.ID(), c.Model, c.Meta.Model, c.Request.Model, c.Meta.Source)
		}
		if want := filepath.Join(out, "anthropic", "claude-b%3A1", c.Name); c.Dir != want {
			t.Errorf("case dir = %s, want %s", c.Dir, want)
		}
		if len(c.Meta.AllowedUnknownFields) != 1 {
			t.Errorf("cloned meta lost the -from model's allowlist: %+v", c.Meta.AllowedUnknownFields)
		}
	}
}

func TestRecordSkipsWithoutCredentials(t *testing.T) {
	srv := newFakeAnthropic(t)
	f := newFixtureTree(t)
	f.model("anthropic", "claude-a", "text_basic")
	f.model("bedrock", "us.amazon.nova-2-lite-v1:0", "text_basic")
	f.write("speech/testdata/contracts/m/c/request.json", requestJSON("m", false))

	code, out := runRecorder(t, srv, nil, "-providers-dir", f.root)
	if code != 0 {
		t.Fatalf("missing keys must not fail: exit %d:\n%s", code, out)
	}
	for _, want := range []string{
		"anthropic: skipped 1 cases: ANTHROPIC_API_KEY not set",
		"bedrock: skipped 1 cases: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY not set",
		"speech: skipped: the recorder has no upstream configuration for it",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("output lacks %q:\n%s", want, out)
		}
	}
	if len(srv.requests()) != 0 || f.read("anthropic/testdata/contracts/claude-a/text_basic/response.json") != "synthetic" {
		t.Error("a skipped provider was called or written")
	}
}

func TestRecordUpstreamErrorIsNotSaved(t *testing.T) {
	srv := newFakeAnthropic(t)
	f := newFixtureTree(t)
	f.model("anthropic", "claude-broken", "text_basic")
	f.model("anthropic", "claude-a", "text_basic")

	code, out := runRecorder(t, srv, withKey, "-providers-dir", f.root, "-provider", "anthropic", "-base-url", srv.URL)
	if code != 1 {
		t.Errorf("exit %d, want 1 for an upstream error", code)
	}
	if !strings.Contains(out, "FAILED: upstream answered 400") || !strings.Contains(out, "REDACTED") || strings.Contains(out, testKey) {
		t.Errorf("want a redacted failure report:\n%s", out)
	}
	if got := f.read("anthropic/testdata/contracts/claude-broken/text_basic/response.json"); got != "synthetic" {
		t.Errorf("an error body was saved as a fixture: %s", got)
	}
	if !strings.Contains(f.read("anthropic/testdata/contracts/claude-broken/meta.json"), `"synthetic"`) {
		t.Error("meta.json of a failed model was marked recorded")
	}
	if got := f.read("anthropic/testdata/contracts/claude-a/text_basic/response.json"); got != anthropicJSON("claude-a") {
		t.Errorf("one failing model must not stop the others: %s", got)
	}
}

func TestRecordAppliesPatch(t *testing.T) {
	srv := newFakeAnthropic(t)
	f := newFixtureTree(t)
	f.model("anthropic", "claude-a", "text_basic")
	f.write("anthropic/testdata/contracts/claude-a/text_basic/upstream_patch.json", `{"thinking":{"type":"enabled","budget_tokens":1024}}`)

	code, out := runRecorder(t, srv, withKey, "-providers-dir", f.root, "-provider", "anthropic", "-base-url", srv.URL)
	if code != 0 {
		t.Fatalf("exit %d:\n%s", code, out)
	}
	reqs := srv.requests()
	if len(reqs) != 1 || !strings.Contains(reqs[0], `"thinking":{"budget_tokens":1024,"type":"enabled"}`) {
		t.Errorf("upstream got %q, want the patch applied", reqs)
	}
}

func TestRecordDryRunTouchesNothing(t *testing.T) {
	srv := newFakeAnthropic(t)
	f := newFixtureTree(t)
	f.model("anthropic", "claude-a", "text_basic")
	out := t.TempDir()

	code, log := runRecorder(t, srv, withKey, "-providers-dir", f.root, "-dry-run", "-out", out, "-base-url", srv.URL, "-provider", "anthropic")
	if code != 0 {
		t.Fatalf("exit %d:\n%s", code, log)
	}
	if len(srv.requests()) != 0 {
		t.Error("dry run called upstream")
	}
	if !strings.Contains(log, filepath.Join(out, "anthropic", "claude-a", "text_basic")+": response.json") {
		t.Errorf("dry run does not show the destination:\n%s", log)
	}
}

func TestParseFlagsRejects(t *testing.T) {
	dir := t.TempDir()
	for _, args := range [][]string{
		{"-from", "a"},
		{"-from", "a", "-as", "a", "-provider", "anthropic"},
		{"-from", "a", "-as", "b"},
		{"-from", "a", "-as", "b", "-provider", "anthropic,gemini"},
		{"-from", "a", "-as", "b", "-provider", "anthropic", "-model", "x"},
		{"-provider", "nope"},
		{"-base-url", "https://x"},
		{"-provider", "anthropic", "-base-url", "ftp://x"},
		{"-timeout", "0s"},
		{"stray"},
	} {
		if _, err := parseFlags(append(args, "-providers-dir", dir), io.Discard); err == nil {
			t.Errorf("parseFlags(%q): want an error", args)
		}
	}
	if _, err := parseFlags([]string{"-providers-dir", filepath.Join(dir, "missing")}, io.Discard); err == nil {
		t.Error("missing -providers-dir: want an error")
	}
}
