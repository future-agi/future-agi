package server

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/pii"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	budgetplugin "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	"github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type decisionGuardrailCapture struct {
	inputs []string
	needle string
}

func (c *decisionGuardrailCapture) Name() string            { return "decision-capture" }
func (c *decisionGuardrailCapture) Stage() guardrails.Stage { return guardrails.StagePre }
func (c *decisionGuardrailCapture) Check(_ context.Context, in *guardrails.CheckInput) *guardrails.CheckResult {
	text := ""
	if in.Request != nil && len(in.Request.Messages) != 0 {
		_ = json.Unmarshal(in.Request.Messages[0].Content, &text)
	}
	c.inputs = append(c.inputs, text)
	hit := strings.Contains(text, c.needle)
	if !hit {
		return &guardrails.CheckResult{Pass: true}
	}
	return &guardrails.CheckResult{Pass: false, Score: 1, Message: "blocked decision content"}
}
func decisionWithGuardrail(t *testing.T, action, needle string) (*decisionGateway, *decisionGuardrailCapture) {
	t.Helper()
	g := newDecisionGateway(t, decisionGatewayOptions{})
	c := &decisionGuardrailCapture{needle: needle}
	reg := map[string]guardrails.Guardrail{c.Name(): c}
	engine := guardrails.NewEngine(config.GuardrailsConfig{Rules: []config.GuardrailRuleConfig{{Name: c.Name(), Stage: "pre", Mode: "sync", Action: action}}}, reg)
	useDecisionGuardrails(g, engine, pipeline.NewEngine(authplugin.New(g.keyStore, true), guardrails.NewPlugin(engine, reg, nil, nil, nil), g.observer))
	return g, c
}

// useDecisionGuardrails runs the gateway with engine's rules, as the server
// wires them: the same guardrail engine in the pipeline's plugin and in the
// handlers, which read it to decide whether input guardrails apply.
func useDecisionGuardrails(g *decisionGateway, engine *guardrails.Engine, pipe *pipeline.Engine) {
	g.srv.handlers.engine = pipe
	g.srv.handlers.guardrailEngine = engine
}

func TestDecisionGuardrailBlocksState(t *testing.T) {
	g, c := decisionWithGuardrail(t, "block", "private-state")
	expectError(t, g.post(t, keyByok, decisionSysBody), http.StatusForbidden, "content_blocked")
	if len(c.inputs) != 1 || !strings.Contains(c.inputs[0], "private-state") {
		t.Fatalf("guardrail saw %q, want state", c.inputs)
	}
	expectNoCalls(t, g)
}

func TestDecisionGuardrailStringState(t *testing.T) {
	g, c := decisionWithGuardrail(t, "block", "string-state-marker")
	body := `{"model":"typesafe/jev-latest","state":"string-state-marker","questions":{"one":{"type":"noul"}}}`
	expectError(t, g.post(t, keyByok, body), http.StatusForbidden, "content_blocked")
	if len(c.inputs) != 1 || !strings.HasPrefix(c.inputs[0], "string-state-marker") {
		t.Fatalf("guardrail saw %q, want unquoted state", c.inputs)
	}
	expectNoCalls(t, g)
}

func TestDecisionGuardrailMonitorAndQuestions(t *testing.T) {
	g, c := decisionWithGuardrail(t, "log", "criterion-marker")
	body := `{"model":"typesafe/jev-latest","state":{"value":"state-marker"},"questions":{"one":{"type":"noul","instructions":"instruction-marker","criteria":[{"text":"criterion-marker"}]}}}`
	if rr := g.post(t, keyByok, body); rr.Code != http.StatusOK {
		t.Fatalf("monitor status %d: %s", rr.Code, rr.Body.String())
	}
	if len(c.inputs) != 1 {
		t.Fatalf("guardrail checks = %d, want 1", len(c.inputs))
	}
	// Object state is read as decoded keys and values, not compact JSON.
	for _, want := range []string{"value\nstate-marker", "instruction-marker", "criterion-marker"} {
		if !strings.Contains(c.inputs[0], want) {
			t.Errorf("guardrail missed %q: %q", want, c.inputs[0])
		}
	}
	if !strings.Contains(g.observer.dump(), "guardrail_action:logged") {
		t.Error("monitor result was not recorded")
	}
	expectOnlyKey(t, g, orgTypeSafeKey, 1)
}

func TestDecisionGuardrailOpenAIInputAndQuestion(t *testing.T) {
	body := strings.Replace(decisionOAIBody, `"instructions":"private-instructions"`, `"question":"question-marker","instructions":"private-instructions"`, 1)
	for _, needle := range []string{"private-input", "question-marker", "private-instructions"} {
		t.Run(needle, func(t *testing.T) {
			g, c := decisionWithGuardrail(t, "block", needle)
			expectError(t, g.post(t, keyByok, body), http.StatusForbidden, "content_blocked")
			if len(c.inputs) != 1 || !strings.Contains(c.inputs[0], needle) {
				t.Fatalf("guardrail saw %q, want %q", c.inputs, needle)
			}
			expectNoCalls(t, g)
		})
	}
}

