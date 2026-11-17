package server

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing/eligibility"
	"github.com/futureagi/agentcc-gateway/internal/routing/reqopts"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// One Future AGI key on the Anthropic Messages API (/v1/messages), the API
// Claude Code speaks: an org that has turned Future AGI models on reaches
// them there as it does on chat, billed at the upstream's charge plus the
// markup, through the same managed allowlist and upstream rewrite.

// managedMessagesBody is a minimal Messages API request for model.
func managedMessagesBody(model string, stream bool, fields string) string {
	b := `{"model":"` + model + `","max_tokens":64,"messages":[{"role":"user","content":"hi"}]`
	if stream {
		b += `,"stream":true`
	}
	if fields != "" {
		b += "," + fields
	}
	return b + "}"
}

// postMessages sends body to path (/v1/messages or its count_tokens) on srv
// with key, as an Anthropic SDK does.
func postMessages(t *testing.T, srv *Server, path, key, body string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, path, bytes.NewBufferString(body))
	req.Header.Set("Authorization", "Bearer "+key)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("anthropic-version", "2023-06-01")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

// TestManagedMessagesBilledWithMarkup: a managed key on /v1/messages gets a
// 200 Messages answer from the managed provider, under FutureAGI's upstream
// key, billed at the upstream's charge plus 5%, which x-agentcc-cost shows
// and the org's balance loses.
func TestManagedMessagesBilledWithMarkup(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	g.ledger.SetMicros("org-m", auth.USDToMicros(1))

	w := postMessages(t, g.srv, "/v1/messages", managedOrgKey, managedMessagesBody("anthropic/claude-sonnet-5.5", false, ""))

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d; body %s", w.Code, w.Body)
	}
	if g.managed.lastModel() != "anthropic/claude-sonnet-5.5" || g.managed.auths[0] != "Bearer fagi-openrouter-key" {
		t.Fatalf("upstream got model %q auth %q", g.managed.lastModel(), g.managed.auths[0])
	}
	if got := w.Header().Get("x-agentcc-billing-mode"); got != "managed" {
		t.Errorf("billing mode header = %q, want managed", got)
	}
	billed := upstreamCost * 1.05
	if got := w.Header().Get("x-agentcc-cost"); got != fmt.Sprintf("%.6f", billed) {
		t.Errorf("x-agentcc-cost = %s, want upstream cost plus 5%% (%.6f)", got, billed)
	}
	var msg struct {
		Type  string `json:"type"`
		Usage struct {
			InputTokens  int `json:"input_tokens"`
			OutputTokens int `json:"output_tokens"`
		} `json:"usage"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &msg); err != nil || msg.Type != "message" {
		t.Fatalf("not a Messages answer: %v %s", err, w.Body)
	}
	if msg.Usage.InputTokens != 1000 || msg.Usage.OutputTokens != 500 {
		t.Errorf("usage = %+v, want the upstream's 1000 in / 500 out", msg.Usage)
	}
	if strings.Contains(w.Body.String(), "0.007") {
		t.Errorf("the upstream's charge reached the client: %s", w.Body)
	}
	if bal := g.ledger.BalanceUSD("org-m"); fmt.Sprintf("%.6f", bal) != fmt.Sprintf("%.6f", 1-billed) {
		t.Errorf("org balance = %.6f, want %.6f", bal, 1-billed)
	}
}

// TestManagedMessagesStreamReportsUsage: a managed /v1/messages stream ends
// with the upstream's usage in its message_delta and is billed from it, at
// the upstream's charge plus 5%.
func TestManagedMessagesStreamReportsUsage(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	g.ledger.SetMicros("org-m", auth.USDToMicros(1))

	w := postMessages(t, g.srv, "/v1/messages", managedOrgKey, managedMessagesBody("anthropic/claude-sonnet-5.5", true, ""))

	if w.Code != http.StatusOK || !strings.HasPrefix(w.Header().Get("Content-Type"), "text/event-stream") {
		t.Fatalf("status = %d content-type %q; body %s", w.Code, w.Header().Get("Content-Type"), w.Body)
	}
	if got := w.Header().Get("x-agentcc-billing-mode"); got != "managed" {
		t.Errorf("billing mode header = %q, want managed", got)
	}
	out := w.Body.String()
	if !strings.Contains(out, "event: message_delta") || !strings.Contains(out, `"output_tokens":500`) {
		t.Fatalf("the stream did not report the upstream's usage in message_delta: %s", out)
	}
	if !strings.Contains(out, "event: message_stop") {
		t.Errorf("the stream did not end with message_stop: %s", out)
	}
	billed := upstreamCost * 1.05
	if bal := g.ledger.BalanceUSD("org-m"); fmt.Sprintf("%.6f", bal) != fmt.Sprintf("%.6f", 1-billed) {
		t.Errorf("org balance = %.6f, want %.6f: a managed stream must be billed from its reported usage", bal, 1-billed)
	}
}

// TestManagedMessagesAsksUpstreamForUsage: the managed attempt from
// /v1/messages is rewritten as chat's is: the upstream is asked to report
// the call's cost (usage.include), streams ask for usage too
// (stream_options.include_usage), and the user sent is the hashed org id.
func TestManagedMessagesAsksUpstreamForUsage(t *testing.T) {
	g := newRoutingGateway(t, managedOrg())
	for _, stream := range []bool{false, true} {
		w := postMessages(t, g.srv, "/v1/messages", routingKey, managedMessagesBody(managedModel, stream, ""))
		if w.Code != http.StatusOK {
			t.Fatalf("stream=%v: status %d body %s", stream, w.Code, w.Body)
		}
		got := lastBody(t, g.openrouter)
		if got["model"] != managedModel {
			t.Errorf("stream=%v: upstream model = %v, want %s", stream, got["model"], managedModel)
		}
		if u, _ := got["usage"].(map[string]any); u["include"] != true {
			t.Errorf("stream=%v: usage = %v, want include true", stream, got["usage"])
		}
		if got["user"] != reqopts.UpstreamUser(routingOrg) {
			t.Errorf("stream=%v: upstream user = %v, want the hashed org id", stream, got["user"])
		}
		if stream {
			if so, _ := got["stream_options"].(map[string]any); so["include_usage"] != true {
				t.Errorf("stream_options = %v, want include_usage true", got["stream_options"])
			}
			if !strings.Contains(w.Body.String(), `"output_tokens":1`) {
				t.Errorf("the stream did not report the upstream's usage: %s", w.Body)
			}
		}
	}
}

// TestManagedMessagesRefusedRoutingFieldsStayRefused: the routing fields a
// managed chat request may not send are refused on /v1/messages too, streamed
// or not, before any credit is held or upstream called; so are the routing
// fields /v1/messages does not honour, rather than being dropped unread.
func TestManagedMessagesRefusedRoutingFieldsStayRefused(t *testing.T) {
	g := newRoutingGateway(t, managedOrg())
	for param, fragment := range map[string]string{
		"plugins":    `"plugins":[{"id":"web"}]`,
		"route":      `"route":"fallback"`,
		"transforms": `"transforms":["middle-out"]`,
		"provider":   `"provider":{"only":["anthropic"]}`,
		"models":     `"models":["openai/gpt-6-sol"]`,
	} {
		for _, stream := range []bool{false, true} {
			w := postMessages(t, g.srv, "/v1/messages", routingKey, managedMessagesBody(managedModel, stream, fragment))
			if w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), param) {
				t.Errorf("%s stream=%v: status %d body %s, want 400 naming %s", param, stream, w.Code, w.Body, param)
			}
		}
	}
	if n := len(g.openrouter.received()); n != 0 {
		t.Fatalf("%d refused managed requests reached FutureAGI's upstream account", n)
	}
	if w := postMessages(t, g.srv, "/v1/messages", routingKey, managedMessagesBody(managedModel, false, `"session_id":"s-1"`)); w.Code != http.StatusOK {
		t.Errorf("session_id is kept, not refused: status %d body %s", w.Code, w.Body)
	}
}

// TestManagedCountTokensStaysBYOK: count_tokens selects the Messages route,
// but a credit-funded result is deliberately unsupported (501) before the
// pipeline. It neither calls FutureAGI's upstream nor spends credits.
func TestManagedCountTokensStaysBYOK(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	g.ledger.SetMicros("org-m", auth.USDToMicros(1))

	w := postMessages(t, g.srv, "/v1/messages/count_tokens", managedOrgKey,
		`{"model":"anthropic/claude-sonnet-5.5","messages":[{"role":"user","content":"hi"}]}`)

	if w.Code != http.StatusNotImplemented || g.managed.calls() != 0 {
		t.Fatalf("status = %d, managed upstream calls = %d; want 501 with no managed call", w.Code, g.managed.calls())
	}
	var refusal struct {
		Type  string `json:"type"`
		Error struct {
			Type string `json:"type"`
		} `json:"error"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &refusal); err != nil || refusal.Type != "error" || refusal.Error.Type != "not_supported_error" {
		t.Fatalf("count_tokens refusal = %s, decode error = %v; want Anthropic not_supported_error", w.Body, err)
	}
	if balance := g.ledger.BalanceUSD("org-m"); balance != 1 {
		t.Fatalf("balance = %v; unsupported count_tokens must not spend credits", balance)
	}
}

