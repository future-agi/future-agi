package server

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// GATEWAY-CONSOLE-RUN-R3 (fi-security READ-r1): a Console run's grant names
// the models it may call, and every upstream attempt of the call serves one
// of them, whatever the org's or the node's routing does after the body's
// model was checked: complexity routing (the org's and the global), auto,
// the org's Model Map, org and node fallback chains, failover and retries.
// Each "never the off-cap model" row has a control on the same server and
// config: a playground grant (no cap) that does reach the off-cap model, so
// the row cannot pass because the routing it guards against never ran.

// crE2ECapped and crE2EOffCap are the model a Console run's grant names and
// a model the org serves that the grant does not name.
const (
	crE2ECapped = "gpt-4o-mini"
	crE2EOffCap = "gpt-4o"
)

// crE2EUpstream is the org provider's upstream. It counts the chat calls it
// is sent per model (the model id the host received) and fails the models in
// failing with a 500.
type crE2EUpstream struct {
	*httptest.Server
	mu      sync.Mutex
	calls   map[string]int
	failing map[string]bool
}

func startCrE2EUpstream(t *testing.T, failing ...string) *crE2EUpstream {
	t.Helper()
	u := &crE2EUpstream{calls: map[string]int{}, failing: map[string]bool{}}
	for _, m := range failing {
		u.failing[m] = true
	}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || !strings.HasSuffix(r.URL.Path, "/chat/completions") {
			w.Header().Set("Content-Type", "application/json")
			fmt.Fprint(w, `{"object":"list","data":[]}`)
			return
		}
		var body struct {
			Model  string `json:"model"`
			Stream bool   `json:"stream"`
		}
		_ = json.NewDecoder(r.Body).Decode(&body)
		u.mu.Lock()
		u.calls[body.Model]++
		fail := u.failing[body.Model]
		u.mu.Unlock()
		if fail {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusInternalServerError)
			fmt.Fprint(w, `{"error":{"message":"upstream failed","type":"server_error"}}`)
			return
		}
		if body.Stream {
			w.Header().Set("Content-Type", "text/event-stream")
			fmt.Fprintf(w, "data: {\"id\":\"chat-cr\",\"object\":\"chat.completion.chunk\",\"model\":%q,\"choices\":[{\"index\":0,\"delta\":{\"content\":\"streamed\"}}]}\n\n", body.Model)
			w.(http.Flusher).Flush()
			fmt.Fprintf(w, "data: {\"id\":\"chat-cr\",\"object\":\"chat.completion.chunk\",\"model\":%q,\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}]}\n\n", body.Model)
			fmt.Fprint(w, "data: [DONE]\n\n")
			return
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"id":"chat-cr","object":"chat.completion","model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}`, body.Model)
	}))
	t.Cleanup(u.Close)
	return u
}

// sent is how many chat calls the upstream was sent for model.
func (u *crE2EUpstream) sent(model string) int {
	u.mu.Lock()
	defer u.mu.Unlock()
	return u.calls[model]
}

// total is how many chat calls the upstream was sent in all.
func (u *crE2EUpstream) total() int {
	u.mu.Lock()
	defer u.mu.Unlock()
	n := 0
	for _, c := range u.calls {
		n += c
	}
	return n
}

// crE2EServer is newDelegated733Server whose "openai" provider (and so the
// org's own "openai", which inherits its loopback URL) is up, with the org
// config org and a ready verifier. configure adjusts the node's config.
func crE2EServer(t *testing.T, up *crE2EUpstream, org *tenant.OrgConfig, configure ...func(*config.Config)) *Server {
	t.Helper()
	all := append([]func(*config.Config){func(cfg *config.Config) {
		cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL + "/v1", APIFormat: "openai", Models: []string{crE2ECapped, crE2EOffCap}}
	}}, configure...)
	srv := newDelegated733Server(t, all...)
	srv.delegated = auth.NewEnvelopeVerifier(delegated733Secret, time.Now().Add(-time.Minute))
	if err := srv.handlers.tenantStore.Set(delegated733Org, org); err != nil {
		t.Fatalf("setting the org config: %v", err)
	}
	return srv
}

