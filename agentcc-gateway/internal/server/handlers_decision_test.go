package server

import (
	"bytes"
	"context"
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	otelpkg "github.com/futureagi/agentcc-gateway/internal/otel"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	"github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/plugins/logging"
	otelplugin "github.com/futureagi/agentcc-gateway/internal/plugins/otel"
	"github.com/futureagi/agentcc-gateway/internal/plugins/ratelimit"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The keys a decision call could be sent with. Only the one that belongs to
// the caller may ever reach a vendor.
const (
	fagiHeldKey     = "FAGI-HELD-KEY"
	orgTypeSafeKey  = "ORG-TYPESAFE-KEY"
	orgOpenAIKey    = "ORG-OPENAI-KEY"
	callerOwnKey    = "CALLER-OWN-PROVIDER-KEY-CANARY"
	keyByok         = "sk-agentcc-byok"
	keyByokNoCred   = "sk-agentcc-byok-no-cred"
	keyByokNoOrg    = "sk-agentcc-byok-no-org"
	keyByokOrgB     = "sk-agentcc-byok-org-b"
	keyByokACL      = "sk-agentcc-byok-acl"
	keyByokACLOK    = "sk-agentcc-byok-acl-typesafe"
	keyManaged      = "sk-agentcc-managed"
	keyInternal     = "sk-agentcc-internal"
	adminToken      = "decision-admin-token"
	decisionSysBody = `{"model":"typesafe/jev-latest","state":{"ticket":"private-state"},"questions":{"resolved":{"type":"noul","instructions":"private-instructions"}}}`
	decisionOAIBody = `{"model":"openai/gpt-6-luna","input":"private-input","questions":[{"type":"predicate","name":"resolved","instructions":"private-instructions"}]}`
	decisionAnswer  = `{"model":"jev-1.13.0","answers":{"resolved":{"type":"noul","noul":0.9}},"usage":{"input_tokens":1000,"output_tokens":0}}`
)

// decisionUpstream is a vendor that counts every call by the Authorization it
// carried, whatever the path.
type decisionUpstream struct {
	*httptest.Server
	mu      sync.Mutex
	byAuth  map[string]int
	paths   []string
	bodies  []string
	headers []http.Header
	reply   func(http.ResponseWriter, *http.Request)
}

func newDecisionUpstream(t *testing.T) *decisionUpstream {
	t.Helper()
	u := &decisionUpstream{byAuth: map[string]int{}}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		u.mu.Lock()
		u.byAuth[r.Header.Get("Authorization")]++
		u.paths = append(u.paths, r.Method+" "+r.URL.Path)
		u.bodies = append(u.bodies, string(body))
		u.headers = append(u.headers, r.Header.Clone())
		reply := u.reply
		u.mu.Unlock()
		if reply != nil {
			reply(w, r)
			return
		}
		io.WriteString(w, decisionAnswer)
	}))
	t.Cleanup(u.Close)
	return u
}

func (u *decisionUpstream) calls() int {
	u.mu.Lock()
	defer u.mu.Unlock()
	n := 0
	for _, c := range u.byAuth {
		n += c
	}
	return n
}

func (u *decisionUpstream) callsWith(authorization string) int {
	u.mu.Lock()
	defer u.mu.Unlock()
	return u.byAuth[authorization]
}

func (u *decisionUpstream) authorizations() []string {
	u.mu.Lock()
	defer u.mu.Unlock()
	var seen []string
	for k := range u.byAuth {
		seen = append(seen, k)
	}
	return seen
}

func (u *decisionUpstream) last() (path, body string) {
	u.mu.Lock()
	defer u.mu.Unlock()
	if len(u.paths) == 0 {
		return "", ""
	}
	return u.paths[len(u.paths)-1], u.bodies[len(u.bodies)-1]
}

// lastHeaders are the headers of the vendor's last call.
func (u *decisionUpstream) lastHeaders() http.Header {
	u.mu.Lock()
	defer u.mu.Unlock()
	if len(u.headers) == 0 {
		return nil
	}
	return u.headers[len(u.headers)-1]
}

func (u *decisionUpstream) setReply(reply func(http.ResponseWriter, *http.Request)) {
	u.mu.Lock()
	u.reply = reply
	u.mu.Unlock()
}

// sentModel is the model the vendor received last.
func (u *decisionUpstream) sentModel(t *testing.T) string {
	t.Helper()
	_, body := u.last()
	var probe struct {
		Model string `json:"model"`
	}
	if err := json.Unmarshal([]byte(body), &probe); err != nil {
		t.Fatalf("upstream body %q: %v", body, err)
	}
	return probe.Model
}

// safeBuffer collects log output written from several goroutines.
type safeBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (b *safeBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *safeBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.String()
}

// spanCapture is an OTel exporter that keeps every span.
type spanCapture struct {
	mu    sync.Mutex
	spans []*otelpkg.Span
}

func (s *spanCapture) Export(spans []*otelpkg.Span) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.spans = append(s.spans, spans...)
	return nil
}
func (s *spanCapture) Shutdown() error { return nil }

func (s *spanCapture) dump() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	var b strings.Builder
	for _, span := range s.spans {
		fmt.Fprintf(&b, "%v\n", span.Attributes)
	}
	return b.String()
}

// decisionObserver keeps what the pipeline saw of each decision call.
type decisionObserver struct {
	mu        sync.Mutex
	seen      []string
	endpoints []string
	usage     []int
}

func (o *decisionObserver) Name() string  { return "decision_observer" }
func (o *decisionObserver) Priority() int { return 950 }
func (o *decisionObserver) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
func (o *decisionObserver) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	o.mu.Lock()
	defer o.mu.Unlock()
	o.seen = append(o.seen, fmt.Sprintf("%v %v %v %v", rc.Metadata, rc.RequestHeaders, rc.CustomMetadataKeys, rc.CallerExtras))
	o.endpoints = append(o.endpoints, rc.EndpointType)
	if rc.Response != nil && rc.Response.Usage != nil {
		o.usage = append(o.usage, rc.Response.Usage.PromptTokens)
	}
	return pipeline.ResultContinue()
}

func (o *decisionObserver) dump() string {
	o.mu.Lock()
	defer o.mu.Unlock()
	return strings.Join(o.seen, "\n")
}

// decisionGateway is a gateway with auth on (unless authOff), a config.yaml
// TypeSafe provider holding fagiHeldKey, and org-a's own TypeSafe and OpenAI
// credentials. Every plugin that could record or bill a call is wired in.
type decisionGateway struct {
	srv      *Server
	keyStore *auth.KeyStore
	tenants  *tenant.Store
	upstream *decisionUpstream
	mirror   *decisionUpstream
	webhook  *safeBuffer
	spans    *spanCapture
	observer *decisionObserver
	logging  *logging.Plugin
}

type decisionGatewayOptions struct {
	authOff bool
	// includeBodies turns on request-log bodies, which put a decision's full
	// state in the log where privacy mode allows.
	includeBodies bool
	config        func(cfg *config.Config, upstreamURL string)
	orgs          map[string]*tenant.OrgConfig
}

func defaultOrgA() *tenant.OrgConfig {
	return &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"typesafe": {APIKey: orgTypeSafeKey, APIFormat: "systemone", Models: []string{"jev-latest", "jev-1.13.0"}, Enabled: true},
		"openai":   {APIKey: orgOpenAIKey, APIFormat: "openai", Models: []string{"gpt-4o", "gpt-6-luna"}, Enabled: true},
	}}
}

func newDecisionGateway(t *testing.T, opts decisionGatewayOptions) *decisionGateway {
	t.Helper()
	g := &decisionGateway{
		upstream: newDecisionUpstream(t),
		mirror:   newDecisionUpstream(t),
		webhook:  &safeBuffer{},
		spans:    &spanCapture{},
		observer: &decisionObserver{},
	}
	webhook := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		g.webhook.Write(body)
		w.WriteHeader(http.StatusOK)
	}))
	t.Cleanup(webhook.Close)

	remote := false
	cfg := config.DefaultConfig()
	cfg.Admin.Token = adminToken
	cfg.Auth.Enabled = !opts.authOff
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "byok", Key: keyByok, Owner: "u", KeyType: "byok", Metadata: map[string]string{"org_id": "org-a"}},
		{Name: "byok-no-cred", Key: keyByokNoCred, Owner: "u", KeyType: "byok", Metadata: map[string]string{"org_id": "org-empty"}},
		{Name: "byok-no-org", Key: keyByokNoOrg, Owner: "u", KeyType: "byok"},
		{Name: "byok-org-b", Key: keyByokOrgB, Owner: "u", KeyType: "byok", Metadata: map[string]string{"org_id": "org-b"}},
		{Name: "byok-acl", Key: keyByokACL, Owner: "u", KeyType: "byok", Providers: []string{"perplexity-decider"}, Metadata: map[string]string{"org_id": "org-a"}},
		{Name: "byok-acl-typesafe", Key: keyByokACLOK, Owner: "u", KeyType: "byok", Providers: []string{"typesafe"}, Metadata: map[string]string{"org_id": "org-a"}},
		{Name: "managed", Key: keyManaged, Owner: "u", KeyType: "managed", CreditBalance: 5, Metadata: map[string]string{"org_id": "org-a"}},
		{Name: "internal", Key: keyInternal, Owner: "svc", KeyType: "internal"},
	}
	cfg.Providers["typesafe"] = config.ProviderConfig{
		APIFormat: "systemone", BaseURL: g.upstream.URL, APIKey: fagiHeldKey,
		Models: []string{"jev-latest", "jev-1.13.0", "jev-unknown-to-orgs"}, Local: &remote,
	}
	cfg.Providers["openai"] = config.ProviderConfig{
		APIFormat: "openai", BaseURL: g.upstream.URL, APIKey: fagiHeldKey,
		Models: []string{"gpt-4o", "gpt-6-luna"}, Local: &remote,
	}
	cfg.Providers["mirror-sink"] = config.ProviderConfig{
		APIFormat: "openai", BaseURL: g.mirror.URL, APIKey: fagiHeldKey, Models: []string{"mirror-model"}, Local: &remote,
	}
	if opts.config != nil {
		opts.config(cfg, g.upstream.URL)
	}
	// The registry probes a provider on loopback; that probe is no call.
	for id, pcfg := range cfg.Providers {
		pcfg.Local = &remote
		cfg.Providers[id] = pcfg
	}

	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { registry.Close() })

	g.tenants = tenant.NewStore()
	orgs := opts.orgs
	if orgs == nil {
		orgs = map[string]*tenant.OrgConfig{"org-a": defaultOrgA()}
	}
	for id, org := range orgs {
		g.tenants.Set(id, org)
	}

	g.keyStore = auth.NewKeyStore(cfg.Auth)
	db := modeldb.New(modeldb.BundledModels, nil)
	g.logging = logging.New(config.RequestLoggingConfig{Enabled: true, IncludeBodies: opts.includeBodies, BufferSize: 64, Workers: 1}, g.tenants)
	g.logging.SetFlusher(logging.NewLogFlusher(webhook.URL, "", time.Hour, 1000))
	engine := pipeline.NewEngine(
		authplugin.New(g.keyStore, !opts.authOff),
		ratelimit.New(config.RateLimitConfig{Enabled: true}, g.keyStore, nil),
		cost.New(true, func() *modeldb.ModelDB { return db }, g.tenants, nil),
		credits.New(true, g.keyStore),
		g.logging,
		otelplugin.NewWithExporter(g.spans, 1.0, true),
		g.observer,
	)
	g.srv = New(cfg, "", registry, engine, g.keyStore, nil, nil, nil, testModelDBPtr(), g.tenants, nil)
	g.srv.ready.Store(true)
	t.Cleanup(func() { g.srv.Shutdown(context.Background()) })
	return g
}