// TestManagedMessagesNeedCredits: at $0 the Messages API answers the same
// 402 as chat, before the upstream is called.
func TestManagedMessagesNeedCredits(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	g.grant(t, "org-m", 0)

	w := postMessages(t, g.srv, "/v1/messages", managedOrgKey, managedMessagesBody("anthropic/claude-sonnet-5.5", false, ""))

	if w.Code != http.StatusPaymentRequired || g.managed.calls() != 0 {
		t.Fatalf("status = %d body %s, upstream calls %d; want 402 with no upstream call", w.Code, w.Body, g.managed.calls())
	}
}

// TestManagedMessagesJudgedLikeChat: a managed /v1/messages request passes
// the eligibility engine as chat does, before any credit is held or
// upstream called: the org's managed_models deny list refuses it, and the
// org's data policy reaches the provider object FutureAGI's upstream is
// pinned to.
func TestManagedMessagesJudgedLikeChat(t *testing.T) {
	deny := &tenant.OrgConfig{ManagedModels: &tenant.ManagedModelsConfig{Enabled: true, DenyModels: []string{managedModel}}}
	g := newRoutingGateway(t, deny)
	w := postMessages(t, g.srv, "/v1/messages", routingKey, managedMessagesBody(managedModel, false, ""))
	if w.Code != http.StatusForbidden || len(g.openrouter.received()) != 0 {
		t.Fatalf("status %d body %s, upstream calls %d; the org's deny list must refuse the model on /v1/messages",
			w.Code, w.Body, len(g.openrouter.received()))
	}

	allow := &tenant.OrgConfig{ManagedModels: &tenant.ManagedModelsConfig{Enabled: true, AllowDataCollection: true}}
	g = newRoutingGateway(t, allow)
	if w := postMessages(t, g.srv, "/v1/messages", routingKey, managedMessagesBody(managedModel, false, "")); w.Code != http.StatusOK {
		t.Fatalf("status %d body %s", w.Code, w.Body)
	}
	prov, _ := lastBody(t, g.openrouter)["provider"].(map[string]any)
	if prov["data_collection"] != "allow" || prov["allow_fallbacks"] != false {
		t.Fatalf("pinned provider object = %v, want the org's data policy (data_collection allow)", prov)
	}
}

