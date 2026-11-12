package server

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/pii"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/plugins/agentbudget"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	capbudget "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/plugins/spendguard"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The decision routes, POST /v1/systemone and POST /v1/decisions, send the
// caller's body to a decision model byte for byte: System One's state, OpenAI
// Decisions' input and each question's instructions are text the model reads.
// The checks on the request read that text, as they read a chat request's
// messages: a check in block mode refuses a decision whose text it blocks with
// its own refusal, lets one whose text it passes go on, and a check's rewrite
// (pii-detection's mask) reaches the vendor in the decision it is sent. These
// tests send decisions while pii-detection runs, as the org's check and as a
// rule in the config file at the pre stage, and look at what the vendor gets.
// The control sends the same decisions with no check on the request: the
// vendor answers them, so a refusal under a check is the check's.

const (
	decisionCheckCallerKey = "CALLER-OWN-DECISION-KEY"
	decisionCheckAnswer    = `{"model":"jev-1.13.0","answers":{"resolved":{"type":"noul","noul":0.9}},"usage":{"input_tokens":10,"output_tokens":1}}`
)

// decisionCheckSurface is one place a decision carries text a model reads.
type decisionCheckSurface struct {
	name string
	path string
	body func(text string) string
}

func decisionCheckBody(format string) func(string) string {
	return func(text string) string {
		quoted, _ := json.Marshal(text)
		return fmt.Sprintf(format, quoted)
	}
}

var decisionCheckSurfaces = []decisionCheckSurface{
	{"System One, the state", "/v1/systemone", decisionCheckBody(`{"model":"typesafe/jev-latest","state":{"ticket":%s},"questions":{"resolved":{"type":"noul","instructions":"Is the ticket resolved?"}}}`)},
	{"System One, a question's instructions", "/v1/systemone", decisionCheckBody(`{"model":"typesafe/jev-latest","state":{"ticket":"refund sent"},"questions":{"resolved":{"type":"noul","instructions":%s}}}`)},
	{"System One body on /v1/decisions", "/v1/decisions", decisionCheckBody(`{"model":"typesafe/jev-latest","state":{"ticket":%s},"questions":{"resolved":{"type":"noul","instructions":"Is the ticket resolved?"}}}`)},
	{"OpenAI Decisions, the input", "/v1/decisions", decisionCheckBody(`{"model":"openai/gpt-6-luna","input":%s,"questions":[{"type":"predicate","name":"resolved","instructions":"Is the ticket resolved?"}]}`)},
	{"OpenAI Decisions, a question's instructions", "/v1/decisions", decisionCheckBody(`{"model":"openai/gpt-6-luna","input":"refund sent","questions":[{"type":"predicate","name":"resolved","instructions":%s}]}`)},
}

// decisionVendorCalls records the decision calls the loopback vendor got.
type decisionVendorCalls struct {
	mu     sync.Mutex
	bodies []string
}

func (c *decisionVendorCalls) count() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return len(c.bodies)
}

// newDecisionCheckGateway is newPostBlockGateway with a TypeSafe provider in
// the config file on the loopback upstream, which answers every System One
// and OpenAI Decisions call and records it. Requests go with the org's
// internal key and the caller's own provider key, so the decision goes to
// the config file's base URL for the vendor (internalDecisionTarget).
func newDecisionCheckGateway(t *testing.T, setup postBlockSetup) (*postBlockGateway, *decisionVendorCalls) {
	t.Helper()
	calls := &decisionVendorCalls{}
	setup.upstream = func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || !(strings.HasSuffix(r.URL.Path, "/systemone") || strings.HasSuffix(r.URL.Path, "/decisions")) {
			return
		}
		body, _ := io.ReadAll(r.Body)
		calls.mu.Lock()
		calls.bodies = append(calls.bodies, string(body))
		calls.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, decisionCheckAnswer)
	}
	setup.extraProviders = map[string]config.ProviderConfig{
		"typesafe": {APIFormat: "systemone", APIKey: "gateway-held-key", Models: []string{"jev-latest"}},
	}
	g := newPostBlockGateway(t, setup)
	// Every decision in these tests goes to the loopback upstream, never a
	// vendor's default host.
	for _, dialect := range []string{"typesafe", "openai"} {
		if base := g.srv.handlers.decisionBaseURLs[dialect]; !strings.HasPrefix(base, "http://127.0.0.1:") {
			t.Fatalf("decision base URL for %s is %q, want the loopback upstream", dialect, base)
		}
	}
	return g, calls
}