// do sends a request; headers alternate name, value.
func (g *decisionGateway) do(t *testing.T, method, path, key, body string, headers ...string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	if key != "" {
		req.Header.Set("Authorization", "Bearer "+key)
	}
	for i := 0; i+1 < len(headers); i += 2 {
		req.Header.Set(headers[i], headers[i+1])
	}
	rr := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(rr, req)
	return rr
}

func (g *decisionGateway) post(t *testing.T, key, body string, headers ...string) *httptest.ResponseRecorder {
	t.Helper()
	path := "/v1/systemone"
	if strings.Contains(body, `"questions":[`) {
		path = "/v1/decisions"
	}
	return g.do(t, http.MethodPost, path, key, body, headers...)
}

// flushLogs delivers every buffered request log to the webhook.
func (g *decisionGateway) flushLogs() string {
	g.logging.Close()
	return g.webhook.String()
}

func errorOf(t *testing.T, rr *httptest.ResponseRecorder) models.ErrorDetail {
	t.Helper()
	var resp models.ErrorResponse
	if err := json.Unmarshal(rr.Body.Bytes(), &resp); err != nil {
		t.Fatalf("status %d body %q is not an error envelope", rr.Code, rr.Body.String())
	}
	return resp.Error
}

func expectError(t *testing.T, rr *httptest.ResponseRecorder, status int, code string) models.ErrorDetail {
	t.Helper()
	e := errorOf(t, rr)
	if rr.Code != status || e.Code != code {
		t.Fatalf("got %d %s (%s), want %d %s", rr.Code, e.Code, e.Message, status, code)
	}
	for _, secret := range []string{fagiHeldKey, orgTypeSafeKey, orgOpenAIKey, callerOwnKey, "private-state", "private-instructions", "private-input"} {
		if strings.Contains(rr.Body.String(), secret) {
			t.Fatalf("error response leaked %q: %s", secret, rr.Body.String())
		}
	}
	return e
}

func expectNoCalls(t *testing.T, g *decisionGateway) {
	t.Helper()
	if n := g.upstream.calls(); n != 0 {
		t.Fatalf("upstream received %d calls (by key: %v), want none", n, g.upstream.authorizations())
	}
}

func expectOnlyKey(t *testing.T, g *decisionGateway, key string, calls int) {
	t.Helper()
	if got := g.upstream.callsWith("Bearer " + key); got != calls {
		t.Fatalf("calls with the expected key = %d, want %d (by key: %v)", got, calls, g.upstream.authorizations())
	}
	if total := g.upstream.calls(); total != calls {
		t.Fatalf("upstream received %d calls, %d of them with another key (by key: %v)", total, total-calls, g.upstream.authorizations())
	}
	if g.upstream.callsWith("Bearer "+fagiHeldKey) != 0 {
		t.Fatal("the Future AGI-held key reached the vendor")
	}
}

// --- The BYOK matrix -------------------------------------------------------

// A byok key whose org saved a TypeSafe credential is served with that
// credential, and the vendor sees only the org's key.
func TestDecisionByokUsesTheOrgCredential(t *testing.T) {
	g := newDecisionGateway(t, decisionGatewayOptions{})
	rr := g.post(t, keyByok, decisionSysBody)
	if rr.Code != http.StatusOK {
		t.Fatalf("status %d: %s", rr.Code, rr.Body.String())
	}
	if rr.Body.String() != decisionAnswer {
		t.Fatalf("the vendor's answer was changed: %s", rr.Body.String())
	}
	expectOnlyKey(t, g, orgTypeSafeKey, 1)
	if path, _ := g.upstream.last(); path != "POST /v1/systemone" {
		t.Fatalf("upstream path = %q", path)
	}
	for _, h := range []string{"x-agentcc-provider", "x-agentcc-model-used", "x-agentcc-latency-ms", "x-agentcc-cost", "x-agentcc-request-id"} {
		if rr.Header().Get(h) == "" {
			t.Errorf("missing %s", h)
		}
	}
	if rr.Header().Get("x-agentcc-provider") != "typesafe" || rr.Header().Get("x-agentcc-model-used") != "jev-1.13.0" {
		t.Errorf("provider = %q model = %q", rr.Header().Get("x-agentcc-provider"), rr.Header().Get("x-agentcc-model-used"))
	}

	// The org call is logged with its decision metadata and a summary only
	// of what was asked; never the state or the instructions.
	logs := g.flushLogs()
	for _, want := range []string{`"endpoint_type":"decision"`, `"decision_format":"systemone"`, `"decision_question_count":"1"`, `question_ids`} {
		if !strings.Contains(logs, want) {
			t.Errorf("request log missing %s: %s", want, logs)
		}
	}
	if strings.Contains(logs, orgTypeSafeKey) {
		t.Fatal("the org's key reached the request log")
	}
	if g.observer.endpoints[0] != "decision" || g.observer.usage[0] != 1000 {
		t.Errorf("pipeline saw %v %v", g.observer.endpoints, g.observer.usage)
	}
}

// A byok key whose org has no credential for the model is refused before any
// call, with what to do about it. So is a key that names no org at all:
// config.yaml lists the model with a Future AGI-held key, and that key is
// never anyone's fallback. An org that has the TypeSafe key but not the model
// is told to add the model, not the key.
func TestDecisionByokWithoutOrgCredentialIsRefused(t *testing.T) {
	g := newDecisionGateway(t, decisionGatewayOptions{})
	for key, want := range map[string]string{
		keyByokNoCred: "Add your TypeSafe key under Gateway > Providers",
		keyByokNoOrg:  "Add your TypeSafe key under Gateway > Providers",
		keyByok:       `Provider "typesafe" has your TypeSafe key but does not list "jev-unknown-to-orgs". Add the model to it under Gateway > Providers.`,
	} {
		body := decisionSysBody
		if key == keyByok {
			body = strings.Replace(decisionSysBody, "typesafe/jev-latest", "jev-unknown-to-orgs", 1)
		}
		e := expectError(t, g.post(t, key, body), http.StatusForbidden, models.CodeDecisionProviderNotConfigured)
		if !strings.Contains(e.Message, want) {
			t.Errorf("message = %q, want %q", e.Message, want)
		}
	}
	expectError(t, g.post(t, keyByokNoOrg, decisionOAIBody), http.StatusForbidden, models.CodeDecisionProviderNotConfigured)
	expectNoCalls(t, g)
}

// An org provider counts only while it is enabled and holds a key. One that
// is switched off, or saved without a key, is no credential: the call is
// refused, and config.yaml's key for the same provider ID never stands in.
func TestDecisionOrgProviderMustBeEnabledWithAKey(t *testing.T) {
	g := newDecisionGateway(t, decisionGatewayOptions{orgs: map[string]*tenant.OrgConfig{"org-a": {Providers: map[string]*tenant.ProviderConfig{
		"typesafe":        {APIKey: orgTypeSafeKey, APIFormat: "systemone", Models: []string{"jev-latest"}, Enabled: false},
		"typesafe-no-key": {APIFormat: "systemone", Models: []string{"jev-latest", "jev-1.13.0"}, Enabled: true},
		"openai":          {APIFormat: "openai", Models: []string{"gpt-6-luna"}, Enabled: true},
	}}}})
	for _, body := range []string{
		decisionSysBody,
		strings.Replace(decisionSysBody, "typesafe/jev-latest", "jev-latest", 1),
		strings.Replace(decisionSysBody, "typesafe/jev-latest", "typesafe-no-key/jev-1.13.0", 1),
		decisionOAIBody,
	} {
		expectError(t, g.post(t, keyByok, body), http.StatusForbidden, models.CodeDecisionProviderNotConfigured)
	}
	if got := g.do(t, http.MethodGet, "/v1/decisions/models", keyByok, ""); strings.Contains(got.Body.String(), `"id"`) {
		t.Errorf("unusable providers are listed: %s", got.Body.String())
	}
	expectNoCalls(t, g)
}

// An internal key must bring the caller's own key: without the header it is
// refused, and config.yaml's key is never used in its place.
func TestDecisionInternalKeyWithoutProviderKeyIsRefused(t *testing.T) {
	g := newDecisionGateway(t, decisionGatewayOptions{})
	expectError(t, g.post(t, keyInternal, decisionSysBody), http.StatusForbidden, models.CodeBYOKRequired)
	expectError(t, g.post(t, keyInternal, decisionSysBody, models.ProviderKeyHeader, "   "), http.StatusForbidden, models.CodeBYOKRequired)
	expectError(t, g.post(t, keyInternal, decisionOAIBody), http.StatusForbidden, models.CodeBYOKRequired)
	expectNoCalls(t, g)
}

