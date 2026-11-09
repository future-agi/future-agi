package server

// Shared seams for the R7-ROUTING tests (NG-09, NG-10, NG-11, NG-13): a
// gateway built as cmd/agentcc builds it, customer keys synced the way
// Django syncs them (POST /-/keys/sync, so KeyType "byok"), and org configs
// pushed as Django pushes them (PUT /-/orgs/{org}/config, contract decode
// then the contract -> tenant conversion). Ported from the no-gimmick
// probes console-a-proofs/routing_console_probe_test.go and
// console-a-b0/probe/cab0_console_probe_test.go.
//
// Org providers carry no base_url, so an org's key is layered onto the
// config.yaml entry of the same id (providers.OrgProviderCache): the org's
// own key reaches the loopback upstream with no dial-check seam. Every
// upstream records the Authorization it was called with, so a test can tell
// an org's credential ("Bearer org-...") from the operator's ("Bearer
// node-...").

import (
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
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const (
	r7Admin = "r7-routing-admin-token-0b5e2c7d9a1f4e83"
	r7OrgA  = "org-r7-a"
	r7OrgB  = "org-r7-b"
	// Customer keys (Django-minted, synced as byok) and an internal key of
	// org A (the operator's own service key).
	r7CustA = "sk-agentcc-r7-routing-customer-a-0123456789abcdef01"
	r7CustB = "sk-agentcc-r7-routing-customer-b-0123456789abcdef01"
	r7IntlA = "sk-agentcc-r7-routing-internal-a-0123456789abcdef01"
	// The operator's own service key, bound to no org.
	r7IntlOp = "sk-agentcc-r7-routing-internal-op-0123456789abcdef0"
)

type r7Call struct{ Model, Auth string }

// r7Upstream is an OpenAI-compatible upstream. fail(model, n) answers 503 for
// the n-th call (1 for the first); delay sleeps before answering.
type r7Upstream struct {
	*httptest.Server
	mu    sync.Mutex
	calls []r7Call
	fail  func(model string, n int) bool
	delay time.Duration
}

func r7StartUpstream(t *testing.T, fail func(model string, n int) bool) *r7Upstream {
	t.Helper()
	u := &r7Upstream{fail: fail}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		if r.Method != http.MethodPost || !strings.HasSuffix(r.URL.Path, "/chat/completions") {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		var body struct {
			Model  string `json:"model"`
			Stream bool   `json:"stream"`
		}
		_ = json.Unmarshal(b, &body)
		u.mu.Lock()
		u.calls = append(u.calls, r7Call{Model: body.Model, Auth: r.Header.Get("Authorization")})
		n := len(u.calls)
		failNow := u.fail != nil && u.fail(body.Model, n)
		delay := u.delay
		u.mu.Unlock()
		if delay > 0 {
			time.Sleep(delay)
		}
		if failNow {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusServiceUnavailable)
			_, _ = io.WriteString(w, `{"error":{"message":"r7 upstream unavailable","type":"server_error","code":"unavailable"}}`)
			return
		}
		if body.Stream {
			w.Header().Set("Content-Type", "text/event-stream")
			fmt.Fprintf(w, "data: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":%q,\"choices\":[{\"index\":0,\"delta\":{\"role\":\"assistant\",\"content\":\"ok\"},\"finish_reason\":null}]}\n\n", body.Model)
			if f, ok := w.(http.Flusher); ok {
				f.Flush()
			}
			fmt.Fprintf(w, "data: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":%q,\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}],\"usage\":{\"prompt_tokens\":3,\"completion_tokens\":1,\"total_tokens\":4}}\n\n", body.Model)
			_, _ = io.WriteString(w, "data: [DONE]\n\n")
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"id":"chatcmpl-r7","object":"chat.completion","created":1,"model":"`+body.Model+`","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":1,"total_tokens":4}}`)
	}))
	t.Cleanup(u.Close)
	return u
}

func (u *r7Upstream) snapshot() []r7Call {
	u.mu.Lock()
	defer u.mu.Unlock()
	return append([]r7Call(nil), u.calls...)
}

func (u *r7Upstream) count() int {
	u.mu.Lock()
	defer u.mu.Unlock()
	return len(u.calls)
}

// r7Op is one config.yaml (operator) provider: its upstream and models.
type r7Op struct {
	up     *r7Upstream
	models []string
}

// r7Gateway builds the gateway: config.yaml providers, auth on, the internal
// key of org A, an empty tenant store, then the customer keys of orgs A and
// B synced as Django syncs them. tune edits the config before the registry
// is built (nil for none).
func r7Gateway(t *testing.T, ops map[string]r7Op, tune func(*config.Config)) *Server {
	t.Helper()
	off := false
	cfg := config.DefaultConfig()
	cfg.Admin.Token = r7Admin
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "internal", Key: r7IntlA, Owner: r7OrgA, KeyType: "internal", Metadata: map[string]string{"org_id": r7OrgA}},
		{Name: "operator", Key: r7IntlOp, Owner: "operator", KeyType: "internal"},
	}
	for id, op := range ops {
		cfg.Providers[id] = config.ProviderConfig{BaseURL: op.up.URL + "/v1", APIKey: "node-" + id + "-key", APIFormat: "openai", Models: op.models, AutoDiscover: &off}
	}
	if tune != nil {
		tune(cfg)
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	keyStore := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keyStore, true)), keyStore, nil, nil, nil, testModelDBPtr(), tenant.NewStore(), nil)
	srv.ready.Store(true)

	sync := `{"keys":[` +
		`{"id":"key_r7_cust_a","name":"console key a","owner":"a@example.test","key_hash":"` + auth.HashKey(r7CustA) + `","key_prefix":"sk-agentcc-r7a...","metadata":{"org_id":"` + r7OrgA + `"}},` +
		`{"id":"key_r7_cust_b","name":"console key b","owner":"b@example.test","key_hash":"` + auth.HashKey(r7CustB) + `","key_prefix":"sk-agentcc-r7b...","metadata":{"org_id":"` + r7OrgB + `"}}]}`
	if code, body := r7AdminCall(t, srv, http.MethodPost, "/-/keys/sync", sync); code != http.StatusOK {
		t.Fatalf("key sync -> %d %s", code, body)
	}
	for _, k := range []string{r7CustA, r7CustB} {
		if got := keyStore.Authenticate(k); got == nil || got.KeyType != "byok" {
			t.Fatalf("synced key = %+v, want KeyType byok", got)
		}
	}
	return srv
}

func r7AdminCall(t *testing.T, srv *Server, method, path, body string) (int, string) {
	t.Helper()
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+r7Admin)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w.Code, strings.TrimSpace(w.Body.String())
}

// r7Push sends an org config JSON exactly as given through the admin route.
func r7Push(t *testing.T, srv *Server, org, orgJSON string) {
	t.Helper()
	if code, body := r7AdminCall(t, srv, http.MethodPut, "/-/orgs/"+org+"/config", orgJSON); code != http.StatusOK {
		t.Fatalf("PUT /-/orgs/%s/config -> %d %s (payload %s)", org, code, body, orgJSON)
	}
}

// r7Chat sends one chat request; stream asks for SSE.
func r7Chat(t *testing.T, srv *Server, key, model string, stream bool) (int, string, http.Header) {
	t.Helper()
	body := `{"model":"` + model + `","messages":[{"role":"user","content":"hello"}]`
	if stream {
		body += `,"stream":true`
	}
	body += `}`
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+key)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	out := strings.TrimSpace(w.Body.String())
	if len(out) > 400 {
		out = out[:400] + "..."
	}
	return w.Code, out, w.Header()
}

// r7AssertOrgCredentialOnly fails when any call reached an upstream with a
// credential other than the org's own ("Bearer org-...").
func r7AssertOrgCredentialOnly(t *testing.T, ups ...*r7Upstream) {
	t.Helper()
	for _, u := range ups {
		for i, c := range u.snapshot() {
			if !strings.HasPrefix(c.Auth, "Bearer org-") {
				t.Errorf("upstream %s call %d used credential %q, want an org's own key", u.URL, i+1, c.Auth)
			}
		}
	}
}