// crE2EOrg is an org whose own "openai" provider serves models, with the
// routing routingJSON (none when "").
func crE2EOrg(t *testing.T, routingJSON string, providerModels ...string) *tenant.OrgConfig {
	t.Helper()
	if len(providerModels) == 0 {
		providerModels = []string{crE2ECapped, crE2EOffCap}
	}
	org := &tenant.OrgConfig{
		Providers: map[string]*tenant.ProviderConfig{
			"openai": {Enabled: true, APIKey: "test-only-org-provider", Models: providerModels},
		},
		Meta: &tenant.ConfigMeta{Version: 1, ConfigID: "cfg-" + t.Name()},
	}
	if routingJSON != "" {
		var r tenant.RoutingConfig
		if err := json.Unmarshal([]byte(routingJSON), &r); err != nil {
			t.Fatalf("org routing %s: %v", routingJSON, err)
		}
		org.Routing = &r
	}
	return org
}

// crE2EBodySeq makes every request body distinct, so no response cache
// answers one request with another's answer.
var crE2EBodySeq atomic.Int64

// crE2EBody is a chat body for model, streamed when stream is set.
func crE2EBody(model string, stream bool) string {
	return fmt.Sprintf(`{"model":%q,"stream":%t,"messages":[{"role":"user","content":"hello %d"}]}`, model, stream, crE2EBodySeq.Add(1))
}

// crE2EConsoleRun sends body as a Console run whose grant names capped, with
// the header pairs given.
func crE2EConsoleRun(srv *Server, body string, capped []string, headers ...string) *httptest.ResponseRecorder {
	env, grant, _ := signedConsoleRun733(delegated733Route, body, capped, time.Now().Unix())
	return crE2ESend(srv, body, env, grant, headers...)
}

// crE2EPlayground sends body under a playground grant (fe1, no models): the
// control, which no cap holds.
func crE2EPlayground(srv *Server, body string, headers ...string) *httptest.ResponseRecorder {
	env, grant, _ := signed733(delegated733Route, body, "playground", time.Now().Unix())
	return crE2ESend(srv, body, env, grant, headers...)
}

func crE2ESend(srv *Server, body, env, grant string, headers ...string) *httptest.ResponseRecorder {
	r := httptest.NewRequest(http.MethodPost, delegated733Route, strings.NewReader(body))
	r.Header.Set("Content-Type", "application/json")
	r.Header.Set(auth.EnvelopeHeader, env)
	r.Header.Set(auth.GrantHeader, grant)
	for i := 0; i+1 < len(headers); i += 2 {
		r.Header.Set(headers[i], headers[i+1])
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, r)
	return w
}

// crE2ENotPermitted asserts w is a 403 model_not_permitted with no stream.
func crE2ENotPermitted(t *testing.T, w *httptest.ResponseRecorder) {
	t.Helper()
	if w.Code != http.StatusForbidden || !strings.Contains(w.Body.String(), `"model_not_permitted"`) {
		t.Fatalf("status %d, want 403 model_not_permitted: %s", w.Code, w.Body.String())
	}
	if strings.Contains(w.Body.String(), "data:") {
		t.Fatalf("a refused call carried SSE: %s", w.Body.String())
	}
}

// crE2EOrgComplexity is an org complexity config whose every tier is the
// off-cap model.
const crE2EOrgComplexity = `{"complexity":{"enabled":true,"default_tier":"simple","tiers":{
	"simple":{"max_score":30,"model":"gpt-4o"},
	"complex":{"max_score":100,"model":"gpt-4o"}}}}`