// An internal key with the header is served with exactly that key. Nothing
// the request leaves behind — metadata, logs, spans, the mirror, the gateway's
// own log output, the response — carries it.
func TestDecisionInternalKeyUsesOnlyTheCallersKey(t *testing.T) {
	logs := &safeBuffer{}
	previous := slog.Default()
	slog.SetDefault(slog.New(slog.NewJSONHandler(logs, &slog.HandlerOptions{Level: slog.LevelDebug})))
	defer slog.SetDefault(previous)

	g := newDecisionGateway(t, decisionGatewayOptions{includeBodies: true, config: func(cfg *config.Config, _ string) {
		cfg.Routing.Mirror = config.MirrorConfig{Enabled: true, Rules: []config.MirrorRule{
			{SourceModel: "*", TargetProvider: "mirror-sink", TargetModel: "mirror-model", SampleRate: 1},
			{SourceModel: "typesafe/jev-latest", TargetProvider: "mirror-sink", TargetModel: "mirror-model", SampleRate: 1},
		}}
	}})

	rr := g.post(t, keyInternal, decisionSysBody, models.ProviderKeyHeader, callerOwnKey,
		"x-agentcc-metadata", `{"workflow":"jev-eval"}`)
	if rr.Code != http.StatusOK || rr.Body.String() != decisionAnswer {
		t.Fatalf("status %d: %s", rr.Code, rr.Body.String())
	}
	expectOnlyKey(t, g, callerOwnKey, 1)
	if got := g.upstream.sentModel(t); got != "jev-latest" {
		t.Errorf("vendor was sent model %q", got)
	}

	// An OpenAI Decisions call goes the same way, to /v1/decisions.
	rr = g.post(t, keyInternal, decisionOAIBody, models.ProviderKeyHeader, callerOwnKey)
	if rr.Code != http.StatusOK {
		t.Fatalf("openai: status %d: %s", rr.Code, rr.Body.String())
	}
	if path, _ := g.upstream.last(); path != "POST /v1/decisions" || g.upstream.sentModel(t) != "gpt-6-luna" {
		t.Errorf("openai: path %q model %q", path, g.upstream.sentModel(t))
	}
	expectOnlyKey(t, g, callerOwnKey, 2)

	time.Sleep(50 * time.Millisecond) // let the emitter's worker write
	records := g.flushLogs()
	for where, text := range map[string]string{
		"pipeline metadata and headers": g.observer.dump(),
		"request log webhook":           records,
		"trace spans":                   g.spans.dump(),
		"gateway log output":            logs.String(),
		"response headers":              fmt.Sprint(rr.Header()),
		"cached provider keys":          fmt.Sprint(keysOf(g.srv.handlers)),
	} {
		if strings.Contains(text, callerOwnKey) {
			t.Errorf("the caller's provider key appears in the %s", where)
		}
	}
	if !strings.Contains(g.observer.dump(), "jev-eval") {
		t.Error("caller metadata was lost; the leak check above would prove nothing")
	}
	// Internal calls are billed by the backend, so the gateway logs none.
	if strings.Contains(records, "jev-latest") {
		t.Error("an internal decision call was request-logged")
	}
	if n := g.mirror.calls(); n != 0 {
		t.Errorf("the mirror received %d calls", n)
	}
}

func keysOf(h *Handlers) []string {
	h.keyedDecisionProviders.mu.Lock()
	defer h.keyedDecisionProviders.mu.Unlock()
	var keys []string
	for k := range h.keyedDecisionProviders.providers {
		keys = append(keys, string(k[:]))
	}
	return keys
}

// The provider built for a caller's key is cached under a hash of it: one
// entry per key, reused, never keyed by the key itself.
func TestDecisionInternalKeyProviderCache(t *testing.T) {
	g := newDecisionGateway(t, decisionGatewayOptions{})
	for _, key := range []string{callerOwnKey, callerOwnKey, "ANOTHER-CALLER-KEY"} {
		if rr := g.post(t, keyInternal, decisionSysBody, models.ProviderKeyHeader, key); rr.Code != http.StatusOK {
			t.Fatalf("status %d: %s", rr.Code, rr.Body.String())
		}
	}
	keys := keysOf(g.srv.handlers)
	if len(keys) != 2 {
		t.Fatalf("cache entries = %d, want 2", len(keys))
	}
	want := sha256.Sum256([]byte("typesafe\x00" + g.upstream.URL + "\x00" + callerOwnKey))
	found := false
	for _, k := range keys {
		found = found || k == string(want[:])
		if strings.Contains(k, callerOwnKey) {
			t.Fatal("a cache entry is keyed by the plaintext key")
		}
	}
	if !found {
		t.Fatal("no entry under the key's hash")
	}
	if g.upstream.callsWith("Bearer "+callerOwnKey) != 2 || g.upstream.callsWith("Bearer ANOTHER-CALLER-KEY") != 1 {
		t.Fatalf("calls by key: %v", g.upstream.authorizations())
	}
}

// The cache is bounded: past the bound an entry goes, never the newest.
func TestKeyedDecisionProvidersAreBounded(t *testing.T) {
	var c keyedDecisionProviders
	var newest providers.Provider
	for i := 0; i <= maxKeyedDecisionProviders; i++ {
		p, err := c.get("typesafe", "", fmt.Sprintf("CALLER-KEY-%d", i))
		if err != nil {
			t.Fatal(err)
		}
		newest = p
	}
	if len(c.providers) != maxKeyedDecisionProviders {
		t.Fatalf("cache entries = %d, want %d", len(c.providers), maxKeyedDecisionProviders)
	}
	p, err := c.get("typesafe", "", fmt.Sprintf("CALLER-KEY-%d", maxKeyedDecisionProviders))
	if err != nil {
		t.Fatal(err)
	}
	if p != newest {
		t.Fatal("the newest key's provider is no longer cached")
	}
}

// A key used once isn't kept until 256 others push it out: an entry idle past
// the TTL is dropped, and the least recently used one goes at the bound.
func TestKeyedDecisionProvidersDropIdleKeys(t *testing.T) {
	now := time.Date(2026, 10, 22, 12, 0, 0, 0, time.UTC)
	c := keyedDecisionProviders{now: func() time.Time { return now }}
	once, err := c.get("typesafe", "", "ONE-CALL-KEY")
	if err != nil {
		t.Fatal(err)
	}
	now = now.Add(keyedDecisionIdleTTL / 2)
	if _, err := c.get("typesafe", "", "BUSY-KEY"); err != nil {
		t.Fatal(err)
	}
	if c.size() != 2 {
		t.Fatalf("cache entries = %d, want 2", c.size())
	}
	now = now.Add(keyedDecisionIdleTTL/2 + time.Second)
	if _, err := c.get("typesafe", "", "BUSY-KEY"); err != nil {
		t.Fatal(err)
	}
	if c.size() != 1 {
		t.Fatalf("cache entries = %d, want 1: the idle key is still held", c.size())
	}
	again, err := c.get("typesafe", "", "ONE-CALL-KEY")
	if err != nil {
		t.Fatal(err)
	}
	if again == once {
		t.Fatal("the idle key's provider was kept")
	}
}

// With no further call to drop it, the sweep drops an idle key on its own,
// and stops once the cache is empty.
func TestKeyedDecisionProvidersSweepIdleKeys(t *testing.T) {
	now := time.Date(2026, 10, 22, 12, 0, 0, 0, time.UTC)
	c := keyedDecisionProviders{now: func() time.Time { return now }}
	if _, err := c.get("typesafe", "", "ONE-CALL-KEY"); err != nil {
		t.Fatal(err)
	}
	armed := func() bool {
		c.mu.Lock()
		defer c.mu.Unlock()
		return c.sweep != nil
	}
	if !armed() {
		t.Fatal("no sweep is scheduled for the cached key")
	}
	now = now.Add(keyedDecisionIdleTTL / 2)
	c.sweepIdle()
	if c.size() != 1 || !armed() {
		t.Fatalf("cache entries = %d, sweep scheduled %v: a key not yet idle was dropped", c.size(), armed())
	}
	now = now.Add(keyedDecisionIdleTTL)
	c.sweepIdle()
	if c.size() != 0 {
		t.Fatal("the idle key is still held")
	}
	if armed() {
		t.Fatal("an empty cache keeps a sweep scheduled")
	}
}

// At the bound the least recently used key goes, not one at random.
func TestKeyedDecisionProvidersEvictLeastRecentlyUsed(t *testing.T) {
	now := time.Date(2026, 10, 22, 12, 0, 0, 0, time.UTC)
	c := keyedDecisionProviders{now: func() time.Time { return now }}
	key := func(i int) string { return fmt.Sprintf("CALLER-KEY-%d", i) }
	first, err := c.get("typesafe", "", key(0))
	if err != nil {
		t.Fatal(err)
	}
	second, err := c.get("typesafe", "", key(1))
	if err != nil {
		t.Fatal(err)
	}
	for i := 2; i < maxKeyedDecisionProviders; i++ {
		now = now.Add(time.Millisecond)
		if _, err := c.get("typesafe", "", key(i)); err != nil {
			t.Fatal(err)
		}
	}
	// The first key is used again, so the second is now the oldest.
	now = now.Add(time.Millisecond)
	if p, _ := c.get("typesafe", "", key(0)); p != first {
		t.Fatal("the first key's provider was not cached")
	}
	now = now.Add(time.Millisecond)
	if _, err := c.get("typesafe", "", key(maxKeyedDecisionProviders)); err != nil {
		t.Fatal(err)
	}
	if c.size() != maxKeyedDecisionProviders {
		t.Fatalf("cache entries = %d, want %d", c.size(), maxKeyedDecisionProviders)
	}
	if p, _ := c.get("typesafe", "", key(0)); p != first {
		t.Fatal("the recently used key was evicted")
	}
	if p, _ := c.get("typesafe", "", key(1)); p == second {
		t.Fatal("the least recently used key was kept")
	}
}