// orgKeyUnderFutureAGIID is an org that has Future AGI models on and has also
// added its own key under the Future AGI provider's id ("openrouter"), listing
// the catalogue id of a model it may send by its dated name.
func orgKeyUnderFutureAGIID() *tenant.OrgConfig {
	return &tenant.OrgConfig{
		ManagedModels: &tenant.ManagedModelsConfig{Enabled: true},
		Providers: map[string]*tenant.ProviderConfig{
			"openrouter": {APIKey: "org-or-key", Enabled: true, Models: []string{"anthropic/claude-sonnet-5.5"}},
		},
	}
}

// TestMessagesOnTheOrgsOwnKeyAreNeverBilledToCredits: a /v1/messages call
// that goes out on the org's own provider key is the org's own call (byok),
// as it is on chat: the org's key reaches the upstream, its Future AGI
// credit is untouched, and at $0 credit it is served, never refused with
// the 402.
func TestMessagesOnTheOrgsOwnKeyAreNeverBilledToCredits(t *testing.T) {
	const dated = "claude-sonnet-5-5-20260928"
	g := newManagedTestGateway(t, orgKeyUnderFutureAGIID())
	g.ledger.SetMicros("org-m", auth.USDToMicros(1))
	lastAuth := func() string {
		g.managed.mu.Lock()
		defer g.managed.mu.Unlock()
		if len(g.managed.auths) == 0 {
			return ""
		}
		return g.managed.auths[len(g.managed.auths)-1]
	}

	for _, call := range []struct {
		name string
		send func() *httptest.ResponseRecorder
	}{
		{"/v1/messages", func() *httptest.ResponseRecorder {
			return postMessages(t, g.srv, "/v1/messages", managedOrgKey, managedMessagesBody(dated, false, ""))
		}},
		{"/v1/messages stream", func() *httptest.ResponseRecorder {
			return postMessages(t, g.srv, "/v1/messages", managedOrgKey, managedMessagesBody(dated, true, ""))
		}},
		{"chat", func() *httptest.ResponseRecorder { return g.chat(t, chatBody(dated), nil) }},
	} {
		before := g.managed.calls()
		w := call.send()
		if w.Code != http.StatusOK {
			t.Fatalf("%s: status %d body %s", call.name, w.Code, w.Body)
		}
		if got := w.Header().Get("x-agentcc-billing-mode"); got != "byok" {
			t.Errorf("%s: billing mode %q, want byok: the call went out on the org's own key", call.name, got)
		}
		if g.managed.calls() != before+1 || lastAuth() != "Bearer org-or-key" {
			t.Errorf("%s: upstream calls %d (want %d), auth %q, want the org's own key", call.name, g.managed.calls(), before+1, lastAuth())
		}
		if bal := g.ledger.BalanceUSD("org-m"); fmt.Sprintf("%.6f", bal) != "1.000000" {
			t.Errorf("%s: org balance = %.6f, want 1.000000: a call on the org's own key draws no credit", call.name, bal)
		}
	}

	zero := newManagedTestGateway(t, orgKeyUnderFutureAGIID())
	zero.grant(t, "org-m", 0)
	w := postMessages(t, zero.srv, "/v1/messages", managedOrgKey, managedMessagesBody(dated, false, ""))
	if w.Code != http.StatusOK || w.Header().Get("x-agentcc-billing-mode") != "byok" {
		t.Fatalf("at $0 credit: status %d, billing mode %q, body %s; want 200 byok, not the 402",
			w.Code, w.Header().Get("x-agentcc-billing-mode"), w.Body)
	}
	if zero.managed.calls() != 1 || zero.managed.auths[0] != "Bearer org-or-key" {
		t.Fatalf("at $0 credit: upstream calls %d, auth %v; want one call on the org's own key", zero.managed.calls(), zero.managed.auths)
	}
}