// (1) The org routes by complexity to a tier model outside the cap, and the
// Console run names a tier in x-agentcc-complexity-override: refused 403
// model_not_permitted before any upstream call. The control shows the header
// does move an uncapped call to the tier's model.
func TestConsoleRunCapComplexityOverrideHeaderRefused(t *testing.T) {
	up := startCrE2EUpstream(t)
	srv := crE2EServer(t, up, crE2EOrg(t, crE2EOrgComplexity))

	for _, stream := range []bool{false, true} {
		w := crE2EConsoleRun(srv, crE2EBody(crE2ECapped, stream), []string{crE2ECapped}, "x-agentcc-complexity-override", "complex")
		crE2ENotPermitted(t, w)
	}
	// An empty value is refused too (403 here; a header check ahead of the
	// delegated route may answer 400 first): never served.
	if w := crE2EConsoleRun(srv, crE2EBody(crE2ECapped, false), []string{crE2ECapped}, "X-Agentcc-Complexity-Override", ""); w.Code != http.StatusForbidden && w.Code != http.StatusBadRequest {
		t.Fatalf("an empty complexity override answered %d, want 403 (or 400): %s", w.Code, w.Body.String())
	}
	if n := up.total(); n != 0 {
		t.Fatalf("the upstream was sent %d model calls for refused Console runs", n)
	}

	// Control: the same header moves an uncapped call to the tier's model.
	if w := crE2EPlayground(srv, crE2EBody(crE2ECapped, false), "x-agentcc-complexity-override", "complex"); w.Code != http.StatusOK {
		t.Fatalf("control: a playground call answered %d: %s", w.Code, w.Body.String())
	}
	if n := up.sent(crE2EOffCap); n != 1 {
		t.Fatalf("control: the tier model %s was sent %d calls, want 1; the org's complexity routing did not run", crE2EOffCap, n)
	}
}

// (2) The org routes by complexity (the prompt's score, no header) to a tier
// model outside the cap: a Console run is served by its own model, never the
// tier's, streamed or not. The control is routed to the tier's model.
func TestConsoleRunCapOrgComplexityScoredKeepsCappedModel(t *testing.T) {
	up := startCrE2EUpstream(t)
	srv := crE2EServer(t, up, crE2EOrg(t, crE2EOrgComplexity))

	for _, stream := range []bool{false, true} {
		w := crE2EConsoleRun(srv, crE2EBody(crE2ECapped, stream), []string{crE2ECapped})
		if w.Code != http.StatusOK && w.Code != http.StatusForbidden {
			t.Fatalf("stream=%t: a Console run answered %d, want 200 on its own model (or 403): %s", stream, w.Code, w.Body.String())
		}
		if n := up.sent(crE2EOffCap); n != 0 {
			t.Fatalf("stream=%t: the tier model %s outside the cap was sent %d calls", stream, crE2EOffCap, n)
		}
	}
	if n := up.sent(crE2ECapped); n != 2 {
		t.Fatalf("the capped model %s was sent %d calls, want 2 (one per Console run)", crE2ECapped, n)
	}

	if w := crE2EPlayground(srv, crE2EBody(crE2ECapped, false)); w.Code != http.StatusOK {
		t.Fatalf("control: a playground call answered %d: %s", w.Code, w.Body.String())
	}
	if n := up.sent(crE2EOffCap); n != 1 {
		t.Fatalf("control: the tier model %s was sent %d calls, want 1; the org's complexity routing did not run", crE2EOffCap, n)
	}
}

// (3) The node routes by complexity (config.yaml routing.complexity) to a
// tier model outside the cap: a Console run keeps its own model. The control
// is routed to the tier's model.
func TestConsoleRunCapGlobalComplexityKeepsCappedModel(t *testing.T) {
	up := startCrE2EUpstream(t)
	srv := crE2EServer(t, up, crE2EOrg(t, ""), func(cfg *config.Config) {
		cfg.Routing.Complexity = config.ComplexityConfig{Enabled: true, DefaultTier: "simple", Tiers: map[string]config.ComplexityTierConfig{
			"simple":  {MaxScore: 30, Model: crE2EOffCap},
			"complex": {MaxScore: 100, Model: crE2EOffCap},
		}}
	})

	for _, stream := range []bool{false, true} {
		w := crE2EConsoleRun(srv, crE2EBody(crE2ECapped, stream), []string{crE2ECapped})
		if w.Code != http.StatusOK && w.Code != http.StatusForbidden {
			t.Fatalf("stream=%t: a Console run answered %d, want 200 on its own model (or 403): %s", stream, w.Code, w.Body.String())
		}
		if n := up.sent(crE2EOffCap); n != 0 {
			t.Fatalf("stream=%t: the global tier model %s outside the cap was sent %d calls", stream, crE2EOffCap, n)
		}
	}

	if w := crE2EPlayground(srv, crE2EBody(crE2ECapped, false)); w.Code != http.StatusOK {
		t.Fatalf("control: a playground call answered %d: %s", w.Code, w.Body.String())
	}
	if n := up.sent(crE2EOffCap); n != 1 {
		t.Fatalf("control: the global tier model %s was sent %d calls, want 1; global complexity routing did not run", crE2EOffCap, n)
	}
}