// An internal caller names the vendor as the model prefix; anything else is
// refused before any call.
func TestDecisionInternalKeyNeedsAVendorPrefix(t *testing.T) {
	g := newDecisionGateway(t, decisionGatewayOptions{})
	for _, model := range []string{"jev-latest", "anthropic/jev-latest", "typesafe/", "custom-systemone/jev"} {
		body := strings.Replace(decisionSysBody, "typesafe/jev-latest", model, 1)
		expectError(t, g.post(t, keyInternal, body, models.ProviderKeyHeader, callerOwnKey), http.StatusBadRequest, "decision_model_prefix_required")
	}
	// A vendor of the other format is a format mismatch.
	body := strings.Replace(decisionSysBody, "typesafe/jev-latest", "openai/gpt-6-luna", 1)
	expectError(t, g.post(t, keyInternal, body, models.ProviderKeyHeader, callerOwnKey), http.StatusUnprocessableEntity, models.CodeFormatMismatch)
	body = strings.Replace(decisionOAIBody, "openai/gpt-6-luna", "typesafe/jev-latest", 1)
	expectError(t, g.post(t, keyInternal, body, models.ProviderKeyHeader, callerOwnKey), http.StatusUnprocessableEntity, models.CodeFormatMismatch)
	expectNoCalls(t, g)
}

// An internal caller reaches each vendor the way that vendor wants: TypeSafe
// at /v1/systemone, Perplexity at /v1/decisions with a Bearer key and never
// x-api-key, OpenAI at /v1/decisions. Only config.yaml's base_url for the
// vendor is used; its key, extra headers and path prefix never are.
func TestDecisionInternalKeyDialects(t *testing.T) {
	prefix := "/operator-prefix"
	g := newDecisionGateway(t, decisionGatewayOptions{config: func(cfg *config.Config, upstreamURL string) {
		for _, id := range []string{"typesafe", "openai"} {
			p := cfg.Providers[id]
			p.APIPathPrefix = &prefix
			p.Headers = map[string]string{"x-operator-canary": fagiHeldKey}
			cfg.Providers[id] = p
		}
		cfg.Providers["perplexity-decider"] = config.ProviderConfig{
			APIFormat: "systemone", BaseURL: upstreamURL, APIKey: fagiHeldKey, APIPathPrefix: &prefix,
			Headers: map[string]string{"x-operator-canary": fagiHeldKey},
			Models:  []string{"pplx-decider-v1.1-27b"},
		}
	}})

	for i, tt := range []struct{ model, body, path, sent string }{
		{"typesafe/jev-latest", decisionSysBody, "POST /v1/systemone", "jev-latest"},
		{"perplexity/pplx-decider-v1.1-27b", decisionSysBody, "POST /v1/decisions", "pplx-decider-v1.1-27b"},
		{"openai/gpt-6-luna", decisionOAIBody, "POST /v1/decisions", "gpt-6-luna"},
	} {
		body := tt.body
		if tt.body == decisionSysBody {
			body = strings.Replace(decisionSysBody, "typesafe/jev-latest", tt.model, 1)
		}
		rr := g.post(t, keyInternal, body, models.ProviderKeyHeader, callerOwnKey)
		if rr.Code != http.StatusOK {
			t.Fatalf("%s: status %d: %s", tt.model, rr.Code, rr.Body.String())
		}
		if path, _ := g.upstream.last(); path != tt.path {
			t.Errorf("%s: upstream path %q, want %q", tt.model, path, tt.path)
		}
		if got := g.upstream.sentModel(t); got != tt.sent {
			t.Errorf("%s: vendor was sent %q", tt.model, got)
		}
		headers := g.upstream.lastHeaders()
		if headers.Get("x-api-key") != "" || headers.Get("x-operator-canary") != "" {
			t.Errorf("%s: vendor saw %v", tt.model, headers)
		}
		expectOnlyKey(t, g, callerOwnKey, i+1)
	}
}

// Customers save keys under Gateway > Providers; a byok or managed key may not
// bring one in the header.
func TestDecisionProviderKeyHeaderOnCustomerKey(t *testing.T) {
	g := newDecisionGateway(t, decisionGatewayOptions{})
	for _, key := range []string{keyByok, keyManaged, keyByokNoCred} {
		expectError(t, g.post(t, key, decisionSysBody, models.ProviderKeyHeader, callerOwnKey), http.StatusBadRequest, models.CodeProviderKeyHeaderNotAllowed)
		expectError(t, g.post(t, key, decisionSysBody, models.ProviderKeyHeader, ""), http.StatusBadRequest, models.CodeProviderKeyHeaderNotAllowed)
	}
	expectNoCalls(t, g)
}

// A decision provider saved under Gateway > Providers reaches the gateway as
// the backend pushes it: through the admin contract, as api_format systemone
// or its typesafe alias. Pushed with the backend's default format instead, it
// is refused with what to change.
func TestDecisionProviderPushedThroughTheAdminContract(t *testing.T) {
	g := newDecisionGateway(t, decisionGatewayOptions{config: func(cfg *config.Config, upstreamURL string) {
		cfg.Providers["typesafe-legacy"] = config.ProviderConfig{APIFormat: "openai", BaseURL: upstreamURL, Models: []string{"jev-legacy"}}
	}})
	push := func(provider, apiFormat string, models ...string) {
		t.Helper()
		body := mustJSON(map[string]any{"providers": map[string]any{provider: map[string]any{
			"api_key": "ORG-B-KEY", "api_format": apiFormat, "models": models,
			"enabled": true, "timeout": 60, "max_concurrent": 100, "conn_pool_size": 100,
		}}})
		req := httptest.NewRequest(http.MethodPut, "/-/orgs/org-b/config", strings.NewReader(body))
		req.Header.Set("Authorization", "Bearer "+adminToken)
		rr := httptest.NewRecorder()
		g.srv.httpServer.Handler.ServeHTTP(rr, req)
		if rr.Code != http.StatusOK {
			t.Fatalf("push %s %s: %d %s", provider, apiFormat, rr.Code, rr.Body.String())
		}
	}

	for i, apiFormat := range []string{"systemone", "typesafe"} {
		push("typesafe", apiFormat, "jev-1.13.0", "jev-latest")
		rr := g.post(t, keyByokOrgB, decisionSysBody)
		if rr.Code != http.StatusOK || rr.Body.String() != decisionAnswer {
			t.Fatalf("%s: status %d: %s", apiFormat, rr.Code, rr.Body.String())
		}
		expectOnlyKey(t, g, "ORG-B-KEY", i+1)
	}

	push("typesafe-legacy", "openai", "jev-legacy")
	e := expectError(t, g.post(t, keyByokOrgB, strings.Replace(decisionSysBody, "typesafe/jev-latest", "typesafe-legacy/jev-legacy", 1)),
		http.StatusForbidden, models.CodeDecisionProviderWrongFormat)
	if !strings.Contains(e.Message, `"openai"`) || !strings.Contains(e.Message, "api_format systemone") {
		t.Errorf("message = %q", e.Message)
	}
	expectOnlyKey(t, g, "ORG-B-KEY", 2)

	// A provider config.yaml does not list is built from what was pushed
	// alone, which is how a cloud gateway holds every customer's provider: the
	// pushed api_format decides whether it serves System One. Resolved only,
	// since its upstream is the vendor's own.
	req, _ := models.ParseDecisionRequest([]byte(strings.Replace(decisionSysBody, "typesafe/jev-latest", "typesafe-eu/jev-1.14.0", 1)))
	for _, tt := range []struct{ apiFormat, code string }{
		{"systemone", ""},
		{"typesafe", ""},
		{"openai", models.CodeDecisionProviderWrongFormat},
	} {
		push("typesafe-eu", tt.apiFormat, "jev-1.14.0")
		rc := models.AcquireRequestContext()
		rc.Metadata["authorization"] = "Bearer " + keyByokOrgB
		target, err := g.srv.handlers.resolveDecision(context.Background(), http.Header{}, rc, req, "", false, "org-b", g.tenants.Get("org-b"))
		rc.Release()
		var apiErr *models.APIError
		switch {
		case tt.code == "" && (err != nil || target.providerID != "typesafe-eu" || target.upstreamModel != "jev-1.14.0"):
			t.Errorf("%s: target %+v, err %v", tt.apiFormat, target, err)
		case tt.code != "" && (!errors.As(err, &apiErr) || apiErr.Code != tt.code):
			t.Errorf("%s: err %v, want %s", tt.apiFormat, err, tt.code)
		}
	}
	expectOnlyKey(t, g, "ORG-B-KEY", 2)
}

// A managed key resolves to its org's own credential like a byok key, and the
// call draws no Future AGI credits.
func TestDecisionManagedKeyIsOrgOnlyAndDrawsNoCredits(t *testing.T) {
	g := newDecisionGateway(t, decisionGatewayOptions{})
	var managed *auth.APIKey
	for _, k := range g.keyStore.List() {
		if k.KeyType == "managed" {
			managed = k
		}
	}
	before := managed.BalanceUSD()

	rr := g.post(t, keyManaged, decisionSysBody)
	if rr.Code != http.StatusOK {
		t.Fatalf("status %d: %s", rr.Code, rr.Body.String())
	}
	expectOnlyKey(t, g, orgTypeSafeKey, 1)
	if rr.Header().Get("x-agentcc-cost") != "0.000042" {
		t.Errorf("cost = %q; the decision price should still be reported", rr.Header().Get("x-agentcc-cost"))
	}
	if got := managed.BalanceUSD(); got != before {
		t.Errorf("credit balance %f -> %f", before, got)
	}
	if v := rr.Header().Get("x-agentcc-credits-used"); v != "" {
		t.Errorf("x-agentcc-credits-used = %q", v)
	}

	// Without an org credential a managed key is refused, never served by
	// config.yaml's key.
	g.tenants.Set("org-a", &tenant.OrgConfig{})
	expectError(t, g.post(t, keyManaged, decisionSysBody), http.StatusForbidden, models.CodeDecisionProviderNotConfigured)
	if g.upstream.calls() != 1 {
		t.Fatal("a managed key without an org credential reached the vendor")
	}
}