func sendDecision(t *testing.T, g *postBlockGateway, sf decisionCheckSurface, text string) (int, string) {
	t.Helper()
	return g.doAs(t, postBlockInternalOrgKey, sf.path, sf.body(text), map[string]string{models.ProviderKeyHeader: decisionCheckCallerKey})
}

// With no check on the request, every decision reaches the vendor and is
// answered.
func TestDecisionRequestCheck_ServedWithNoCheck(t *testing.T) {
	g, calls := newDecisionCheckGateway(t, postBlockSetup{})
	for i, sf := range decisionCheckSurfaces {
		code, body := sendDecision(t, g, sf, "My SSN is "+requestCheckSSN+".")
		if code != http.StatusOK {
			t.Fatalf("%s: status %d, want 200: %s", sf.name, code, body)
		}
		if n := calls.count(); n != i+1 {
			t.Fatalf("%s: the vendor got %d calls, want %d", sf.name, n, i+1)
		}
	}
}

// A check on the request in block mode keeps a decision whose text it blocks
// from the vendor: the caller gets the check's 403, which names it and does
// not echo the text, and the vendor gets nothing.
func TestDecisionRequestCheck_BlockedTextNeverReachesTheVendor(t *testing.T) {
	for _, shape := range requestCheckShapes {
		t.Run(shape.name, func(t *testing.T) {
			g, calls := newDecisionCheckGateway(t, shape.setup)
			// The check is in force for this key: it blocks the same text in
			// a chat request.
			chat, _ := json.Marshal("My SSN is " + requestCheckSSN + ".")
			if code, body := g.doAs(t, postBlockInternalOrgKey, "/v1/chat/completions",
				`{"model":"gpt-4o","messages":[{"role":"user","content":`+string(chat)+`}]}`, nil); code != http.StatusForbidden {
				t.Fatalf("chat with the same text: status %d, want 403 from %s: %s", code, requestCheck, body)
			}
			for _, sf := range decisionCheckSurfaces {
				t.Run(sf.name, func(t *testing.T) {
					before := calls.count()
					code, body := sendDecision(t, g, sf, "My SSN is "+requestCheckSSN+".")
					if n := calls.count() - before; n != 0 {
						t.Errorf("the vendor got the decision %d time(s); status %d", n, code)
					}
					if code != http.StatusForbidden {
						t.Fatalf("status %d, want 403 from %s: %s", code, requestCheck, body)
					}
					var resp models.ErrorResponse
					if err := json.Unmarshal([]byte(body), &resp); err != nil {
						t.Fatalf("body %q is not an error envelope: %v", body, err)
					}
					if resp.Error.Type != models.ErrTypeGuardrail || !strings.Contains(resp.Error.Message, requestCheck) {
						t.Errorf("error %s/%s %q, want a guardrail refusal naming %s", resp.Error.Type, resp.Error.Code, resp.Error.Message, requestCheck)
					}
					// The check read the text and blocked it: its own refusal,
					// not a refusal of a request it cannot read.
					if resp.Error.Code != "content_blocked" {
						t.Errorf("error code %s (%q), want content_blocked: the check reads the decision", resp.Error.Code, resp.Error.Message)
					}
					if strings.Contains(body, requestCheckSSN) {
						t.Errorf("the refusal echoes the request's text: %s", body)
					}
				})
			}
		})
	}
}

// A check in block mode lets a decision whose text it passes go on: it read
// the text, so the vendor answers the decision as it would with no check.
func TestDecisionRequestCheck_TextTheCheckPassesIsServed(t *testing.T) {
	for _, shape := range requestCheckShapes {
		t.Run(shape.name, func(t *testing.T) {
			g, calls := newDecisionCheckGateway(t, shape.setup)
			for _, sf := range decisionCheckSurfaces {
				t.Run(sf.name, func(t *testing.T) {
					before := calls.count()
					code, body := sendDecision(t, g, sf, "The ticket says the refund was sent.")
					if code != http.StatusOK {
						t.Fatalf("status %d, want 200: %s", code, body)
					}
					if n := calls.count() - before; n != 1 {
						t.Errorf("the vendor got the decision %d time(s), want 1", n)
					}
				})
			}
		})
	}
}

// decisionWithout is a decision body decoded, without its model (the vendor
// gets the model it is sent as), for comparing what the vendor got with what
// it should get.
func decisionWithout(t *testing.T, body string) map[string]any {
	t.Helper()
	var v map[string]any
	if err := json.Unmarshal([]byte(body), &v); err != nil {
		t.Fatalf("%q is not a JSON object: %v", body, err)
	}
	delete(v, "model")
	return v
}

