package server

import (
	"context"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
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
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// embedUpstream is one fake upstream for the embeddings tests: it answers OpenAI
// /v1/embeddings, Cohere /v2/embed and /v2/rerank, and Gemini embedContent,
// fails every call with status when status is set, and keeps each request's
// path, body and credential.
type embedUpstream struct {
	*httptest.Server
	status  atomic.Int32
	calls   atomic.Int32
	mu      sync.Mutex
	bodies  []string
	creds   []string
	vectors string // the OpenAI-shaped embedding value; a 4-dim float array by default
}

const defaultVectors = `[0.1,0.2,0.3,0.4]`

// setVectors sets the OpenAI-shaped embedding value later calls answer.
func (u *embedUpstream) setVectors(v string) {
	u.mu.Lock()
	defer u.mu.Unlock()
	u.vectors = v
}

func startEmbedUpstream(t *testing.T) *embedUpstream {
	t.Helper()
	u := &embedUpstream{vectors: defaultVectors}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			w.Header().Set("Content-Type", "application/json")
			io.WriteString(w, `{"object":"list","data":[]}`)
			return
		}
		u.calls.Add(1)
		body, _ := io.ReadAll(r.Body)
		u.mu.Lock()
		u.bodies = append(u.bodies, r.URL.Path+" "+string(body))
		// The credential as sent: Authorization, Gemini's x-goog-api-key, and a
		// query-string key, which no provider may use (it would land in error
		// text and logs), so it shows up doubled and fails the comparison.
		u.creds = append(u.creds, r.Header.Get("Authorization")+r.Header.Get("x-goog-api-key")+r.URL.Query().Get("key"))
		vectors := u.vectors
		u.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		if s := u.status.Load(); s != 0 {
			w.WriteHeader(int(s))
			io.WriteString(w, `{"error":{"message":"upstream down","type":"server_error"}}`)
			return
		}
		switch {
		case strings.HasSuffix(r.URL.Path, "/v2/embed"):
			io.WriteString(w, `{"id":"e1","embeddings":{"float":[[0.5,0.6]]},"meta":{"billed_units":{"input_tokens":6}}}`)
		case strings.HasSuffix(r.URL.Path, "/v2/rerank"):
			io.WriteString(w, `{"id":"rr","results":[{"index":1,"relevance_score":0.9},{"index":0,"relevance_score":0.2}],"meta":{"billed_units":{"search_units":2,"input_tokens":900}}}`)
		case strings.HasSuffix(r.URL.Path, ":embedContent"):
			io.WriteString(w, `{"embedding":{"values":[0.1,0.2,0.3]}}`) // Gemini reports no usage
		default:
			fmt.Fprintf(w, `{"object":"list","data":[{"object":"embedding","index":0,"embedding":%s}],"model":"text-embedding-3-small","usage":{"prompt_tokens":1000,"total_tokens":1000}}`, vectors)
		}
	}))
	t.Cleanup(u.Close)
	return u
}

func (u *embedUpstream) last() (body, cred string) {
	u.mu.Lock()
	defer u.mu.Unlock()
	if len(u.bodies) == 0 {
		return "", ""
	}
	return u.bodies[len(u.bodies)-1], u.creds[len(u.creds)-1]
}

// embedCapture is a post plugin, after cost, that keeps what the request
// log reads off rc: attempts, billing, metadata and agent context.
type embedCapture struct {
	mu       sync.Mutex
	runs     int
	attempts []models.Attempt
	billing  models.BillingState
	meta     map[string]string
	agent    models.AgentContext
}

func (c *embedCapture) Name() string      { return "embed-capture" }
func (c *embedCapture) Priority() int     { return 900 }
func (c *embedCapture) PostPriority() int { return 900 }
func (c *embedCapture) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
func (c *embedCapture) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.runs++
	c.attempts = rc.AttemptsSnapshot()
	c.billing = rc.Billing.Clone()
	c.meta = make(map[string]string, len(rc.Metadata))
	for k, v := range rc.Metadata {
		c.meta[k] = v
	}
	c.agent = rc.Agent.Clone()
	return pipeline.ResultContinue()
}