// Every route keeps a copy of the request's headers for logs, spans and
// plugins. None of those copies carries a caller's provider key, whatever
// route the key was sent to.
func TestClonedRequestHeadersDropTheProviderKey(t *testing.T) {
	for _, path := range []string{"/v1/chat/completions", "/v1/embeddings", "/v1/systemone"} {
		r := httptest.NewRequest(http.MethodPost, path, nil)
		r.Header.Set(models.ProviderKeyHeader, callerOwnKey)
		r.Header.Set("x-agentcc-metadata", `{"workflow":"jev-eval"}`)
		cloned := cloneRequestHeaders(r)
		if strings.Contains(fmt.Sprint(cloned), callerOwnKey) {
			t.Errorf("%s: cloned headers carry the provider key: %v", path, cloned)
		}
		if cloned.Get("x-agentcc-metadata") == "" {
			t.Errorf("%s: other headers were dropped too", path)
		}
		if r.Header.Get(models.ProviderKeyHeader) != callerOwnKey {
			t.Errorf("%s: cloning changed the request itself", path)
		}
	}
}

// A licence token is refused at the edge, and the resolver refuses it again.
func TestDecisionLicenceTokenIsRefused(t *testing.T) {
	privateKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	der, _ := x509.MarshalPKIXPublicKey(&privateKey.PublicKey)
	publicPEM := string(pem.EncodeToMemory(&pem.Block{Type: "PUBLIC KEY", Bytes: der}))
	token := signLicenceToken(t, privateKey, []string{"turing", "jev"}, []string{"turing_small", "jev-latest", "typesafe/jev-latest"})

	g := newDecisionGateway(t, decisionGatewayOptions{config: func(cfg *config.Config, _ string) {
		cfg.LicenseAuth = config.LicenseAuthConfig{Enabled: true, PublicKey: publicPEM}
	}})
	expectError(t, g.post(t, token, decisionSysBody), http.StatusForbidden, "permission_denied")
	expectError(t, g.post(t, token, decisionSysBody, models.ProviderKeyHeader, callerOwnKey), http.StatusForbidden, "permission_denied")
	if rr := g.do(t, http.MethodGet, "/v1/decisions/models", token, ""); rr.Code != http.StatusForbidden {
		t.Errorf("models listing: %d", rr.Code)
	}
	expectNoCalls(t, g)

	// Behind the edge: a request a licence authorized, as chat completions
	// would carry it, still gets nothing from the decision resolver.
	var licensed context.Context
	middleware.LicenseAuth(config.LicenseAuthConfig{Enabled: true, PublicKey: publicPEM}, nil)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		licensed = r.Context()
	})).ServeHTTP(httptest.NewRecorder(), func() *http.Request {
		r := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(`{"model":"turing_small"}`))
		r.Header.Set("Authorization", "Bearer "+token)
		return r
	}())
	if licensed == nil || !middleware.IsLicenseAuthorized(licensed) {
		t.Fatal("could not obtain a licence-authorized context")
	}
	req, _ := models.ParseDecisionRequest([]byte(decisionSysBody))
	for _, hasHeader := range []bool{false, true} {
		rc := models.AcquireRequestContext()
		rc.Metadata["authorization"] = "Bearer " + keyInternal
		_, err := g.srv.handlers.resolveDecision(licensed, http.Header{}, rc, req, callerOwnKey, hasHeader, "", nil)
		rc.Release()
		var apiErr *models.APIError
		if !errors.As(err, &apiErr) || apiErr.Code != models.CodeBYOKRequired {
			t.Fatalf("licensed context resolved: %v", err)
		}
	}
	// And so does a request the auth plugin marked as licence-authorized.
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Metadata["auth_type"] = "license"
	if _, err := g.srv.handlers.resolveDecision(context.Background(), http.Header{}, rc, req, "", false, "", nil); err == nil {
		t.Fatal("auth_type=license resolved")
	}
	expectNoCalls(t, g)
}

func signLicenceToken(t *testing.T, key *rsa.PrivateKey, services, scopeModels []string) string {
	t.Helper()
	now := time.Now().Unix()
	header, _ := json.Marshal(map[string]string{"alg": "RS256", "kid": "default"})
	payload, _ := json.Marshal(map[string]any{
		"typ": "futureagi-managed-service-token", "iss": "https://licenses.futureagi.com", "aud": "futureagi-agentcc-gateway",
		"iat": now, "nbf": now, "exp": now + 3600, "jti": "tok_test", "license_id": "lic_test", "instance_id": "inst_test",
		"authorization_version": 1, "scope": "enterprise", "services": services, "models": scopeModels,
	})
	signed := base64.RawURLEncoding.EncodeToString(header) + "." + base64.RawURLEncoding.EncodeToString(payload)
	digest := sha256.Sum256([]byte(signed))
	sig, err := rsa.SignPKCS1v15(rand.Reader, key, crypto.SHA256, digest[:])
	if err != nil {
		t.Fatal(err)
	}
	return signed + "." + base64.RawURLEncoding.EncodeToString(sig)
}

// A provider lock filters the org's own providers. One naming any other
// provider, from the header or from metadata, is refused before any call.
func TestDecisionProviderLock(t *testing.T) {
	g := newDecisionGateway(t, decisionGatewayOptions{})
	for _, headers := range [][]string{
		{"x-agentcc-provider-lock", "mirror-sink"},
		{"x-agentcc-provider-lock", "anthropic"},
		{"x-agentcc-metadata", `{"provider_lock":"mirror-sink"}`},
	} {
		expectError(t, g.post(t, keyByok, decisionSysBody, headers...), http.StatusForbidden, "provider_lock_not_allowed")
	}
	expectNoCalls(t, g)

	// Locked to the org's own provider, the call goes through on its key.
	if rr := g.post(t, keyByok, decisionSysBody, "x-agentcc-provider-lock", "typesafe"); rr.Code != http.StatusOK {
		t.Fatalf("status %d: %s", rr.Code, rr.Body.String())
	}
	if rr := g.post(t, keyByok, strings.Replace(decisionSysBody, "typesafe/jev-latest", "jev-latest", 1), "x-agentcc-metadata", `{"provider_lock":"typesafe"}`); rr.Code != http.StatusOK {
		t.Fatalf("status %d: %s", rr.Code, rr.Body.String())
	}
	expectOnlyKey(t, g, orgTypeSafeKey, 2)

	// Locked to an org provider that does not list the model: no credential.
	expectError(t, g.post(t, keyByok, decisionSysBody, "x-agentcc-provider-lock", "openai"), http.StatusForbidden, models.CodeDecisionProviderNotConfigured)
	expectOnlyKey(t, g, orgTypeSafeKey, 2)
}

// Conditional routes, model fallbacks, failover, retries and the mirror are
// all configured to take decision traffic; none of them ever touches it.
func TestDecisionNeverUsesRoutingFallbacksOrMirror(t *testing.T) {
	g := newDecisionGateway(t, decisionGatewayOptions{
		config: func(cfg *config.Config, upstreamURL string) {
			cfg.Routing.ConditionalRoutes = []config.ConditionalRouteConfig{{
				Name:      "rewrite-decisions",
				Condition: config.ConditionConfig{Field: "model", Op: "$exists", Value: true},
				Action:    config.RouteActionConfig{Provider: "typesafe", ModelOverride: "jev-evil"},
			}}
			cfg.Routing.ModelFallbacks = map[string][]string{
				"jev-latest":       {"jev-1.13.0"},
				"jev-missing":      {"jev-latest"},
				"typesafe/jev-bad": {"jev-latest"},
			}
			cfg.Routing.Failover = config.FailoverConfig{Enabled: true, MaxAttempts: 3, OnStatusCodes: []int{500, 502, 503}}
			cfg.Routing.Retry = config.RetryConfig{Enabled: true, MaxRetries: 3, InitialDelay: time.Millisecond, Multiplier: 1, OnStatusCodes: []int{503}}
			cfg.Routing.Mirror = config.MirrorConfig{Enabled: true, Rules: []config.MirrorRule{
				{SourceModel: "*", TargetProvider: "mirror-sink", TargetModel: "mirror-model", SampleRate: 1},
				{SourceModel: "jev-latest", TargetProvider: "mirror-sink", TargetModel: "mirror-model", SampleRate: 1},
			}}
			cfg.Providers["typesafe-b"] = config.ProviderConfig{APIFormat: "systemone", BaseURL: upstreamURL, APIKey: fagiHeldKey, Models: []string{"jev-latest"}}
		},
		orgs: map[string]*tenant.OrgConfig{"org-a": func() *tenant.OrgConfig {
			org := defaultOrgA()
			org.Routing = &tenant.RoutingConfig{
				FallbackEnabled: true,
				ModelFallbacks:  map[string][]string{"jev-missing": {"jev-latest"}},
			}
			return org
		}()},
	})

	rr := g.post(t, keyByok, decisionSysBody)
	if rr.Code != http.StatusOK {
		t.Fatalf("status %d: %s", rr.Code, rr.Body.String())
	}
	if got := g.upstream.sentModel(t); got != "jev-latest" {
		t.Fatalf("vendor was sent %q: a route rewrote the model", got)
	}
	if rr.Header().Get("x-agentcc-fallback-used") != "" || rr.Header().Get("x-agentcc-routing-strategy") != "" {
		t.Fatalf("routing headers set: %v", rr.Header())
	}

	// A model no org provider lists has no fallback, org or global.
	for _, model := range []string{"jev-missing", "typesafe/jev-bad"} {
		expectError(t, g.post(t, keyByok, strings.Replace(decisionSysBody, "typesafe/jev-latest", model, 1)), http.StatusForbidden, models.CodeDecisionProviderNotConfigured)
	}

	// A failing vendor is not retried and not failed over.
	g.upstream.setReply(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Retry-After", "3")
		w.WriteHeader(http.StatusServiceUnavailable)
	})
	rr = g.post(t, keyByok, decisionSysBody)
	expectError(t, rr, http.StatusServiceUnavailable, "provider_overloaded")
	if rr.Header().Get("Retry-After") != "3" || rr.Header().Get("x-agentcc-upstream-status") != "503" {
		t.Errorf("headers = %v", rr.Header())
	}
	expectOnlyKey(t, g, orgTypeSafeKey, 2)
	if n := g.mirror.calls(); n != 0 {
		t.Fatalf("the mirror received %d calls", n)
	}
}

