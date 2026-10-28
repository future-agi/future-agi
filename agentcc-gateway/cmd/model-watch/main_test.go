package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/providers/contractwatch"
)

// TestMain refuses any request to a non-loopback host, so a test that
// forgot to inject its client fails instead of calling a real provider.
func TestMain(m *testing.M) {
	guard := roundTripFunc(func(r *http.Request) (*http.Response, error) {
		if ip := net.ParseIP(r.URL.Hostname()); ip != nil && ip.IsLoopback() {
			return (&http.Transport{}).RoundTrip(r)
		}
		return nil, fmt.Errorf("model-watch tests: network access to %s refused", r.URL.Host)
	})
	http.DefaultTransport = guard
	http.DefaultClient = &http.Client{Transport: guard}
	os.Exit(m.Run())
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

const (
	repoRegistry  = "../../internal/providers/contractwatch/known_models.json"
	repoProviders = "../../internal/providers"
)

type result struct {
	code           int
	stdout, stderr string
}

func runWith(t *testing.T, env map[string]string, client *http.Client, listers map[string]contractwatch.Lister, args ...string) result {
	t.Helper()
	var out, errb bytes.Buffer
	if client == nil {
		client = &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
			t.Errorf("unexpected request to %s", r.URL)
			return nil, fmt.Errorf("refused")
		})}
	}
	code := run(args, deps{
		stdout:  &out,
		stderr:  &errb,
		env:     func(k string) string { return env[k] },
		client:  client,
		now:     func() time.Time { return time.Date(2026, 10, 29, 4, 41, 0, 0, time.UTC) },
		listers: listers,
	})
	return result{code, out.String(), errb.String()}
}

func TestOfflineOnTheRepo(t *testing.T) {
	r := runWith(t, nil, nil, nil, "-offline", "-registry", repoRegistry, "-providers-dir", repoProviders)
	if r.code != exitOK || !strings.Contains(r.stdout, "offline: ok") {
		t.Fatalf("exit %d\n%s%s", r.code, r.stdout, r.stderr)
	}
}

// TestCheckWithoutKeys: with no credentials every provider is skipped with
// the variable it needs, nothing is contacted, and the plan is [].
func TestCheckWithoutKeys(t *testing.T) {
	plan := filepath.Join(t.TempDir(), "out", "plan.json")
	r := runWith(t, nil, nil, nil, "-check", "-registry", repoRegistry, "-providers-dir", repoProviders, "-plan-out", plan)
	if r.code != exitOK {
		t.Fatalf("exit %d\n%s%s", r.code, r.stdout, r.stderr)
	}
	for _, want := range []string{
		"anthropic: skipped: ANTHROPIC_API_KEY not set",
		"openai: skipped: OPENAI_API_KEY not set",
		"gemini: skipped: GEMINI_API_KEY not set",
		"cohere: skipped: COHERE_API_KEY not set",
		"bedrock: skipped: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY not set",
		"groq: skipped: GROQ_API_KEY not set",
		"perplexity: skipped: PERPLEXITY_API_KEY not set",
		"azure: not listed:",
		"check: 0 providers listed, 0 new models, 0 stale",
	} {
		if !strings.Contains(r.stdout, want) {
			t.Errorf("output lacks %q:\n%s", want, r.stdout)
		}
	}
	raw, err := os.ReadFile(plan)
	if err != nil || strings.TrimSpace(string(raw)) != "[]" {
		t.Errorf("plan = %q, %v; want []", raw, err)
	}
}

