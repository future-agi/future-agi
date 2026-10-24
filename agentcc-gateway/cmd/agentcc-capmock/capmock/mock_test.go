package capmock

import (
	"encoding/base64"
	"encoding/json"
	"net/http"
	"slices"
	"strings"
	"testing"
	"time"
)

// Every vendor the contract names has a fake, and each fake's first route
// answers a plain request with a vendor-shaped 2xx.
func TestEveryFakeAnswers(t *testing.T) {
	_, srv, _ := start(t)
	want := []string{"exa", "parallel", "perplexity", "tavily", "brave", "searxng", "firecrawl", "jina", "mistral-ocr",
		"azure-di", "textract", "deepgram", "assemblyai", "voyage", "cohere", "kernel", "browserbase", "sandbox",
		"fal", "pipedream", "x402", "echo-a", "echo-b"}
	var listed []string
	for _, f := range do(t, "GET", srv.URL+"/__fakes", nil).json(t)["fakes"].([]any) {
		listed = append(listed, f.(map[string]any)["name"].(string))
	}
	if !slices.Equal(listed, want) {
		t.Fatalf("fakes %v, want %v", listed, want)
	}
	requests := map[string]func() resp{
		"exa":        func() resp { return do(t, "POST", srv.URL+"/exa/search", map[string]any{"query": "q"}) },
		"parallel":   func() resp { return do(t, "POST", srv.URL+"/parallel/v1beta/search", map[string]any{"objective": "q"}) },
		"perplexity": func() resp { return do(t, "POST", srv.URL+"/perplexity/search", map[string]any{"query": "q"}) },
		"tavily":     func() resp { return do(t, "POST", srv.URL+"/tavily/search", map[string]any{"query": "q"}) },
		"brave":      func() resp { return do(t, "GET", srv.URL+"/brave/res/v1/web/search?q=q", nil) },
		"searxng":    func() resp { return do(t, "GET", srv.URL+"/searxng/search?q=q&format=json", nil) },
		"firecrawl": func() resp {
			return do(t, "POST", srv.URL+"/firecrawl/v2/scrape", map[string]any{"url": "https://example.com"})
		},
		"jina": func() resp {
			return do(t, "GET", srv.URL+"/jina/https://example.com", nil, "Accept", "application/json")
		},
		"mistral-ocr": func() resp { return do(t, "POST", srv.URL+"/mistral-ocr/v1/ocr", map[string]any{"model": "x"}) },
		"azure-di": func() resp {
			return do(t, "POST", srv.URL+"/azure-di/documentintelligence/documentModels/prebuilt-read:analyze", map[string]any{})
		},
		"textract": func() resp {
			return do(t, "POST", srv.URL+"/textract/", map[string]any{}, "X-Amz-Target", "Textract.DetectDocumentText")
		},
		"deepgram":   func() resp { return do(t, "POST", srv.URL+"/deepgram/v1/listen", "audio") },
		"assemblyai": func() resp { return do(t, "POST", srv.URL+"/assemblyai/v2/upload", "audio") },
		"voyage": func() resp {
			return do(t, "POST", srv.URL+"/voyage/v1/embeddings", map[string]any{"input": []string{"a"}})
		},
		"cohere":      func() resp { return do(t, "POST", srv.URL+"/cohere/v2/rerank", map[string]any{"query": "q"}) },
		"kernel":      func() resp { return do(t, "POST", srv.URL+"/kernel/browsers", map[string]any{}) },
		"browserbase": func() resp { return do(t, "POST", srv.URL+"/browserbase/v1/sessions", map[string]any{}) },
		"sandbox":     func() resp { return do(t, "GET", srv.URL+"/sandbox/v1/usage", nil) },
		"fal":         func() resp { return do(t, "POST", srv.URL+"/fal/fal-ai/flux/dev", map[string]any{"prompt": "p"}) },
		"pipedream": func() resp {
			return do(t, "POST", srv.URL+"/pipedream/mcp", map[string]any{"jsonrpc": "2.0", "id": 1, "method": "initialize"},
				"x-pd-project-id", "p", "x-pd-environment", "development", "x-pd-external-user-id", "u1", "x-pd-app-slug", "slack")
		},
		"x402":   func() resp { return do(t, "GET", srv.URL+"/x402/weather", nil) },
		"echo-a": func() resp { return do(t, "POST", srv.URL+"/echo-a/echo", map[string]any{"x": 1}) },
		"echo-b": func() resp { return do(t, "POST", srv.URL+"/echo-b/echo", map[string]any{"x": 1}) },
	}
	for _, name := range want {
		r := requests[name]()
		ok := r.status/100 == 2 || (name == "x402" && r.status == http.StatusPaymentRequired)
		if !ok {
			t.Errorf("%s answered %d: %s", name, r.status, r.body)
		}
	}
}

