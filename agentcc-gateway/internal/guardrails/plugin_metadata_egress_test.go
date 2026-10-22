package guardrails_test

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/expression"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/webhook"
	"github.com/futureagi/agentcc-gateway/internal/mcp/egress"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A guardrail input carries no caller credential. The request's metadata
// holds the caller's Authorization header for the auth plugin; each check
// gets a copy without the key and without any entry whose name reads as a
// credential, as the request log's copy is built.

const (
	egressOrg       = "org-egress"
	egressKey       = "sk-agentcc-egress-0123456789abcdef0123"
	egressCallerTok = "planted-caller-session-token-8a9b"
)

// egressRC is a request as the guardrail plugin gets it: the auth plugin
// has run, the caller sent a dimension of its own that names a credential.
func egressRC(t *testing.T) *models.RequestContext {
	t.Helper()
	rc := models.AcquireRequestContext()
	t.Cleanup(rc.Release)
	rc.Model = "gpt-4o"
	rc.Request = &models.ChatCompletionRequest{
		Model:    "gpt-4o",
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"summarise this ticket"`)}},
	}
	for k, v := range map[string]string{
		"authorization":     "Bearer " + egressKey,
		"client_ip":         "203.0.113.7",
		"org_id":            egressOrg,
		"auth_key_id":       "key_egress",
		"auth_key_name":     "egress client",
		"auth_key_owner":    "owner@example.test",
		"key_org_id":        egressOrg,
		"key_access_groups": "default",
		"session_token":     egressCallerTok,
		"team":              "research",
	} {
		rc.Metadata[k] = v
	}
	return rc
}

// assertNoCredential: what a check got (or sent) holds neither the caller's
// gateway key nor the caller's credential-named dimension, and still holds
// the key's identity and the request's other dimensions.
func assertNoCredential(t *testing.T, where, got string) {
	t.Helper()
	if strings.Contains(got, egressKey) {
		t.Errorf("%s holds the caller's gateway key: %s", where, got)
	}
	if strings.Contains(got, egressCallerTok) {
		t.Errorf("%s holds the caller's session_token: %s", where, got)
	}
	for _, kept := range []string{"key_egress", "203.0.113.7", egressOrg, "research"} {
		if !strings.Contains(got, kept) {
			t.Errorf("%s lost %q, which is not a credential: %s", where, kept, got)
		}
	}
}

// egressRecorder is the org's webhook endpoint, on loopback: it keeps every
// body it receives and passes the check.
type egressRecorder struct {
	*httptest.Server
	mu     sync.Mutex
	bodies []string
}

func newEgressRecorder(t *testing.T) *egressRecorder {
	t.Helper()
	r := &egressRecorder{}
	r.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		b, _ := io.ReadAll(req.Body)
		r.mu.Lock()
		r.bodies = append(r.bodies, string(b))
		r.mu.Unlock()
		_, _ = w.Write([]byte(`{"pass":true,"score":0}`))
	}))
	t.Cleanup(r.Close)
	return r
}

func (r *egressRecorder) only(t *testing.T) string {
	t.Helper()
	r.mu.Lock()
	defer r.mu.Unlock()
	if len(r.bodies) != 1 {
		t.Fatalf("the webhook got %d requests, want 1", len(r.bodies))
	}
	return r.bodies[0]
}

// egressTestDialer lists loopback, so an org webhook check reaches the test
// recorder through the guarded dialer; a real deployment reaches only
// what the operator lists in mcp.egress.private_allow.
var egressTestDialer = func() *egress.Dialer {
	d, err := egress.New(egress.Options{PrivateAllow: []string{"127.0.0.0/8", "::1"}})
	if err != nil {
		panic(err)
	}
	return d
}()

// egressFactory builds an org check from its config, as cmd/agentcc's
// dynamic factory builds a webhook or an expression check: a webhook dials
// through the guarded egress dialer.
func egressFactory(name string, cfg map[string]interface{}) guardrails.Guardrail {
	if webhook.IsWebhookConfig(cfg) {
		return webhook.New(name, cfg, egressTestDialer)
	}
	if expression.IsExpressionConfig(cfg) {
		return expression.New(name, cfg)
	}
	return nil
}

func orgCheckStore(checks map[string]*tenant.GuardrailCheck) *tenant.Store {
	tenants := tenant.NewStore()
	tenants.Set(egressOrg, &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{Checks: checks}})
	return tenants
}

func TestOrgWebhookCheck_SendsTheEndpointNoCredential(t *testing.T) {
	rec := newEgressRecorder(t)
	tenants := orgCheckStore(map[string]*tenant.GuardrailCheck{
		"org-webhook": {Enabled: true, Action: "log", ConfidenceThreshold: 0.5,
			Config: map[string]interface{}{"url": rec.URL}},
	})
	plugin := guardrails.NewPlugin(nil, nil, egressFactory, nil, tenants)
	rc := egressRC(t)

	if res := plugin.ProcessRequest(context.Background(), rc); res.Action != pipeline.Continue {
		t.Fatalf("the request was refused: %+v", res)
	}

	assertNoCredential(t, "the webhook's request", rec.only(t))
	if rc.Metadata["authorization"] != "Bearer "+egressKey {
		t.Error("the request's own metadata lost its authorization, which the handlers read")
	}
}

func TestConfigFileWebhookRule_SendsTheEndpointNoCredential(t *testing.T) {
	rec := newEgressRecorder(t)
	const rule = "config-webhook"
	registry := map[string]guardrails.Guardrail{rule: webhook.NewConfigFileRule(rule, map[string]interface{}{"url": rec.URL})}
	engine := guardrails.NewEngine(config.GuardrailsConfig{Enabled: true, DefaultTimeout: 2 * time.Second,
		Rules: []config.GuardrailRuleConfig{{Name: rule, Stage: "pre", Mode: "sync", Action: "log", Threshold: 0.5}},
	}, registry)
	plugin := guardrails.NewPlugin(engine, registry, nil, policy.NewStore(), nil)
	rc := egressRC(t)

	if res := plugin.ProcessRequest(context.Background(), rc); res.Action != pipeline.Continue {
		t.Fatalf("the request was refused: %+v", res)
	}

	assertNoCredential(t, "the webhook's request", rec.only(t))
}

// An expression check cannot read the key, and still reads the rest.
func TestOrgExpressionCheck_CannotReadACredential(t *testing.T) {
	for _, tt := range []struct {
		name, expr string
		blocks     bool
	}{
		{"the gateway key", `metadata.authorization contains "sk-agentcc-"`, false},
		{"a credential-named dimension", `metadata.session_token == "` + egressCallerTok + `"`, false},
		{"the key's id", `metadata.auth_key_id == "key_egress"`, true},
		{"the client address", `metadata.client_ip == "203.0.113.7"`, true},
		{"a dimension", `metadata.team == "research"`, true},
	} {
		t.Run(tt.name, func(t *testing.T) {
			tenants := orgCheckStore(map[string]*tenant.GuardrailCheck{
				"org-expression": {Enabled: true, Action: "block", ConfidenceThreshold: 0.5,
					Config: map[string]interface{}{"expression": tt.expr}},
			})
			plugin := guardrails.NewPlugin(nil, nil, egressFactory, nil, tenants)

			res := plugin.ProcessRequest(context.Background(), egressRC(t))

			if blocked := res.Action != pipeline.Continue; blocked != tt.blocks {
				t.Errorf("%s: blocked = %v, want %v", tt.expr, blocked, tt.blocks)
			}
		})
	}
}

// recordingCheck keeps the metadata of every input it gets.
type recordingCheck struct {
	name  string
	stage guardrails.Stage
	mu    sync.Mutex
	seen  []string
}

func (c *recordingCheck) Name() string            { return c.name }
func (c *recordingCheck) Stage() guardrails.Stage { return c.stage }
func (c *recordingCheck) Check(_ context.Context, in *guardrails.CheckInput) *guardrails.CheckResult {
	b, _ := json.Marshal(in.Metadata)
	c.mu.Lock()
	c.seen = append(c.seen, string(b))
	c.mu.Unlock()
	return &guardrails.CheckResult{Pass: true}
}

func (c *recordingCheck) assertNoCredential(t *testing.T) {
	t.Helper()
	c.mu.Lock()
	defer c.mu.Unlock()
	if len(c.seen) == 0 {
		t.Fatalf("%s never ran", c.name)
	}
	for _, got := range c.seen {
		assertNoCredential(t, c.name+"'s input", got)
	}
}

// Every input any check gets, wherever the plugin builds it: the config
// file's rules before and after the provider, the org's checks before and
// after it, and the streaming checker's.
func TestEveryCheckInput_CarriesNoCredential(t *testing.T) {
	preRule := &recordingCheck{name: "pre-rule", stage: guardrails.StagePre}
	postRule := &recordingCheck{name: "post-rule", stage: guardrails.StagePost}
	orgPre := &recordingCheck{name: "org-pre", stage: guardrails.StagePre}
	orgPost := &recordingCheck{name: "org-post", stage: guardrails.StagePost}
	registry := map[string]guardrails.Guardrail{
		preRule.name: preRule, postRule.name: postRule, orgPre.name: orgPre, orgPost.name: orgPost,
	}
	cfg := config.GuardrailsConfig{Enabled: true, DefaultTimeout: 2 * time.Second, Rules: []config.GuardrailRuleConfig{
		{Name: preRule.name, Stage: "pre", Mode: "sync", Action: "log", Threshold: 0.5},
		{Name: postRule.name, Stage: "post", Mode: "sync", Action: "log", Threshold: 0.5},
	}}
	engine := guardrails.NewEngine(cfg, registry)
	tenants := orgCheckStore(map[string]*tenant.GuardrailCheck{
		orgPre.name:  {Enabled: true, Action: "log", ConfidenceThreshold: 0.5},
		orgPost.name: {Enabled: true, Action: "log", ConfidenceThreshold: 0.5},
	})
	plugin := guardrails.NewPlugin(engine, registry, nil, policy.NewStore(), tenants)
	rc := egressRC(t)

	if res := plugin.ProcessRequest(context.Background(), rc); res.Action != pipeline.Continue {
		t.Fatalf("the request was refused: %+v", res)
	}
	rc.Response = &models.ChatCompletionResponse{Choices: []models.Choice{
		{Message: models.Message{Role: "assistant", Content: json.RawMessage(`"done"`)}},
	}}
	if res := plugin.ProcessResponse(context.Background(), rc); res.Action != pipeline.Continue {
		t.Fatalf("the answer was refused: %+v", res)
	}
	afterResponse := len(postRule.seen)
	stream := guardrails.NewStreamChecker(engine, config.StreamingGuardrailConfig{Enabled: true, CheckInterval: 1},
		nil, policy.RequestPolicyNone, rc.Request, rc.Metadata)
	text := "streamed answer text"
	stream.ProcessChunk(context.Background(), models.StreamChunk{Choices: []models.StreamChoice{{Delta: models.Delta{Content: &text}}}})
	if len(postRule.seen) != afterResponse+1 {
		t.Fatalf("the streaming checker ran the post rule %d times, want 1", len(postRule.seen)-afterResponse)
	}

	for _, c := range []*recordingCheck{preRule, postRule, orgPre, orgPost} {
		c.assertNoCredential(t)
	}
	if rc.Metadata["authorization"] != "Bearer "+egressKey {
		t.Error("the request's own metadata lost its authorization, which the handlers read")
	}
}
