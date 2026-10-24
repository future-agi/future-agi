package server

import (
	"bytes"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const (
	aclEveryOrg = "org-acl-every"
	aclEveryKey = "sk-acl-every"
)

// aclUpstream counts every request it is sent, on any path, except the
// registry's connectivity check and model discovery (GET /v1/models), and
// keeps the body of the last one.
type aclUpstream struct {
	*httptest.Server
	calls atomic.Int32
	last  atomic.Pointer[[]byte]
}

// lastBody is the body of the last request the upstream was sent.
func (u *aclUpstream) lastBody() []byte {
	if b := u.last.Load(); b != nil {
		return *b
	}
	return nil
}

func startACLUpstream(t *testing.T) *aclUpstream {
	t.Helper()
	u := &aclUpstream{}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet && strings.HasSuffix(r.URL.Path, "/models") {
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"object":"list","data":[]}`))
			return
		}
		body, _ := io.ReadAll(r.Body)
		u.last.Store(&body)
		u.calls.Add(1)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"id":"x","object":"chat.completion","model":"m-chat","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`))
	}))
	t.Cleanup(u.Close)
	return u
}

// aclModels are the models org aclEveryOrg reaches through its own key on
// provider "openai", one per endpoint kind.
var aclModels = []string{"m-chat", "m-embed", "m-image", "m-tts", "m-stt", "m-rerank", "m-ocr", "m-search", "gpt-4o"}

// newEveryEndpointACLServer serves aclModels through the org's own key on
// "openai" (a BYOK instance of the global provider, so it calls upstreamURL)
// to a BYOK key of aclEveryOrg that may use only the allowed providers.
func newEveryEndpointACLServer(t *testing.T, upstreamURL string, allowed []string) *Server {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{
		Name: "acl-every", Key: aclEveryKey, Owner: "t", KeyType: "byok",
		Providers: allowed, Metadata: map[string]string{"org_id": aclEveryOrg},
	}}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: upstreamURL, APIKey: "operator-key", APIFormat: "openai", Models: []string{"m-realtime"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	tenants := tenant.NewStore()
	tenants.Set(aclEveryOrg, &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"openai": {APIKey: "org-key", Enabled: true, Models: aclModels},
	}})
	keyStore := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keyStore, true)), keyStore, nil, nil, nil, testModelDBPtr(), tenants, nil)
	srv.ready.Store(true)
	return srv
}

// multipartAudio is a transcription or translation body for model m-stt.
func multipartAudio(t *testing.T) (string, string) {
	t.Helper()
	var buf bytes.Buffer
	mw := multipart.NewWriter(&buf)
	_ = mw.WriteField("model", "m-stt")
	fw, err := mw.CreateFormFile("file", "a.wav")
	if err != nil {
		t.Fatal(err)
	}
	_, _ = fw.Write([]byte("RIFF0000WAVE"))
	_ = mw.Close()
	return buf.String(), mw.FormDataContentType()
}

type aclEndpoint struct {
	name, method, path, body, contentType string
}