// The directives: status, fail_after, delay_ms, report_cost and
// drop_response behave as documented, and a bad one is a 400.
func TestDirectives(t *testing.T) {
	_, srv, _ := start(t)
	search := srv.URL + "/exa/search"
	r := do(t, "POST", search, map[string]any{"query": "q"}, BehaviorHeader, "status:429")
	if r.status != 429 || r.header.Get("Retry-After") != "1" || r.json(t)["error"] == nil {
		t.Errorf("status:429 answered %d %v %s", r.status, r.header, r.body)
	}
	do(t, "POST", srv.URL+"/__reset", nil)
	for i, want := range []int{200, 200, 503, 503} {
		if got := do(t, "POST", search, "{}", BehaviorHeader, "fail_after:2,status:503").status; got != want {
			t.Errorf("fail_after:2,status:503 call %d: %d, want %d", i+1, got, want)
		}
	}
	if got := do(t, "POST", search, "{}", BehaviorHeader, "fail_after:0").status; got != 500 {
		t.Errorf("fail_after without status failed with %d, want 500", got)
	}
	began := time.Now()
	if r := do(t, "POST", search, "{}", BehaviorHeader, "delay_ms:120"); r.status != 200 || time.Since(began) < 120*time.Millisecond {
		t.Errorf("delay_ms:120 answered %d after %v", r.status, time.Since(began))
	}
	r = do(t, "POST", search, "{}", BehaviorHeader, "report_cost:0.25")
	if got := path(t, r.json(t), "costDollars.total"); got != 0.25 {
		t.Errorf("report_cost:0.25 on exa reported %v", got)
	}
	for _, bad := range []string{"report_cost:0.1", "nonsense", "status:200", "fail_after:-1", "drop_response:1", "job_delay:5ms"} {
		r := do(t, "GET", srv.URL+"/brave/res/v1/web/search?q=x", nil, BehaviorHeader, bad)
		if r.status != 400 || !strings.Contains(string(r.body), "invalid_capmock_directive") {
			t.Errorf("%q on brave answered %d %s, want 400 invalid_capmock_directive", bad, r.status, r.body)
		}
	}
	req, _ := http.NewRequest("POST", search, strings.NewReader("{}"))
	req.Header.Set(BehaviorHeader, "drop_response")
	if res, err := http.DefaultClient.Do(req); err == nil {
		res.Body.Close()
		t.Errorf("drop_response answered %d", res.StatusCode)
	}
}

