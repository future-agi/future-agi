package server

// The Assistants, Vector Stores and Files APIs run the pipeline as the model
// endpoints do. A key's allowed providers and models, rate
// limits and the request log apply to them, and the assistants and
// vector-store routes never use managed credentials.

import (
	"bytes"
	"context"
	"encoding/json"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	loggingplugin "github.com/futureagi/agentcc-gateway/internal/plugins/logging"
	ratelimitplugin "github.com/futureagi/agentcc-gateway/internal/plugins/ratelimit"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const (
	asstOrg = "org-asst"
	asstKey = "sk-asst-key"
)

// asstKeyOpts is the one key of an assistants test gateway.
type asstKeyOpts struct {
	keyType   string // "byok" when empty
	providers []string
	models    []string
	rpm       int
	// groups are the key's access groups, as the gateway's config defines
	// them (routing.access_groups).
	groups config.AccessGroupsConfig
}

type asstGateway struct {
	srv     *Server
	up      *aclUpstream
	sink    *logSink
	flusher *loggingplugin.LogFlusher
}

// newAsstGateway serves the key k of org asstOrg. The org's own key is on
// provider "openai" (a BYOK instance of the global provider, which calls
// up), unless managedOpenAI makes the global "openai" provider a managed one
// and leaves the org without a key of its own. The pipeline is the auth,
// rate-limit and request-log plugins, with the log flushed to sink.
func newAsstGateway(t *testing.T, k asstKeyOpts, managedOpenAI bool) *asstGateway {
	t.Helper()
	up := startACLUpstream(t)
	sink := startLogSink(t)
	keyType := k.keyType
	if keyType == "" {
		keyType = "byok"
	}
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	meta := map[string]string{"org_id": asstOrg}
	if len(k.groups) > 0 {
		names := make([]string, 0, len(k.groups))
		for name := range k.groups {
			names = append(names, name)
		}
		sort.Strings(names)
		meta["access_groups"] = strings.Join(names, ",")
		cfg.Routing.AccessGroups = k.groups
	}
	cfg.Auth.Keys = []config.AuthKeyConfig{{
		Name: "asst", Key: asstKey, Owner: "t", KeyType: keyType,
		Providers: k.providers, Models: k.models, RateLimitRPM: k.rpm,
		Metadata: meta,
	}}
	off := false
	cfg.Providers["openai"] = config.ProviderConfig{
		BaseURL: up.URL, APIKey: "operator-key", APIFormat: "openai",
		Models: []string{"gpt-4o", "gpt-4o-mini"}, AutoDiscover: &off, Managed: managedOpenAI,
	}
	cfg.RateLimiting.Enabled = true
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("registry: %v", err)
	}
	tenants := tenant.NewStore()
	org := &tenant.OrgConfig{}
	if !managedOpenAI {
		org.Providers = map[string]*tenant.ProviderConfig{
			"openai": {APIKey: "org-key", Enabled: true, Models: []string{"gpt-4o", "gpt-4o-mini"}},
		}
	}
	tenants.Set(asstOrg, org)
	ks := auth.NewKeyStore(cfg.Auth)
	flusher := loggingplugin.NewLogFlusher(sink.URL, "", time.Hour, 100)
	logPlugin := loggingplugin.New(config.RequestLoggingConfig{Enabled: true}, tenants)
	logPlugin.SetFlusher(flusher)
	rl := ratelimitplugin.New(cfg.RateLimiting, ks, nil)
	t.Cleanup(rl.Stop)
	engine := pipeline.NewEngine(authplugin.New(ks, true), rl, logPlugin)
	srv := New(cfg, "", registry, engine, ks, nil, nil, nil, testModelDBPtr(), tenants, nil)
	srv.ready.Store(true)
	return &asstGateway{srv: srv, up: up, sink: sink, flusher: flusher}
}

