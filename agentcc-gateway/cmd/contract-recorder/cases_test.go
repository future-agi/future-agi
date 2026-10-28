package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

const badRequestBody = `{"type":"error","error":{"type":"invalid_request_error","message":"max_tokens: 1000000 > 64000, the maximum allowed (key sk-ant-****6789)"},"request_id":"req_live"}`

// anthropicUpstream is a fake Messages API: a request with max_tokens
// 1000000 gets a 400 with retry, rate-limit and other headers (one carrying
// the API key, one a bearer token); "max_tokens": 7 gets a 200, which no
// error case should accept; anything else a normal answer.
func anthropicUpstream(t *testing.T) *fakeUpstream {
	srv := newFakeUpstream(t)
	srv.respond = func(w http.ResponseWriter, r *http.Request, body []byte) bool {
		var req struct {
			Model     string `json:"model"`
			MaxTokens int    `json:"max_tokens"`
			Stream    bool   `json:"stream"`
		}
		_ = json.Unmarshal(body, &req)
		h := w.Header()
		h.Set("Request-Id", "req_live")
		h.Set("Anthropic-Ratelimit-Requests-Remaining", "49")
		h.Set("Anthropic-Organization-Id", "org-secret-tenant")
		h.Set("Set-Cookie", "session=abc")
		switch {
		case req.MaxTokens == 1000000:
			h.Set("Retry-After", "0")
			h.Set("X-Ratelimit-Debug", "key="+testKey)
			h.Set("X-Request-Id", "Bearer abcdefghijklmnop")
			h.Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusBadRequest)
			_, _ = io.WriteString(w, badRequestBody)
		case req.Stream:
			h.Set("Content-Type", "text/event-stream; charset=utf-8")
			_, _ = io.WriteString(w, anthropicSSE(req.Model))
		default:
			h.Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, anthropicJSON(req.Model))
		}
		return true
	}
	return srv
}

func overLimitRequest(model string) string {
	return `{"model":"` + model + `","messages":[{"role":"user","content":"Hi"}],"max_tokens":1000000}`
}

// errorCases adds to model an error case the fake upstream induces
// (error_invalid, 400, recordable, with a sanity gap) and one it cannot
// (error_rate_limit, 429, not recordable).
func (f *fixtureTree) errorCases(provider, model string) {
	dir := provider + "/testdata/contracts/" + contracttest.ModelDir(model) + "/"
	f.write(dir+"error_invalid/request.json", overLimitRequest(model))
	f.write(dir+"error_invalid/response.json", `{"type":"error","error":{"type":"invalid_request_error","message":"synthetic"}}`)
	f.write(dir+"error_invalid/case.json", `{"status": 400, "headers": {"Request-Id": "req_synthetic", "x-made-up": "1"},
		"notes": "Synthetic; recordable with an over-limit max_tokens.",
		"sanity_gaps": [{"failure": "request id", "reason": "known gap X2: dropped"}]}`)
	f.write(dir+"error_rate_limit/request.json", requestJSON(model, false))
	f.write(dir+"error_rate_limit/response.json", `{"type":"error","error":{"type":"rate_limit_error","message":"synthetic"}}`)
	f.write(dir+"error_rate_limit/case.json", `{"status": 429, "headers": {"retry-after": "3"}, "recordable": false, "notes": "cannot induce a 429"}`)
}