// A gateway forwards no test header, so directives are armed per fake: for
// the next n calls or until cleared, with a header still winning.
func TestArmingAppliesWithoutHeader(t *testing.T) {
	_, srv, _ := start(t)
	arm := func(body any) resp { return do(t, "POST", srv.URL+"/__arm", body) }
	if r := arm(map[string]any{"fake": "tavily", "behavior": "status:429", "calls": 1}); r.status != 200 {
		t.Fatalf("arm: %d %s", r.status, r.body)
	}
	for i, want := range []int{429, 200} {
		if got := do(t, "POST", srv.URL+"/tavily/search", "{}").status; got != want {
			t.Errorf("armed for 1 call, call %d: %d, want %d", i+1, got, want)
		}
	}
	arm(map[string]any{"fake": "tavily", "behavior": "status:503"})
	if got := do(t, "POST", srv.URL+"/tavily/search", "{}", BehaviorHeader, "delay_ms:0").status; got != 200 {
		t.Errorf("a header did not win over the arming: %d", got)
	}
	if got := do(t, "POST", srv.URL+"/exa/search", "{}").status; got != 200 {
		t.Errorf("arming tavily changed exa: %d", got)
	}
	for i := 0; i < 2; i++ {
		if got := do(t, "POST", srv.URL+"/tavily/search", "{}").status; got != 503 {
			t.Errorf("armed until cleared, call %d: %d", i+1, got)
		}
	}
	do(t, "DELETE", srv.URL+"/__arm?fake=tavily", nil)
	if got := do(t, "POST", srv.URL+"/tavily/search", "{}").status; got != 200 {
		t.Errorf("after clearing: %d", got)
	}
	for _, bad := range []any{map[string]any{"fake": "nobody", "behavior": "status:500"},
		map[string]any{"fake": "exa", "behavior": "nonsense"}, map[string]any{"fake": "exa", "calls": -1}} {
		if r := arm(bad); r.status != 400 {
			t.Errorf("arm %v: %d, want 400", bad, r.status)
		}
	}
}

// /__received returns what a fake saw, last body included, with credential
// headers kept as a hash only.
func TestReceivedEchoesLastBody(t *testing.T) {
	_, srv, _ := start(t)
	do(t, "POST", srv.URL+"/exa/search", map[string]any{"query": "first"})
	do(t, "POST", srv.URL+"/exa/search", map[string]any{"query": "second", "numResults": 3},
		"x-api-key", "not-a-real-key", "Authorization", "Bearer not-a-real-token")
	do(t, "POST", srv.URL+"/tavily/search", map[string]any{"query": "other"})
	got := do(t, "GET", srv.URL+"/__received?fake=exa&last=1", nil).json(t)["received"].([]any)
	if len(got) != 1 {
		t.Fatalf("%d received, want 1", len(got))
	}
	rec := got[0].(map[string]any)
	if q := path(t, rec, "body.query"); q != "second" || path(t, rec, "body.numResults") != 3.0 || rec["path"] != "/exa/search" {
		t.Errorf("last exa request recorded as %v", rec)
	}
	raw, _ := json.Marshal(rec)
	if strings.Contains(string(raw), "not-a-real") {
		t.Errorf("a credential header was recorded in clear: %s", raw)
	}
	if h := path(t, rec, "headers.x-api-key").(string); !strings.HasPrefix(h, "sha256:") {
		t.Errorf("x-api-key recorded as %q", h)
	}
	if all := do(t, "GET", srv.URL+"/__received", nil).json(t)["received"].([]any); len(all) != 3 {
		t.Errorf("%d received in all, want 3", len(all))
	}
}