// (4) The org's fallback chain leads from the capped model to one outside
// the cap and the capped model's upstream fails (500): the off-cap model is
// never sent, and the answer is the failure (or 403), never a 200 from the
// off-cap model, streamed or not. The control walks the chain to it.
func TestConsoleRunCapOrgFallbackChainNeverLeavesCap(t *testing.T) {
	up := startCrE2EUpstream(t, crE2ECapped)
	srv := crE2EServer(t, up, crE2EOrg(t, `{"model_fallbacks":{"gpt-4o-mini":["gpt-4o"]}}`))

	for _, stream := range []bool{false, true} {
		w := crE2EConsoleRun(srv, crE2EBody(crE2ECapped, stream), []string{crE2ECapped})
		if w.Code == http.StatusOK {
			t.Fatalf("stream=%t: a Console run whose model failed answered 200: %s", stream, w.Body.String())
		}
		if n := up.sent(crE2EOffCap); n != 0 {
			t.Fatalf("stream=%t: the chain's off-cap model %s was sent %d calls", stream, crE2EOffCap, n)
		}
	}
	if n := up.sent(crE2ECapped); n < 2 {
		t.Fatalf("the capped model was sent %d calls, want at least one per Console run", n)
	}

	if w := crE2EPlayground(srv, crE2EBody(crE2ECapped, false)); w.Code != http.StatusOK {
		t.Fatalf("control: a playground call answered %d: %s", w.Code, w.Body.String())
	}
	if n := up.sent(crE2EOffCap); n != 1 {
		t.Fatalf("control: the chain's model %s was sent %d calls, want 1; the org's fallback chain did not run", crE2EOffCap, n)
	}
}

// (5) An org Model Map entry that only chooses the provider for the capped id
// is served: "openai/gpt-4o-mini" goes to the org's own openai, whose host
// is sent its registered id "gpt-4o-mini", on the org's key (byok).
func TestConsoleRunCapModelMapSameIDServed(t *testing.T) {
	const prefixed = "openai/" + crE2ECapped
	up := startCrE2EUpstream(t)
	org := crE2EOrg(t, "", crE2ECapped)
	org.ModelMap = map[string]string{prefixed: "openai"}
	srv := crE2EServer(t, up, org)

	w := crE2EConsoleRun(srv, crE2EBody(prefixed, false), []string{prefixed})
	if w.Code != http.StatusOK {
		t.Fatalf("a Console run on a Model Map entry for its own id answered %d: %s", w.Code, w.Body.String())
	}
	if got := w.Header().Get("x-agentcc-billing-mode"); got != "byok" {
		t.Fatalf("x-agentcc-billing-mode = %q, want byok", got)
	}
	if n := up.sent(crE2ECapped); n != 1 {
		t.Fatalf("the org's host was sent its registered id %s %d times, want 1", crE2ECapped, n)
	}
	if n := up.total(); n != 1 {
		t.Fatalf("the upstream was sent %d model calls, want 1", n)
	}
}

// (5b) A Model Map entry that sends another model in the capped id's name
// (an org provider with one model that does not list the id, orgMappedRoute
// rule 1) is refused 403 model_not_permitted before any upstream call. The
// control is served the provider's own model.
func TestConsoleRunCapModelMapAliasRefused(t *testing.T) {
	up := startCrE2EUpstream(t)
	org := crE2EOrg(t, "", crE2EOffCap)
	org.ModelMap = map[string]string{crE2ECapped: "openai"}
	srv := crE2EServer(t, up, org)

	crE2ENotPermitted(t, crE2EConsoleRun(srv, crE2EBody(crE2ECapped, false), []string{crE2ECapped}))
	if n := up.total(); n != 0 {
		t.Fatalf("the upstream was sent %d model calls for a refused Console run", n)
	}

	if w := crE2EPlayground(srv, crE2EBody(crE2ECapped, false)); w.Code != http.StatusOK {
		t.Fatalf("control: a playground call answered %d: %s", w.Code, w.Body.String())
	}
	if n := up.sent(crE2EOffCap); n != 1 {
		t.Fatalf("control: the alias's model %s was sent %d calls, want 1", crE2EOffCap, n)
	}
}

