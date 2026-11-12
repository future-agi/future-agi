package server

import (
	"context"
	"fmt"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	capbudget "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	"github.com/futureagi/agentcc-gateway/internal/plugins/toolpolicy"
)

// This observes ordinary uncapped native requests, not positive CUA authority.
type nativeCompatPolicySpy struct {
	calls    int
	ordinary bool
}

func (*nativeCompatPolicySpy) Name() string  { return "native-compat-spy" }
func (*nativeCompatPolicySpy) Priority() int { return 61 }
func (s *nativeCompatPolicySpy) ProcessRequest(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	s.calls++
	_, native := rc.PassThrough.(models.NativeBody)
	s.ordinary = native && rc.Request == nil && !rc.Routing.ComputerUse.Required && rc.Agent.MaxCostUSD == nil && rc.Agent.BudgetUSD == nil && rc.Agent.BudgetRemainingUSD == nil && capbudget.RequestAdmission(rc) == nil
	return pipeline.ResultContinue()
}
func (*nativeCompatPolicySpy) ProcessResponse(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

func nativeCompatGateway(t *testing.T, cfg config.ToolPolicyConfig) (*postBlockGateway, *upstreamCalls, *nativeCompatPolicySpy) {
	t.Helper()
	g, calls := newRequestCheckGateway(t, postBlockSetup{})
	h := g.srv.handlers
	if h.nativeCaps != nil || capbudget.CapsEnabled(h.tenantStore.Get(postBlockOrg)) {
		t.Fatal("ordinary native fixture unexpectedly has cap control")
	}
	spy := &nativeCompatPolicySpy{}
	// Keep the fixture's real authentication, route and provider implementations.
	// No guardrail is configured in this fixture; install the policy under test.
	h.engine = pipeline.NewEngine(authplugin.New(h.keyStore, true), toolpolicy.New(cfg, nil), spy)
	return g, calls, spy
}

func assertNativeCompatOutgoing(t *testing.T, g *postBlockGateway, calls *upstreamCalls, spy *nativeCompatPolicySpy, path, body, want string, refuse bool) {
	t.Helper()
	before, observed := calls.count(), spy.calls
	code, _ := g.do(t, path, body)
	if refuse {
		if code != 400 || calls.count() != before || spy.calls != observed {
			t.Fatal("invalid reference reached provider or failed outside policy")
		}
		return
	}
	if code != 200 || calls.count() != before+1 || spy.calls != observed+1 || !spy.ordinary {
		t.Fatalf("ordinary native request did not reach provider exactly once: status=%d", code)
	}
	calls.mu.Lock()
	got := calls.bodies[len(calls.bodies)-1]
	calls.mu.Unlock()
	if got != want {
		t.Fatal("actual outgoing native body lost shape or untouched bytes")
	}
}

func TestNativePolicyGeminiSingletonOutgoing(t *testing.T) {
	for _, strip := range []bool{false, true} {
		t.Run(fmt.Sprintf("strip=%t", strip), func(t *testing.T) {
			cfg := config.ToolPolicyConfig{Enabled: true, Allow: []string{"lookup"}, MaxToolsPerRequest: 1, DefaultAction: "strip"}
			if strip {
				cfg.Deny = []string{"lookup"}
			}
			g, calls, spy := nativeCompatGateway(t, cfg)
			for _, alias := range []string{"functionDeclarations", "function_declarations"} {
				for _, toolsArray := range []bool{false, true} {
					for _, declarationsArray := range []bool{false, true} {
						t.Run(fmt.Sprintf("%s/toolsArray=%t/declarationsArray=%t", alias, toolsArray, declarationsArray), func(t *testing.T) {
							decl := `{ "name":"lookup", "parameters":{"type":"object"} }`
							if declarationsArray {
								decl = "[" + decl + "]"
							}
							tools := `{"` + alias + `":` + decl + `}`
							if toolsArray {
								tools = "[" + tools + "]"
							}
							body := `{ "contents":[{"role":"user","parts":[{"text":"hello"}]}], "tools":` + tools + `, "opaque":{"n":1e+03,"s":"a"} }`
							want := body
							if strip {
								want = strings.Replace(body, ` "tools":`+tools+`,`, "", 1)
								if toolsArray {
									want = strings.Replace(body, tools, "[]", 1)
								}
							}
							assertNativeCompatOutgoing(t, g, calls, spy, "/v1beta/models/"+postBlockGemini+":generateContent", body, want, false)
						})
					}
				}
			}
		})
	}
}

func TestNativePolicyResponsesIncidentalOutgoing(t *testing.T) {
	g, calls, spy := nativeCompatGateway(t, config.ToolPolicyConfig{Enabled: true, Deny: []string{"drop"}, DefaultAction: "strip"})
	for _, tc := range []struct {
		name, input, selection string
		refuse                 bool
	}{
		{"input", `"drop"`, "", false},
		{"text", `[{"role":"user","content":[{"type":"input_text","text":"drop"}]}]`, "", false},
		{"removed-selection", `"drop"`, `,"tool_choice":{"type":"function","name":"drop"}`, true},
		{"unknown-selection", `"drop"`, `,"tool_choice":{"type":"future","name":"keep"}`, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			body := `{"model":"gpt-4o","input":` + tc.input + `,"tools":[{"type":"function","name":"keep"},{"type":"function","name":"drop"}],"opaque":{"name":"drop","text":"drop","n":1e+03}` + tc.selection + `}`
			want := strings.Replace(body, `,{"type":"function","name":"drop"}`, "", 1)
			assertNativeCompatOutgoing(t, g, calls, spy, "/v1/responses", body, want, tc.refuse)
		})
	}
}