// refusalMessage is error.message of an error answer, in chat's shape or the
// Messages API's: the two carry the same message.
func refusalMessage(t *testing.T, w *httptest.ResponseRecorder) string {
	t.Helper()
	var e struct {
		Error struct {
			Message string `json:"message"`
		} `json:"error"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &e); err != nil || e.Error.Message == "" {
		t.Fatalf("not an error answer: status %d body %s", w.Code, w.Body)
	}
	return e.Error.Message
}

// orgPolicies are an org's data policy and provider lock, each of which
// refuses provider (an org's own key that is not attested ZDR, or a provider
// the lock denies), with the reason chat's refusal gives.
func orgPolicies(provider string) []struct {
	name    string
	routing *tenant.RoutingConfig
	reason  string
} {
	return []struct {
		name    string
		routing *tenant.RoutingConfig
		reason  string
	}{
		{"data policy requires ZDR", &tenant.RoutingConfig{DataPolicy: &tenant.DataPolicyConfig{ZDR: true}}, eligibility.CodeDataPolicyZDR},
		{"provider lock denies " + provider, &tenant.RoutingConfig{ProviderLock: &tenant.ProviderLockConfig{
			Enabled: true, DenyProviders: []string{provider}}}, eligibility.CodeOrgProviderDenied},
	}
}

// TestMessagesOnTheOrgsOwnKeyAreJudgedLikeChat: a /v1/messages call on the
// org's own key is judged by the org's data policy and provider lock as
// chat's is. With its own key under the Future AGI provider's id, an org
// whose data policy requires zero data retention (the key is not attested
// ZDR), or whose provider lock denies that provider, gets chat's refusal on
// /v1/messages, streamed or not, and no upstream is called, whether it names
// the model its key lists or a dated name the catalogue resolves.
func TestMessagesOnTheOrgsOwnKeyAreJudgedLikeChat(t *testing.T) {
	for _, policy := range orgPolicies("openrouter") {
		for _, model := range []string{"anthropic/claude-sonnet-5.5", "claude-sonnet-5-5-20260928"} {
			t.Run(policy.name+"/"+model, func(t *testing.T) {
				cfg := orgKeyUnderFutureAGIID()
				cfg.Routing = policy.routing
				g := newManagedTestGateway(t, cfg)
				g.ledger.SetMicros("org-m", auth.USDToMicros(1))

				chat := g.chat(t, chatBody(model), nil)
				expectRefused(t, chat, http.StatusForbidden, codeModelNotPermitted, policy.reason)
				want := refusalMessage(t, chat)
				for _, stream := range []bool{false, true} {
					w := postMessages(t, g.srv, "/v1/messages", managedOrgKey, managedMessagesBody(model, stream, ""))
					if w.Code != chat.Code || refusalMessage(t, w) != want {
						t.Errorf("/v1/messages stream=%v: status %d body %s; want chat's %d %q", stream, w.Code, w.Body, chat.Code, want)
					}
				}
				if n := g.managed.calls(); n != 0 {
					t.Errorf("%d refused calls reached the upstream", n)
				}
				if bal := g.ledger.BalanceUSD("org-m"); fmt.Sprintf("%.6f", bal) != "1.000000" {
					t.Errorf("org balance = %.6f, want 1.000000", bal)
				}
			})
		}
	}
}

const (
	nativeOrg   = "org-native"
	nativeKey   = "sk-agentcc-native-org"
	nativeModel = "claude-org-native"
)

// newNativeOrgGateway is a gateway whose "anthropic" provider speaks the
// Messages API, at an upstream that counts the calls it gets, and an org
// that brings its own key for it, with routing as its routing config:
// /v1/messages reaches that provider on its native path, the body passed
// through as the client sent it.
func newNativeOrgGateway(t *testing.T, routing *tenant.RoutingConfig) (*Server, *atomic.Int32) {
	t.Helper()
	var calls atomic.Int32
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		w.Header().Set("Content-Type", "application/json")
		if r.Method != http.MethodPost {
			_, _ = io.WriteString(w, `{"data":[]}`)
			return
		}
		calls.Add(1)
		_, _ = io.WriteString(w, `{"id":"msg_1","type":"message","role":"assistant","model":"`+nativeModel+
			`","content":[{"type":"text","text":"ok"}],"stop_reason":"end_turn","usage":{"input_tokens":1,"output_tokens":1}}`)
	}))
	t.Cleanup(up.Close)

	off := false
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "native", Key: nativeKey, Owner: "o", KeyType: "byok",
		Metadata: map[string]string{"org_id": nativeOrg}}}
	cfg.Providers["anthropic"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "operator-anthropic-key",
		APIFormat: "anthropic", Models: []string{nativeModel}, AutoDiscover: &off}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("registry: %v", err)
	}
	tenants := tenant.NewStore()
	tenants.Set(nativeOrg, &tenant.OrgConfig{
		Providers: map[string]*tenant.ProviderConfig{
			"anthropic": {APIKey: "org-anthropic-key", Enabled: true, Models: []string{nativeModel}},
		},
		Routing: routing,
	})
	srv := New(cfg, "", registry, pipeline.NewEngine(), auth.NewKeyStore(cfg.Auth), nil, nil, nil, testModelDBPtr(), tenants, nil)
	releaseTestServer(t, srv)
	srv.ready.Store(true)
	return srv, &calls
}

