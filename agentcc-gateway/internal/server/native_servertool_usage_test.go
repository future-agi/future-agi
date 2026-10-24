package server

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// The native pass-through paths meter server
// tools too. Chat cannot request Gemini grounding (translateRequest drops
// non-function tools), so native generateContent is where grounding happens,
// and its fees and eval_export=forbidden depend on this.

func TestNativeGenAIGroundingMetered(t *testing.T) {
	grounded := []byte(`{"candidates":[{"content":{"parts":[{"text":"x"}]},"groundingMetadata":{` +
		`"webSearchQueries":["a","b"],"groundingChunks":[{"web":{"uri":"https://e.example","title":"e"}}]}}],` +
		`"usageMetadata":{"promptTokenCount":10,"candidatesTokenCount":5},"modelVersion":"gemini-3-pro"}`)
	rc := models.AcquireRequestContext()
	defer rc.Release()
	applyGenAIUsage(rc, grounded)
	if rc.Response == nil || rc.Response.Usage == nil {
		t.Fatal("no usage set")
	}
	if u := rc.Response.Usage.ServerToolUse; u == nil || u.WebSearchQueries != 2 || u.GroundedPrompts != 1 {
		t.Fatalf("server tool use = %+v, want 2 queries and a grounded prompt", u)
	}

	// Grounding with no token counts still reaches the cost plugin.
	noTokens := []byte(`{"candidates":[{"groundingMetadata":{"webSearchQueries":["a"]}}]}`)
	rc2 := models.AcquireRequestContext()
	defer rc2.Release()
	applyGenAIUsage(rc2, noTokens)
	if rc2.Response == nil || rc2.Response.Usage.ServerToolUse == nil || rc2.Response.Usage.ServerToolUse.WebSearchQueries != 1 {
		t.Fatalf("a grounded body with no tokens was not metered: %+v", rc2.Response)
	}

	plain := []byte(`{"candidates":[{"content":{"parts":[{"text":"x"}]}}],"usageMetadata":{"promptTokenCount":3,"candidatesTokenCount":2}}`)
	rc3 := models.AcquireRequestContext()
	defer rc3.Release()
	applyGenAIUsage(rc3, plain)
	if rc3.Response == nil || rc3.Response.Usage.ServerToolUse != nil {
		t.Fatalf("an ungrounded body has server tool use: %+v", rc3.Response)
	}
}

func TestNativeMessagesServerToolsMetered(t *testing.T) {
	body := []byte(`{"id":"msg_1","model":"claude-sonnet-4-5","content":[{"type":"text","text":"x"}],` +
		`"usage":{"input_tokens":100,"output_tokens":20,"server_tool_use":{"web_search_requests":3,"web_fetch_requests":1}}}`)
	rc := models.AcquireRequestContext()
	defer rc.Release()
	applyAnthropicUsage(rc, body)
	if rc.Response == nil || rc.Response.Usage == nil {
		t.Fatal("no usage set")
	}
	if u := rc.Response.Usage.ServerToolUse; u == nil || u.WebSearchRequests != 3 || u.WebFetchRequests != 1 {
		t.Fatalf("server tool use = %+v, want 3 searches and 1 fetch", u)
	}
	plain := []byte(`{"id":"msg_2","model":"claude-sonnet-4-5","usage":{"input_tokens":1,"output_tokens":1}}`)
	rc2 := models.AcquireRequestContext()
	defer rc2.Release()
	applyAnthropicUsage(rc2, plain)
	if rc2.Response == nil || rc2.Response.Usage.ServerToolUse != nil {
		t.Fatalf("a body with no server tools has server tool use: %+v", rc2.Response)
	}
}

// evidenceCapture records the request's log evidence and attempts after the
// cost plugin ran.
type evidenceCapture struct {
	mu       sync.Mutex
	evidence map[string]string
	attempts []models.Attempt
}

func (c *evidenceCapture) Name() string      { return "genai-evidence-capture" }
func (c *evidenceCapture) Priority() int     { return 900 }
func (c *evidenceCapture) PostPriority() int { return 900 }
func (c *evidenceCapture) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
func (c *evidenceCapture) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.evidence = map[string]string{}
	for k, v := range rc.LogEvidence {
		c.evidence[k] = string(v)
	}
	c.attempts = rc.AttemptsSnapshot()
	return pipeline.ResultContinue()
}

// Through the real handler and the cost plugin: a grounded native
// generateContent response is marked eval_export forbidden.
func TestNativeGenAIGroundingMarksEvalExportForbidden(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == "/v1/models" {
			_, _ = w.Write([]byte(`{"data":[]}`))
			return
		}
		_, _ = w.Write([]byte(`{"candidates":[{"content":{"role":"model","parts":[{"text":"x"}]},"finishReason":"STOP",` +
			`"groundingMetadata":{"webSearchQueries":["q"],"groundingChunks":[{"web":{"uri":"https://e.example","title":"e"}}]}}],` +
			`"usageMetadata":{"promptTokenCount":5,"candidatesTokenCount":2,"totalTokenCount":7}}`))
	}))
	defer upstream.Close()

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "k", Key: "genai-test-key", Owner: "o", KeyType: "internal"}}
	cfg.Providers["gemini"] = config.ProviderConfig{BaseURL: upstream.URL, APIKey: "k", APIFormat: "gemini", Models: []string{"gemini-2.5-pro"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	mdb := testModelDBPtr()
	capture := &evidenceCapture{}
	keyStore := auth.NewKeyStore(cfg.Auth)
	engine := pipeline.NewEngine(authplugin.New(keyStore, true), costplugin.New(true, mdb.Load, nil, nil), capture)
	srv := New(cfg, "", registry, engine, keyStore, nil, nil, nil, mdb, nil, nil)
	srv.ready.Store(true)

	req := httptest.NewRequest(http.MethodPost, "/v1beta/models/gemini-2.5-pro:generateContent",
		strings.NewReader(`{"contents":[{"role":"user","parts":[{"text":"news?"}]}],"tools":[{"google_search":{}}]}`))
	req.Header.Set("Authorization", "Bearer genai-test-key")
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d: %s", w.Code, w.Body.String())
	}
	capture.mu.Lock()
	defer capture.mu.Unlock()
	if got := capture.evidence["eval_export"]; got != `"forbidden"` {
		t.Fatalf("eval_export evidence = %q, want \"forbidden\" (evidence %v)", got, capture.evidence)
	}
}