func (c *embedCapture) snapshot(t *testing.T) ([]models.Attempt, models.BillingState, map[string]string, models.AgentContext) {
	t.Helper()
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.runs == 0 {
		t.Fatal("the pipeline's post plugins never ran")
	}
	return c.attempts, c.billing, c.meta, c.agent
}

const (
	embedInternalKey = "sk-embed-internal"
	embedOrgKey      = "sk-embed-org"
	embedOrg         = "org-embed"
)

// embedGateway is a real server with an internal key and an org (BYOK) key,
// and, under each of the names in upstreams, a provider on that upstream:
// "oai" and "other" OpenAI-format, "cohere" Cohere, "gemini" Gemini. mutate
// may add providers (with addProvider) and routing before the server is
// built.
type embedGateway struct {
	srv       *Server
	capture   *embedCapture
	upstreams map[string]*embedUpstream
	noCost    bool // mutate sets it to build the pipeline without the cost plugin
}

var embedProviderModels = map[string]struct {
	format string
	models []string
}{
	"oai":    {"openai", []string{"text-embedding-3-small", "BAAI/bge-m3"}},
	"other":  {"openai", []string{"other-embed"}},
	"cohere": {"cohere", []string{"embed-english-v3.0", "rerank-english-v3.0"}},
	"gemini": {"gemini", []string{"gemini-embedding-001"}},
}

func newEmbedGateway(t *testing.T, orgCfg *tenant.OrgConfig, mutate func(*embedGateway, *config.Config)) *embedGateway {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "internal", Key: embedInternalKey, Owner: "t", KeyType: "internal", Metadata: map[string]string{"org_id": embedOrg}},
		{Name: "org", Key: embedOrgKey, Owner: "t", KeyType: "byok", Metadata: map[string]string{"org_id": embedOrg}},
	}
	g := &embedGateway{capture: &embedCapture{}, upstreams: map[string]*embedUpstream{}}
	for id, p := range embedProviderModels {
		g.addProvider(t, cfg, id, p.format, p.models...)
	}
	if mutate != nil {
		mutate(g, cfg)
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	keyStore := auth.NewKeyStore(cfg.Auth)
	tenants := tenant.NewStore()
	if orgCfg != nil {
		tenants.Set(embedOrg, orgCfg)
	}
	mdb := testModelDBPtr()
	plugins := []pipeline.Plugin{authplugin.New(keyStore, true), g.capture}
	if !g.noCost {
		plugins = append(plugins, costplugin.New(true, mdb.Load, tenants, nil))
	}
	engine := pipeline.NewEngine(plugins...)
	g.srv = New(cfg, "", registry, engine, keyStore, nil, nil, nil, mdb, tenants, nil)
	g.srv.ready.Store(true)
	g.srv.handlers.failover.Store(nil)
	return g
}

// addProvider adds provider id, of format, on an upstream of its own.
func (g *embedGateway) addProvider(t *testing.T, cfg *config.Config, id, format string, models ...string) {
	t.Helper()
	u := startEmbedUpstream(t)
	g.upstreams[id] = u
	cfg.Providers[id] = config.ProviderConfig{BaseURL: u.URL, APIKey: "operator-" + id, APIFormat: format, Models: models}
}

func (g *embedGateway) post(t *testing.T, path, key, body string, headers map[string]string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, path, strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+key)
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

// agentHeaders are the typed agent headers plus a caller dimension; the
// forged run_id in x-agentcc-metadata must not become the run.
var agentHeaders = map[string]string{
	"x-agentcc-agent-id": "memory-writer",
	"x-agentcc-run-id":   "run-embed-1",
	"x-agentcc-step":     "memory",
	"x-agentcc-metadata": `{"team":"recall","run_id":"forged"}`,
}

