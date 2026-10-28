package main

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
)

func TestUpstreamFor(t *testing.T) {
	cases := []struct{ provider, key, name, model string }{
		{"openai", "groq@openai/gpt-oss-120b", "groq", "openai/gpt-oss-120b"},
		{"openai", "perplexity@sonar", "perplexity", "sonar"},
		{"openai", "gpt-6-sol", "openai", "gpt-6-sol"},
		{"openai", "nosuchpreset@x", "openai", "nosuchpreset@x"},
		{"openai", "azure@gpt-4.1", "openai", "azure@gpt-4.1"}, // azure is not an OpenAI-format preset
		{"openai", "groq@", "openai", "groq@"},
		// "@" is part of real IDs elsewhere (Vertex): only openai splits.
		{"anthropic", "groq@claude-x", "anthropic", "groq@claude-x"},
	}
	for _, tc := range cases {
		name, model := upstreamFor(tc.provider, tc.key)
		if name != tc.name || model != tc.model {
			t.Errorf("upstreamFor(%q, %q) = %q, %q; want %q, %q", tc.provider, tc.key, name, model, tc.name, tc.model)
		}
	}
	if got := compatSpec("groq").need; len(got) != 1 || got[0] != "GROQ_API_KEY" {
		t.Errorf("groq needs %q, want GROQ_API_KEY", got)
	}
	if got := envName("fire-works.ai"); got != "FIRE_WORKS_AI" {
		t.Errorf("envName = %q", got)
	}
}

func TestPlanCloneCompat(t *testing.T) {
	f := newFixtureTree(t)
	dir := "openai/testdata/contracts/groq%40openai%2Fgpt-oss-120b"
	f.write(dir+"/meta.json", metaJSON("openai", "groq@openai/gpt-oss-120b"))
	f.write(dir+"/tool_call/request.json", requestJSON("openai/gpt-oss-120b", false))

	pl, err := planProvider(f.root, "openai", selection{From: "groq@openai/gpt-oss-120b", As: "groq@openai/gpt-oss-240b"})
	if err != nil {
		t.Fatal(err)
	}
	j := pl.Jobs[0]
	if j.Upstream != "groq" || !strings.Contains(string(j.Body), `"model": "openai/gpt-oss-240b"`) {
		t.Errorf("clone = upstream %q body %s; want groq and the bare upstream model in request.json", j.Upstream, j.Body)
	}
	if !strings.HasSuffix(j.OutDir, "groq%40openai%2Fgpt-oss-240b/tool_call") {
		t.Errorf("OutDir = %s", j.OutDir)
	}
}

func TestRecordCompatPreset(t *testing.T) {
	const groqKey = "gsk-test-0123456789"
	var mu sync.Mutex
	var got []string
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		mu.Lock()
		got = append(got, r.URL.Path+" "+r.Header.Get("Authorization")+" "+string(body))
		mu.Unlock()
		var req struct{ Model string }
		_ = json.Unmarshal(body, &req)
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"id":"chatcmpl-1","object":"chat.completion","created":1,"model":"`+req.Model+`",`+
			`"choices":[{"index":0,"message":{"role":"assistant","content":"Hi."},"finish_reason":"stop"}],`+
			`"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}}`)
	}))
	t.Cleanup(srv.Close)

	f := newFixtureTree(t)
	f.model("openai", "gpt-a", "text_basic")
	dir := "openai/testdata/contracts/groq%40openai%2Fgpt-oss-120b"
	f.write(dir+"/meta.json", metaJSON("openai", "groq@openai/gpt-oss-120b"))
	f.write(dir+"/text_basic/request.json", requestJSON("openai/gpt-oss-120b", false))
	f.write(dir+"/text_basic/response.json", "synthetic")

	opts, err := parseFlags([]string{"-providers-dir", f.root, "-provider", "openai", "-base-url", srv.URL}, io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	var out strings.Builder
	env := map[string]string{"GROQ_API_KEY": groqKey}
	r := &recorder{opts: opts, out: &out, env: func(k string) string { return env[k] }, now: fixedNow, client: srv.Client()}
	if code := r.run(t.Context()); code != 0 {
		t.Fatalf("exit %d:\n%s", code, out.String())
	}

	if !strings.Contains(out.String(), "openai: skipped 1 cases: OPENAI_API_KEY not set") {
		t.Errorf("the native group must be skipped on its own key:\n%s", out.String())
	}
	mu.Lock()
	defer mu.Unlock()
	if len(got) != 1 || !strings.HasPrefix(got[0], "/v1/chat/completions Bearer "+groqKey+" ") ||
		!strings.Contains(got[0], `"model":"openai/gpt-oss-120b"`) {
		t.Errorf("upstream got %q; want the groq preset's path and key, and the bare upstream model", got)
	}
	if body := f.read(dir + "/text_basic/response.json"); !strings.Contains(body, `"chatcmpl-1"`) {
		t.Errorf("response.json = %s", body)
	}
	if strings.Contains(out.String(), groqKey) {
		t.Error("key in output")
	}
}