func readSpec(t *testing.T, path string) contracttest.CaseSpec {
	t.Helper()
	s, err := contracttest.ReadCaseSpec(filepath.Dir(path))
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func TestRecordErrorCaseInPlace(t *testing.T) {
	srv := anthropicUpstream(t)
	f := newFixtureTree(t)
	f.model("anthropic", "claude-a", "text_basic")
	f.errorCases("anthropic", "claude-a")
	dir := "anthropic/testdata/contracts/claude-a/"
	rateBefore := f.read(dir + "error_rate_limit/case.json")

	code, out := recordAgainst(t, srv.Server, withKey, "-providers-dir", f.root, "-provider", "anthropic", "-base-url", srv.URL)
	if code != 0 {
		t.Fatalf("exit %d:\n%s", code, out)
	}
	if n := len(srv.received()); n != 2 {
		t.Errorf("upstream got %d requests, want 2 (the not-recordable case is never sent)", n)
	}
	if !strings.Contains(out, `claude-a/error_rate_limit: skipped: case.json says "recordable": false`) {
		t.Errorf("no skip line for the not-recordable case:\n%s", out)
	}

	body := f.read(dir + "error_invalid/response.json")
	if !strings.Contains(body, `"req_live"`) || strings.Contains(body, "sk-ant-****6789") || !strings.Contains(body, "REDACTED") {
		t.Errorf("error body = %s, want the live body with the masked key scrubbed", body)
	}
	spec := readSpec(t, filepath.Join(f.root, dir, "error_invalid", "case.json"))
	wantHeaders := map[string]string{
		"Request-Id":                             "req_live", // the committed spelling is kept
		"retry-after":                            "0",
		"anthropic-ratelimit-requests-remaining": "49",
		"x-ratelimit-debug":                      "key=REDACTED",
		"x-request-id":                           "REDACTED",
	}
	if !reflect.DeepEqual(spec.Headers, wantHeaders) {
		t.Errorf("headers = %v\nwant %v (whitelisted, redacted; no organization ID, cookie or made-up header)", spec.Headers, wantHeaders)
	}
	if spec.Status != 400 || len(spec.SanityGaps) != 1 || !strings.HasPrefix(spec.Notes, "Synthetic;") {
		t.Errorf("case.json lost what the recording does not replace: %+v", spec)
	}
	if strings.Contains(out, testKey) || strings.Contains(f.read(dir+"error_invalid/case.json"), testKey) {
		t.Error("the API key leaked")
	}

	if f.read(dir+"error_rate_limit/case.json") != rateBefore ||
		f.read(dir+"error_rate_limit/response.json") != `{"type":"error","error":{"type":"rate_limit_error","message":"synthetic"}}` {
		t.Error("the not-recordable case was changed")
	}
	if meta := f.read(dir + "meta.json"); !strings.Contains(meta, `"source": "recorded"`) {
		t.Errorf("every recordable case was recorded, so meta.json should say recorded:\n%s", meta)
	}
	if !strings.Contains(out, "1 not-recordable cases stay synthetic") {
		t.Errorf("output does not say the not-recordable case stays synthetic:\n%s", out)
	}
}

func TestRecordStatusMismatchIsNotSaved(t *testing.T) {
	srv := anthropicUpstream(t)
	f := newFixtureTree(t)
	dir := "anthropic/testdata/contracts/claude-a/"
	f.write(dir+"meta.json", metaJSON("anthropic", "claude-a"))
	// Expects a 400, gets a 200.
	f.write(dir+"error_gone/request.json", requestJSON("claude-a", false))
	f.write(dir+"error_gone/response.json", "synthetic")
	f.write(dir+"error_gone/case.json", `{"status": 400}`)
	// Expects a 429, gets a 400.
	f.write(dir+"error_other/request.json", overLimitRequest("claude-a"))
	f.write(dir+"error_other/response.json", "synthetic")
	f.write(dir+"error_other/case.json", `{"status": 429}`)
	// Expects a 200, gets a 400.
	f.write(dir+"text_basic/request.json", overLimitRequest("claude-a"))
	f.write(dir+"text_basic/response.json", "synthetic")

	code, out := recordAgainst(t, srv.Server, withKey, "-providers-dir", f.root, "-provider", "anthropic", "-base-url", srv.URL)
	if code != 1 {
		t.Errorf("exit %d, want 1", code)
	}
	for _, want := range []string{
		"claude-a/error_gone: FAILED: upstream answered 200 application/json, but case.json expects 400 (not saved)",
		"claude-a/error_other: FAILED: upstream answered 400 application/json, but case.json expects 429 (not saved)",
		"claude-a/text_basic: FAILED: upstream answered 400 application/json (not saved; a 2xx case cannot become an error case)",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("output lacks %q:\n%s", want, out)
		}
	}
	for _, c := range []string{"error_gone", "error_other", "text_basic"} {
		if got := f.read(dir + c + "/response.json"); got != "synthetic" {
			t.Errorf("%s/response.json was overwritten: %s", c, got)
		}
	}
	if got := f.read(dir + "error_gone/case.json"); got != `{"status": 400}` {
		t.Errorf("error_gone/case.json changed: %s", got)
	}
	if strings.Contains(out, testKey) || strings.Contains(out, "sk-ant-****6789") {
		t.Errorf("a credential reached the output:\n%s", out)
	}
}

func TestRecordErrorCaseToOutIsDiscoverable(t *testing.T) {
	srv := anthropicUpstream(t)
	f := newFixtureTree(t)
	f.model("anthropic", "claude-a", "text_basic")
	f.errorCases("anthropic", "claude-a")
	out := t.TempDir()

	code, log := recordAgainst(t, srv.Server, withKey, "-providers-dir", f.root, "-provider", "anthropic", "-base-url", srv.URL, "-out", out)
	if code != 0 {
		t.Fatalf("exit %d:\n%s", code, log)
	}
	live := filepath.Join(out, "anthropic", "claude-a")
	for _, absent := range []string{"meta.json", "text_basic/case.json", "error_rate_limit"} {
		if _, err := os.Stat(filepath.Join(live, absent)); err == nil {
			t.Errorf("%s was written; live mode takes it from the repo (or ignores the case)", absent)
		}
	}

	// Live mode reads exactly this: the error case's status and headers are
	// the live ones, its notes and sanity gaps the repo's.
	t.Chdir(filepath.Join(f.root, "anthropic"))
	t.Setenv(contracttest.LiveEnv, out)
	cases := contracttest.Discover(t, "anthropic")
	if len(cases) != 2 {
		t.Fatalf("live Discover found %d cases, want 2", len(cases))
	}
	for _, c := range cases {
		switch c.Name {
		case "error_invalid":
			if c.Status != 400 || c.Headers["Request-Id"] != "req_live" || c.Headers["x-made-up"] != "" ||
				len(c.SanityGaps) != 1 || !strings.Contains(string(c.Body), "req_live") || !c.Live {
				t.Errorf("live error case = %+v", c)
			}
		case "text_basic":
			if c.Status != 200 || c.IsError() || !c.Live {
				t.Errorf("live 2xx case = %+v", c)
			}
		default:
			t.Errorf("unexpected live case %s", c.Name)
		}
	}
}