func checkAttribution(t *testing.T, meta map[string]string, agent models.AgentContext) {
	t.Helper()
	if agent.RunID != "run-embed-1" || agent.Step != models.StepMemory || agent.AgentID != "memory-writer" {
		t.Errorf("agent context = run %q, step %q, agent %q; want run-embed-1, memory, memory-writer", agent.RunID, agent.Step, agent.AgentID)
	}
	if meta["team"] != "recall" {
		t.Errorf("caller dimension team = %q, want recall (x-agentcc-metadata applied)", meta["team"])
	}
	if _, forged := meta["run_id"]; forged {
		t.Error("x-agentcc-metadata planted run_id; the run comes only from the typed agent context")
	}
}

// Every embedding call records the caller's dimensions (x-agentcc-metadata)
// and the typed agent context, so agent, run and step reach the log entry
// and the run scope; its one upstream call is recorded as an attempt.
func TestEmbeddingsCallerMetadataApplied(t *testing.T) {
	g := newEmbedGateway(t, nil, nil)
	w := g.post(t, "/v1/embeddings", embedInternalKey, `{"model":"text-embedding-3-small","input":"remember this"}`, agentHeaders)
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d: %s", w.Code, w.Body.String())
	}
	attempts, _, meta, agent := g.capture.snapshot(t)
	checkAttribution(t, meta, agent)
	if len(attempts) != 1 || attempts[0].Kind != models.AttemptPrimary || attempts[0].Outcome != models.OutcomeSuccess || attempts[0].PromptTokens != 1000 {
		t.Fatalf("attempts = %+v, want one successful primary with 1000 prompt tokens", attempts)
	}
	checkAttemptsMatchSchema(t, attempts)

	// A malformed agent header is refused before the upstream, as on chat.
	calls := g.upstreams["oai"].calls.Load()
	w = g.post(t, "/v1/embeddings", embedInternalKey, `{"model":"text-embedding-3-small","input":"x"}`, map[string]string{"x-agentcc-step": "dreaming"})
	if w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), errAgentHeaderInvalid) || g.upstreams["oai"].calls.Load() != calls {
		t.Errorf("invalid step: status %d, upstream calls %d -> %d: %s", w.Code, calls, g.upstreams["oai"].calls.Load(), w.Body.String())
	}
}

// Rerank records the same attribution, its one upstream call as an attempt,
// and the search units the upstream billed, which a caller cannot plant.
func TestRerankCallerMetadataApplied(t *testing.T) {
	g := newEmbedGateway(t, nil, nil)
	headers := map[string]string{}
	for k, v := range agentHeaders {
		headers[k] = v
	}
	headers["x-agentcc-metadata"] = `{"team":"recall","run_id":"forged","search_units":"999"}`
	w := g.post(t, "/v1/rerank", embedInternalKey, `{"model":"rerank-english-v3.0","query":"q","documents":["a","b"]}`, headers)
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d: %s", w.Code, w.Body.String())
	}
	attempts, _, meta, agent := g.capture.snapshot(t)
	checkAttribution(t, meta, agent)
	if got := meta[models.RerankSearchUnitsMetadataKey]; got != "2" {
		t.Errorf("%s = %q, want 2 (the upstream's billed units, not the caller's 999)", models.RerankSearchUnitsMetadataKey, got)
	}
	if len(attempts) != 1 || attempts[0].Provider != "cohere" || attempts[0].Outcome != models.OutcomeSuccess {
		t.Fatalf("attempts = %+v, want one successful cohere attempt", attempts)
	}
	checkAttemptsMatchSchema(t, attempts)

	// A rerank the upstream failed billed nothing, and the caller's count
	// does not stand in for one.
	g.upstreams["cohere"].status.Store(http.StatusInternalServerError)
	w = g.post(t, "/v1/rerank", embedInternalKey, `{"model":"rerank-english-v3.0","query":"q","documents":["a"]}`, headers)
	if w.Code == http.StatusOK {
		t.Fatalf("a failed upstream answered 200: %s", w.Body.String())
	}
	attempts, _, meta, agent = g.capture.snapshot(t)
	if got, ok := meta[models.RerankSearchUnitsMetadataKey]; ok {
		t.Errorf("a failed rerank carries %s = %q, want none", models.RerankSearchUnitsMetadataKey, got)
	}
	if len(attempts) != 1 || attempts[0].Outcome != models.OutcomeError || agent.RunID != "run-embed-1" {
		t.Errorf("failed rerank: attempts %+v, run %q; want one error attempt, attributed", attempts, agent.RunID)
	}
}