// (6) x-agentcc-auto-preference moves auto's choice: a Console run carrying
// it is refused 403 model_not_permitted before any upstream call.
func TestConsoleRunCapAutoPreferenceHeaderRefused(t *testing.T) {
	up := startCrE2EUpstream(t)
	srv := crE2EServer(t, up, crE2EOrg(t, ""))
	for _, stream := range []bool{false, true} {
		crE2ENotPermitted(t, crE2EConsoleRun(srv, crE2EBody(crE2ECapped, stream), []string{crE2ECapped}, "x-agentcc-auto-preference", "quality"))
	}
	if n := up.total(); n != 0 {
		t.Fatalf("the upstream was sent %d model calls for refused Console runs", n)
	}
}

// (7) No console-run grant may name a model the gateway treats as auto: each
// of routing.AutoModelNames, as the grant's model and the body's, is refused
// as every bad grant is (401), so auth's copy of the rule cannot drift from
// routing.IsAutoModel.
func TestConsoleRunGrantRefusesEveryAutoModelName(t *testing.T) {
	up := startCrE2EUpstream(t)
	srv := crE2EServer(t, up, crE2EOrg(t, ""))
	for _, name := range routing.AutoModelNames {
		w := crE2EConsoleRun(srv, crE2EBody(name, false), []string{name})
		if w.Code != http.StatusUnauthorized {
			t.Errorf("a console-run grant naming %q answered %d, want 401: %s", name, w.Code, w.Body.String())
		}
	}
	if n := up.total(); n != 0 {
		t.Fatalf("the upstream was sent %d model calls", n)
	}
}