func TestRecordCloneInPlace(t *testing.T) {
	srv := newFakeUpstream(t)
	srv.respond = func(w http.ResponseWriter, r *http.Request, body []byte) bool {
		if !bytes.Contains(body, []byte(`"max_tokens":1000000`)) {
			return false
		}
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Request-Id", "req_clone")
		w.WriteHeader(http.StatusBadRequest)
		_, _ = io.WriteString(w, `{"error":{"message":"max_tokens is too large","type":"invalid_request_error","param":"max_tokens","code":null}}`)
		return true
	}
	f := newFixtureTree(t)
	src := "openai/testdata/contracts/gpt-a/"
	f.model("openai", "gpt-a", "text_basic")
	f.write(src+"text_basic/response.json", "{}") // Discover reads gpt-a too
	f.errorCases("openai", "gpt-a")
	f.endpointCase("openai", "gpt-a", "transcribe", "transcription", `{"model":"gpt-a","file":"audio.mp3"}`,
		map[string]string{"audio.mp3": "ID3-audio", "response.json": `{"text":"synthetic"}`})
	// The source holds JSON, the new model answers text: a clone's file
	// follows the Content-Type.
	f.endpointCase("openai", "gpt-a", "transcribe_text", "transcription", `{"model":"gpt-a","file":"audio.mp3","response_format":"text"}`,
		map[string]string{"audio.mp3": "ID3-audio", "response.json": `{"text":"synthetic"}`})
	f.endpointCase("openai", "gpt-a", "list_assistants", "assistants", `{"method":"GET","path":"/v1/assistants","query":{"limit":["1"]}}`,
		map[string]string{"response.json": `{"object":"list","data":[]}`})
	env := map[string]string{"OPENAI_API_KEY": "sk-openai-test-0123456789"}

	code, out := recordAgainst(t, srv.Server, env, "-providers-dir", f.root, "-provider", "openai",
		"-from", "gpt-a", "-as", "gpt-b", "-base-url", srv.URL)
	if code != 0 {
		t.Fatalf("exit %d:\n%s", code, out)
	}
	if n := len(srv.received()); n != 5 {
		t.Errorf("upstream got %d requests, want 5 (not the not-recordable case)", n)
	}
	if got := f.read("openai/testdata/contracts/gpt-b/transcribe_text/response.txt"); got != "Hello there.\n" {
		t.Errorf("clone of a JSON case answered with text = %q, want response.txt", got)
	}
	dst := "openai/testdata/contracts/gpt-b/"
	var meta contracttest.Meta
	if err := json.Unmarshal([]byte(f.read(dst+"meta.json")), &meta); err != nil {
		t.Fatal(err)
	}
	if meta.ClonedFrom != "gpt-a" || meta.Model != "gpt-b" || meta.Source != "recorded" || len(meta.AllowedUnknownFields) != 1 ||
		!strings.Contains(meta.Notes, "1 not-recordable cases are gpt-a's synthetic bodies") {
		t.Errorf("cloned meta = %s", f.read(dst+"meta.json"))
	}
	if got := f.read(dst + "transcribe/audio.mp3"); got != "ID3-audio" {
		t.Errorf("upload not copied: %q", got)
	}
	if _, err := os.Stat(filepath.Join(f.root, dst, "transcribe", "speech.wav")); err == nil {
		t.Error("the default upload was written")
	}
	if got := f.read(dst + "transcribe/case.json"); got != f.read(src+"transcribe/case.json") {
		t.Errorf("case.json not copied as it was: %s", got)
	}
	if !strings.Contains(f.read(dst+"transcribe/request.json"), `"model": "gpt-b"`) {
		t.Error("cloned request.json does not name the new model")
	}
	if got, want := f.read(dst+"list_assistants/request.json"), f.read(src+"list_assistants/request.json"); got != want {
		t.Errorf("a passthrough request.json has no top-level model and is copied as it is: %s", got)
	}
	if c := srv.find(t, "GET", "/v1/assistants"); c.Query != "limit=1" {
		t.Errorf("assistants clone sent query %q", c.Query)
	}
	errSpec := readSpec(t, filepath.Join(f.root, dst, "error_invalid", "case.json"))
	if errSpec.Status != 400 || errSpec.Headers["x-request-id"] != "req_clone" || errSpec.Headers["Request-Id"] != "" {
		t.Errorf("cloned error case.json = %+v, want the status and this model's headers", errSpec)
	}

	// The not-recordable case is copied, still synthetic, and says so.
	if got, want := f.read(dst+"error_rate_limit/response.json"), f.read(src+"error_rate_limit/response.json"); got != want {
		t.Errorf("synthetic body = %s, want the source's %s", got, want)
	}
	rate := readSpec(t, filepath.Join(f.root, dst, "error_rate_limit", "case.json"))
	if rate.CanRecord() || rate.Status != 429 || !strings.HasPrefix(rate.Notes, "Synthetic, not recorded: copied unchanged from gpt-a/error_rate_limit") ||
		!strings.HasSuffix(rate.Notes, "cannot induce a 429") {
		t.Errorf("copied not-recordable case.json = %+v", rate)
	}
	if !strings.Contains(f.read(dst+"error_rate_limit/request.json"), `"model": "gpt-b"`) {
		t.Error("the copied case's request.json does not name the new model")
	}

	// Discover accepts the new model as it is.
	t.Chdir(filepath.Join(f.root, "openai"))
	n := 0
	for _, c := range contracttest.Discover(t, "openai") {
		if c.Model != "gpt-b" {
			continue
		}
		n++
		if c.Meta.ClonedFrom != "gpt-a" {
			t.Errorf("%s: cloned_from %q", c.ID(), c.Meta.ClonedFrom)
		}
		if in, err := c.InputFile(); c.Name == "transcribe" && (err != nil || in == nil || in.Name != "audio.mp3") {
			t.Errorf("%s: InputFile() = %+v, %v", c.ID(), in, err)
		}
	}
	if n != 6 {
		t.Errorf("Discover found %d gpt-b cases, want 6", n)
	}
}