// nilEmbedder is an embedding provider that answers neither a response nor
// an error.
type nilEmbedder struct{ providers.Provider }

func (nilEmbedder) CreateEmbedding(context.Context, *models.EmbeddingRequest) (*models.EmbeddingResponse, error) {
	return nil, nil
}

// A provider that answers nothing is a failed attempt and a 500, never a
// successful attempt with no response behind it.
func TestEmbeddingNilResponseIsAFailedAttempt(t *testing.T) {
	g := newEmbedGateway(t, nil, nil)
	h := g.srv.handlers
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.RequestID = "req-nil-embed"
	rc.EndpointType = "embedding"
	rc.Provider = "oai"
	rc.EmbeddingRequest = &models.EmbeddingRequest{Model: "text-embedding-3-small", Input: json.RawMessage(`"x"`)}
	err := h.embedOnce(context.Background(), rc, nilEmbedder{})
	var apiErr *models.APIError
	if !errors.As(err, &apiErr) || apiErr.Status != http.StatusInternalServerError || rc.EmbeddingResponse != nil {
		t.Fatalf("err = %v, response %v; want a 500 and no response", err, rc.EmbeddingResponse)
	}
	if attempts := rc.AttemptsSnapshot(); len(attempts) != 1 || attempts[0].Outcome == models.OutcomeSuccess {
		t.Fatalf("attempts = %+v, want one attempt that did not succeed", attempts)
	}
}

// An org's own rerank key makes a byok attempt that the node's shared model
// health never sees: the org's upstream failing says nothing about the
// node's own instance of the provider.
func TestRerankOrgKeyAttemptIsByok(t *testing.T) {
	orgCfg := &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"cohere": {APIKey: "org-cohere-key", Models: []string{"rerank-english-v3.0"}, Enabled: true},
	}}
	g := newEmbedGateway(t, orgCfg, nil)
	health := routing.NewModelHealth()
	g.srv.handlers.SetInjectedDeps(InjectedDeps{ModelHealth: health})
	g.upstreams["cohere"].status.Store(http.StatusInternalServerError)
	for i := 0; i < 3; i++ {
		if w := g.post(t, "/v1/rerank", embedOrgKey, `{"model":"rerank-english-v3.0","query":"q","documents":["a"]}`, nil); w.Code == http.StatusOK {
			t.Fatalf("a failing upstream answered 200: %s", w.Body.String())
		}
	}
	if _, cred := g.upstreams["cohere"].last(); cred != "Bearer org-cohere-key" {
		t.Fatalf("upstream credential = %q, want the org's own", cred)
	}
	attempts, _, meta, _ := g.capture.snapshot(t)
	if meta["billing_mode"] != billingBYOK || len(attempts) != 1 || attempts[0].CredentialType != models.CredentialBYOK {
		t.Errorf("billing_mode %q, attempts %+v; want one byok attempt", meta["billing_mode"], attempts)
	}
	if snap := health.Snapshot("cohere", "rerank-english-v3.0"); snap.N != 0 {
		t.Errorf("the org's own failures fed the shared health: %+v", snap)
	}
}