// pii-detection's mask (a remediation, in a check's own config or as its
// action) reaches the vendor: the decision it gets is the caller's, with the
// social security number masked wherever it was (the state, the input or a
// question's instructions) and every other value as sent.
func TestDecisionRequestCheck_MaskReachesTheVendor(t *testing.T) {
	for _, shape := range remediationShapes("mask") {
		t.Run(shape.name, func(t *testing.T) {
			g, calls := newDecisionCheckGateway(t, shape.setup)
			for _, sf := range decisionCheckSurfaces {
				t.Run(sf.name, func(t *testing.T) {
					before := calls.count()
					code, body := sendDecision(t, g, sf, requestCheckPrompt)
					if code != http.StatusOK {
						t.Fatalf("status %d, want 200 with the masked decision sent: %s", code, body)
					}
					if n := calls.count() - before; n != 1 {
						t.Fatalf("the vendor got the decision %d time(s), want 1", n)
					}
					calls.mu.Lock()
					sent := calls.bodies[len(calls.bodies)-1]
					calls.mu.Unlock()
					if strings.Contains(sent, requestCheckSSN) || strings.Contains(decodedJSON(t, sent), requestCheckSSN) {
						t.Errorf("the vendor got the social security number: %s", sent)
					}
					if n := strings.Count(sent, "***"); n != 1 {
						t.Errorf("the vendor got the mask %d times, want 1: %s", n, sent)
					}
					want := decisionWithout(t, sf.body(strings.ReplaceAll(requestCheckPrompt, requestCheckSSN, "***")))
					if got := decisionWithout(t, sent); !reflect.DeepEqual(got, want) {
						t.Errorf("the vendor got %v, want the caller's decision with only the number masked: %v", got, want)
					}
					// The model is aliased: the caller names it with its vendor
					// (typesafe/jev-latest, openai/gpt-6-luna) and the vendor is
					// sent its own name. The masked decision still goes out:
					// the checks read the body as it is sent, so the rewrite
					// keeps the model the vendor is sent.
					var caller, vendor struct {
						Model string `json:"model"`
					}
					_ = json.Unmarshal([]byte(sf.body("")), &caller)
					_ = json.Unmarshal([]byte(sent), &vendor)
					if vendor.Model == "" || caller.Model != strings.SplitN(caller.Model, "/", 2)[0]+"/"+vendor.Model {
						t.Errorf("the vendor got model %q for the caller's %q, want the caller's model without its vendor prefix", vendor.Model, caller.Model)
					}
				})
			}
		})
	}
}

// decisionLabelSurfaces are a decision's labels, which the vendor's model
// reads too: a System One question's id (its key), an OpenAI Decisions
// question's name, and a question's type.
var decisionLabelSurfaces = []decisionCheckSurface{
	{"System One, a question's id", "/v1/systemone", decisionCheckBody(`{"model":"typesafe/jev-latest","state":{"ticket":"refund sent"},"questions":{%s:{"type":"noul","instructions":"Is the ticket resolved?"}}}`)},
	{"System One, a question's type", "/v1/systemone", decisionCheckBody(`{"model":"typesafe/jev-latest","state":{"ticket":"refund sent"},"questions":{"resolved":{"type":%s,"instructions":"Is the ticket resolved?"}}}`)},
	{"OpenAI Decisions, a question's name", "/v1/decisions", decisionCheckBody(`{"model":"openai/gpt-6-luna","input":"refund sent","questions":[{"type":"predicate","name":%s,"instructions":"Is the ticket resolved?"}]}`)},
	{"OpenAI Decisions, a question's type", "/v1/decisions", decisionCheckBody(`{"model":"openai/gpt-6-luna","input":"refund sent","questions":[{"type":%s,"name":"resolved","instructions":"Is the ticket resolved?"}]}`)},
}

// A check reads a decision's labels as it reads its other text: one in block
// mode refuses a decision whose question id, name or type carries text it
// blocks, with its own refusal, and the vendor gets nothing.
func TestDecisionRequestCheck_LabelsAreRead(t *testing.T) {
	for _, shape := range requestCheckShapes {
		t.Run(shape.name, func(t *testing.T) {
			g, calls := newDecisionCheckGateway(t, shape.setup)
			for _, sf := range decisionLabelSurfaces {
				t.Run(sf.name, func(t *testing.T) {
					before := calls.count()
					code, body := sendDecision(t, g, sf, "My SSN is "+requestCheckSSN+".")
					if n := calls.count() - before; n != 0 {
						t.Errorf("the vendor got the decision %d time(s); status %d", n, code)
					}
					var resp models.ErrorResponse
					_ = json.Unmarshal([]byte(body), &resp)
					if code != http.StatusForbidden || resp.Error.Code != "content_blocked" || !strings.Contains(resp.Error.Message, requestCheck) {
						t.Errorf("status %d %s/%s %q, want 403 content_blocked naming %s", code, resp.Error.Type, resp.Error.Code, resp.Error.Message, requestCheck)
					}
					if strings.Contains(body, requestCheckSSN) {
						t.Errorf("the refusal echoes the request's text: %s", body)
					}
				})
			}
		})
	}
}