func TestRecordCloneToOutSkipsNotRecordable(t *testing.T) {
	srv := anthropicUpstream(t)
	f := newFixtureTree(t)
	f.model("anthropic", "claude-a", "text_basic")
	f.errorCases("anthropic", "claude-a")
	out := t.TempDir()

	code, log := recordAgainst(t, srv.Server, withKey, "-providers-dir", f.root, "-provider", "anthropic",
		"-from", "claude-a", "-as", "claude-b", "-base-url", srv.URL, "-out", out)
	if code != 0 {
		t.Fatalf("exit %d:\n%s", code, log)
	}
	if _, err := os.Stat(filepath.Join(out, "anthropic", "claude-b", "error_rate_limit")); err == nil {
		t.Error("a not-recordable case was copied into -out, where live mode ignores it")
	}
	t.Chdir(filepath.Join(f.root, "anthropic"))
	t.Setenv(contracttest.LiveEnv, out)
	cases := contracttest.Discover(t, "anthropic")
	if len(cases) != 2 {
		t.Fatalf("live Discover found %d cases, want 2", len(cases))
	}
	for _, c := range cases {
		if c.Meta.ClonedFrom != "claude-a" || c.Model != "claude-b" {
			t.Errorf("%s: cloned_from %q", c.ID(), c.Meta.ClonedFrom)
		}
		// A clone has no repo case to fall back to: its case.json came along.
		if c.Name == "error_invalid" && (c.Status != 400 || c.Headers["Request-Id"] != "req_live" || len(c.SanityGaps) != 1) {
			t.Errorf("cloned live error case = %+v", c)
		}
	}
}

func TestCapCases(t *testing.T) {
	var cands []caseRef
	add := func(model, endpoint string, names ...string) {
		for _, n := range names {
			c := caseRef{Model: model, Name: n, Endpoint: endpoint}
			if strings.HasPrefix(n, "error") {
				c.Spec.Status = 400
			}
			cands = append(cands, c)
		}
	}
	add("m1", "chat", "aaa", "error_x", "stream_text", "text_basic", "tool_call")
	add("m1", "embeddings", "embed_a", "embed_b")
	add("m2", "chat", "text_basic", "zzz")
	add("m3", "chat", "text_basic")
	add("m3", "embeddings", "embed_a")
	kept := func(keep []bool) string {
		var ids []string
		for i, k := range keep {
			if k {
				ids = append(ids, cands[i].Model+"/"+cands[i].Name)
			}
		}
		return strings.Join(ids, " ")
	}
	tests := []struct {
		maxModels, maxCases, rotate int
		want                        string
	}{
		{0, 0, 0, "m1/aaa m1/error_x m1/stream_text m1/text_basic m1/tool_call m1/embed_a m1/embed_b m2/text_basic m2/zzz m3/text_basic m3/embed_a"},
		// Per model and endpoint, in priority order: text, stream, tool, then by name, errors last.
		{0, 3, 0, "m1/stream_text m1/text_basic m1/tool_call m1/embed_a m1/embed_b m2/text_basic m2/zzz m3/text_basic m3/embed_a"},
		{0, 1, 0, "m1/text_basic m1/embed_a m2/text_basic m3/text_basic m3/embed_a"},
		// Per provider and endpoint: chat models m1 m2 m3, embeddings models m1 m3.
		{1, 0, 0, "m1/aaa m1/error_x m1/stream_text m1/text_basic m1/tool_call m1/embed_a m1/embed_b"},
		{1, 0, 1, "m2/text_basic m2/zzz m3/embed_a"},
		{2, 1, 2, "m1/text_basic m1/embed_a m3/text_basic m3/embed_a"},
	}
	for _, tc := range tests {
		if got := kept(capCases(cands, tc.maxModels, tc.maxCases, tc.rotate)); got != tc.want {
			t.Errorf("capCases(models %d, cases %d, rotate %d):\n got %s\nwant %s", tc.maxModels, tc.maxCases, tc.rotate, got, tc.want)
		}
	}
}