// tree copies a small registry and fixture tree into a temp directory.
func tree(t *testing.T, registry string, dirs map[string][]string) (reg, providers string) {
	t.Helper()
	root := t.TempDir()
	reg = filepath.Join(root, "known_models.json")
	if err := os.WriteFile(reg, []byte(registry), 0o644); err != nil {
		t.Fatal(err)
	}
	providers = filepath.Join(root, "providers")
	for pd, cases := range dirs {
		p, d, _ := strings.Cut(pd, "/")
		md := filepath.Join(providers, p, "testdata", "contracts", d)
		for _, c := range cases {
			if err := os.MkdirAll(filepath.Join(md, c), 0o755); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(md, c, "request.json"), []byte(`{"model":"x"}`), 0o644); err != nil {
				t.Fatal(err)
			}
		}
		if err := os.WriteFile(filepath.Join(md, "meta.json"), []byte(`{}`), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return reg, providers
}

const smallRegistry = `{
  "schema_version": 1,
  "about": "test",
  "providers": {
    "openai": {"list": "api", "ignore_rules": [{"match": "*embedding*", "reason": "embeddings"}], "models": [
      {"id": "gpt-5.6-sol", "status": "covered"},
      {"id": "gpt-6-sol", "status": "covered"}
    ]}
  }
}`

var smallTree = map[string][]string{
	"openai/gpt-5.6-sol": {"text_basic"},
	"openai/gpt-6-sol":   {"text_basic", "stream_text", "tool_call", "logprobs", "moderation"},
}

// TestCheckFindsNewModel drives the real OpenAI lister: the client sends
// every request to a local fake of api.openai.com, after checking that the
// request was addressed to the real host with the key.
func TestCheckFindsNewModel(t *testing.T) {
	reg, providers := tree(t, smallRegistry, smallTree)
	var mu sync.Mutex
	var hosts []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer sk-test" || r.URL.Path != "/v1/models" {
			http.Error(w, "no", http.StatusUnauthorized)
			return
		}
		_, _ = w.Write([]byte(`{"object":"list","data":[
			{"id":"gpt-6-sol","created":1780000000},{"id":"gpt-5.6-sol","created":1770000000},
			{"id":"gpt-6.1-sol","created":1790000000},{"id":"text-embedding-4","created":1790000000}]}`))
	}))
	t.Cleanup(srv.Close)
	target, _ := url.Parse(srv.URL)
	client := &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
		mu.Lock()
		hosts = append(hosts, r.URL.Scheme+"://"+r.URL.Host)
		mu.Unlock()
		r = r.Clone(r.Context())
		r.URL.Scheme, r.URL.Host = target.Scheme, target.Host
		return http.DefaultTransport.RoundTrip(r)
	})}

	dir := t.TempDir()
	plan, summary := filepath.Join(dir, "plan.json"), filepath.Join(dir, "summary.md")
	r := runWith(t, map[string]string{"OPENAI_API_KEY": "sk-test"}, client, nil,
		"-check", "-registry", reg, "-providers-dir", providers, "-plan-out", plan, "-summary-out", summary, "-max-cases", "2")
	if r.code != exitFindings {
		t.Fatalf("exit %d, want %d (a NEW model)\n%s%s", r.code, exitFindings, r.stdout, r.stderr)
	}
	if strings.Join(hosts, ",") != "https://api.openai.com" {
		t.Errorf("requests went to %v", hosts)
	}
	if !strings.Contains(r.stdout, "NEW openai gpt-6.1-sol: template gpt-6-sol") {
		t.Errorf("stdout:\n%s", r.stdout)
	}
	var entries []map[string]any
	raw, _ := os.ReadFile(plan)
	if err := json.Unmarshal(raw, &entries); err != nil || len(entries) != 1 {
		t.Fatalf("plan: %s %v", raw, err)
	}
	e := entries[0]
	if e["provider"] != "openai" || e["new_model"] != "gpt-6.1-sol" || e["template_model"] != "gpt-6-sol" || e["reason"] == "" || e["record"] != true {
		t.Errorf("plan entry: %v", e)
	}
	rec := e["recorder"].(map[string]any)
	if rec["from"] != "gpt-6-sol" || rec["as"] != "gpt-6.1-sol" || fmt.Sprint(rec["cases"]) != "[text_basic stream_text]" {
		t.Errorf("recorder args: %v", rec)
	}
	md, _ := os.ReadFile(summary)
	if !strings.Contains(string(md), "## New models (1)") {
		t.Errorf("summary:\n%s", md)
	}
}