// Azure DI: 202 with Operation-Location, then notStarted, running and
// succeeded as job_delay passes; job_fail_at:failed ends it failed.
func TestAzureJob202ThenPoll(t *testing.T) {
	_, srv, clk := start(t)
	analyze := srv.URL + "/azure-di/documentintelligence/documentModels/prebuilt-read:analyze?api-version=2024-11-30"
	r := do(t, "POST", analyze, map[string]any{"urlSource": "https://example.com/a.pdf"}, BehaviorHeader, "job_delay:1000")
	op := r.header.Get("Operation-Location")
	if r.status != 202 || !strings.HasPrefix(op, srv.URL+"/azure-di/") || len(r.body) != 0 {
		t.Fatalf("analyze answered %d, Operation-Location %q, body %q", r.status, op, r.body)
	}
	for _, step := range []struct {
		advance time.Duration
		status  string
	}{{0, "notStarted"}, {600 * time.Millisecond, "running"}, {500 * time.Millisecond, "succeeded"}} {
		clk.Advance(step.advance)
		doc := do(t, "GET", op, nil).json(t)
		if doc["status"] != step.status {
			t.Errorf("after %v: status %v, want %s", step.advance, doc["status"], step.status)
		}
		if step.status == "succeeded" && path(t, doc, "analyzeResult.modelId") != "prebuilt-read" {
			t.Errorf("result %v", doc)
		}
	}
	r = do(t, "POST", analyze, "{}", BehaviorHeader, "job_fail_at:failed")
	if doc := do(t, "GET", r.header.Get("Operation-Location"), nil).json(t); doc["status"] != "failed" || doc["error"] == nil {
		t.Errorf("job_fail_at:failed: %v", doc)
	}
}

// AssemblyAI: upload returns a file URL, a transcript is queued, then
// processing, then completed; job_fail_at:error (or failed) ends in error.
func TestAssemblyAIUploadQueuedCompleted(t *testing.T) {
	_, srv, clk := start(t)
	up := do(t, "POST", srv.URL+"/assemblyai/v2/upload", "RIFF audio bytes").json(t)["upload_url"].(string)
	if got := do(t, "GET", up, nil); got.status != 200 || string(got.body) != "RIFF audio bytes" {
		t.Errorf("upload_url %s answered %d %q", up, got.status, got.body)
	}
	tr := do(t, "POST", srv.URL+"/assemblyai/v2/transcript", map[string]any{"audio_url": up}, BehaviorHeader, "job_delay:400").json(t)
	if tr["status"] != "queued" {
		t.Fatalf("create: %v", tr)
	}
	poll := srv.URL + "/assemblyai/v2/transcript/" + tr["id"].(string)
	for _, step := range []struct {
		advance time.Duration
		status  string
	}{{100 * time.Millisecond, "queued"}, {200 * time.Millisecond, "processing"}, {200 * time.Millisecond, "completed"}} {
		clk.Advance(step.advance)
		doc := do(t, "GET", poll, nil).json(t)
		if doc["status"] != step.status {
			t.Errorf("after +%v: %v, want %s", step.advance, doc["status"], step.status)
		}
		if step.status == "completed" && (doc["text"] == "" || doc["audio_duration"] == nil || doc["id"] != tr["id"]) {
			t.Errorf("completed transcript %v", doc)
		}
	}
	for _, word := range []string{"error", "failed"} {
		tr := do(t, "POST", srv.URL+"/assemblyai/v2/transcript", map[string]any{"audio_url": up}, BehaviorHeader, "job_fail_at:"+word).json(t)
		if doc := do(t, "GET", srv.URL+"/assemblyai/v2/transcript/"+tr["id"].(string), nil).json(t); doc["status"] != "error" {
			t.Errorf("job_fail_at:%s: %v", word, doc)
		}
	}
	if r := do(t, "POST", srv.URL+"/assemblyai/v2/transcript", map[string]any{"audio_url": up}, BehaviorHeader, "job_fail_at:running"); r.status != 400 {
		t.Errorf("job_fail_at:running answered %d, want 400", r.status)
	}
	if r := do(t, "POST", srv.URL+"/assemblyai/v2/transcript", "{}"); r.status != 400 {
		t.Errorf("a transcript without audio_url answered %d", r.status)
	}
}