func TestPlanFiltersAndCaps(t *testing.T) {
	f := newFixtureTree(t)
	f.model("openai", "gpt-a", "text_basic", "stream_text", "tool_call")
	f.model("openai", "gpt-b", "text_basic", "stream_text")
	f.errorCases("openai", "gpt-a")
	f.endpointCase("openai", "gpt-a", "embed", "embeddings", `{"model":"gpt-a","input":"x"}`, map[string]string{"response.json": "{}"})
	f.endpointCase("openai", "gpt-b", "embed", "embeddings", `{"model":"gpt-b","input":"x"}`, map[string]string{"response.json": "{}"})
	f.endpointCase("openai", "gpt-b", "image", "images", `{"model":"gpt-b","prompt":"x"}`, map[string]string{"response.json": "{}"})

	ids := func(pl *plan) string {
		var s []string
		for _, j := range pl.Jobs {
			s = append(s, j.Target+"/"+j.Name)
		}
		return strings.Join(s, " ")
	}
	tests := []struct {
		sel            selection
		want           string
		notRec, capped int
	}{
		{selection{Endpoints: []string{"embeddings", "images"}}, "gpt-a/embed gpt-b/embed gpt-b/image", 0, 0},
		{selection{Exclude: []string{"chat"}}, "gpt-a/embed gpt-b/embed gpt-b/image", 0, 0},
		// error_rate_limit is chat and not recordable: skipped, and not counted against the caps.
		{selection{Endpoints: []string{"chat"}, MaxCases: 2}, "gpt-a/stream_text gpt-a/text_basic gpt-b/stream_text gpt-b/text_basic", 1, 2},
		{selection{MaxModels: 1, MaxCases: 1}, "gpt-a/embed gpt-a/text_basic gpt-b/image", 1, 6},
		{selection{MaxModels: 1, MaxCases: 1, Rotate: 1}, "gpt-b/embed gpt-b/image gpt-b/text_basic", 1, 6},
	}
	for _, tc := range tests {
		pl, err := planProvider(f.root, "openai", tc.sel)
		if err != nil {
			t.Fatal(err)
		}
		if got := ids(pl); got != tc.want || len(pl.NotRecordable) != tc.notRec || len(pl.Capped) != tc.capped {
			t.Errorf("%+v:\n got %s (%d not recordable, %d capped)\nwant %s (%d, %d)",
				tc.sel, got, len(pl.NotRecordable), len(pl.Capped), tc.want, tc.notRec, tc.capped)
		}
	}
}

func TestDryRunShowsEndpointsSkipsAndCaps(t *testing.T) {
	f := newFixtureTree(t)
	f.model("openai", "gpt-a", "text_basic", "stream_text", "tool_call")
	f.errorCases("openai", "gpt-a")
	f.endpointCase("openai", "gpt-a", "embed", "embeddings", `{"model":"gpt-a","input":"x"}`, nil)

	code, out := recordAgainst(t, newFakeUpstream(t).Server, nil, "-providers-dir", f.root, "-dry-run", "-max-cases", "3")
	if code != 0 {
		t.Fatalf("exit %d:\n%s", code, out)
	}
	for _, want := range []string{
		"openai: 4 cases to record (chat 3, embeddings 1); 1 not recordable, 1 over the caps",
		"gpt-a/embed [embeddings] -> ",
		": response.* (by Content-Type)",
		`not recordable (case.json "recordable": false), not sent:`,
		"gpt-a/error_rate_limit [chat, 429]",
		"over -max-cases/-max-models, not sent: gpt-a/error_invalid",
		"dry run: 4 cases selected (chat 3, embeddings 1); 1 not recordable (skipped), 1 over the caps",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("dry run output lacks %q:\n%s", want, out)
		}
	}
	_, out = recordAgainst(t, newFakeUpstream(t).Server, nil, "-providers-dir", f.root, "-dry-run", "-case", "error_*")
	if want := "gpt-a/error_invalid [expects 400] -> "; !strings.Contains(out, want) ||
		!strings.Contains(out, "response.json + case.json (status checked, observed headers)") {
		t.Errorf("dry run output lacks %q and the case.json note:\n%s", want, out)
	}
}

func TestRecordNothingSelectedExits3(t *testing.T) {
	f := newFixtureTree(t)
	f.model("anthropic", "claude-a", "text_basic")
	f.errorCases("anthropic", "claude-a")
	srv := newFakeUpstream(t)
	for _, args := range [][]string{
		{"-case", "error_rate_limit"}, // only a not-recordable case
		{"-endpoint", "images"},       // an endpoint no fixture has
		{"-case", "nope"},
	} {
		code, out := recordAgainst(t, srv.Server, withKey, append(args, "-providers-dir", f.root, "-base-url", srv.URL, "-provider", "anthropic")...)
		if code != exitNothing || !strings.Contains(out, "nothing to record") {
			t.Errorf("%q: exit %d, want %d:\n%s", args, code, exitNothing, out)
		}
	}
	if len(srv.received()) != 0 {
		t.Error("upstream was called")
	}
}