// input_type is sent per request where the provider has the parameter, and
// refused with 400 input_type_unsupported where it does not (OpenAI's
// embeddings), before any upstream call; a value outside the four is 400
// invalid_input_type. Without input_type nothing changes.
func TestInputTypeUnsupported400(t *testing.T) {
	g := newEmbedGateway(t, nil, nil)
	oai := g.upstreams["oai"]

	w := g.post(t, "/v1/embeddings", embedInternalKey, `{"model":"text-embedding-3-small","input":"q","input_type":"query"}`, nil)
	if w.Code != http.StatusBadRequest || oai.calls.Load() != 0 {
		t.Fatalf("OpenAI with input_type: status %d, upstream calls %d: %s", w.Code, oai.calls.Load(), w.Body.String())
	}
	var e models.ErrorResponse
	if err := json.Unmarshal(w.Body.Bytes(), &e); err != nil || e.Error.Code != errInputTypeUnsupported || e.Error.Param == nil || *e.Error.Param != "input_type" {
		t.Fatalf("error = %s, want input_type_unsupported on param input_type", w.Body.String())
	}

	w = g.post(t, "/v1/embeddings", embedInternalKey, `{"model":"embed-english-v3.0","input":"q","input_type":"search_query"}`, nil)
	if w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), errInvalidInputType) || g.upstreams["cohere"].calls.Load() != 0 {
		t.Fatalf("a value outside the four: status %d, cohere calls %d: %s", w.Code, g.upstreams["cohere"].calls.Load(), w.Body.String())
	}

	w = g.post(t, "/v1/embeddings", embedInternalKey, `{"model":"text-embedding-3-small","input":"q"}`, nil)
	if body, _ := oai.last(); w.Code != http.StatusOK || strings.Contains(body, "input_type") {
		t.Fatalf("OpenAI without input_type: status %d, upstream body %s", w.Code, body)
	}

	w = g.post(t, "/v1/embeddings", embedInternalKey, `{"model":"embed-english-v3.0","input":"q","input_type":"query"}`, nil)
	if body, _ := g.upstreams["cohere"].last(); w.Code != http.StatusOK || !strings.Contains(body, `"input_type":"search_query"`) {
		t.Fatalf("Cohere with input_type query: status %d, upstream body %s", w.Code, body)
	}
}

// Every embeddings response names its vector space:
// <model without its routing prefix>@<dims>:<dtype>:<input_type>. Only the
// serving provider's "<id>/" prefix is dropped; a slash inside a model id
// stays, so two models never print the same space.
func TestSpaceHeader(t *testing.T) {
	g := newEmbedGateway(t, nil, nil)
	for _, tc := range []struct {
		name, body, vectors, want string
	}{
		{"openai default", `{"model":"text-embedding-3-small","input":"x"}`, "", "text-embedding-3-small@4:float:default"},
		{"cohere query, provider-prefixed", `{"model":"cohere/embed-english-v3.0","input":"x","input_type":"query"}`, "", "embed-english-v3.0@2:float:query"},
		{"gemini document", `{"model":"gemini-embedding-001","input":"x","input_type":"document"}`, "", "gemini-embedding-001@3:float:document"},
		{"base64 float32", `{"model":"text-embedding-3-small","input":"x","encoding_format":"base64"}`, base64Floats(5), "text-embedding-3-small@5:float:default"},
		{"slash in the model id", `{"model":"BAAI/bge-m3","input":"x"}`, "", "BAAI/bge-m3@4:float:default"},
		{"routing prefix and a slashed id", `{"model":"oai/BAAI/bge-m3","input":"x"}`, "", "BAAI/bge-m3@4:float:default"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if tc.vectors != "" {
				g.upstreams["oai"].setVectors(tc.vectors)
				defer g.upstreams["oai"].setVectors(defaultVectors)
			}
			w := g.post(t, "/v1/embeddings", embedInternalKey, tc.body, nil)
			if w.Code != http.StatusOK {
				t.Fatalf("status = %d: %s", w.Code, w.Body.String())
			}
			if got := w.Header().Get(headerEmbeddingSpace); got != tc.want {
				t.Errorf("%s = %q, want %q", headerEmbeddingSpace, got, tc.want)
			}
		})
	}
}