// TestMessagesNativePathIsJudgedLikeChat: a /v1/messages request that
// reaches a provider speaking the Messages API natively is judged by the
// org's data policy and provider lock as chat's is, on the request as the
// provider would read it: refused with chat's refusal, streamed or not,
// before the provider is called. With neither policy it is served there.
func TestMessagesNativePathIsJudgedLikeChat(t *testing.T) {
	send := func(srv *Server, path, body string) *httptest.ResponseRecorder {
		if path == "/v1/messages" {
			return postMessages(t, srv, path, nativeKey, body)
		}
		req := httptest.NewRequest(http.MethodPost, path, bytes.NewBufferString(body))
		req.Header.Set("Authorization", "Bearer "+nativeKey)
		req.Header.Set("Content-Type", "application/json")
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)
		return w
	}

	srv, calls := newNativeOrgGateway(t, nil)
	if w := send(srv, "/v1/messages", managedMessagesBody(nativeModel, false, "")); w.Code != http.StatusOK || calls.Load() != 1 {
		t.Fatalf("with no policy: status %d body %s, upstream calls %d; want 200 from the org's provider", w.Code, w.Body, calls.Load())
	}

	for _, policy := range orgPolicies("anthropic") {
		t.Run(policy.name, func(t *testing.T) {
			srv, calls := newNativeOrgGateway(t, policy.routing)
			chat := send(srv, "/v1/chat/completions", chatBody(nativeModel))
			expectRefused(t, chat, http.StatusForbidden, codeModelNotPermitted, policy.reason)
			want := refusalMessage(t, chat)
			for _, stream := range []bool{false, true} {
				w := send(srv, "/v1/messages", managedMessagesBody(nativeModel, stream, ""))
				if w.Code != chat.Code || refusalMessage(t, w) != want {
					t.Errorf("/v1/messages stream=%v: status %d body %s; want chat's %d %q", stream, w.Code, w.Body, chat.Code, want)
				}
			}
			if n := calls.Load(); n != 0 {
				t.Errorf("%d refused requests reached the org's provider", n)
			}
		})
	}
}