func TestParseFlagsEndpointsAndCaps(t *testing.T) {
	dir := t.TempDir()
	for _, args := range [][]string{
		{"-endpoint", "chats"},
		{"-exclude-endpoint", "speech,nope"},
		{"-endpoint", "chat,speech", "-exclude-endpoint", "speech"},
		{"-max-cases", "-1"},
		{"-max-models", "-2"},
		{"-rotate", "-1"},
	} {
		if _, err := parseFlags(append(args, "-providers-dir", dir), io.Discard); err == nil {
			t.Errorf("parseFlags(%q): want an error", args)
		}
	}
	o, err := parseFlags([]string{"-providers-dir", dir, "-endpoint", "speech, images", "-exclude-endpoint", "chat",
		"-max-cases", "2", "-max-models", "1", "-rotate", "7"}, io.Discard)
	if err != nil || strings.Join(o.Endpoints, ",") != "speech,images" || o.ExcludeEndpoints[0] != "chat" ||
		o.MaxCases != 2 || o.MaxModels != 1 || o.Rotate != 7 {
		t.Errorf("parseFlags = %+v, %v", o, err)
	}
}

func TestSavedHeaders(t *testing.T) {
	h := http.Header{}
	h.Set("Retry-After", "3")
	h.Set("Retry-After-Ms", "3000")
	h.Add("X-Ratelimit-Remaining-Tokens", "10")
	h.Set("X-Amzn-Errortype", "ThrottlingException:http://internal.amazon.com/coral/com.amazon.bedrock/")
	h.Set("X-Request-Id", "req_1")
	h.Set("Anthropic-Ratelimit-Tokens-Reset", "2026-10-28T12:00:00Z")
	h.Set("Authorization", "Bearer x")
	h.Set("Openai-Organization", "org-123")
	h.Set("Set-Cookie", "a=b")
	h.Set("X-Ratelimit-Leak", "AKIAABCDEFGHIJKLMNOP and gsk_abcdefghij")
	got, redacted := savedHeaders(h, map[string]string{"x-amzn-ErrorType": "old"}, []string{"short", "the-configured-secret"})
	want := map[string]string{
		"retry-after":                      "3",
		"retry-after-ms":                   "3000",
		"x-ratelimit-remaining-tokens":     "10",
		"x-amzn-ErrorType":                 "ThrottlingException:http://internal.amazon.com/coral/com.amazon.bedrock/",
		"x-request-id":                     "req_1",
		"anthropic-ratelimit-tokens-reset": "2026-10-28T12:00:00Z",
		"x-ratelimit-leak":                 "REDACTED and REDACTED",
	}
	if !redacted || !reflect.DeepEqual(got, want) {
		t.Errorf("savedHeaders = %v, %v\nwant %v, true", got, redacted, want)
	}
	if got, _ := savedHeaders(http.Header{"Server": {"x"}}, nil, nil); got != nil {
		t.Errorf("nothing whitelisted: got %v, want nil", got)
	}
	if b, hit := scrubCredentials([]byte(`ask-me task-12345678 "sk-proj-****abcd" bearer abc.def-ghij`)); !hit ||
		string(b) != `ask-me task-12345678 "REDACTED" REDACTED` {
		t.Errorf("scrubCredentials = %s, %v", b, hit)
	}
}

func TestFileForContentType(t *testing.T) {
	for _, tc := range []struct {
		ct, body string
		isError  bool
		want     string
	}{
		{"application/json; charset=utf-8", `{}`, false, "response.json"},
		{"application/problem+json", `{}`, true, "response.json"},
		{"text/event-stream", "data: {}\n\n", false, "response.sse"},
		{"application/vnd.amazon.eventstream", "\x00", false, "response.eventstream"},
		{"audio/mpeg", "\xff\xfb", false, "response.bin"},
		{"application/octet-stream", "\xff", false, "response.bin"},
		{"text/html; charset=utf-8", "<html>", true, "response.txt"},
		{"text/plain", "hello", false, "response.txt"},
		{"", `{"a":1}`, false, "response.json"},
		{"", "\xff", false, "response.bin"},
		{"", "oops", true, "response.txt"},
	} {
		if got := fileForContentType(tc.ct, []byte(tc.body), tc.isError); got != tc.want {
			t.Errorf("fileForContentType(%q, error=%v) = %s, want %s", tc.ct, tc.isError, got, tc.want)
		}
	}
}