func TestUpdateRegistry(t *testing.T) {
	reg, providers := tree(t, smallRegistry, smallTree)
	listers := map[string]contractwatch.Lister{
		"openai": contractwatch.StaticLister{Models: []string{"gpt-6-sol", "gpt-5.6-sol", "gpt-6.1-sol", "gpt-6.1-terra"}},
	}
	env := map[string]string{"OPENAI_API_KEY": "k"}
	r := runWith(t, env, nil, listers, "-update-registry", "-registry", reg, "-providers-dir", providers)
	if r.code != exitOK || !strings.Contains(r.stdout, "added 2 models") {
		t.Fatalf("exit %d\n%s%s", r.code, r.stdout, r.stderr)
	}
	raw, _ := os.ReadFile(reg)
	if !strings.Contains(string(raw), `{"id": "gpt-6.1-sol", "status": "ignored", "reason": "TODO(triage): listed upstream on 2026-10-29`) {
		t.Errorf("registry after update:\n%s", raw)
	}
	// Triage pending: -check is clean again, -offline (what CI runs) is not.
	if r := runWith(t, env, nil, listers, "-check", "-registry", reg, "-providers-dir", providers); r.code != exitOK {
		t.Errorf("-check after -update-registry: exit %d\n%s", r.code, r.stdout)
	}
	r = runWith(t, nil, nil, nil, "-offline", "-registry", reg, "-providers-dir", providers)
	if r.code != exitFindings || !strings.Contains(r.stdout, "TRIAGE openai/gpt-6.1-sol") {
		t.Errorf("-offline with placeholders: exit %d\n%s", r.code, r.stdout)
	}
	// Nothing new: the registry is left alone.
	before, _ := os.ReadFile(reg)
	if r := runWith(t, env, nil, listers, "-update-registry", "-registry", reg, "-providers-dir", providers); !strings.Contains(r.stdout, "nothing new") {
		t.Errorf("second update: %s", r.stdout)
	}
	if after, _ := os.ReadFile(reg); !bytes.Equal(before, after) {
		t.Error("a run with nothing new rewrote the registry")
	}
}

func TestOfflineFindsStale(t *testing.T) {
	reg, providers := tree(t, smallRegistry, map[string][]string{
		"openai/gpt-6-sol": {"text_basic"},
		"openai/gpt-7":     {"text_basic"},
	})
	r := runWith(t, nil, nil, nil, "-offline", "-registry", reg, "-providers-dir", providers)
	if r.code != exitFindings || !strings.Contains(r.stdout, "covered_without_fixtures") || !strings.Contains(r.stdout, "fixtures_without_entry") {
		t.Fatalf("exit %d\n%s", r.code, r.stdout)
	}
}

func TestNewest(t *testing.T) {
	out := filepath.Join(t.TempDir(), "newest.json")
	env := map[string]string{"ANTHROPIC_API_KEY": "k", "GEMINI_API_KEY": "k", "OPENAI_API_KEY": "k"}
	r := runWith(t, env, nil, nil, "-newest", "-registry", repoRegistry, "-providers-dir", repoProviders,
		"-plan-out", out, "-max-record", "3", "-max-cases", "2", "-rotate", "1")
	if r.code != exitOK {
		t.Fatalf("exit %d\n%s%s", r.code, r.stdout, r.stderr)
	}
	if !strings.Contains(r.stdout, "cohere: skipped: COHERE_API_KEY not set") || !strings.Contains(r.stdout, "marked (*) to re-record") {
		t.Errorf("stdout:\n%s", r.stdout)
	}
	var entries []contractwatch.NewestEntry
	raw, _ := os.ReadFile(out)
	if err := json.Unmarshal(raw, &entries); err != nil {
		t.Fatal(err)
	}
	marked := 0
	providers := map[string]bool{}
	for _, e := range entries {
		providers[e.Provider] = true
		if e.Record {
			marked++
			if len(e.Recorder.Cases) == 0 || len(e.Recorder.Cases) > 2 || e.Recorder.Model == "" {
				t.Errorf("entry %+v", e)
			}
		}
	}
	if marked != 3 || len(providers) != 3 {
		t.Errorf("marked %d of %d entries from %v", marked, len(entries), providers)
	}
}

func TestFmt(t *testing.T) {
	var pretty bytes.Buffer
	if err := json.Indent(&pretty, []byte(smallRegistry), "", "    "); err != nil {
		t.Fatal(err)
	}
	reg := filepath.Join(t.TempDir(), "known_models.json")
	if err := os.WriteFile(reg, pretty.Bytes(), 0o644); err != nil {
		t.Fatal(err)
	}
	if r := runWith(t, nil, nil, nil, "-fmt", "-registry", reg); r.code != exitOK {
		t.Fatalf("exit %d %s", r.code, r.stderr)
	}
	first, _ := os.ReadFile(reg)
	if bytes.Equal(first, pretty.Bytes()) || !bytes.Contains(first, []byte(`{"id": "gpt-6-sol", "status": "covered"}`)) {
		t.Fatalf("not canonicalised:\n%s", first)
	}
	runWith(t, nil, nil, nil, "-fmt", "-registry", reg)
	if second, _ := os.ReadFile(reg); !bytes.Equal(first, second) {
		t.Error("-fmt is not idempotent")
	}
}