// routingEndpoints is every endpoint that routes a request to a provider.
// Files, videos, token counting, stored responses, async jobs and scheduled
// jobs do not (they serve local stores or refuse org keys), and
// TestRealtimeEnforcesKeyProviderACL covers the WebSocket endpoint.
func routingEndpoints(t *testing.T) []aclEndpoint {
	audio, audioType := multipartAudio(t)
	const msgs = `"messages":[{"role":"user","content":"hi"}]`
	return []aclEndpoint{
		{"chat", "POST", "/v1/chat/completions", `{"model":"m-chat",` + msgs + `}`, ""},
		{"completions", "POST", "/v1/completions", `{"model":"m-chat","prompt":"hi"}`, ""},
		{"embeddings", "POST", "/v1/embeddings", `{"model":"m-embed","input":"hi"}`, ""},
		{"images", "POST", "/v1/images/generations", `{"model":"m-image","prompt":"a cat"}`, ""},
		{"speech", "POST", "/v1/audio/speech", `{"model":"m-tts","input":"hi","voice":"alloy"}`, ""},
		{"speech stream", "POST", "/v1/audio/speech/stream", `{"model":"m-tts","input":"hi","voice":"alloy"}`, ""},
		{"transcriptions", "POST", "/v1/audio/transcriptions", audio, audioType},
		{"translations", "POST", "/v1/audio/translations", audio, audioType},
		{"rerank", "POST", "/v1/rerank", `{"model":"m-rerank","query":"q","documents":["a","b"]}`, ""},
		{"responses", "POST", "/v1/responses", `{"model":"m-chat","input":"hi"}`, ""},
		{"messages", "POST", "/v1/messages", `{"model":"m-chat","max_tokens":16,` + msgs + `}`, ""},
		{"messages count_tokens", "POST", "/v1/messages/count_tokens", `{"model":"m-chat",` + msgs + `}`, ""},
		{"genai", "POST", "/v1beta/models/m-chat:generateContent", `{"contents":[{"role":"user","parts":[{"text":"hi"}]}]}`, ""},
		{"search", "POST", "/v1/search", `{"query":"q","search_provider":"m-search"}`, ""},
		{"ocr", "POST", "/v1/ocr", `{"model":"m-ocr","document":{"type":"document_url","document_url":"https://example.com/a.pdf"}}`, ""},
		{"assistants create", "POST", "/v1/assistants", `{"model":"gpt-4o"}`, ""},
		{"assistants list", "GET", "/v1/assistants", "", ""},
		{"threads create", "POST", "/v1/threads", `{}`, ""},
		{"thread runs", "POST", "/v1/threads/th_1/runs", `{"assistant_id":"as_1"}`, ""},
		{"vector stores create", "POST", "/v1/vector_stores", `{"name":"v"}`, ""},
		{"vector stores list", "GET", "/v1/vector_stores", "", ""},
	}
}

func (e aclEndpoint) serve(srv *Server) *httptest.ResponseRecorder {
	req := httptest.NewRequest(e.method, e.path, strings.NewReader(e.body))
	ct := e.contentType
	if ct == "" {
		ct = "application/json"
	}
	req.Header.Set("Content-Type", ct)
	req.Header.Set("Authorization", "Bearer "+aclEveryKey)
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

const aclDenied = "API key does not have access to provider: openai"

// TestEveryRoutingEndpointEnforcesKeyProviderACL: on every endpoint that
// routes to a provider, a key allowed only another provider is refused with
// 403 before the provider is called; the same key allowed the provider gets
// past the check, which proves each row reaches the provider.
func TestEveryRoutingEndpointEnforcesKeyProviderACL(t *testing.T) {
	for _, e := range routingEndpoints(t) {
		t.Run(e.name, func(t *testing.T) {
			up := startACLUpstream(t)
			denied := e.serve(newEveryEndpointACLServer(t, up.URL, []string{"anthropic"}))
			if denied.Code != http.StatusForbidden || !strings.Contains(denied.Body.String(), aclDenied) || up.calls.Load() != 0 {
				t.Fatalf("key allowed only anthropic: status %d, upstream calls %d, body %s; want 403 naming provider openai and no upstream call",
					denied.Code, up.calls.Load(), denied.Body)
			}
			allowed := e.serve(newEveryEndpointACLServer(t, up.URL, []string{"anthropic", "openai"}))
			if strings.Contains(allowed.Body.String(), aclDenied) {
				t.Fatalf("key allowed openai was refused: status %d, body %s", allowed.Code, allowed.Body)
			}
		})
	}
}

// TestCompletionsEnforcesKeyProviderACL: /v1/completions refuses a key whose
// allowed providers leave out the routed one, before the provider is
// called, and serves a key that names it.
func TestCompletionsEnforcesKeyProviderACL(t *testing.T) {
	e := aclEndpoint{"completions", "POST", "/v1/completions", `{"model":"m-chat","prompt":"hi"}`, ""}
	up := startACLUpstream(t)
	if w := e.serve(newEveryEndpointACLServer(t, up.URL, []string{"anthropic"})); w.Code != http.StatusForbidden ||
		!strings.Contains(w.Body.String(), aclDenied) || up.calls.Load() != 0 {
		t.Fatalf("status %d, upstream calls %d, body %s; want 403 and no upstream call", w.Code, up.calls.Load(), w.Body)
	}
	if w := e.serve(newEveryEndpointACLServer(t, up.URL, []string{"openai"})); w.Code != http.StatusOK || up.calls.Load() != 1 {
		t.Fatalf("allowed key: status %d, upstream calls %d, body %s; want 200 from one upstream call", w.Code, up.calls.Load(), w.Body)
	}
}

// TestRealtimeEnforcesKeyProviderACL: the realtime endpoint, which
// authenticates its key itself, never dials the upstream for a key that may
// not use the provider serving the model: 403 permission_denied, no upstream
// call. It refuses every key but an internal one before it resolves a
// provider ("model ... is not available for this API key"), so this BYOK
// key of an org is refused whatever its allowed_providers, and the provider
// ACL check after resolution ("API key does not have access to provider")
// is a second guard. realtime_org_confinement_test.go pins the first
// refusal, an internal key included.
func TestRealtimeEnforcesKeyProviderACL(t *testing.T) {
	up := startACLUpstream(t)
	for _, allowed := range [][]string{{"anthropic"}, {"openai"}} {
		srv := newEveryEndpointACLServer(t, up.URL, allowed)
		req := httptest.NewRequest(http.MethodGet, "/v1/realtime?model=m-realtime", nil)
		req.Header.Set("Authorization", "Bearer "+aclEveryKey)
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)
		if w.Code != http.StatusForbidden || !strings.Contains(w.Body.String(), `"code":"permission_denied"`) || up.calls.Load() != 0 {
			t.Fatalf("allowed %v: status %d, body %s, upstream calls %d; want 403 permission_denied before any upstream call",
				allowed, w.Code, w.Body, up.calls.Load())
		}
	}
}