// base64Floats is an OpenAI base64 embedding of n float32 values, as a JSON
// string.
func base64Floats(n int) string {
	buf := make([]byte, 4*n)
	for i := 0; i < n; i++ {
		binary.LittleEndian.PutUint32(buf[4*i:], math.Float32bits(float32(i)/10))
	}
	return `"` + base64.StdEncoding.EncodeToString(buf) + `"`
}

// An embeddings request never fails over: with provider failover across two
// hosts of the same model and a model-fallback chain configured, a target
// that is down returns its error, attributed, after one attempt, and no
// other host or model is called. Vectors from another host or model would
// land in another space. The contract allows retries on the same target;
// the handler makes none, so adding them changes the target's expected
// call count here deliberately, and only that.
func TestEmbeddingsNoCrossHostFailover(t *testing.T) {
	g := newEmbedGateway(t, nil, func(g *embedGateway, cfg *config.Config) {
		g.addProvider(t, cfg, "oai2", "openai", "text-embedding-3-small")
		cfg.Routing.DefaultStrategy = "least-latency" // the first target while neither has a latency
		cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{"text-embedding-3-small": {{Provider: "oai", Weight: 1}, {Provider: "oai2", Weight: 1}}}
		cfg.Routing.ModelFallbacks = map[string][]string{"text-embedding-3-small": {"other-embed"}}
	})
	h := g.srv.handlers
	h.failover.Store(routing.NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 3, OnStatusCodes: []int{http.StatusBadGateway, http.StatusServiceUnavailable}},
		g.srv.handlers.registry.Router(), routing.NewRetryer(config.RetryConfig{Enabled: true, MaxRetries: 2, InitialDelay: time.Millisecond, MaxDelay: time.Millisecond}), nil))
	h.modelFallbacks.Store(routing.NewModelFallbacks(map[string][]string{"text-embedding-3-small": {"other-embed"}}))
	g.upstreams["oai"].status.Store(http.StatusServiceUnavailable)

	w := g.post(t, "/v1/embeddings", embedInternalKey, `{"model":"text-embedding-3-small","input":"x"}`, agentHeaders)
	// The OpenAI adapter reports an upstream 503 as 502.
	if w.Code != http.StatusBadGateway || !strings.Contains(w.Body.String(), "upstream down") {
		t.Fatalf("status = %d, want the target's own error (502): %s", w.Code, w.Body.String())
	}
	if got := g.upstreams["oai"].calls.Load(); got != 1 {
		t.Errorf("the target was called %d times, want 1", got)
	}
	for _, id := range []string{"oai2", "other"} {
		if got := g.upstreams[id].calls.Load(); got != 0 {
			t.Errorf("provider %s was called %d times; an embedding must not fail over", id, got)
		}
	}
	attempts, _, meta, agent := g.capture.snapshot(t)
	checkAttribution(t, meta, agent)
	if len(attempts) != 1 || attempts[0].Provider != "oai" || attempts[0].Outcome != models.OutcomeError || attempts[0].StatusCode != http.StatusBadGateway {
		t.Fatalf("attempts = %+v, want one failed attempt on oai", attempts)
	}
	if w.Header().Get("x-agentcc-fallback-used") != "" {
		t.Error("a failed embedding reports a fallback")
	}
}

// The resolution side of the same rule: a model reachable only through the
// model-fallback chain is refused, and no upstream is called with the
// fallback model in its place: a fallback never serves an embedding.
func TestEmbeddingsNeverResolveToAFallbackModel(t *testing.T) {
	g := newEmbedGateway(t, nil, nil)
	g.srv.handlers.modelFallbacks.Store(routing.NewModelFallbacks(map[string][]string{"unserved-embed": {"other-embed"}}))
	w := g.post(t, "/v1/embeddings", embedInternalKey, `{"model":"unserved-embed","input":"x"}`, nil)
	if w.Code < http.StatusBadRequest {
		t.Fatalf("status = %d: a model no provider serves was answered: %s", w.Code, w.Body.String())
	}
	for id, u := range g.upstreams {
		if got := u.calls.Load(); got != 0 {
			t.Errorf("provider %s was called %d times for a model it does not serve", id, got)
		}
	}
	if w.Header().Get("x-agentcc-fallback-used") != "" || w.Header().Get(headerEmbeddingSpace) != "" {
		t.Errorf("a refused embedding reports a fallback or a space: %v", w.Header())
	}
}