func TestDecisionGuardrailRedactionCannotRewriteBody(t *testing.T) {
	for _, remediation := range []string{"mask", "redact"} {
		for _, action := range []string{"log", "block"} {
			t.Run(remediation+"/"+action, func(t *testing.T) {
				g := newDecisionGateway(t, decisionGatewayOptions{})
				piiRule := pii.New(map[string]interface{}{"remediation": remediation})
				reg := map[string]guardrails.Guardrail{piiRule.Name(): piiRule}
				engine := guardrails.NewEngine(config.GuardrailsConfig{Rules: []config.GuardrailRuleConfig{{Name: piiRule.Name(), Stage: "pre", Mode: "sync", Action: action}}}, reg)
				useDecisionGuardrails(g, engine, pipeline.NewEngine(authplugin.New(g.keyStore, true), guardrails.NewPlugin(engine, reg, nil, nil, nil)))
				body := strings.Replace(decisionSysBody, "private-state", "person@example.test", 1)
				err := expectError(t, g.post(t, keyByok, body), http.StatusForbidden, "content_blocked")
				if !strings.Contains(err.Message, "redaction is not applied to decisions") {
					t.Fatalf("error did not explain refusal: %q", err.Message)
				}
				expectNoCalls(t, g)
			})
		}
	}
}

func TestDecisionPerKeyRPM(t *testing.T) {
	g := newDecisionGateway(t, decisionGatewayOptions{config: func(cfg *config.Config, _ string) {
		cfg.Auth.Keys[0].RateLimitRPM = 1
	}})
	if rr := g.post(t, keyByok, decisionSysBody); rr.Code != http.StatusOK {
		t.Fatalf("first: %d %s", rr.Code, rr.Body.String())
	}
	expectError(t, g.post(t, keyByok, decisionSysBody), http.StatusTooManyRequests, "rate_limit_exceeded")
	expectOnlyKey(t, g, orgTypeSafeKey, 1)
}

func TestDecisionOrgBudgetStopsBeforeUpstream(t *testing.T) {
	org := defaultOrgA()
	org.Budgets = &tenant.BudgetsConfig{Enabled: true, OrgLimit: 0.001, HardLimit: true}
	org.CostTracking = &tenant.CostTrackingConfig{Enabled: true, CustomPricing: map[string]*tenant.CustomPricing{
		"typesafe/jev-latest": {InputPerMTok: 10},
	}}
	g := newDecisionGateway(t, decisionGatewayOptions{orgs: map[string]*tenant.OrgConfig{"org-a": org}})
	g.srv.handlers.engine = pipeline.NewEngine(
		authplugin.New(g.keyStore, true),
		budgetplugin.New(nil, nil, false, g.tenants),
		cost.New(true, func() *modeldb.ModelDB { return g.srv.handlers.ModelDB() }, g.tenants, nil),
	)
	if rr := g.post(t, keyByok, decisionSysBody); rr.Code != http.StatusOK {
		t.Fatalf("first: %d %s", rr.Code, rr.Body.String())
	}
	expectError(t, g.post(t, keyByok, decisionSysBody), http.StatusTooManyRequests, "org_budget_exceeded")
	expectOnlyKey(t, g, orgTypeSafeKey, 1)
}

// The guardrail text is the same for the same body: questions and named
// criteria are read in key order, not Go's random map order.
func TestDecisionGuardrailContentIsDeterministic(t *testing.T) {
	body := `{"model":"typesafe/jev-latest","state":"s","questions":{"q3":{"type":"noul","instructions":"three"},"q1":{"type":"choice","instructions":"one","criteria":{"b":"bee","a":"ay","c":"see"}},"q2":{"type":"noul","instructions":"two"}}}`
	content := func() string {
		t.Helper()
		got, apiErr := decisionGuardrailContent([]byte(body), 0)
		if apiErr != nil {
			t.Fatal(apiErr)
		}
		return string(got)
	}
	// Fields are read in body order, at every level.
	want := content()
	if want != `"s\nthree\none\nb\nbee\na\nay\nc\nsee\ntwo"` {
		t.Fatalf("content = %s", want)
	}
	for i := 0; i < 50; i++ {
		if got := content(); got != want {
			t.Fatalf("run %d: %s, want %s", i, got, want)
		}
	}
}

// Criteria named by their keys ({"<criterion text>": ...}) are checked too.
func TestDecisionGuardrailSeesCriteriaKeys(t *testing.T) {
	g, c := decisionWithGuardrail(t, "block", "criteria-key-marker")
	body := `{"model":"typesafe/jev-latest","state":"safe","questions":{"one":{"type":"choice","criteria":{"criteria-key-marker":null,"other":"fine"}}}}`
	expectError(t, g.post(t, keyByok, body), http.StatusForbidden, "content_blocked")
	if len(c.inputs) != 1 || !strings.Contains(c.inputs[0], "criteria-key-marker") {
		t.Fatalf("guardrail saw %q", c.inputs)
	}
	expectNoCalls(t, g)
}