// fal's queue: submit, status IN_QUEUE to COMPLETED, then the result, whose
// image URL expires after url_expiry.
func TestFalQueueAndURLExpiry(t *testing.T) {
	_, srv, clk := start(t)
	sub := do(t, "POST", srv.URL+"/fal/fal-ai/flux/dev", map[string]any{"prompt": "a cat"}, BehaviorHeader, "job_delay:200,url_expiry:60").json(t)
	status, result := sub["status_url"].(string), sub["response_url"].(string)
	if s := do(t, "GET", status, nil).json(t)["status"]; s != "IN_QUEUE" {
		t.Errorf("status %v, want IN_QUEUE", s)
	}
	if r := do(t, "GET", result, nil); r.status != 400 {
		t.Errorf("result before completion answered %d", r.status)
	}
	clk.Advance(150 * time.Millisecond)
	if s := do(t, "GET", status, nil).json(t)["status"]; s != "IN_PROGRESS" {
		t.Errorf("status %v, want IN_PROGRESS", s)
	}
	clk.Advance(100 * time.Millisecond)
	doc := do(t, "GET", result, nil).json(t)
	img := path(t, doc, "images.0.url").(string)
	if path(t, doc, "prompt") != "a cat" || !strings.Contains(img, "expires=") {
		t.Fatalf("result %v", doc)
	}
	if again := path(t, do(t, "GET", result, nil).json(t), "images.0.url"); again != img {
		t.Errorf("a second fetch minted another URL: %v", again)
	}
	if r := do(t, "GET", img, nil); r.status != 200 {
		t.Errorf("image before expiry: %d", r.status)
	}
	clk.Advance(61 * time.Second)
	if r := do(t, "GET", img, nil); r.status != 403 {
		t.Errorf("image after url_expiry: %d, want 403", r.status)
	}
	failed := do(t, "POST", srv.URL+"/fal/fal-ai/flux/dev", "{}", BehaviorHeader, "job_fail_at:failed").json(t)
	if s := do(t, "GET", failed["status_url"].(string), nil).json(t); s["error"] == nil {
		t.Errorf("job_fail_at:failed status %v", s)
	}
	if r := do(t, "GET", failed["response_url"].(string), nil); r.status != 500 {
		t.Errorf("failed result answered %d", r.status)
	}
}

// Kernel and Browserbase sessions: create, get, release or delete.
func TestBrowserSessions(t *testing.T) {
	_, srv, _ := start(t)
	r := do(t, "POST", srv.URL+"/browserbase/v1/sessions", map[string]any{"projectId": "p"})
	bb := r.json(t)
	if r.status != 201 || bb["status"] != "RUNNING" || bb["endedAt"] != nil || bb["connectUrl"] == "" {
		t.Fatalf("browserbase create %d %v", r.status, bb)
	}
	one := srv.URL + "/browserbase/v1/sessions/" + bb["id"].(string)
	if s := do(t, "POST", one, map[string]any{"status": "REQUEST_RELEASE"}).json(t); s["status"] != "COMPLETED" || s["endedAt"] == nil {
		t.Errorf("release: %v", s)
	}
	if s := do(t, "GET", one, nil).json(t); s["status"] != "COMPLETED" {
		t.Errorf("get after release: %v", s)
	}
	k := do(t, "POST", srv.URL+"/kernel/browsers", map[string]any{}).json(t)
	kb := srv.URL + "/kernel/browsers/" + k["session_id"].(string)
	if k["cdp_ws_url"] == "" || do(t, "GET", kb, nil).status != 200 {
		t.Errorf("kernel create/get: %v", k)
	}
	if got := do(t, "DELETE", kb, nil).status; got != 204 {
		t.Errorf("kernel delete: %d", got)
	}
	if got := do(t, "GET", kb, nil).status; got != 404 {
		t.Errorf("kernel get after delete: %d", got)
	}
}