// An org's own key (BYOK) is free to the org and still metered: its attempt
// is byok, billable and priced at the upstream cost (reported by OpenAI, or
// the Gemini adapter's estimate, marked usage_estimated, when Gemini reports
// none), and nothing is billed to managed credit.
func TestByokEmbeddingsFreeMetered(t *testing.T) {
	orgCfg := &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"oai":    {APIKey: "org-oai-key", Models: []string{"text-embedding-3-small"}, Enabled: true},
		"gemini": {APIKey: "org-gemini-key", Models: []string{"gemini-embedding-001"}, Enabled: true},
	}}
	g := newEmbedGateway(t, orgCfg, nil)
	for _, tc := range []struct {
		provider, model, cred string
		estimated             bool
	}{
		{"oai", "text-embedding-3-small", "Bearer org-oai-key", false},
		{"gemini", "gemini-embedding-001", "org-gemini-key", true},
	} {
		t.Run(tc.provider, func(t *testing.T) {
			w := g.post(t, "/v1/embeddings", embedOrgKey, `{"model":"`+tc.model+`","input":"a passage the org indexes with its own key"}`, nil)
			if w.Code != http.StatusOK {
				t.Fatalf("status = %d: %s", w.Code, w.Body.String())
			}
			if _, cred := g.upstreams[tc.provider].last(); cred != tc.cred {
				t.Fatalf("upstream credential = %q, want the org's own %q", cred, tc.cred)
			}
			attempts, billing, meta, _ := g.capture.snapshot(t)
			if meta["billing_mode"] != billingBYOK {
				t.Errorf("billing_mode = %q, want byok", meta["billing_mode"])
			}
			if len(attempts) != 1 {
				t.Fatalf("attempts = %+v, want one", attempts)
			}
			a := attempts[0]
			if a.CredentialType != models.CredentialBYOK || !a.Billable || a.UpstreamCostMicros <= 0 || a.PromptTokens <= 0 {
				t.Errorf("attempt = %+v, want a billable byok attempt with tokens and an upstream cost", a)
			}
			if a.UsageEstimated != tc.estimated || billing.UsageEstimated != tc.estimated {
				t.Errorf("usage_estimated attempt %v, request %v; want %v", a.UsageEstimated, billing.UsageEstimated, tc.estimated)
			}
			if billing.BilledMicros != 0 || billing.Reserved || billing.UpstreamMicros != a.UpstreamCostMicros {
				t.Errorf("billing = %+v: BYOK must bill nothing to managed credit and meter the upstream cost", billing)
			}
			if meta["cost"] == "" || meta["cost"] == "0.000000" {
				t.Errorf("cost = %q, want the metered BYOK estimate", meta["cost"])
			}
			checkAttemptsMatchSchema(t, attempts)
		})
	}

	// The attempt is marked as an estimate by the handler itself, so the
	// log says so even where cost tracking is off.
	g = newEmbedGateway(t, orgCfg, func(g *embedGateway, _ *config.Config) { g.noCost = true })
	if w := g.post(t, "/v1/embeddings", embedOrgKey, `{"model":"gemini-embedding-001","input":"x"}`, nil); w.Code != http.StatusOK {
		t.Fatalf("without cost tracking: status = %d: %s", w.Code, w.Body.String())
	}
	if attempts, _, meta, _ := g.capture.snapshot(t); len(attempts) != 1 || !attempts[0].UsageEstimated || attempts[0].PromptTokens <= 0 || meta["usage_estimated"] != "true" {
		t.Errorf("without cost tracking: attempts %+v, usage_estimated %q; want one estimated attempt", attempts, meta["usage_estimated"])
	}
}