// A check's rewrite of a label (pii-detection's mask of a question's id,
// name or type) cannot be sent: the gateway sends labels as they are. The
// decision is refused before the vendor (remediation_unavailable), saying in
// plain words what to change, and the refusal does not echo the text.
func TestDecisionRequestCheck_MaskOfALabelIsRefused(t *testing.T) {
	for _, shape := range remediationShapes("mask") {
		t.Run(shape.name, func(t *testing.T) {
			g, calls := newDecisionCheckGateway(t, shape.setup)
			for _, sf := range decisionLabelSurfaces {
				t.Run(sf.name, func(t *testing.T) {
					before := calls.count()
					code, body := sendDecision(t, g, sf, requestCheckPrompt)
					if n := calls.count() - before; n != 0 {
						t.Errorf("the vendor got the decision %d time(s); status %d", n, code)
					}
					var resp models.ErrorResponse
					_ = json.Unmarshal([]byte(body), &resp)
					if code != http.StatusForbidden || resp.Error.Type != models.ErrTypeGuardrail || resp.Error.Code != "remediation_unavailable" {
						t.Fatalf("status %d %s/%s %q, want 403 remediation_unavailable", code, resp.Error.Type, resp.Error.Code, resp.Error.Message)
					}
					for _, want := range []string{requestCheck, "a question's", "take that text out of it"} {
						if !strings.Contains(resp.Error.Message, want) {
							t.Errorf("message %q, want it to name %q", resp.Error.Message, want)
						}
					}
					if strings.Contains(body, requestCheckSSN) {
						t.Errorf("the refusal echoes the request's text: %s", body)
					}
				})
			}
		})
	}
}

// A decision that repeats a key in the same object, at any depth, is refused
// 400 before the vendor, with or without a check: parsers differ on which
// occurrence they keep, so the vendor could act on a value no check read.
// The refusal does not echo the body. The same key in different objects is
// not a repeat.
func TestDecisionRequestCheck_RepeatedKeysAreRefused(t *testing.T) {
	g, calls := newDecisionCheckGateway(t, postBlockSetup{})
	secret := "My SSN is " + requestCheckSSN + "."
	q := strconv.Quote(secret)
	for _, tc := range []struct {
		name, path, body string
		want             int
	}{
		{"a top-level key", "/v1/systemone", `{"model":"typesafe/jev-latest","state":{"ticket":"refund sent"},"state":{"ticket":` + q + `},"questions":{"resolved":{"type":"noul","instructions":"Is it resolved?"}}}`, http.StatusBadRequest},
		{"a key in the state", "/v1/systemone", `{"model":"typesafe/jev-latest","state":{"ticket":"refund sent","ticket":` + q + `},"questions":{"resolved":{"type":"noul","instructions":"Is it resolved?"}}}`, http.StatusBadRequest},
		{"a key in a question", "/v1/decisions", `{"model":"typesafe/jev-latest","state":{"ticket":"refund sent"},"questions":{"resolved":{"type":"noul","instructions":"Is it resolved?","instructions":` + q + `}}}`, http.StatusBadRequest},
		{"a key in an object in a list", "/v1/systemone", `{"model":"typesafe/jev-latest","state":{"items":[{"note":"ok"},{"note":"ok","note":` + q + `}]},"questions":{"resolved":{"type":"noul"}}}`, http.StatusBadRequest},
		{"OpenAI Decisions: the input", "/v1/decisions", `{"model":"openai/gpt-6-luna","input":"refund sent","input":` + q + `,"questions":[{"type":"predicate","name":"resolved","instructions":"Is it resolved?"}]}`, http.StatusBadRequest},
		{"OpenAI Decisions: a key in a question", "/v1/decisions", `{"model":"openai/gpt-6-luna","input":"refund sent","questions":[{"type":"predicate","name":"resolved","name":` + q + `,"instructions":"Is it resolved?"}]}`, http.StatusBadRequest},
		{"the same key in different objects (served)", "/v1/systemone", `{"model":"typesafe/jev-latest","state":{"a":{"note":"x"},"b":{"note":"y"},"l":[{"note":"x"},{"note":"y"}]},"questions":{"resolved":{"type":"noul","note":"x"},"late":{"type":"noul","note":"y"}}}`, http.StatusOK},
	} {
		t.Run(tc.name, func(t *testing.T) {
			before := calls.count()
			code, body := g.doAs(t, postBlockInternalOrgKey, tc.path, tc.body, map[string]string{models.ProviderKeyHeader: decisionCheckCallerKey})
			if code != tc.want {
				t.Fatalf("status %d, want %d: %s", code, tc.want, body)
			}
			if tc.want == http.StatusOK {
				return
			}
			if n := calls.count() - before; n != 0 {
				t.Errorf("the vendor got the decision %d time(s)", n)
			}
			var resp models.ErrorResponse
			_ = json.Unmarshal([]byte(body), &resp)
			if resp.Error.Code != "duplicate_field" || !strings.Contains(resp.Error.Message, "Send each key once") {
				t.Errorf("error %s %q, want duplicate_field saying to send each key once", resp.Error.Code, resp.Error.Message)
			}
			if strings.Contains(body, requestCheckSSN) {
				t.Errorf("the refusal echoes the request's text: %s", body)
			}
		})
	}
}

