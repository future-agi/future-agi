package guardrails

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type callbackGuardrail struct {
	name  string
	check func(*CheckInput) *CheckResult
}

func (g *callbackGuardrail) Name() string { return g.name }
func (g *callbackGuardrail) Stage() Stage { return StagePost }
func (g *callbackGuardrail) Check(_ context.Context, input *CheckInput) *CheckResult {
	return g.check(input)
}

func TestPostRejectShortCircuitsWithoutBypassingVetoes(t *testing.T) {
	for _, tc := range []struct {
		name                string
		static, tenant, ran []string
		policy, code        string
		reflect             bool
	}{
		{name: "static reject", static: []string{"a-reject", "z-late"}, ran: []string{"a-reject"}, code: "content_blocked"},
		{name: "static reflect then reject", static: []string{"a-reflect", "b-reject", "z-late"}, ran: []string{"a-reflect", "b-reject"}, code: "content_blocked"},
		{name: "static reject skips tenant reflect", static: []string{"a-reject"}, tenant: []string{"b-reflect"}, ran: []string{"a-reject"}, code: "content_blocked"},
		{name: "tenant reject vetoes static reflect", static: []string{"a-reflect"}, tenant: []string{"b-reject", "z-late"}, ran: []string{"a-reflect", "b-reject"}, code: "content_blocked"},
		{name: "tenant reflect then reject", tenant: []string{"a-reflect", "b-reject", "z-late"}, ran: []string{"a-reflect", "b-reject"}, code: "content_blocked"},
		{name: "static operational failure", static: []string{"a-operational-reflect", "z-late"}, tenant: []string{"b-reflect"}, ran: []string{"a-operational-reflect"}, code: "guardrail_evaluation_failed"},
		{name: "tenant operational veto", static: []string{"a-reflect"}, tenant: []string{"b-operational-reflect", "z-late"}, ran: []string{"a-reflect", "b-operational-reflect"}, code: "guardrail_evaluation_failed"},
		{name: "only reflecting blocks", static: []string{"a-reflect"}, tenant: []string{"b-reflect"}, ran: []string{"a-reflect", "b-reflect"}, code: "content_blocked", reflect: true},
		{name: "warning cannot veto", static: []string{"a-reflect"}, tenant: []string{"b-warn"}, ran: []string{"a-reflect", "b-warn"}, code: "content_blocked", reflect: true},
		{name: "strict warning becomes reject", static: []string{"a-warn", "z-late"}, tenant: []string{"b-reflect"}, ran: []string{"a-warn"}, policy: "strict", code: "content_blocked"},
		{name: "log-only never rejects", static: []string{"a-reject", "z-late"}, tenant: []string{"b-reflect"}, ran: []string{"a-reject", "z-late", "b-reflect"}, policy: "log-only"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var ran []string
			registry := make(map[string]Guardrail)
			checks := make(map[string]*tenant.GuardrailCheck)
			cfg := config.GuardrailsConfig{DefaultTimeout: time.Second}
			add := func(name string) (string, map[string]interface{}) {
				registry[name] = &callbackGuardrail{name: name, check: func(*CheckInput) *CheckResult {
					ran = append(ran, name)
					return &CheckResult{Pass: false, Score: 1, Message: "private candidate", OperationalError: strings.Contains(name, "operational")}
				}}
				action := "block"
				if strings.Contains(name, "warn") {
					action = "warn"
				}
				controls := map[string]interface{}{"stage": "post"}
				if strings.Contains(name, "reflect") {
					controls["on_block"] = "reflect"
				}
				return action, controls
			}
			for _, name := range tc.static {
				action, controls := add(name)
				cfg.Rules = append(cfg.Rules, config.GuardrailRuleConfig{Name: name, Stage: "post", Action: action, Config: controls})
			}
			for _, name := range tc.tenant {
				action, controls := add(name)
				checks[name] = &tenant.GuardrailCheck{Enabled: true, Action: action, Config: controls}
			}
			store := tenant.NewStore()
			store.Set("org", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{Checks: checks}})
			rc := reflectionRC()
			defer rc.Release()
			rc.Metadata["org_id"] = "org"
			if tc.policy != "" {
				rc.Metadata["x-guardrail-policy"] = tc.policy
				rc.Metadata["key_allow_policy_override"] = "true"
			}
			rc.Response = reflectionResponse("private candidate")
			result := NewPlugin(NewEngine(cfg, registry), registry, nil, nil, store).ProcessResponse(context.Background(), rc)
			if strings.Join(ran, ",") != strings.Join(tc.ran, ",") {
				t.Fatalf("ran=%v, want %v", ran, tc.ran)
			}
			if (result.Reflection != nil) != tc.reflect {
				t.Fatalf("reflection=%+v, want enabled=%t", result.Reflection, tc.reflect)
			}
			if tc.code == "" {
				if result.Error != nil {
					t.Fatal(result.Error)
				}
			} else if result.Error == nil || result.Error.Code != tc.code || rc.Response != nil {
				t.Fatalf("error=%v response=%+v, want %s", result.Error, rc.Response, tc.code)
			}
		})
	}
}