func TestDecide(t *testing.T) {
	ok := exchange{Status: 201, ContentType: "application/json", Body: []byte(`{"id":1}`)}
	rec, err := decide(job{caseRef: caseRef{RespFile: "response.json"}}, ok, nil)
	if err != nil || rec.Spec == nil || rec.Spec.Status != 201 || rec.File != "response.json" {
		t.Errorf("a 201 for a 200 case = %+v, %v; want case.json status 201", rec, err)
	}
	// In live mode a 2xx case with nothing to change leaves case.json to
	// the repo; an error case always brings its own.
	rec, err = decide(job{caseRef: caseRef{RespFile: "response.json"}, Live: true}, exchange{Status: 200, ContentType: "application/json", Body: []byte(`{}`)}, nil)
	if err != nil || rec.Spec != nil {
		t.Errorf("live 2xx = %+v, %v; want no case.json", rec, err)
	}
	errCase := job{caseRef: caseRef{RespFile: "response.json", Spec: contracttest.CaseSpec{Status: 400}, HasSpec: true}, Live: true}
	rec, err = decide(errCase, exchange{Status: 400, ContentType: "application/json", Body: []byte(`{}`)}, nil)
	if err != nil || rec.Spec == nil || rec.Spec.Status != 400 {
		t.Errorf("live error case = %+v, %v; want its own case.json", rec, err)
	}
	// An error answered as a stream, and an error HTML page in a JSON file.
	if _, err := decide(job{caseRef: caseRef{Spec: contracttest.CaseSpec{Status: 500}}}, exchange{Status: 500, ContentType: "text/event-stream", Body: []byte("data: x\n\n")}, nil); err == nil {
		t.Error("an error case answered as a stream: want an error")
	}
	if _, err := decide(errCase, exchange{Status: 400, ContentType: "text/html", Body: []byte("<html>")}, nil); err == nil {
		t.Error("an HTML error for a response.json case: want an error")
	}
	// A .txt error case keeps its HTML and takes the content type.
	txt := job{caseRef: caseRef{RespFile: "response.txt", Spec: contracttest.CaseSpec{Status: 502}}}
	rec, err = decide(txt, exchange{Status: 502, ContentType: "text/html", Body: []byte("<html>bad gateway</html>")}, nil)
	if err != nil || rec.Spec == nil || rec.Spec.ContentType != "text/html" || string(rec.Body) != "<html>bad gateway</html>" {
		t.Errorf(".txt error = %+v, %v", rec, err)
	}
	// A stream request answered with JSON is saved, with a warning.
	rec, err = decide(job{caseRef: caseRef{Stream: true}}, exchange{Status: 200, ContentType: "application/json", Body: []byte(`{}`)}, nil)
	if err != nil || rec.File != "response.json" || len(rec.Warnings) != 1 || !strings.Contains(rec.Warnings[0], "asks for a stream") {
		t.Errorf("stream answered with JSON = %+v, %v", rec, err)
	}
	// A JSON file whose media type differs is a warning, not a case.json change.
	rec, err = decide(job{caseRef: caseRef{RespFile: "response.json"}}, exchange{Status: 200, ContentType: "text/plain", Body: []byte(`{}`)}, nil)
	if err != nil || rec.Spec != nil || len(rec.Warnings) != 1 {
		t.Errorf("JSON as text/plain = %+v, %v; want one warning", rec, err)
	}
}

func TestSetModelOptionalAndPrefixed(t *testing.T) {
	got, err := setModel([]byte(`{"model":"models/gemini-embedding-1","content":{}}`), "gemini-embedding-2", false)
	if err != nil || !strings.Contains(string(got), `"model": "models/gemini-embedding-2"`) {
		t.Errorf("prefixed model = %s, %v", got, err)
	}
	raw := []byte(`{"contents": []}`)
	if got, err := setModel(raw, "g-2", false); err != nil || !bytes.Equal(got, raw) {
		t.Errorf("a body without model, not required = %s, %v; want it unchanged", got, err)
	}
	if !modelInBody("chat") || modelInBody("genai_generate") || modelInBody("list_models") {
		t.Error("modelInBody")
	}
}

func TestRecordUploadMissingFailsOnlyThatCase(t *testing.T) {
	srv := newFakeUpstream(t)
	f := newFixtureTree(t)
	f.model("openai", "whisper-x", "text_basic")
	// Replayed with placeholder bytes, never recorded: no audio in the repo.
	f.endpointCase("openai", "whisper-x", "transcribe", "transcription", `{"model":"whisper-x","file":"audio.mp3"}`,
		map[string]string{"response.json": `{"text":"synthetic"}`})
	env := map[string]string{"OPENAI_API_KEY": "sk-openai-test-0123456789"}

	_, plan := recordAgainst(t, srv.Server, env, "-providers-dir", f.root, "-provider", "openai", "-dry-run")
	if !strings.Contains(plan, `whisper-x/transcribe [transcription] -> `) || !strings.Contains(plan, `(will fail: request.json names the upload "audio.mp3"`) {
		t.Errorf("dry run does not flag the missing upload:\n%s", plan)
	}
	code, out := recordAgainst(t, srv.Server, env, "-providers-dir", f.root, "-provider", "openai", "-base-url", srv.URL)
	if code != 1 || !strings.Contains(out, "whisper-x/transcribe: FAILED: cannot record it: request.json names the upload") ||
		!strings.Contains(out, "whisper-x/text_basic: recorded 200") {
		t.Errorf("exit %d, want 1 with only the transcription failing:\n%s", code, out)
	}
	if got := f.read("openai/testdata/contracts/whisper-x/transcribe/response.json"); got != `{"text":"synthetic"}` {
		t.Errorf("response.json = %s", got)
	}
}