// X-Guardrail-Policy applies to a decision as on chat: a key without
// allow_policy_override that sends it is refused before the vendor, and a
// permitted key's "disabled" turns the config file's rules off, so a decision
// the rule blocks goes on. The keys are the org's own (System One goes to the
// org's TypeSafe provider, on the loopback upstream).
func TestDecisionRequestCheck_PolicyHeaderAppliesAsOnChat(t *testing.T) {
	g, calls := newDecisionCheckGateway(t, postBlockSetup{requestRule: requestCheck})
	header := map[string]string{"X-Guardrail-Policy": "disabled"}
	for _, sf := range decisionCheckSurfaces {
		if !strings.Contains(sf.body(""), `"questions":{`) {
			continue // OpenAI Decisions: the org's OpenAI provider does not serve the model
		}
		t.Run(sf.name, func(t *testing.T) {
			before := calls.count()
			code, body := g.doAs(t, postBlockKey, sf.path, sf.body(requestCheckPrompt), header)
			if code != http.StatusForbidden || !strings.Contains(body, "does not allow guardrail policy overrides") {
				t.Errorf("a key without the permission: status %d, want 403 saying the key does not allow overrides: %s", code, body)
			}
			if n := calls.count() - before; n != 0 {
				t.Errorf("a key without the permission: the vendor got %d call(s)", n)
			}
			before = calls.count()
			code, body = g.doAs(t, postBlockOverrideKey, sf.path, sf.body(requestCheckPrompt), header)
			if code != http.StatusOK {
				t.Errorf("a permitted key's disabled: status %d, want 200: %s", code, body)
			}
			if n := calls.count() - before; n != 1 {
				t.Errorf("a permitted key's disabled: the vendor got %d call(s), want 1", n)
			}
		})
	}
}

// A decision a check refuses is charged nothing and leaves nothing held. The
// plugins that run before the check on the request (priority 34) are the IP
// rules, auth, the key's scope, RBAC and the request quota, and none holds
// money; every plugin that holds or charges (the budget's caps and spend at
// 40, the spend guard at 505, the run budget at 509, credits at 510) runs
// after it, and the decision handler holds nothing before the pipeline. These
// tests put each in force, with its state in Redis and the org's credits on
// an in-memory ledger, refuse a decision on both routes with pii-detection
// in block mode, and check that nothing changed: no budget spend, cap or run
// hold, spend-guard window or ledger entry, the org's and a managed key's
// balance as they were, and no cost on the request. The control serves the
// same decisions with the check off, so the same state records a served
// decision's spend.

// decisionChargeSeen is what the plugins after the provider left on a
// decision's request context.
type decisionChargeSeen struct {
	endpoint, cost, unavailable string
	reserved                    bool
	billed                      int64
}

// decisionChargeObserver records, after every billing plugin's post stage,
// what each decision call left on its request context.
type decisionChargeObserver struct {
	mu   sync.Mutex
	seen []decisionChargeSeen
}