// Several org providers listing the model resolve the same way every time, in
// provider ID order; and a credential saved with a format that serves no
// decisions gets an error that says so.
func TestDecisionCandidateOrderAndWrongFormat(t *testing.T) {
	g := newDecisionGateway(t, decisionGatewayOptions{
		config: func(cfg *config.Config, upstreamURL string) {
			for _, id := range []string{"typesafe-b", "typesafe-a"} {
				cfg.Providers[id] = config.ProviderConfig{APIFormat: "systemone", BaseURL: upstreamURL, Models: []string{"jev-latest"}}
			}
			// A TypeSafe credential saved as an OpenAI chat provider, and a
			// provider that is no decision provider at all.
			cfg.Providers["typesafe-legacy"] = config.ProviderConfig{APIFormat: "openai", BaseURL: upstreamURL, Models: []string{"jev-legacy"}}
			cfg.Providers["anthropic"] = config.ProviderConfig{APIFormat: "anthropic", BaseURL: upstreamURL, Models: []string{"jev-anthropic"}}
		},
		orgs: map[string]*tenant.OrgConfig{"org-a": {Providers: map[string]*tenant.ProviderConfig{
			"typesafe-b":      {APIKey: "KEY-B", APIFormat: "systemone", Models: []string{"jev-latest"}, Enabled: true},
			"typesafe-a":      {APIKey: "KEY-A", APIFormat: "systemone", Models: []string{"jev-latest"}, Enabled: true},
			"typesafe-legacy": {APIKey: "KEY-LEGACY", APIFormat: "openai", Models: []string{"jev-legacy"}, Enabled: true},
			"anthropic":       {APIKey: "KEY-ANTHROPIC", APIFormat: "anthropic", Models: []string{"jev-anthropic"}, Enabled: true},
			"typesafe-off":    {APIKey: "KEY-OFF", APIFormat: "systemone", Models: []string{"jev-latest"}, Enabled: false},
			// Saved with 'Select All', which leaves gpt-6-luna out.
			"openai": {APIKey: "KEY-OPENAI", APIFormat: "openai", Models: []string{"gpt-4o"}, Enabled: true},
		}}},
	})
	body := strings.Replace(decisionSysBody, "typesafe/jev-latest", "jev-latest", 1)
	for i := 0; i < 20; i++ {
		if rr := g.post(t, keyByok, body); rr.Code != http.StatusOK || rr.Header().Get("x-agentcc-provider") != "typesafe-a" {
			t.Fatalf("attempt %d: status %d provider %q", i, rr.Code, rr.Header().Get("x-agentcc-provider"))
		}
	}
	expectOnlyKey(t, g, "KEY-A", 20)

	// The prefix picks the other one.
	if rr := g.post(t, keyByok, strings.Replace(decisionSysBody, "typesafe/", "typesafe-b/", 1)); rr.Code != http.StatusOK {
		t.Fatalf("status %d: %s", rr.Code, rr.Body.String())
	}
	if g.upstream.callsWith("Bearer KEY-B") != 1 {
		t.Fatalf("calls by key: %v", g.upstream.authorizations())
	}

	for model, wantSaved := range map[string]string{"jev-legacy": `"openai"`, "jev-anthropic": `"anthropic"`} {
		e := expectError(t, g.post(t, keyByok, strings.Replace(decisionSysBody, "typesafe/jev-latest", model, 1)), http.StatusForbidden, models.CodeDecisionProviderWrongFormat)
		if !strings.Contains(e.Message, wantSaved) || !strings.Contains(e.Message, "System One") {
			t.Errorf("%s: message = %q", model, e.Message)
		}
	}
	// A provider that holds the vendor's key but does not list the model is
	// named, with the fix: the first in ID order, or the one the request names.
	for model, want := range map[string]string{
		"jev-1.13.0":            `Provider "typesafe-a" has your TypeSafe key but does not list "jev-1.13.0". Add the model to it under Gateway > Providers.`,
		"typesafe-b/jev-1.13.0": `Provider "typesafe-b" has your TypeSafe key but does not list "jev-1.13.0".`,
		"mystery-model":         "No provider in your organization serves",
	} {
		e := expectError(t, g.post(t, keyByok, strings.Replace(decisionSysBody, "typesafe/jev-latest", model, 1)), http.StatusForbidden, models.CodeDecisionProviderNotConfigured)
		if !strings.Contains(e.Message, want) {
			t.Errorf("%s: message = %q, want %q", model, e.Message, want)
		}
	}
	for _, model := range []string{"openai/gpt-6-luna", "gpt-6-luna"} {
		e := expectError(t, g.post(t, keyByok, strings.Replace(decisionOAIBody, "openai/gpt-6-luna", model, 1)), http.StatusForbidden, models.CodeDecisionProviderNotConfigured)
		if want := `Provider "openai" has your OpenAI key but does not list "gpt-6-luna".`; !strings.Contains(e.Message, want) {
			t.Errorf("%s: message = %q, want %q", model, e.Message, want)
		}
	}
	for key, want := range map[string]int{"KEY-A": 20, "KEY-B": 1, "KEY-LEGACY": 0, "KEY-ANTHROPIC": 0, "KEY-OFF": 0, "KEY-OPENAI": 0, fagiHeldKey: 0} {
		if got := g.upstream.callsWith("Bearer " + key); got != want {
			t.Errorf("calls with %s = %d, want %d", key, got, want)
		}
	}
	if g.upstream.calls() != 21 {
		t.Fatalf("a wrong-format credential reached its vendor: %v", g.upstream.authorizations())
	}
}

// The "<provider>/" prefix names the org provider and is stripped; a vendor
// whose own model IDs carry a slash keeps them.
func TestDecisionPrefixStripping(t *testing.T) {
	g := newDecisionGateway(t, decisionGatewayOptions{
		config: func(cfg *config.Config, upstreamURL string) {
			cfg.Providers["openrouter"] = config.ProviderConfig{APIFormat: "systemone", BaseURL: upstreamURL, Models: []string{"typesafe/jev-1.13.0"}}
		},
		orgs: map[string]*tenant.OrgConfig{"org-a": func() *tenant.OrgConfig {
			org := defaultOrgA()
			org.Providers["openrouter"] = &tenant.ProviderConfig{APIKey: "KEY-OPENROUTER", APIFormat: "systemone", Models: []string{"typesafe/jev-1.13.0"}, Enabled: true}
			return org
		}()},
	})
	for _, tt := range []struct{ model, provider, sent string }{
		{"typesafe/jev-latest", "typesafe", "jev-latest"},
		{"TypeSafe/jev-latest", "typesafe", "jev-latest"},
		{"jev-latest", "typesafe", "jev-latest"},
		{"typesafe/jev-1.13.0", "typesafe", "jev-1.13.0"},
		{"openrouter/typesafe/jev-1.13.0", "openrouter", "typesafe/jev-1.13.0"},
	} {
		rr := g.post(t, keyByok, strings.Replace(decisionSysBody, "typesafe/jev-latest", tt.model, 1))
		if rr.Code != http.StatusOK || rr.Header().Get("x-agentcc-provider") != tt.provider {
			t.Fatalf("%s: status %d provider %q: %s", tt.model, rr.Code, rr.Header().Get("x-agentcc-provider"), rr.Body.String())
		}
		if got := g.upstream.sentModel(t); got != tt.sent {
			t.Errorf("%s: vendor was sent %q, want %q", tt.model, got, tt.sent)
		}
		// Only the model changed; the rest is the caller's bytes.
		_, sentBody := g.upstream.last()
		if want := strings.Replace(decisionSysBody, `"typesafe/jev-latest"`, `"`+tt.sent+`"`, 1); sentBody != want {
			t.Errorf("%s: body =\n%s\nwant\n%s", tt.model, sentBody, want)
		}
	}
	if g.upstream.callsWith("Bearer "+orgTypeSafeKey) != 4 || g.upstream.callsWith("Bearer KEY-OPENROUTER") != 1 || g.upstream.calls() != 5 {
		t.Fatalf("calls by key: %v", g.upstream.authorizations())
	}
}

// OpenAI Decisions go to the org's OpenAI credential at /v1/decisions; a body
// whose format the resolved provider does not speak is refused, untranslated.
func TestDecisionOpenAIFormat(t *testing.T) {
	g := newDecisionGateway(t, decisionGatewayOptions{})
	rr := g.post(t, keyByok, decisionOAIBody)
	if rr.Code != http.StatusOK {
		t.Fatalf("status %d: %s", rr.Code, rr.Body.String())
	}
	if path, _ := g.upstream.last(); path != "POST /v1/decisions" || g.upstream.sentModel(t) != "gpt-6-luna" {
		t.Fatalf("path %q model %q", path, g.upstream.sentModel(t))
	}
	expectOnlyKey(t, g, orgOpenAIKey, 1)
	if rr.Header().Get("x-agentcc-cost") == "" {
		t.Error("no cost for gpt-6-luna")
	}

	e := expectError(t, g.post(t, keyByok, strings.Replace(decisionSysBody, "typesafe/jev-latest", "openai/gpt-6-luna", 1)), http.StatusUnprocessableEntity, models.CodeFormatMismatch)
	if !strings.Contains(e.Message, "/v1/decisions") {
		t.Errorf("hint = %q", e.Message)
	}
	e = expectError(t, g.post(t, keyByok, strings.Replace(decisionOAIBody, "openai/gpt-6-luna", "typesafe/jev-latest", 1)), http.StatusUnprocessableEntity, models.CodeFormatMismatch)
	if !strings.Contains(e.Message, "/v1/systemone") {
		t.Errorf("hint = %q", e.Message)
	}
	expectOnlyKey(t, g, orgOpenAIKey, 1)

	// /v1/decisions serves a System One body exactly as /v1/systemone does.
	if rr := g.do(t, http.MethodPost, "/v1/decisions", keyByok, decisionSysBody); rr.Code != http.StatusOK || rr.Body.String() != decisionAnswer {
		t.Fatalf("System One on /v1/decisions: %d %s", rr.Code, rr.Body.String())
	}
	if path, _ := g.upstream.last(); path != "POST /v1/systemone" || g.upstream.callsWith("Bearer "+orgTypeSafeKey) != 1 {
		t.Fatalf("System One on /v1/decisions went to %q with keys %v", path, g.upstream.authorizations())
	}
}