// The backstop: the per-attempt guard in startAttempt, which every chat
// attempt passes (primary, retry, failover, model and auto fallback, stream
// start), refuses an attempt whose logical model is not in the cap, and the
// request's failure is then answered 403 model_not_permitted.
func TestConsoleRunCapAttemptGuard(t *testing.T) {
	srv := newDelegated733Server(t)
	h := srv.handlers
	capped := models.ModelCap{Capped: true, Models: []string{crE2ECapped}}

	refuse := func(t *testing.T, rc *models.RequestContext, kind models.AttemptKind, wantModel string) {
		t.Helper()
		before := len(rc.AttemptsSnapshot())
		_, finish, refusal := h.startAttempt(context.Background(), rc, kind, "", "openai", rc.Request.Model, models.CredentialBYOK)
		if refusal == "" || finish != nil {
			t.Fatalf("%s attempt for %q was not refused", kind, rc.Model)
		}
		attempts := rc.AttemptsSnapshot()
		if len(attempts) != before+1 || attempts[len(attempts)-1].Outcome != models.OutcomeSkipped {
			t.Fatalf("the refused attempt was not recorded as skipped: %+v", attempts)
		}
		err := h.attemptLimitError(httptest.NewRecorder(), rc, routing.SkipHop(nil, refusal))
		var apiErr *models.APIError
		if !errors.As(err, &apiErr) || apiErr.Status != http.StatusForbidden || apiErr.Code != "model_not_permitted" ||
			apiErr.Message != fmt.Sprintf("model %q is not one this Console run may call", wantModel) {
			t.Fatalf("the request's failure = %v, want 403 model_not_permitted for %q", err, wantModel)
		}
		// Whatever the run ended with, a cap refusal is the answer.
		if err := h.attemptLimitError(httptest.NewRecorder(), rc, models.ErrUpstreamProvider(http.StatusInternalServerError, "upstream failed")); !errors.As(err, &apiErr) || apiErr.Code != "model_not_permitted" {
			t.Fatalf("a run that ended with an upstream error after a cap refusal answered %v", err)
		}
	}

	t.Run("a primary attempt routed off the cap", func(t *testing.T) {
		rc := models.AcquireRequestContext()
		defer rc.Release()
		rc.Model = crE2EOffCap
		rc.Request = &models.ChatCompletionRequest{Model: crE2EOffCap}
		rc.ModelCap = capped
		refuse(t, rc, models.AttemptPrimary, crE2EOffCap)
	})

	t.Run("a model fallback hop of a capped request", func(t *testing.T) {
		rc := models.AcquireRequestContext()
		defer rc.Release()
		rc.Model = crE2ECapped
		rc.Request = &models.ChatCompletionRequest{Model: crE2EOffCap} // the hop rewrote it
		rc.ModelCap = capped
		refuse(t, rc, models.AttemptModelFallback, crE2EOffCap)
	})

	t.Run("an auto fallback hop of a capped request", func(t *testing.T) {
		rc := models.AcquireRequestContext()
		defer rc.Release()
		rc.Model = crE2ECapped
		rc.Request = &models.ChatCompletionRequest{Model: crE2EOffCap} // the hop rewrote it
		rc.ModelCap = capped
		refuse(t, rc, models.AttemptAutoFallback, crE2EOffCap)
	})

	t.Run("a resolution-time fallback of a capped request", func(t *testing.T) {
		rc := models.AcquireRequestContext()
		defer rc.Release()
		rc.Model = crE2ECapped
		rc.Request = &models.ChatCompletionRequest{Model: crE2EOffCap}
		rc.Metadata["fallback_model"] = crE2EOffCap
		rc.ModelCap = capped
		refuse(t, rc, models.AttemptPrimary, crE2EOffCap)
	})

	t.Run("a principal's cap holds a request not stamped with it", func(t *testing.T) {
		rc := models.AcquireRequestContext()
		defer rc.Release()
		rc.Model = crE2EOffCap
		rc.Request = &models.ChatCompletionRequest{Model: crE2EOffCap}
		ctx := auth.WithDelegatedPrincipal(context.Background(), auth.EnvelopeClaims{Kind: "console-run", Models: crE2ECapped, Org: delegated733Org, User: delegated733User, Workspace: "-", ID: "cap", Exp: time.Now().Add(time.Minute).Unix()}, "grant")
		if refusal := consoleRunAttemptRefusal(ctx, rc, models.AttemptPrimary); refusal == "" {
			t.Fatal("an attempt off a console-run principal's cap was not refused")
		}
	})

	t.Run("on the cap, or uncapped, the guard passes", func(t *testing.T) {
		rc := models.AcquireRequestContext()
		defer rc.Release()
		rc.Model = crE2ECapped
		rc.Request = &models.ChatCompletionRequest{Model: crE2ECapped}
		rc.ModelCap = capped
		for _, kind := range []models.AttemptKind{models.AttemptPrimary, models.AttemptRetry, models.AttemptFailover} {
			if refusal := consoleRunAttemptRefusal(context.Background(), rc, kind); refusal != "" {
				t.Errorf("a %s attempt on the capped model was refused (%s)", kind, refusal)
			}
		}
		// The org provider's wire id is not the logical model: the guard
		// reads rc.Model, so a registered id sent upstream is not refused.
		rc.Request.Model = "provider-registered-id"
		if refusal := consoleRunAttemptRefusal(context.Background(), rc, models.AttemptPrimary); refusal != "" {
			t.Errorf("an attempt sending the provider's wire id for the capped model was refused (%s)", refusal)
		}
		if err := h.attemptLimitError(httptest.NewRecorder(), rc, models.ErrUpstreamProvider(http.StatusInternalServerError, "upstream failed")); strings.Contains(fmt.Sprint(err), "Console run") {
			t.Errorf("an unrefused run's failure was answered as a cap refusal: %v", err)
		}

		other := models.AcquireRequestContext()
		defer other.Release()
		other.Model = crE2EOffCap
		other.Request = &models.ChatCompletionRequest{Model: crE2EOffCap}
		if refusal := consoleRunAttemptRefusal(context.Background(), other, models.AttemptModelFallback); refusal != "" {
			t.Errorf("an uncapped request's attempt was refused (%s)", refusal)
		}
	})
}