func (*decisionChargeObserver) Name() string  { return "decision-charge-observer" }
func (*decisionChargeObserver) Priority() int { return 950 }
func (*decisionChargeObserver) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
func (o *decisionChargeObserver) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	o.mu.Lock()
	defer o.mu.Unlock()
	o.seen = append(o.seen, decisionChargeSeen{endpoint: rc.EndpointType, cost: rc.Metadata["cost"],
		unavailable: rc.Metadata["request_unavailable"], reserved: rc.Billing.Reserved, billed: rc.Billing.BilledMicros})
	return pipeline.ResultContinue()
}

func (o *decisionChargeObserver) count() int {
	o.mu.Lock()
	defer o.mu.Unlock()
	return len(o.seen)
}

func (o *decisionChargeObserver) last(t *testing.T) decisionChargeSeen {
	t.Helper()
	o.mu.Lock()
	defer o.mu.Unlock()
	if len(o.seen) == 0 {
		t.Fatal("no post stage ran for the decision")
	}
	return o.seen[len(o.seen)-1]
}

// decisionChargeGateway is newDecisionGateway with the production billing
// plugins in its pipeline, in priority order, over Redis under prefix.
type decisionChargeGateway struct {
	*decisionGateway
	client  *redisstate.Client
	prefix  string
	ledger  *creditsplugin.OrgLedger
	runs    *redisstate.AgentBudgetStore
	seen    *decisionChargeObserver
	keyIDs  []string
	managed func() float64
}

func newDecisionChargeGateway(t *testing.T, client *redisstate.Client, prefix string) *decisionChargeGateway {
	t.Helper()
	g := newDecisionGateway(t, decisionGatewayOptions{})
	h := g.srv.handlers
	db := modeldb.New(modeldb.BundledModels, nil)
	checks := map[string]guardrails.Guardrail{requestCheck: pii.New(nil)}
	grEngine := guardrails.NewEngine(config.GuardrailsConfig{Enabled: true, DefaultTimeout: 2 * time.Second}, checks)
	budget := capbudget.New(nil, nil, true, g.tenants)
	budget.SetRedisBudget(redisstate.NewBudgetStore(client, prefix))
	budget.SetCapStore(redisstate.NewCapHoldStore(client, prefix))
	guard := spendguard.NewGuard(redisstate.NewSpendGuardStore(client, prefix),
		config.SpendGuardConfig{MaxUSDPerMinute: 100, MaxUSDPerHour: 1000, NewOrgDailyCapUSD: 25})
	runs := redisstate.NewAgentBudgetStore(client, prefix)
	run := agentbudget.New(agentbudget.Deps{
		Store:    runs,
		Settings: agentbudget.Settings{HoldTTL: time.Minute, ReserveDefaultOutputTokens: 4096},
		Prices: func(providerID, model, endpointID string) (models.PriceCard, bool) {
			return h.Deps().Prices(providerID, model, endpointID)
		},
		OrgConfig: g.tenants.Get,
	})
	ledger := creditsplugin.NewOrgLedger(nil)
	credits := creditsplugin.New(true, g.keyStore)
	credits.SetOrgLedger(ledger, false)
	seen := &decisionChargeObserver{}
	*g.srv.engine = *pipeline.NewEngine(
		authplugin.New(g.keyStore, true),
		guardrails.NewPlugin(grEngine, checks, piiFromOrgConfig, policy.NewStore(), g.tenants),
		budget,
		costplugin.New(true, func() *modeldb.ModelDB { return db }, g.tenants, nil),
		spendguard.New(guard, func(*models.RequestContext) int64 { return 1000 }),
		run,
		credits,
		seen,
	)
	if h.engine.PluginCount() != 8 {
		t.Fatalf("the handlers run %d plugins, want the 8 of this pipeline", h.engine.PluginCount())
	}
	h.SetRunBudget(run)
	t.Cleanup(func() { h.SetRunBudget(nil) })
	if _, err := ledger.Grant(context.Background(), "org-a", billing.GrantPush{GrantedTotalMicros: 1_000_000, Version: 1}); err != nil {
		t.Fatal(err)
	}
	cg := &decisionChargeGateway{decisionGateway: g, client: client, prefix: prefix, ledger: ledger, runs: runs, seen: seen}
	for _, raw := range []string{keyByok, keyManaged} {
		k := g.keyStore.Authenticate(raw)
		if k == nil {
			t.Fatalf("key %s is not in the key store", raw)
		}
		cg.keyIDs = append(cg.keyIDs, k.ID)
		if k.KeyType == "managed" {
			cg.managed = k.BalanceUSD
		}
	}
	return cg
}