// The org's RPM limit applies to decisions as it does to chat.
func TestDecisionOrgRateLimit(t *testing.T) {
	org := defaultOrgA()
	org.RateLimiting = &tenant.RateLimitConfig{Enabled: true, GlobalRPM: 1}
	g := newDecisionGateway(t, decisionGatewayOptions{orgs: map[string]*tenant.OrgConfig{"org-a": org}})
	if rr := g.post(t, keyByok, decisionSysBody); rr.Code != http.StatusOK {
		t.Fatalf("first: %d %s", rr.Code, rr.Body.String())
	}
	expectError(t, g.post(t, keyByok, decisionSysBody), http.StatusTooManyRequests, "rate_limit_exceeded")
	expectOnlyKey(t, g, orgTypeSafeKey, 1)
}

// A decision body over the cap is refused before anything else.
func TestDecisionBodyCap(t *testing.T) {
	g := newDecisionGateway(t, decisionGatewayOptions{config: func(cfg *config.Config, _ string) {
		cfg.Server.MaxRequestBodySize = 256
	}})
	body := strings.Replace(decisionSysBody, "private-state", strings.Repeat("x", 300), 1)
	expectError(t, g.post(t, keyByok, body), http.StatusRequestEntityTooLarge, "request_too_large")
	expectNoCalls(t, g)

	// The cap is the server's limit, but never more than 32 MiB.
	h := &Handlers{maxBodySize: 50 << 20}
	if got := h.decisionBodyLimit(); got != 32<<20 {
		t.Errorf("limit = %d", got)
	}
	h.maxBodySize = 1 << 20
	if got := h.decisionBodyLimit(); got != 1<<20 {
		t.Errorf("limit = %d", got)
	}
}

// allowed_providers is checked after the auth plugin has run, against the
// org provider the call resolved to.
func TestDecisionAllowedProviders(t *testing.T) {
	g := newDecisionGateway(t, decisionGatewayOptions{})
	e := expectError(t, g.post(t, keyByokACL, decisionSysBody), http.StatusForbidden, "permission_denied")
	if !strings.Contains(e.Message, "typesafe") {
		t.Errorf("message = %q", e.Message)
	}
	expectNoCalls(t, g)

	// A key allowed that provider is served with the org's key.
	if rr := g.post(t, keyByokACLOK, decisionSysBody); rr.Code != http.StatusOK || rr.Header().Get("x-agentcc-provider") != "typesafe" {
		t.Fatalf("status %d provider %q: %s", rr.Code, rr.Header().Get("x-agentcc-provider"), rr.Body.String())
	}
	expectOnlyKey(t, g, orgTypeSafeKey, 1)
	// The same key is refused a provider it is not allowed.
	expectError(t, g.post(t, keyByokACLOK, decisionOAIBody), http.StatusForbidden, "permission_denied")
	expectOnlyKey(t, g, orgTypeSafeKey, 1)
}

// With auth off (a self-host with no keystore) the registry serves the call
// with config.yaml's key, which there is the operator's own; exact matches
// only, and no header.
func TestDecisionAuthOffUsesTheRegistryOnly(t *testing.T) {
	g := newDecisionGateway(t, decisionGatewayOptions{authOff: true})
	for _, tt := range []struct{ model, sent string }{
		{"jev-latest", "jev-latest"},
		{"typesafe/jev-latest", "jev-latest"},
	} {
		rr := g.post(t, "", strings.Replace(decisionSysBody, "typesafe/jev-latest", tt.model, 1))
		if rr.Code != http.StatusOK {
			t.Fatalf("%s: %d %s", tt.model, rr.Code, rr.Body.String())
		}
		if got := g.upstream.sentModel(t); got != tt.sent {
			t.Errorf("%s: sent %q", tt.model, got)
		}
	}
	if g.upstream.callsWith("Bearer "+fagiHeldKey) != 2 || g.upstream.calls() != 2 {
		t.Fatalf("calls by key: %v", g.upstream.authorizations())
	}
	// No org credential is consulted: there is no key to name an org.
	if g.upstream.callsWith("Bearer "+orgTypeSafeKey) != 0 {
		t.Fatal("an org credential was used with auth off")
	}
	expectError(t, g.post(t, "", decisionSysBody, models.ProviderKeyHeader, callerOwnKey), http.StatusBadRequest, models.CodeProviderKeyHeaderNotAllowed)
	expectError(t, g.post(t, "", strings.Replace(decisionSysBody, "typesafe/jev-latest", "jev-nowhere", 1)), http.StatusNotFound, "model_not_found")
	// A chat provider in the registry serves no System One body.
	expectError(t, g.post(t, "", strings.Replace(decisionSysBody, "typesafe/jev-latest", "gpt-6-luna", 1)), http.StatusUnprocessableEntity, models.CodeFormatMismatch)
	if g.upstream.calls() != 2 {
		t.Fatal("a refused request reached the vendor")
	}
}

func TestDecisionModelsListing(t *testing.T) {
	g := newDecisionGateway(t, decisionGatewayOptions{})
	list := func(key string) []decisionModel {
		rr := g.do(t, http.MethodGet, "/v1/decisions/models", key, "")
		if rr.Code != http.StatusOK {
			t.Fatalf("status %d: %s", rr.Code, rr.Body.String())
		}
		var resp struct {
			Object string          `json:"object"`
			Data   []decisionModel `json:"data"`
		}
		if err := json.Unmarshal(rr.Body.Bytes(), &resp); err != nil || resp.Object != "list" {
			t.Fatalf("body %s: %v", rr.Body.String(), err)
		}
		return resp.Data
	}

	built := g.srv.OrgProviderCache.Count()
	got := list(keyByok)
	if n := g.srv.OrgProviderCache.Count(); n != built {
		t.Errorf("listing built %d providers; it reads the format from their config", n-built)
	}
	want := []decisionModel{
		{ID: "openai/gpt-6-luna", Object: "model", Provider: "openai", Format: "openai", Pricing: &decisionModelPricing{InputPerMTok: 0.1}},
		{ID: "typesafe/jev-1.13.0", Object: "model", Provider: "typesafe", Format: "systemone", Pricing: &decisionModelPricing{InputPerMTok: 0.042}},
		{ID: "typesafe/jev-latest", Object: "model", Provider: "typesafe", Format: "systemone", Pricing: &decisionModelPricing{InputPerMTok: 0.042}},
	}
	if gotJSON, wantJSON := mustJSON(got), mustJSON(want); gotJSON != wantJSON {
		t.Fatalf("models =\n%s\nwant\n%s", gotJSON, wantJSON)
	}
	if got := list(keyByokNoCred); len(got) != 0 {
		t.Errorf("an org without credentials lists %v", got)
	}
	if got := list(keyInternal); len(got) != 0 {
		t.Errorf("an internal key lists %v", got)
	}

	// The org's custom pricing is what it pays.
	org := defaultOrgA()
	org.CostTracking = &tenant.CostTrackingConfig{Enabled: true, CustomPricing: map[string]*tenant.CustomPricing{"jev-latest": {InputPerMTok: 0.03}}}
	g.tenants.Set("org-a", org)
	g.srv.OrgProviderCache.Evict("org-a")
	for _, m := range list(keyByok) {
		if m.ID == "typesafe/jev-latest" && (m.Pricing == nil || m.Pricing.InputPerMTok != 0.03) {
			t.Errorf("custom pricing ignored: %+v", m.Pricing)
		}
	}
	expectNoCalls(t, g)
}

// The listing reads a provider's decision format from its config; a call
// reads it from the provider built. The two agree for every api_format, the
// provider IDs that name a System One vendor, and an org provider layered onto
// a config.yaml entry (whose api_format wins). A provider the listing would
// show is one that builds, for every base_url judged without a lookup.
func TestDecisionFormatFromConfigMatchesTheBuiltProvider(t *testing.T) {
	formats := []string{"", "openai", "anthropic", "gemini", "google", "bedrock", "cohere", "azure", "systemone", "typesafe", "deepgram", "cartesia"}
	baseURLs := []string{"", "https://10.1.2.3", "https://203.0.113.7", "http://127.0.0.1:8080", "http://169.254.169.254",
		"http://metadata.google.internal", "ftp://203.0.113.7", "http://"}
	compared := map[string]int{}
	refused := 0
	for _, allowPrivate := range []bool{true, false} {
		cache := providers.NewOrgProviderCache(map[string]config.ProviderConfig{
			"layered":           {BaseURL: "https://10.1.2.3", APIFormat: "openai"},
			"layered-systemone": {APIFormat: "systemone"},
		}, allowPrivate)
		for _, id := range []string{"custom", "typesafe", "perplexity-decider", "openai", "layered", "layered-systemone"} {
			for _, baseURL := range baseURLs {
				for _, apiFormat := range formats {
					pcfg := &tenant.ProviderConfig{APIKey: "KEY", APIFormat: apiFormat, BaseURL: baseURL, AWSRegion: "us-east-1"}
					name := fmt.Sprintf("%s|%s|%s|private=%v", id, baseURL, apiFormat, allowPrivate)
					fromConfig, ok := cache.APIFormat(id, pcfg)
					format := apiFormatDecisionFormat(fromConfig, id, baseURL)
					p, err := cache.GetOrCreateWithTenantConfig(name, id, "KEY", pcfg)
					if err != nil {
						if ok && format != "" {
							t.Errorf("%s: listed as %q, but the build is refused: %v", name, format, err)
						}
						refused++
						continue
					}
					if !ok {
						t.Errorf("%s: builds, but the listing leaves it out", name)
						continue
					}
					want := decisionFormatOf(p, id, baseURL)
					if format != want {
						t.Errorf("%s: format from config %q, built provider %q", name, format, want)
					}
					compared[want]++
				}
			}
		}
	}
	if compared[models.DecisionFormatSystemOne] == 0 || compared[models.DecisionFormatOpenAI] == 0 || compared[""] == 0 || refused == 0 {
		t.Fatalf("compared %v, refused %d: every outcome should be covered", compared, refused)
	}
	t.Logf("compared %v, refused %d", compared, refused)
}