func (g *asstGateway) do(method, path, body, contentType string) *httptest.ResponseRecorder {
	if contentType == "" {
		contentType = "application/json"
	}
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	req.Header.Set("Content-Type", contentType)
	req.Header.Set("Authorization", "Bearer "+asstKey)
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

// logged is every request log entry the gateway sent, once it has flushed.
func (g *asstGateway) logged(t *testing.T) []map[string]any {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	g.flusher.Close(ctx)
	g.sink.mu.Lock()
	defer g.sink.mu.Unlock()
	return append([]map[string]any(nil), g.sink.entries...)
}

// errorObject is the "error" object of an error answer.
func errorObject(t *testing.T, w *httptest.ResponseRecorder) map[string]any {
	t.Helper()
	var body struct {
		Error map[string]any `json:"error"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil || body.Error == nil {
		t.Fatalf("status %d: not an error answer: %s", w.Code, w.Body)
	}
	return body.Error
}

// asstModelRequests are the requests of the Assistants API that name the
// model they run on: an assistant's model, and a run's model override.
func asstModelRequests(model string) []aclEndpoint {
	m := `"model":"` + model + `"`
	return []aclEndpoint{
		{"assistants create", "POST", "/v1/assistants", `{` + m + `}`, ""},
		{"assistants update", "POST", "/v1/assistants/asst_1", `{` + m + `}`, ""},
		{"runs create", "POST", "/v1/threads/th_1/runs", `{"assistant_id":"asst_1",` + m + `}`, ""},
		{"runs create, streamed", "POST", "/v1/threads/th_1/runs", `{"assistant_id":"asst_1","stream":true,` + m + `}`, ""},
		{"thread and run", "POST", "/v1/threads/runs", `{"assistant_id":"asst_1",` + m + `}`, ""},
	}
}

// TestAssistantsEnforceKeyModelACL: a key allowed provider "openai" and model
// gpt-4o is refused on every Assistants request that names gpt-4o-mini (or
// openai/gpt-4o-mini), with the chat endpoint's answer for that model, before
// the provider is called; the same request naming gpt-4o reaches it.
func TestAssistantsEnforceKeyModelACL(t *testing.T) {
	key := asstKeyOpts{providers: []string{"openai"}, models: []string{"gpt-4o"}}
	for _, denied := range []string{"gpt-4o-mini", "openai/gpt-4o-mini"} {
		chat := newAsstGateway(t, key, false).do("POST", "/v1/chat/completions",
			`{"model":"`+denied+`","messages":[{"role":"user","content":"hi"}]}`, "")
		if chat.Code != http.StatusForbidden {
			t.Fatalf("chat with %s: status %d, want 403: %s", denied, chat.Code, chat.Body)
		}
		want := errorObject(t, chat)
		for _, e := range asstModelRequests(denied) {
			t.Run(e.name+" / "+denied, func(t *testing.T) {
				g := newAsstGateway(t, key, false)
				w := g.do(e.method, e.path, e.body, "")
				if w.Code != chat.Code || g.up.calls.Load() != 0 {
					t.Fatalf("status %d, upstream calls %d; want %d and none: %s", w.Code, g.up.calls.Load(), chat.Code, w.Body)
				}
				if got := errorObject(t, w); !reflect.DeepEqual(got, want) {
					t.Errorf("error = %v, want the chat endpoint's %v", got, want)
				}
			})
		}
	}
	for _, e := range asstModelRequests("gpt-4o") {
		t.Run(e.name+" / allowed", func(t *testing.T) {
			g := newAsstGateway(t, key, false)
			if w := g.do(e.method, e.path, e.body, ""); w.Code != http.StatusOK || g.up.calls.Load() != 1 {
				t.Fatalf("status %d, upstream calls %d; want 200 from one upstream call: %s", w.Code, g.up.calls.Load(), w.Body)
			}
		})
	}
}

// TestAssistantsEnforceKeyProviderACL: a key allowed only provider
// "anthropic" (and model gpt-4o) is refused on the Assistants and Vector
// Stores APIs, which provider "openai" serves, with the chat endpoint's
// answer for a model provider "openai" serves, before the provider is
// called.
func TestAssistantsEnforceKeyProviderACL(t *testing.T) {
	key := asstKeyOpts{providers: []string{"anthropic"}, models: []string{"gpt-4o"}}
	chat := newAsstGateway(t, key, false).do("POST", "/v1/chat/completions",
		`{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}`, "")
	if chat.Code != http.StatusForbidden || !strings.Contains(chat.Body.String(), aclDenied) {
		t.Fatalf("chat: status %d, want 403 %q: %s", chat.Code, aclDenied, chat.Body)
	}
	want := errorObject(t, chat)
	for _, e := range append(asstModelRequests("gpt-4o"),
		aclEndpoint{"assistants list", "GET", "/v1/assistants", "", ""},
		aclEndpoint{"vector store search", "POST", "/v1/vector_stores/vs_1/search", `{"query":"q"}`, ""},
	) {
		t.Run(e.name, func(t *testing.T) {
			g := newAsstGateway(t, key, false)
			w := g.do(e.method, e.path, e.body, "")
			if w.Code != chat.Code || g.up.calls.Load() != 0 {
				t.Fatalf("status %d, upstream calls %d; want %d and none: %s", w.Code, g.up.calls.Load(), chat.Code, w.Body)
			}
			if got := errorObject(t, w); !reflect.DeepEqual(got, want) {
				t.Errorf("error = %v, want the chat endpoint's %v", got, want)
			}
		})
	}
}

// filesList is GET /v1/files, served from the gateway's own file store.
var filesList = aclEndpoint{"files list", "GET", "/v1/files", "", ""}

// passedThroughRoutes is one route of each kind on the Assistants, Vector
// Stores and Files APIs: with a body the checks read, with one they do not,
// with none, a stream, and the gateway's own file store.
func passedThroughRoutes() []aclEndpoint {
	return []aclEndpoint{
		{"assistants create", "POST", "/v1/assistants", `{"model":"gpt-4o"}`, ""},
		{"assistants list", "GET", "/v1/assistants", "", ""},
		{"thread update", "POST", "/v1/threads/th_1", `{"metadata":{"a":"b"}}`, ""},
		{"runs create, streamed", "POST", "/v1/threads/th_1/runs", `{"assistant_id":"asst_1","stream":true}`, ""},
		{"vector stores list", "GET", "/v1/vector_stores", "", ""},
		{"vector store search", "POST", "/v1/vector_stores/vs_1/search", `{"query":"q"}`, ""},
		filesList,
	}
}

// TestPassedThroughRoutesRateLimited: a key's rate_limit_rpm applies to the
// Assistants, Vector Stores and Files APIs as to the model endpoints: with
// an RPM of 1 the second request is refused with 429 rate_limit_exceeded,
// and the provider gets only the first.
func TestPassedThroughRoutesRateLimited(t *testing.T) {
	for _, e := range passedThroughRoutes() {
		t.Run(e.name, func(t *testing.T) {
			g := newAsstGateway(t, asstKeyOpts{rpm: 1}, false)
			if w := g.do(e.method, e.path, e.body, ""); w.Code != http.StatusOK {
				t.Fatalf("first request: status %d, want 200: %s", w.Code, w.Body)
			}
			calls := g.up.calls.Load()
			w := g.do(e.method, e.path, e.body, "")
			if w.Code != http.StatusTooManyRequests || errorObject(t, w)["code"] != "rate_limit_exceeded" {
				t.Fatalf("second request: status %d, want 429 rate_limit_exceeded: %s", w.Code, w.Body)
			}
			if g.up.calls.Load() != calls {
				t.Errorf("upstream calls %d after the refusal, want %d: a refused request never reaches the provider", g.up.calls.Load(), calls)
			}
		})
	}
}

// TestPassedThroughRoutesLogged: every request on the Assistants, Vector
// Stores and Files APIs has a request log entry, with the status the client
// got, the model it names (none for a list) and its operation; a streamed run
// is logged once its stream has ended.
func TestPassedThroughRoutesLogged(t *testing.T) {
	for _, e := range passedThroughRoutes() {
		t.Run(e.name, func(t *testing.T) {
			g := newAsstGateway(t, asstKeyOpts{}, false)
			if w := g.do(e.method, e.path, e.body, ""); w.Code != http.StatusOK {
				t.Fatalf("status %d, want 200: %s", w.Code, w.Body)
			}
			entries := g.logged(t)
			if len(entries) != 1 {
				t.Fatalf("%d request log entries, want 1: %v", len(entries), entries)
			}
			got := entries[0]
			if code, _ := got["status_code"].(float64); code != http.StatusOK {
				t.Errorf("logged status_code = %v, want 200", got["status_code"])
			}
			wantModel := ""
			if strings.Contains(e.body, `"model":"gpt-4o"`) {
				wantModel = "gpt-4o"
			}
			if m, _ := got["model"].(string); m != wantModel {
				t.Errorf("logged model = %q, want %q", m, wantModel)
			}
			meta, _ := got["metadata"].(map[string]any)
			op := ""
			for _, k := range []string{"assistants_operation", "vector_stores_operation", "files_operation"} {
				if v, ok := meta[k].(string); ok && v != "" {
					op = v
				}
			}
			if op == "" {
				t.Errorf("logged metadata names no operation: %v", meta)
			}
		})
	}
}

// TestFileUploadLogged: an upload to the gateway's file store has a request
// log entry, with status 200 and operation files.upload.
func TestFileUploadLogged(t *testing.T) {
	var buf bytes.Buffer
	mw := multipart.NewWriter(&buf)
	_ = mw.WriteField("purpose", "assistants")
	fw, err := mw.CreateFormFile("file", "a.txt")
	if err != nil {
		t.Fatal(err)
	}
	_, _ = fw.Write([]byte("hello"))
	_ = mw.Close()

	g := newAsstGateway(t, asstKeyOpts{}, false)
	if w := g.do("POST", "/v1/files", buf.String(), mw.FormDataContentType()); w.Code != http.StatusOK {
		t.Fatalf("status %d, want 200: %s", w.Code, w.Body)
	}
	entries := g.logged(t)
	if len(entries) != 1 {
		t.Fatalf("%d request log entries, want 1", len(entries))
	}
	meta, _ := entries[0]["metadata"].(map[string]any)
	if code, _ := entries[0]["status_code"].(float64); code != http.StatusOK || meta["files_operation"] != "files.upload" {
		t.Errorf("logged status_code %v, files_operation %v; want 200 and files.upload", entries[0]["status_code"], meta["files_operation"])
	}
}

// TestAssistantsNeverUseManagedCredentials: when the gateway's "openai"
// provider is a managed one (FutureAGI's credentials), the Assistants and
// Vector Stores APIs are refused with 501 not_supported to every key, an
// internal one included, and the provider is never called: the gateway
// meters no usage on these APIs, so managed credentials never serve them.
func TestAssistantsNeverUseManagedCredentials(t *testing.T) {
	for _, keyType := range []string{"internal", "byok"} {
		for _, e := range []aclEndpoint{
			{"assistants create", "POST", "/v1/assistants", `{"model":"gpt-4o"}`, ""},
			{"assistants list", "GET", "/v1/assistants", "", ""},
			{"threads create", "POST", "/v1/threads", `{}`, ""},
			{"runs create", "POST", "/v1/threads/th_1/runs", `{"assistant_id":"asst_1"}`, ""},
			{"runs create, streamed", "POST", "/v1/threads/th_1/runs", `{"assistant_id":"asst_1","stream":true}`, ""},
			{"submit tool outputs", "POST", "/v1/threads/th_1/runs/run_1/submit_tool_outputs", `{"tool_outputs":[]}`, ""},
			{"vector stores create", "POST", "/v1/vector_stores", `{"name":"v"}`, ""},
			{"vector store search", "POST", "/v1/vector_stores/vs_1/search", `{"query":"q"}`, ""},
		} {
			t.Run(keyType+" / "+e.name, func(t *testing.T) {
				g := newAsstGateway(t, asstKeyOpts{keyType: keyType}, true)
				w := g.do(e.method, e.path, e.body, "")
				if w.Code != http.StatusNotImplemented || errorObject(t, w)["code"] != "not_supported" || g.up.calls.Load() != 0 {
					t.Fatalf("status %d, upstream calls %d; want 501 not_supported and none: %s", w.Code, g.up.calls.Load(), w.Body)
				}
			})
		}
	}
}

// TestAssistantsNeverManaged is TestVectorStoresNeverManaged for the
// Assistants API: an org that has opted in to managed models, has credit and
// has no OpenAI key of its own gets 501 not_supported on every Assistants
// route, nothing serves the request, and the managed ledger records nothing.
func TestAssistantsNeverManaged(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	g.ledger.SetMicros("org-m", auth.USDToMicros(5))
	for _, rt := range []struct{ method, path, body string }{
		{"POST", "/v1/assistants", `{"model":"gpt-4o"}`},
		{"GET", "/v1/assistants", ""},
		{"POST", "/v1/assistants/asst_1", `{"model":"gpt-4o"}`},
		{"POST", "/v1/threads", `{}`},
		{"POST", "/v1/threads/th_1/messages", `{"role":"user","content":"hi"}`},
		{"POST", "/v1/threads/th_1/runs", `{"assistant_id":"asst_1","model":"gpt-4o"}`},
		{"POST", "/v1/threads/th_1/runs", `{"assistant_id":"asst_1","stream":true}`},
		{"POST", "/v1/threads/runs", `{"assistant_id":"asst_1"}`},
		{"GET", "/v1/threads/th_1/runs/run_1", ""},
		{"POST", "/v1/threads/th_1/runs/run_1/submit_tool_outputs", `{"tool_outputs":[]}`},
	} {
		t.Run(rt.method+" "+rt.path, func(t *testing.T) {
			req := httptest.NewRequest(rt.method, rt.path, bytes.NewBufferString(rt.body))
			req.Header.Set("Authorization", "Bearer "+managedOrgKey)
			req.Header.Set("Content-Type", "application/json")
			w := httptest.NewRecorder()
			g.srv.httpServer.Handler.ServeHTTP(w, req)
			// The managed upstream answers any non-chat path with 200, so a
			// request it served would come back 200.
			if w.Code != http.StatusNotImplemented || !strings.Contains(w.Body.String(), `"not_supported"`) {
				t.Fatalf("status = %d, want 501 not_supported: %s", w.Code, w.Body)
			}
			if p := w.Header().Get("x-agentcc-provider"); p != "" {
				t.Errorf("x-agentcc-provider = %q; nothing should have served the request", p)
			}
		})
	}
	if n := len(g.outbox("org-m")); n != 0 {
		t.Errorf("settlement records = %d, want 0: no Assistants call may bill the managed ledger", n)
	}
}

// asstCraftedBodies are bodies chat refuses as invalid JSON (those that carry
// a model also name denied), on every Assistants request that reads a model
// or a stream. The Assistants API refuses each of them with chat's answer
// before the provider is called, so the model the key's ACL checks is always
// the model OpenAI reads.
func asstCraftedBodies(denied string) []aclEndpoint {
	var out []aclEndpoint
	for _, first := range []string{`0`, `{}`} {
		m := `"model":` + first + `,"model":"` + denied + `"`
		out = append(out,
			aclEndpoint{"assistants create, model " + first, "POST", "/v1/assistants", `{` + m + `}`, ""},
			aclEndpoint{"assistants update, model " + first, "POST", "/v1/assistants/asst_1", `{` + m + `}`, ""},
			aclEndpoint{"runs create, model " + first, "POST", "/v1/threads/th_1/runs", `{"assistant_id":"asst_1",` + m + `}`, ""},
			aclEndpoint{"runs create, streamed, model " + first, "POST", "/v1/threads/th_1/runs", `{"assistant_id":"asst_1","stream":true,` + m + `}`, ""},
			aclEndpoint{"thread and run, model " + first, "POST", "/v1/threads/runs", `{"assistant_id":"asst_1",` + m + `}`, ""},
		)
	}
	s := `"stream":0,"stream":true`
	return append(out,
		aclEndpoint{"runs create, stream 0 then true", "POST", "/v1/threads/th_1/runs", `{"assistant_id":"asst_1",` + s + `}`, ""},
		aclEndpoint{"thread and run, stream 0 then true", "POST", "/v1/threads/runs", `{"assistant_id":"asst_1",` + s + `}`, ""},
		aclEndpoint{"submit tool outputs, stream 0 then true", "POST", "/v1/threads/th_1/runs/run_1/submit_tool_outputs", `{"tool_outputs":[],` + s + `}`, ""},
	)
}

// TestAssistantsRefuseTheBodyChatRefuses: a key allowed only gpt-4o sends
// each body of asstCraftedBodies; the Assistants API answers as chat answers
// the same body (400 invalid_request_error invalid_json), before the provider
// is called. The message is not compared: it quotes the decoder's error, which
// names the Go type each endpoint decodes into.
func TestAssistantsRefuseTheBodyChatRefuses(t *testing.T) {
	key := asstKeyOpts{providers: []string{"openai"}, models: []string{"gpt-4o"}}
	for _, e := range asstCraftedBodies("gpt-4o-mini") {
		t.Run(e.name, func(t *testing.T) {
			chat := newAsstGateway(t, key, false).do("POST", "/v1/chat/completions", e.body, "")
			want := errorObject(t, chat)
			if chat.Code != http.StatusBadRequest || want["code"] != "invalid_json" {
				t.Fatalf("chat: status %d, want 400 invalid_json: %s", chat.Code, chat.Body)
			}
			g := newAsstGateway(t, key, false)
			w := g.do(e.method, e.path, e.body, "")
			if w.Code != chat.Code || g.up.calls.Load() != 0 {
				t.Fatalf("status %d, upstream calls %d; want chat's %d and none; body %s, upstream got %s",
					w.Code, g.up.calls.Load(), chat.Code, w.Body, g.up.lastBody())
			}
			got := errorObject(t, w)
			for _, f := range []string{"type", "code"} {
				if got[f] != want[f] {
					t.Errorf("error %s = %v, want chat's %v", f, got[f], want[f])
				}
			}
		})
	}
}

// modelMembers is each top-level "model" member of an upstream body, in
// order: the members as written, before any decoder merges two of a name.
func modelMembers(t *testing.T, body []byte) []string {
	t.Helper()
	dec := json.NewDecoder(bytes.NewReader(body))
	if tok, err := dec.Token(); err != nil || tok != json.Delim('{') {
		t.Fatalf("upstream body is not an object: %s", body)
	}
	var out []string
	for dec.More() {
		name, err := dec.Token()
		if err != nil {
			t.Fatalf("upstream body %s: %v", body, err)
		}
		var v json.RawMessage
		if err := dec.Decode(&v); err != nil {
			t.Fatalf("upstream body %s: %v", body, err)
		}
		if name == "model" {
			out = append(out, string(v))
		}
	}
	return out
}

// TestAssistantsSendTheModelTheyCheck: the provider gets one "model", the
// one the key's ACL was checked on: a body naming a model twice is sent with
// the last, as OpenAI reads it, and a provider prefix is stripped.
func TestAssistantsSendTheModelTheyCheck(t *testing.T) {
	key := asstKeyOpts{providers: []string{"openai"}, models: []string{"gpt-4o", "openai/gpt-4o"}}
	for _, model := range []string{`"gpt-4o-mini","model":"gpt-4o"`, `"openai/gpt-4o"`} {
		for _, e := range asstModelRequests("") {
			e.body = strings.Replace(e.body, `"model":""`, `"model":`+model, 1)
			t.Run(e.name+" / "+model, func(t *testing.T) {
				g := newAsstGateway(t, key, false)
				if w := g.do(e.method, e.path, e.body, ""); w.Code != http.StatusOK || g.up.calls.Load() != 1 {
					t.Fatalf("status %d, upstream calls %d; want 200 from one upstream call: %s", w.Code, g.up.calls.Load(), w.Body)
				}
				if got := modelMembers(t, g.up.lastBody()); !reflect.DeepEqual(got, []string{`"gpt-4o"`}) {
					t.Errorf("upstream got model members %v, want only \"gpt-4o\": %s", got, g.up.lastBody())
				}
			})
		}
	}
}

// TestAssistantsEnforceKeyAccessGroups: a key whose access group holds only
// gpt-4o is refused on every Assistants request that names gpt-4o-mini, with
// the chat endpoint's answer for that key and model, before the provider is
// called; an alias the group defines is sent as the model it stands for, as
// chat sends it. A name a group's wildcard pattern matches, with a prefix
// that is not the provider's, gets chat's refusal too.
func TestAssistantsEnforceKeyAccessGroups(t *testing.T) {
	t.Run("wildcard group", testAssistantsWildcardGroupPrefix)
	key := asstKeyOpts{groups: config.AccessGroupsConfig{
		"g-4o": {Models: []string{"gpt-4o"}, Aliases: map[string]string{"fast": "gpt-4o"}},
	}}
	chat := newAsstGateway(t, key, false).do("POST", "/v1/chat/completions",
		`{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}`, "")
	if chat.Code != http.StatusForbidden {
		t.Fatalf("chat with gpt-4o-mini: status %d, want 403: %s", chat.Code, chat.Body)
	}
	want := errorObject(t, chat)
	for _, e := range asstModelRequests("gpt-4o-mini") {
		t.Run(e.name+" / gpt-4o-mini", func(t *testing.T) {
			g := newAsstGateway(t, key, false)
			w := g.do(e.method, e.path, e.body, "")
			if w.Code != chat.Code || g.up.calls.Load() != 0 {
				t.Fatalf("status %d, upstream calls %d; want %d and none: %s", w.Code, g.up.calls.Load(), chat.Code, w.Body)
			}
			if got := errorObject(t, w); !reflect.DeepEqual(got, want) {
				t.Errorf("error = %v, want the chat endpoint's %v", got, want)
			}
		})
	}
	for _, model := range []string{"gpt-4o", "fast"} {
		for _, e := range asstModelRequests(model) {
			t.Run(e.name+" / "+model, func(t *testing.T) {
				g := newAsstGateway(t, key, false)
				if w := g.do(e.method, e.path, e.body, ""); w.Code != http.StatusOK || g.up.calls.Load() != 1 {
					t.Fatalf("status %d, upstream calls %d; want 200 from one upstream call: %s", w.Code, g.up.calls.Load(), w.Body)
				}
				if got := modelMembers(t, g.up.lastBody()); !reflect.DeepEqual(got, []string{`"gpt-4o"`}) {
					t.Errorf("upstream got model members %v, want only \"gpt-4o\": %s", got, g.up.lastBody())
				}
			})
		}
	}
}

// testAssistantsWildcardGroupPrefix: a key whose only access group holds
// "gpt-4o-mini*" (or "claude-*"), with no allowed models or providers, names
// "gpt-4o-mini/gpt-4o" (or "claude-x/gpt-4o"), a name no provider of the key
// serves. The Assistants API refuses it with chat's answer, before the
// provider is called, so the model it sends is always the model it checked.
func testAssistantsWildcardGroupPrefix(t *testing.T) {
	for _, c := range []struct{ group, model string }{
		{"gpt-4o-mini*", "gpt-4o-mini/gpt-4o"},
		{"claude-*", "claude-x/gpt-4o"},
	} {
		key := asstKeyOpts{groups: config.AccessGroupsConfig{"g": {Models: []string{c.group}}}}
		chat := newAsstGateway(t, key, false).do("POST", "/v1/chat/completions",
			`{"model":"`+c.model+`","messages":[{"role":"user","content":"hi"}]}`, "")
		want := errorObject(t, chat)
		// Chat refuses the name as a model no provider of the key serves.
		if wantMsg := `model "` + c.model + `" is not available for this API key`; chat.Code != http.StatusForbidden || want["message"] != wantMsg {
			t.Fatalf("chat with %s: status %d, message %v; want 403 %q", c.model, chat.Code, want["message"], wantMsg)
		}
		for _, e := range asstModelRequests(c.model) {
			t.Run(e.name+" / "+c.model, func(t *testing.T) {
				g := newAsstGateway(t, key, false)
				w := g.do(e.method, e.path, e.body, "")
				if w.Code != chat.Code || g.up.calls.Load() != 0 {
					t.Fatalf("status %d, upstream calls %d (upstream got %s); want chat's %d and none: %s",
						w.Code, g.up.calls.Load(), g.up.lastBody(), chat.Code, w.Body)
				}
				if got := errorObject(t, w); !reflect.DeepEqual(got, want) {
					t.Errorf("error = %v, want the chat endpoint's %v", got, want)
				}
			})
		}
	}
}

// TestAssistantsSendOnlyOpenAIModelNames: the Assistants API is served by
// OpenAI alone, so it strips only an "openai/" prefix, in any letter case, as
// chat strips the prefix that names its provider. A name with any other
// prefix, or "openai/" with nothing after it, is one OpenAI does not serve:
// a key with no restriction is refused with chat's answer for the same name,
// before the provider is called, and never has another model sent.
func TestAssistantsSendOnlyOpenAIModelNames(t *testing.T) {
	key := asstKeyOpts{}
	for _, model := range []string{"anthropic/claude-x", "gpt-4o-mini/gpt-4o", "openai/", "openai/openai/gpt-4o"} {
		chat := newAsstGateway(t, key, false).do("POST", "/v1/chat/completions",
			`{"model":"`+model+`","messages":[{"role":"user","content":"hi"}]}`, "")
		if chat.Code != http.StatusForbidden {
			t.Fatalf("chat with %s: status %d, want 403: %s", model, chat.Code, chat.Body)
		}
		want := errorObject(t, chat)
		for _, e := range asstModelRequests(model) {
			t.Run(e.name+" / "+model, func(t *testing.T) {
				g := newAsstGateway(t, key, false)
				w := g.do(e.method, e.path, e.body, "")
				if w.Code != chat.Code || g.up.calls.Load() != 0 {
					t.Fatalf("status %d, upstream calls %d (upstream got %s); want chat's %d and none: %s",
						w.Code, g.up.calls.Load(), g.up.lastBody(), chat.Code, w.Body)
				}
				if got := errorObject(t, w); !reflect.DeepEqual(got, want) {
					t.Errorf("error = %v, want the chat endpoint's %v", got, want)
				}
			})
		}
	}
	for _, model := range []string{"OpenAI/gpt-4o", "openai/gpt-4o", "gpt-4o"} {
		for _, e := range asstModelRequests(model) {
			t.Run(e.name+" / "+model, func(t *testing.T) {
				g := newAsstGateway(t, key, false)
				if w := g.do(e.method, e.path, e.body, ""); w.Code != http.StatusOK || g.up.calls.Load() != 1 {
					t.Fatalf("status %d, upstream calls %d; want 200 from one upstream call: %s", w.Code, g.up.calls.Load(), w.Body)
				}
				if got := modelMembers(t, g.up.lastBody()); !reflect.DeepEqual(got, []string{`"gpt-4o"`}) {
					t.Errorf("upstream got model members %v, want only \"gpt-4o\": %s", got, g.up.lastBody())
				}
			})
		}
	}
}