// org is org-a (its own TypeSafe and OpenAI keys) with an org budget, the
// org's admission caps on both keys when caps, and pii-detection in block
// mode on the request when check.
func (cg *decisionChargeGateway) org(check, caps bool) *tenant.OrgConfig {
	org := defaultOrgA()
	org.Budgets = &tenant.BudgetsConfig{Enabled: true, OrgLimit: 1000}
	if caps {
		limit := 1.0
		org.Meta = &tenant.ConfigMeta{Version: 1}
		org.Budgets.AdmissionCapsEnabled = true
		org.Budgets.PolicyRevision = 1
		org.Budgets.NoMaxTokens = "clamp"
		org.Budgets.KeyIDs = map[string]*tenant.AdmissionCapConfig{}
		for _, id := range cg.keyIDs {
			org.Budgets.KeyIDs[id] = &tenant.AdmissionCapConfig{Limit: &limit, ActivatedAt: "2026-10-27T00:00:00Z"}
		}
	}
	if check {
		org.Guardrails = &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{
			requestCheck: {Enabled: true, Action: "block", ConfidenceThreshold: 0.5},
		}}
	}
	return org
}

// redisState is every key under the gateway's prefix and its value (DUMP).
func (cg *decisionChargeGateway) redisState(t *testing.T) map[string]string {
	t.Helper()
	ctx := context.Background()
	state := map[string]string{}
	it := cg.client.Redis().Scan(ctx, 0, cg.prefix+"*", 1000).Iterator()
	for it.Next(ctx) {
		v, err := cg.client.Redis().Dump(ctx, it.Val()).Result()
		if err != nil {
			v = "error: " + err.Error()
		}
		state[it.Val()] = v
	}
	if err := it.Err(); err != nil {
		t.Fatal(err)
	}
	return state
}

// redisChanges names the keys added, removed or changed from before to after.
func redisChanges(prefix string, before, after map[string]string) []string {
	var changes []string
	for k, v := range after {
		if old, ok := before[k]; !ok {
			changes = append(changes, "added "+strings.TrimPrefix(k, prefix))
		} else if old != v {
			changes = append(changes, "changed "+strings.TrimPrefix(k, prefix))
		}
	}
	for k := range before {
		if _, ok := after[k]; !ok {
			changes = append(changes, "removed "+strings.TrimPrefix(k, prefix))
		}
	}
	sort.Strings(changes)
	return changes
}

func (cg *decisionChargeGateway) account(t *testing.T) billing.Account {
	t.Helper()
	a, err := cg.ledger.Account(context.Background(), "org-a")
	if err != nil {
		t.Fatal(err)
	}
	return a
}

func (cg *decisionChargeGateway) outbox() int {
	return len(cg.ledger.Ledger().(*billing.MemoryLedger).Outbox("org-a"))
}

const decisionChargeRun = "decision-charge-run"