// A provider whose build its config alone shows would be refused is not
// listed: a System One endpoint with no base_url, or a base_url the gateway
// refuses without resolving it. The one that builds is listed.
func TestDecisionModelsListingLeavesOutProvidersThatCannotBuild(t *testing.T) {
	judge := func(apiFormat, baseURL, model string) *tenant.ProviderConfig {
		return &tenant.ProviderConfig{APIKey: orgTypeSafeKey, APIFormat: apiFormat, BaseURL: baseURL, Models: []string{model}, Enabled: true}
	}
	org := &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"custom":          judge("systemone", "", "judge-1"),
		"custom-public":   judge("systemone", "https://203.0.113.7", "judge-1"),
		"custom-private":  judge("systemone", "https://10.1.2.3", "judge-1"),
		"custom-loopback": judge("systemone", "http://127.0.0.1:8080", "judge-1"),
		"custom-metadata": judge("typesafe", "http://metadata.google.internal", "judge-1"),
		"custom-ftp":      judge("systemone", "ftp://203.0.113.7", "judge-1"),
		"openai-private":  judge("openai", "https://10.1.2.3", "gpt-6-luna"),
	}}
	g := newDecisionGateway(t, decisionGatewayOptions{orgs: map[string]*tenant.OrgConfig{"org-a": org}})

	rr := g.do(t, http.MethodGet, "/v1/decisions/models", keyByok, "")
	var resp struct {
		Data []decisionModel `json:"data"`
	}
	if err := json.Unmarshal(rr.Body.Bytes(), &resp); rr.Code != http.StatusOK || err != nil {
		t.Fatalf("status %d: %s", rr.Code, rr.Body.String())
	}
	want := []decisionModel{{ID: "custom-public/judge-1", Object: "model", Provider: "custom-public", Format: "systemone"}}
	if gotJSON, wantJSON := mustJSON(resp.Data), mustJSON(want); gotJSON != wantJSON {
		t.Fatalf("models =\n%s\nwant\n%s", gotJSON, wantJSON)
	}
	if n := g.srv.OrgProviderCache.Count(); n != 0 {
		t.Fatalf("listing built %d providers", n)
	}
	for id, pcfg := range org.Providers {
		_, err := g.srv.OrgProviderCache.GetOrCreateWithTenantConfig("org-a", id, pcfg.APIKey, pcfg)
		if listed := id == "custom-public"; listed != (err == nil) {
			t.Errorf("%s: listed %v, build error %v", id, listed, err)
		}
	}
	expectNoCalls(t, g)
}

func mustJSON(v any) string {
	b, _ := json.Marshal(v)
	return string(b)
}

// --- The shared fixture ------------------------------------------------------

func loadDecisionFixture(t *testing.T) map[string]json.RawMessage {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "api_contracts", "gateway", "decision-cases.json"))
	if err != nil {
		t.Fatalf("reading the shared decision cases: %v", err)
	}
	var fixture map[string]json.RawMessage
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	return fixture
}

// Every envelope case refused through either route is refused with the same
// status and code, and never echoes the request.
func TestDecisionHandlerSharedEnvelopeCases(t *testing.T) {
	var cases []struct {
		Name        string          `json:"name"`
		Route       string          `json:"route"`
		Body        json.RawMessage `json:"body"`
		BodyRaw     *string         `json:"body_raw"`
		Status      int             `json:"status"`
		Code        string          `json:"code"`
		MustNotEcho []string        `json:"must_not_echo"`
	}
	if err := json.Unmarshal(loadDecisionFixture(t)["envelope_cases"], &cases); err != nil {
		t.Fatal(err)
	}
	g := newDecisionGateway(t, decisionGatewayOptions{})
	for _, tc := range cases {
		if tc.Status == 0 {
			continue
		}
		body := string(tc.Body)
		if tc.BodyRaw != nil {
			body = *tc.BodyRaw
		}
		routes := []string{"/v1/decisions", "/v1/systemone"}
		if tc.Route != "" {
			routes = []string{tc.Route}
		}
		for _, route := range routes {
			rr := g.do(t, http.MethodPost, route, keyByok, body)
			e := expectError(t, rr, tc.Status, tc.Code)
			for _, secret := range tc.MustNotEcho {
				if strings.Contains(e.Message, secret) {
					t.Errorf("%s %s echoed content: %s", tc.Name, route, e.Message)
				}
			}
		}
	}
	expectNoCalls(t, g)
}

// The vendor's answers reach the caller as the shared table says, headers
// included.
func TestDecisionHandlerSharedErrorCases(t *testing.T) {
	var cases []struct {
		Name     string `json:"name"`
		Upstream struct {
			Status  int               `json:"status"`
			Headers map[string]string `json:"headers"`
			Body    json.RawMessage   `json:"body"`
			BodyRaw *string           `json:"body_raw"`
		} `json:"upstream"`
		Returned struct {
			Status     int    `json:"status"`
			Code       string `json:"code"`
			RetryAfter string `json:"retry_after"`
		} `json:"returned"`
	}
	fixture := loadDecisionFixture(t)
	if err := json.Unmarshal(fixture["error_cases"], &cases); err != nil {
		t.Fatal(err)
	}
	var apiKey string
	json.Unmarshal(fixture["api_key"], &apiKey)
	org := defaultOrgA()
	org.Providers["typesafe"].APIKey = apiKey
	g := newDecisionGateway(t, decisionGatewayOptions{orgs: map[string]*tenant.OrgConfig{"org-a": org}})
	for _, tc := range cases {
		g.upstream.setReply(func(w http.ResponseWriter, r *http.Request) {
			for k, v := range tc.Upstream.Headers {
				w.Header().Set(k, v)
			}
			w.WriteHeader(tc.Upstream.Status)
			if tc.Upstream.BodyRaw != nil {
				io.WriteString(w, *tc.Upstream.BodyRaw)
			} else {
				w.Write(tc.Upstream.Body)
			}
		})
		rr := g.post(t, keyByok, string(fixture["request"]))
		expectError(t, rr, tc.Returned.Status, tc.Returned.Code)
		if got := rr.Header().Get("Retry-After"); got != tc.Returned.RetryAfter {
			t.Errorf("%s: Retry-After %q, want %q", tc.Name, got, tc.Returned.RetryAfter)
		}
		if got := rr.Header().Get("x-agentcc-upstream-status"); got != fmt.Sprint(tc.Upstream.Status) {
			t.Errorf("%s: x-agentcc-upstream-status %q", tc.Name, got)
		}
		for _, secret := range []string{"private-state-CANARY", "private-instructions-CANARY", "private-criteria-CANARY", "synthetic-decision-key-CANARY"} {
			if strings.Contains(rr.Body.String(), secret) {
				t.Errorf("%s: response echoes %s", tc.Name, secret)
			}
		}
	}
	// One call per request, each with the org's key: the gateway never
	// retries, and never falls back to another key.
	expectOnlyKey(t, g, apiKey, len(cases))
	// No upstream error text reaches the request log.
	logs := g.flushLogs()
	for _, secret := range []string{"Unsupported criteria shape", "private-state-CANARY", "slow down", "top up"} {
		if strings.Contains(logs, secret) {
			t.Errorf("request log carries upstream error text %q", secret)
		}
	}
}

// Each refusal the shared fixture lists is what the gateway returns.
func TestDecisionSharedGatewayErrorCases(t *testing.T) {
	var cases []struct {
		Name     string `json:"name"`
		Returned struct {
			Status int    `json:"status"`
			Code   string `json:"code"`
		} `json:"returned"`
	}
	if err := json.Unmarshal(loadDecisionFixture(t)["gateway_error_cases"], &cases); err != nil {
		t.Fatal(err)
	}
	// Each scenario returns its gateway too, to show no vendor was called.
	type scenario func(t *testing.T) (*decisionGateway, *httptest.ResponseRecorder)
	posting := func(opts decisionGatewayOptions, key, body string, headers ...string) scenario {
		return func(t *testing.T) (*decisionGateway, *httptest.ResponseRecorder) {
			g := newDecisionGateway(t, opts)
			return g, g.post(t, key, body, headers...)
		}
	}
	scenarios := map[string]scenario{
		"licence_token": func(t *testing.T) (*decisionGateway, *httptest.ResponseRecorder) {
			g := newDecisionGateway(t, decisionGatewayOptions{})
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.Metadata["auth_type"] = "license"
			req, _ := models.ParseDecisionRequest([]byte(decisionSysBody))
			_, err := g.srv.handlers.resolveDecision(context.Background(), http.Header{}, rc, req, "", false, "", nil)
			rr := httptest.NewRecorder()
			models.WriteErrorFromError(rr, err)
			return g, rr
		},
		"internal_key_without_provider_key":   posting(decisionGatewayOptions{}, keyInternal, decisionSysBody),
		"no_org_credential":                   posting(decisionGatewayOptions{}, keyByokNoCred, decisionSysBody),
		"provider_key_header_on_customer_key": posting(decisionGatewayOptions{}, keyByok, decisionSysBody, models.ProviderKeyHeader, callerOwnKey),
		"credential_saved_with_wrong_format": posting(decisionGatewayOptions{config: func(cfg *config.Config, u string) {
			cfg.Providers["typesafe-legacy"] = config.ProviderConfig{APIFormat: "openai", BaseURL: u, Models: []string{"jev-legacy"}}
		}, orgs: map[string]*tenant.OrgConfig{"org-a": {Providers: map[string]*tenant.ProviderConfig{
			"typesafe-legacy": {APIKey: "K", APIFormat: "openai", Models: []string{"jev-legacy"}, Enabled: true},
		}}}}, keyByok, strings.Replace(decisionSysBody, "typesafe/jev-latest", "jev-legacy", 1)),
		"body_format_not_the_providers": posting(decisionGatewayOptions{}, keyByok, strings.Replace(decisionOAIBody, "openai/gpt-6-luna", "typesafe/jev-latest", 1)),
		"body_over_the_cap": posting(decisionGatewayOptions{config: func(cfg *config.Config, _ string) {
			cfg.Server.MaxRequestBodySize = 64
		}}, keyByok, decisionSysBody),
	}
	for _, tc := range cases {
		t.Run(tc.Name, func(t *testing.T) {
			run, ok := scenarios[tc.Name]
			if !ok {
				t.Fatalf("no scenario reproduces the fixture case %q", tc.Name)
			}
			g, rr := run(t)
			expectError(t, rr, tc.Returned.Status, tc.Returned.Code)
			expectNoCalls(t, g)
		})
	}
}