// The route checks: a capped request is routed only under a cap id, on
// rc.Model and on rc.Request.Model (what provider resolution is handed), and
// a resolution that substituted another model through a conditional route
// is refused; an org provider's wire id for the same model is not.
func TestConsoleRunCapRouteChecks(t *testing.T) {
	capped := models.ModelCap{Capped: true, Models: []string{crE2ECapped}}
	route := func(model, requestModel string, c models.ModelCap) *models.APIError {
		rc := models.AcquireRequestContext()
		defer rc.Release()
		rc.Model = model
		rc.Request = &models.ChatCompletionRequest{Model: requestModel}
		rc.ModelCap = c
		return consoleRunRouteRefusal(rc, nil)
	}
	if apiErr := route(crE2ECapped, crE2ECapped, capped); apiErr != nil {
		t.Fatalf("a capped route on its own model was refused: %v", apiErr)
	}
	if apiErr := route(crE2EOffCap, crE2EOffCap, capped); apiErr == nil || apiErr.Code != "model_not_permitted" {
		t.Fatalf("a route renamed off the cap = %v, want 403 model_not_permitted", apiErr)
	}
	if apiErr := route(crE2ECapped, crE2EOffCap, capped); apiErr == nil || apiErr.Code != "model_not_permitted" {
		t.Fatalf("a route resolving another model than rc.Model = %v, want 403 model_not_permitted", apiErr)
	}
	if apiErr := route(crE2EOffCap, crE2EOffCap, models.ModelCap{}); apiErr != nil {
		t.Fatalf("an uncapped route was refused: %v", apiErr)
	}

	resolved := func(requestModel, rule string) *models.APIError {
		rc := models.AcquireRequestContext()
		defer rc.Release()
		rc.Model = crE2ECapped
		rc.Request = &models.ChatCompletionRequest{Model: requestModel}
		rc.ModelCap = capped
		if rule != "" {
			rc.Metadata["routing_rule"] = rule
		}
		return consoleRunResolvedRefusal(rc, crE2ECapped)
	}
	if apiErr := resolved(crE2EOffCap, "cheap-rule"); apiErr == nil || apiErr.Code != "model_not_permitted" {
		t.Fatalf("a conditional route's model_override = %v, want 403 model_not_permitted", apiErr)
	}
	if apiErr := resolved(crE2ECapped, "provider-only-rule"); apiErr != nil {
		t.Fatalf("a conditional route that only chose the provider was refused: %v", apiErr)
	}
	if apiErr := resolved("provider-registered-id", ""); apiErr != nil {
		t.Fatalf("an org provider's wire id for the capped model was refused: %v", apiErr)
	}
}

// A capped request has no model fallback plan from any source: not the org's
// chains, not the node's, not its own models[]. The same request uncapped
// has the org's chain.
func TestConsoleRunCapHasNoFallbackPlan(t *testing.T) {
	srv := newDelegated733Server(t)
	h := srv.handlers
	plan := func(capped bool) []string {
		rc := models.AcquireRequestContext()
		defer rc.Release()
		rc.Model = crE2ECapped
		rc.Request = &models.ChatCompletionRequest{Model: crE2ECapped}
		rc.OrgModelFallbacks = map[string][]string{crE2ECapped: {crE2EOffCap}}
		if capped {
			rc.ModelCap = models.ModelCap{Capped: true, Models: []string{crE2ECapped}}
		}
		fixFallbackRoute(rc)
		return h.effectiveFallbackPlan(rc, rc.FallbackRoute())
	}
	if got := plan(true); len(got) != 0 {
		t.Fatalf("a capped request's plan = %v, want none", got)
	}
	if got := plan(false); len(got) != 1 || got[0] != crE2EOffCap {
		t.Fatalf("control: an uncapped request's plan = %v, want [%s]", got, crE2EOffCap)
	}
}

// A capped request is never copied by the traffic mirror, which sends
// another model on another provider.
func TestConsoleRunCapNeverMirrored(t *testing.T) {
	srv := newDelegated733Server(t)
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Model = crE2ECapped
	rc.Request = &models.ChatCompletionRequest{Model: crE2ECapped}
	rc.Metadata["key_type"] = "internal"
	rc.ModelCap = models.ModelCap{Capped: true, Models: []string{crE2ECapped}}
	if got := srv.handlers.mirrorRefusal(context.Background(), rc, "openai"); got != mirrorRefusedConsoleRun {
		t.Fatalf("mirrorRefusal = %q, want %q", got, mirrorRefusedConsoleRun)
	}
}