// TestKeyACLRecordedBeforeRouting: peekKeyOrgID records the key's provider
// and model ACL in the form the auth plugin writes, and nothing for a key
// with no restriction.
func TestKeyACLRecordedBeforeRouting(t *testing.T) {
	ks := auth.NewKeyStore(config.AuthConfig{Enabled: true, Keys: []config.AuthKeyConfig{
		{Name: "r", Key: "sk-restricted", KeyType: "byok", Providers: []string{"openai", "anthropic"}, Models: []string{"m1", "m2"}},
		{Name: "o", Key: "sk-open", KeyType: "byok"},
	}})
	h := &Handlers{keyStore: ks}
	for _, tc := range []struct{ key, providers, models string }{
		{"sk-restricted", "openai,anthropic", "m1,m2"},
		{"sk-open", "", ""},
	} {
		rc := models.AcquireRequestContext()
		rc.Metadata["authorization"] = "Bearer " + tc.key
		h.peekKeyOrgID(rc)
		gotP, okP := rc.Metadata[metaKeyAllowedProviders]
		gotM, okM := rc.Metadata[metaKeyAllowedModels]
		if gotP != tc.providers || gotM != tc.models || okP != (tc.providers != "") || okM != (tc.models != "") {
			t.Errorf("key %s: providers %q (%v), models %q (%v); want %q and %q", tc.key, gotP, okP, gotM, okM, tc.providers, tc.models)
		}
		rc.Release()
	}
}

// TestKeyProviderACLFailsClosedOnUnresolvedProvider: a key restricted to
// some providers is refused when no provider was recorded, and a key with
// no restriction, or an internal key, is not.
func TestKeyProviderACLFailsClosedOnUnresolvedProvider(t *testing.T) {
	h := &Handlers{}
	for _, tc := range []struct {
		name   string
		meta   map[string]string
		refuse bool
	}{
		{"restricted key", map[string]string{metaKeyAllowedProviders: "openai"}, true},
		{"unrestricted key", map[string]string{}, false},
		{"internal key", map[string]string{metaKeyAllowedProviders: "openai", "key_type": "internal"}, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rc := &models.RequestContext{Metadata: tc.meta}
			err := h.keyProviderACL(rc)
			if (err != nil) != tc.refuse {
				t.Fatalf("keyProviderACL = %v, want refused %v", err, tc.refuse)
			}
			if err != nil && err.Status != http.StatusForbidden {
				t.Fatalf("status %d, want 403", err.Status)
			}
		})
	}
}