func TestRecordAzureAPIVersionFromGolden(t *testing.T) {
	srv := newFakeUpstream(t)
	f := newFixtureTree(t)
	f.model("azure", "gpt-a", "text_basic", "text_preview", "text_new")
	dir := "azure/testdata/contracts/gpt-a/"
	f.write(dir+"text_basic/upstream_request.golden.json", `{"method":"POST","path":"/x","query":"api-version=2024-10-21","headers":{},"body":{}}`)
	f.write(dir+"text_preview/upstream_request.golden.json", `{"method":"POST","path":"/x","query":"api-version=2025-04-01-preview","headers":{},"body":{}}`)
	env := map[string]string{"AZURE_OPENAI_KEY": "azure-key-0123456789", "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com"}

	versions := func() string {
		var v []string
		for _, c := range srv.received() {
			v = append(v, c.Query)
		}
		sort.Strings(v)
		return strings.Join(v, " ")
	}
	code, out := recordAgainst(t, srv.Server, env, "-providers-dir", f.root, "-provider", "azure", "-base-url", srv.URL, "-dry-run")
	if code != 0 || !strings.Contains(out, "azure at api-version 2025-04-01-preview: 1 cases") || !strings.Contains(out, "azure: 1 cases") {
		t.Errorf("dry run does not group by api-version:\n%s", out)
	}
	code, out = recordAgainst(t, srv.Server, env, "-providers-dir", f.root, "-provider", "azure", "-base-url", srv.URL)
	// text_new has no golden yet: the adapter's default.
	if code != 0 || versions() != "api-version=2024-10-21 api-version=2024-10-21 api-version=2025-04-01-preview" {
		t.Errorf("exit %d, upstream got %q; want each case at its golden's api-version:\n%s", code, versions(), out)
	}

	srv.mu.Lock()
	srv.calls = nil
	srv.mu.Unlock()
	env["AZURE_OPENAI_API_VERSION"] = "2026-01-01"
	if code, out := recordAgainst(t, srv.Server, env, "-providers-dir", f.root, "-provider", "azure", "-base-url", srv.URL); code != 0 ||
		versions() != "api-version=2026-01-01 api-version=2026-01-01 api-version=2026-01-01" {
		t.Errorf("exit %d, upstream got %q; AZURE_OPENAI_API_VERSION overrides every case:\n%s", code, versions(), out)
	}
}

func TestRecordVertexKeys(t *testing.T) {
	srv := newFakeUpstream(t)
	f := newFixtureTree(t)
	f.write("gemini/testdata/contracts/vertex%40g-1/meta.json", metaJSON("gemini", "vertex@g-1"))
	f.endpointCase("gemini", "vertex@g-1", "generate", "genai_generate", `{"contents":[{"parts":[{"text":"Hi"}]}]}`,
		map[string]string{"response.json": "synthetic"})
	f.model("gemini", "g-1", "text_basic")

	// The recorder dials Vertex AI's real host name; this client takes every
	// such connection to the fake upstream instead, trusting its certificate.
	tr := srv.Client().Transport.(*http.Transport).Clone()
	tr.TLSClientConfig.ServerName = "example.com" // a name the httptest certificate covers
	var d net.Dialer
	tr.DialContext = func(ctx context.Context, network, _ string) (net.Conn, error) {
		return d.DialContext(ctx, network, srv.Listener.Addr().String())
	}
	var stdout bytes.Buffer
	opts, err := parseFlags([]string{"-providers-dir", f.root, "-provider", "gemini", "-model", "vertex@*"}, io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	env := map[string]string{"VERTEX_PROJECT": "proj-1", "VERTEX_ACCESS_TOKEN": "ya29.vertex-token-0123456789", "VERTEX_LOCATION": "us-east5"}
	r := &recorder{opts: opts, out: &stdout, env: func(k string) string { return env[k] }, now: fixedNow, client: &http.Client{Transport: tr}}
	if code := r.run(t.Context()); code != 0 {
		t.Fatalf("exit %d:\n%s", code, stdout.String())
	}
	c := srv.find(t, "POST", "/v1beta1/projects/proj-1/locations/us-east5/publishers/google/models/g-1:generateContent")
	if c.Header.Get("Authorization") != "Bearer ya29.vertex-token-0123456789" || c.Header.Get("X-Goog-Api-Key") != "" {
		t.Errorf("vertex auth headers = %v", c.Header)
	}
	if !strings.Contains(f.read("gemini/testdata/contracts/vertex%40g-1/generate/response.json"), "generateContent") {
		t.Error("the vertex recording was not saved")
	}
	if got, want := f.read("gemini/testdata/contracts/g-1/text_basic/response.json"), "synthetic"; got != want {
		t.Errorf("-model vertex@* recorded the AI Studio model too: %s", got)
	}

	// Without Vertex credentials, only the vertex@ keys are skipped.
	_, out := recordAgainst(t, srv.Server, map[string]string{"GEMINI_API_KEY": "gemini-test-0123456789"}, "-providers-dir", f.root, "-provider", "gemini", "-dry-run")
	if !strings.Contains(out, "gemini via vertex: 1 cases; VERTEX_PROJECT, VERTEX_ACCESS_TOKEN not set") || !strings.Contains(out, "  gemini: 1 cases\n") {
		t.Errorf("dry run:\n%s", out)
	}
}