func TestReport(t *testing.T) {
	dir := t.TempDir()
	plan := []contractwatch.PlanEntry{{
		Provider: "openai", NewModel: "gpt-6.1-sol", TemplateModel: "gpt-6-sol", Reason: "family gpt", Record: true,
		Recorder: &contractwatch.RecorderArgs{Provider: "openai", From: "gpt-6-sol", As: "gpt-6.1-sol", Cases: []string{"text_basic"}},
	}}
	raw, _ := json.Marshal(plan)
	planPath := filepath.Join(dir, "plan.json")
	if err := os.WriteFile(planPath, raw, 0o644); err != nil {
		t.Fatal(err)
	}
	results := filepath.Join(dir, "results.jsonl")
	if err := os.WriteFile(results, []byte(`{"kind":"new","provider":"openai","model":"gpt-6.1-sol","template":"gpt-6-sol","record_exit":0,"test_exit":0,"recorded":1}`+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	summary, issues := filepath.Join(dir, "results.md"), filepath.Join(dir, "issues.json")
	r := runWith(t, nil, nil, nil, "-report", "-plan", planPath, "-results", results, "-summary-out", summary, "-issues-out", issues,
		"-run-url", "https://github.com/o/r/actions/runs/9", "-artifact", "model-watch-9")
	if r.code != exitOK || !strings.Contains(r.stdout, "report: 1 new models, 1 recordings, 0 not passing") {
		t.Fatalf("exit %d\n%s%s", r.code, r.stdout, r.stderr)
	}
	var got []contractwatch.Issue
	raw, _ = os.ReadFile(issues)
	if err := json.Unmarshal(raw, &got); err != nil || len(got) != 1 || !strings.Contains(got[0].Body, "live contract test **passed**") {
		t.Fatalf("issues: %s %v", raw, err)
	}
	md, _ := os.ReadFile(summary)
	if !strings.Contains(string(md), "| openai | `gpt-6.1-sol` | `gpt-6-sol` | 1 | ok | passed |") {
		t.Errorf("summary:\n%s", md)
	}
}

func TestUsageErrors(t *testing.T) {
	for _, args := range [][]string{
		{},
		{"-check", "-offline"},
		{"-check", "-plan", "x"},
		{"-offline", "-plan-out", "x"},
		{"-report"},
		{"-check", "-max-record", "-1"},
		{"-check", "-providers-dir", "/does/not/exist"},
		{"-check", "-registry", "/does/not/exist", "-providers-dir", repoProviders},
		{"-check", "-registry", repoRegistry, "-providers-dir", repoProviders, "-provider", "deepinfra"},
		{"-check", "extra"},
	} {
		if r := runWith(t, nil, nil, nil, args...); r.code != exitUsage {
			t.Errorf("%v: exit %d, want %d\n%s%s", args, r.code, exitUsage, r.stdout, r.stderr)
		}
	}
	if r := runWith(t, nil, nil, nil, "-h"); r.code != exitOK || !strings.Contains(r.stderr, "usage: go run ./cmd/model-watch") {
		t.Errorf("-h: exit %d\n%s", r.code, r.stderr)
	}
}

// The real main wires a client that never follows redirects, so a key sent
// to a provider cannot be replayed to wherever it redirects.
func TestNoRedirects(t *testing.T) {
	srv := httptest.NewServer(http.RedirectHandler("http://127.0.0.1:1/elsewhere", http.StatusFound))
	t.Cleanup(srv.Close)
	c := &http.Client{CheckRedirect: noRedirects}
	req, _ := http.NewRequestWithContext(context.Background(), http.MethodGet, srv.URL, nil)
	resp, err := c.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusFound {
		t.Errorf("followed a redirect: %d", resp.StatusCode)
	}
}