// Pipedream's MCP server needs the end-user headers and lists and calls the
// app's tool for that user.
func TestPipedreamMCPNeedsEndUser(t *testing.T) {
	_, srv, _ := start(t)
	hdr := []string{"x-pd-project-id", "proj", "x-pd-environment", "development", "x-pd-external-user-id", "user-42", "x-pd-app-slug", "slack"}
	rpc := func(method string, params any, h ...string) resp {
		return do(t, "POST", srv.URL+"/pipedream/mcp", map[string]any{"jsonrpc": "2.0", "id": 7, "method": method, "params": params}, h...)
	}
	if r := rpc("tools/list", nil, hdr[:6]...); r.status != 400 || !strings.Contains(string(r.body), "x-pd-app-slug") {
		t.Errorf("without the app header: %d %s", r.status, r.body)
	}
	if name := path(t, rpc("tools/list", nil, hdr...).json(t), "result.tools.0.name"); name != "slack-send-message" {
		t.Errorf("tools/list: %v", name)
	}
	call := rpc("tools/call", map[string]any{"name": "slack-send-message", "arguments": map[string]any{"text": "hi"}}, hdr...).json(t)
	if text := path(t, call, "result.content.0.text").(string); !strings.Contains(text, "user-42") || path(t, call, "result.isError") != false {
		t.Errorf("tools/call: %v", call)
	}
	if e := rpc("resources/list", nil, hdr...).json(t)["error"]; e == nil {
		t.Errorf("an unknown method answered no error")
	}
}

// The x402 seller answers 402 with PAYMENT-REQUIRED until a request
// carries PAYMENT-SIGNATURE, then 200 with PAYMENT-RESPONSE.
func TestX402Seller(t *testing.T) {
	_, srv, _ := start(t)
	r := do(t, "GET", srv.URL+"/x402/weather", nil)
	raw, err := base64.StdEncoding.DecodeString(r.header.Get("PAYMENT-REQUIRED"))
	var req map[string]any
	if r.status != 402 || err != nil || json.Unmarshal(raw, &req) != nil || path(t, req, "accepts.0.scheme") != "exact" {
		t.Fatalf("unpaid: %d %q", r.status, r.header.Get("PAYMENT-REQUIRED"))
	}
	if bad := do(t, "GET", srv.URL+"/x402/weather", nil, "PAYMENT-SIGNATURE", "not base64 json"); bad.status != 400 {
		t.Errorf("a garbage payment answered %d", bad.status)
	}
	payload := base64.StdEncoding.EncodeToString([]byte(`{"x402Version":2,"accepted":{"scheme":"exact"},"payload":{}}`))
	paid := do(t, "GET", srv.URL+"/x402/weather", nil, "PAYMENT-SIGNATURE", payload)
	if paid.status != 200 || paid.header.Get("PAYMENT-RESPONSE") == "" || paid.json(t)["weather"] == nil {
		t.Errorf("paid: %d %v %s", paid.status, paid.header, paid.body)
	}
}

// The echo fakes return the body and report a cost where the capability core reads it.
func TestEchoFakes(t *testing.T) {
	_, srv, _ := start(t)
	for _, name := range []string{"echo-a", "echo-b"} {
		doc := do(t, "POST", srv.URL+"/"+name+"/echo", map[string]any{"text": "hello"}, BehaviorHeader, "report_cost:0.001").json(t)
		if doc["vendor"] != name || path(t, doc, "echo.text") != "hello" || path(t, doc, "usage.cost_usd") != 0.001 {
			t.Errorf("%s: %v", name, doc)
		}
	}
}

func TestVendorKeyVars(t *testing.T) {
	env := []string{"EXA_API_KEY=x", "FAL_KEY=x", "AWS_SECRET_ACCESS_KEY=x", "CO_API_KEY=x", "BRAVE_SEARCH_API_KEY=x",
		"PATH=/bin", "HOME=/h", "MONKEY_KEY=x", "EXA_BASE_URL=http://x", "PARALLEL_JOBS=4", "SCN_REDIS=x"}
	got := VendorKeyVars(env)
	want := []string{"AWS_SECRET_ACCESS_KEY", "BRAVE_SEARCH_API_KEY", "CO_API_KEY", "EXA_API_KEY", "FAL_KEY"}
	if !slices.Equal(got, want) {
		t.Errorf("VendorKeyVars = %v, want %v", got, want)
	}
}