// A decision a check refuses, on /v1/systemone and on /v1/decisions in both
// formats, with each budget and hold in force, changes no budget, hold or
// balance; the same decisions served with the check off do record spend.
// With the org's admission caps on, a decision never reaches the check: the
// caps' admission guard refuses every route but synchronous chat before the
// handler (call_cap_unenforceable), and that refusal holds nothing either.
func TestDecisionRequestCheck_RefusedUnreadChargesNothing(t *testing.T) {
	base := fmt.Sprintf("test:decision-refused-charge:%d:", time.Now().UnixNano())
	client := r7AgentRedis(t, base)
	scenarios := []struct {
		name    string
		caps    bool
		headers []string
		// status and code are the refusal with the check on; pipeline says
		// whether the request reaches the pipeline (and so its post stage).
		status   int
		code     string
		pipeline bool
		// control: with the check off, every decision is served and its
		// spend changes the state in Redis.
		control bool
	}{
		{"org budget, spend guard and credits", false, nil, http.StatusForbidden, "content_blocked", true, true},
		{"run budget and per-call cap", false, []string{"x-agentcc-run-id", decisionChargeRun, "x-agentcc-budget-usd", "1", "x-agentcc-max-cost-usd", "0.5"},
			http.StatusForbidden, "content_blocked", true, false},
		{"the org's admission caps", true, nil, http.StatusPaymentRequired, "call_cap_unenforceable", false, false},
	}
	callers := []struct{ name, key string }{{"own-key byok key", keyByok}, {"managed key with a credit balance", keyManaged}}
	text := "My SSN is " + requestCheckSSN + "."
	for i, sc := range scenarios {
		t.Run(sc.name, func(t *testing.T) {
			cg := newDecisionChargeGateway(t, client, fmt.Sprintf("%s%d:", base, i))
			cg.tenants.Set("org-a", cg.org(true, sc.caps))
			for _, caller := range callers {
				for _, sf := range decisionCheckSurfaces {
					t.Run(caller.name+"/"+sf.name, func(t *testing.T) {
						redisBefore, accountBefore, outboxBefore := cg.redisState(t), cg.account(t), cg.outbox()
						balanceBefore, vendorBefore, postBefore := cg.managed(), cg.upstream.calls(), cg.seen.count()
						rr := cg.do(t, http.MethodPost, sf.path, caller.key, sf.body(text), sc.headers...)
						if rr.Code != sc.status || errorOf(t, rr).Code != sc.code {
							t.Fatalf("status %d %s, want %d %s", rr.Code, rr.Body.String(), sc.status, sc.code)
						}
						if n := cg.upstream.calls() - vendorBefore; n != 0 {
							t.Errorf("the vendor got %d call(s)", n)
						}
						if changes := redisChanges(cg.prefix, redisBefore, cg.redisState(t)); len(changes) > 0 {
							t.Errorf("the refused decision changed budget or hold state: %v", changes)
						}
						if a := cg.account(t); a != accountBefore || a.HeldMicros != 0 || a.SpentMicros != 0 {
							t.Errorf("org credit account %+v -> %+v, want unchanged with nothing held or spent", accountBefore, a)
						}
						if n := cg.outbox(); n != outboxBefore {
							t.Errorf("the ledger outbox went from %d to %d entries", outboxBefore, n)
						}
						if b := cg.managed(); b != balanceBefore {
							t.Errorf("the managed key's balance went from %f to %f", balanceBefore, b)
						}
						for _, h := range []string{"x-agentcc-cost", "x-agentcc-credits-used"} {
							if v := rr.Header().Get(h); v != "" {
								t.Errorf("%s = %q on a refused decision", h, v)
							}
						}
						if !sc.pipeline {
							if n := cg.seen.count() - postBefore; n != 0 {
								t.Errorf("a request refused before the handler ran %d post stage(s)", n)
							}
							return
						}
						seen := cg.seen.last(t)
						if seen.endpoint != "decision" || seen.cost != "" || seen.reserved || seen.billed != 0 || seen.unavailable != "" {
							t.Errorf("after the post stage: %+v, want a decision with no cost, nothing reserved or billed, read by the check (no request_unavailable)", seen)
						}
					})
				}
			}
			if st, err := cg.runs.Peek(context.Background(), "org-a", decisionChargeRun); err == nil && (st.HeldMicros != 0 || st.SpentMicros != 0) {
				t.Errorf("the run budget %+v holds or spent money after refused decisions", st)
			}

			// The control: the same decisions with the check off.
			cg.tenants.Set("org-a", cg.org(false, sc.caps))
			redisBefore, vendorBefore := cg.redisState(t), cg.upstream.calls()
			var costs []string
			for _, sf := range decisionCheckSurfaces {
				postBefore := cg.seen.count()
				rr := cg.do(t, http.MethodPost, sf.path, keyByok, sf.body(text), sc.headers...)
				if cg.seen.count() == postBefore {
					t.Logf("control %s: status %d, refused before the pipeline: %s", sf.name, rr.Code, rr.Body.String())
				} else {
					seen := cg.seen.last(t)
					t.Logf("control %s: status %d, cost %q, endpoint %q, reserved %t, billed %d", sf.name, rr.Code, rr.Header().Get("x-agentcc-cost"), seen.endpoint, seen.reserved, seen.billed)
					if seen.endpoint != "decision" {
						t.Errorf("control %s: endpoint type %q, want decision", sf.name, seen.endpoint)
					}
				}
				if sc.control && rr.Code != http.StatusOK {
					t.Fatalf("control %s: status %d %s, want 200", sf.name, rr.Code, rr.Body.String())
				}
				if c := rr.Header().Get("x-agentcc-cost"); c != "" {
					costs = append(costs, c)
				}
			}
			changes := redisChanges(cg.prefix, redisBefore, cg.redisState(t))
			t.Logf("control: the vendor got %d call(s); costs %v; state changes %v", cg.upstream.calls()-vendorBefore, costs, changes)
			if sc.control {
				if n := cg.upstream.calls() - vendorBefore; n != len(decisionCheckSurfaces) {
					t.Errorf("control: the vendor got %d calls, want %d", n, len(decisionCheckSurfaces))
				}
				if len(costs) == 0 || len(changes) == 0 {
					t.Errorf("control: served decisions reported costs %v and state changes %v; the state must record a served decision's spend", costs, changes)
				}
			}
		})
	}
}
