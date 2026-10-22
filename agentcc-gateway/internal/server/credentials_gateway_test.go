package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	loggingplugin "github.com/futureagi/agentcc-gateway/internal/plugins/logging"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The whole gateway, for the credential header tests: the auth plugin and the
// request log in the pipeline, the request log keeping bodies and headers
// (request_logging.include_bodies) and shipping each entry to a loopback sink
// (the control plane's log webhook), an org whose own OpenAI and Google
// providers serve the requests from a loopback upstream. Every value is a
// test value.

const (
	credGWKey    = "sk-agentcc-credgw-0123456789abcdef0123"
	credGWOrg    = "org-credgw"
	credGWOrgKey = "org-provider-key-credgw"
	credGWGemini = "gemini-credgw"
	credGWPrompt = "summarise this ticket"
)

// credGWUpstream answers chat completions and the native Google API, and
// hands each native Google request to record first, when it is set.
func credGWUpstream(t *testing.T, record func(*http.Request)) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path := r.URL.Path
		switch {
		case path == "/v1/chat/completions":
			w.Header().Set("Content-Type", "application/json")
			fmt.Fprint(w, `{"id":"c1","object":"chat.completion","created":1760000000,"model":"gpt-4o","choices":[{"index":0,"message":{"role":"assistant","content":"done"},"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":1,"total_tokens":6}}`)
		case strings.HasPrefix(path, "/v1beta/models/"):
			if record != nil {
				record(r)
			}
			switch {
			case strings.HasSuffix(path, ":countTokens"):
				w.Header().Set("Content-Type", "application/json")
				fmt.Fprint(w, `{"totalTokens":5}`)
			case strings.HasSuffix(path, ":embedContent"):
				w.Header().Set("Content-Type", "application/json")
				fmt.Fprint(w, `{"embedding":{"values":[0.1,0.2,0.3]}}`)
			case strings.HasSuffix(path, ":streamGenerateContent"):
				w.Header().Set("Content-Type", "text/event-stream")
				fmt.Fprintf(w, "data: %s\n\n", `{"candidates":[{"content":{"role":"model","parts":[{"text":"done"}]},"finishReason":"STOP","index":0}],"usageMetadata":{"promptTokenCount":5,"candidatesTokenCount":1,"totalTokenCount":6}}`)
			default:
				w.Header().Set("Content-Type", "application/json")
				fmt.Fprint(w, `{"candidates":[{"content":{"role":"model","parts":[{"text":"done"}]},"finishReason":"STOP","index":0}],"usageMetadata":{"promptTokenCount":5,"candidatesTokenCount":1,"totalTokenCount":6}}`)
			}
		default:
			// The provider's start-up probe (/v1/models) and anything else.
			w.Header().Set("Content-Type", "application/json")
			fmt.Fprint(w, `{"object":"list","data":[]}`)
		}
	}))
	t.Cleanup(srv.Close)
	return srv
}

// credGWLogSink is the control plane's log webhook: it keeps every entry.
type credGWLogSink struct {
	*httptest.Server
	mu      sync.Mutex
	entries []map[string]any
}

func startCredGWLogSink(t *testing.T) *credGWLogSink {
	t.Helper()
	s := &credGWLogSink{}
	s.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var batch struct {
			Logs []map[string]any `json:"logs"`
		}
		_ = json.NewDecoder(r.Body).Decode(&batch)
		s.mu.Lock()
		s.entries = append(s.entries, batch.Logs...)
		s.mu.Unlock()
		w.WriteHeader(http.StatusOK)
	}))
	t.Cleanup(s.Close)
	return s
}

type credGW struct {
	srv     *Server
	flusher *loggingplugin.LogFlusher
	logs    *credGWLogSink
}

// newCredGW builds the gateway; record, when set, sees each native Google
// request the provider sends upstream.
func newCredGW(t *testing.T, record func(*http.Request)) *credGW {
	t.Helper()
	upstream := credGWUpstream(t, record)
	g := &credGW{logs: startCredGWLogSink(t)}

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "credgw", Key: credGWKey, Owner: "o", KeyType: "byok",
		Metadata: map[string]string{"org_id": credGWOrg}}}
	cfg.Logging.RequestLogging.IncludeBodies = true
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: upstream.URL, APIFormat: "openai", Models: []string{"gpt-4o"}}
	cfg.Providers["gemini"] = config.ProviderConfig{BaseURL: upstream.URL, APIFormat: "gemini", Models: []string{credGWGemini}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	tenants := tenant.NewStore()
	tenants.Set(credGWOrg, &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"openai": {Enabled: true, APIKey: credGWOrgKey, Models: []string{"gpt-4o"}},
		"gemini": {Enabled: true, APIKey: credGWOrgKey, Models: []string{credGWGemini}},
	}})
	ks := auth.NewKeyStore(cfg.Auth)
	g.flusher = loggingplugin.NewLogFlusher(g.logs.URL, "", time.Hour, 100)
	logPlugin := loggingplugin.New(config.RequestLoggingConfig{Enabled: true, IncludeBodies: true}, tenants)
	logPlugin.SetFlusher(g.flusher)
	engine := pipeline.NewEngine(authplugin.New(ks, true), logPlugin)
	g.srv = New(cfg, "", registry, engine, ks, nil, nil, nil, testModelDBPtr(), tenants, nil)
	g.srv.ready.Store(true)
	if g.srv.asyncWorker != nil {
		t.Cleanup(g.srv.asyncWorker.Stop)
	}
	return g
}

// serve sends req to the gateway and returns what the client got.
func (g *credGW) serve(t *testing.T, req *http.Request) (int, string) {
	t.Helper()
	ctx, cancel := context.WithTimeout(req.Context(), 5*time.Second)
	defer cancel()
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req.WithContext(ctx))
	b, _ := io.ReadAll(w.Result().Body)
	return w.Code, string(b)
}

// entry delivers the buffered log entries and returns the only one.
func (g *credGW) entry(t *testing.T) map[string]any {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	g.flusher.Close(ctx)
	g.logs.mu.Lock()
	defer g.logs.mu.Unlock()
	if len(g.logs.entries) != 1 {
		t.Fatalf("%d log entries, want 1: %v", len(g.logs.entries), g.logs.entries)
	}
	return g.logs.entries[0]
}

func credGWRequest(path, body string) *http.Request {
	req := httptest.NewRequest(http.MethodPost, path, bytes.NewReader([]byte(body)))
	req.Header.Set("Content-Type", "application/json")
	return req
}

func credGWChatBody() string {
	return `{"model":"gpt-4o","messages":[{"role":"user","content":"` + credGWPrompt + `"}]}`
}

func credGWMessagesBody() string {
	return `{"model":"gpt-4o","max_tokens":16,"messages":[{"role":"user","content":"` + credGWPrompt + `"}]}`
}

func credGWGenAIBody() string {
	return `{"contents":[{"role":"user","parts":[{"text":"` + credGWPrompt + `"}]}]}`
}
