package guardrails

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestInspectionProjectionCannotHideFromLaterKeyBlock(t *testing.T) {
	for _, schedule := range []string{"parallel", "sequential"} {
		for _, source := range []string{"same-policy", "org", "static", "monitor"} {
			t.Run(schedule+"/"+source, func(t *testing.T) {
				registry := map[string]Guardrail{"pii": &keyTestCheck{name: "pii", mutate: true}, "secret": &keyTestCheck{name: "secret"}}
				blocker := keyTestPolicy("block", "enforce", "block", false)
				cfg := &tenant.GuardrailConfig{PipelineMode: schedule, KeyPolicies: []*tenant.KeyGuardrailPolicy{blocker}}
				var engine *Engine
				switch source {
				case "same-policy":
					blocker.Checks["pii"] = &tenant.GuardrailCheck{Enabled: true, Action: "log"}
				case "org":
					cfg.Checks = map[string]*tenant.GuardrailCheck{"pii": {Enabled: true, Action: "log"}}
				case "static":
					engine = NewEngine(config.GuardrailsConfig{DefaultTimeout: time.Second, Rules: []config.GuardrailRuleConfig{{Name: "pii", Stage: "pre", Action: "log"}}}, registry)
				case "monitor":
					mon := keyTestPolicy("observe", "monitor", "log", true)
					mon.Priority = -1
					mon.Checks["secret"] = &tenant.GuardrailCheck{Enabled: true, Action: "block"}
					cfg.KeyPolicies = append(cfg.KeyPolicies, mon)
				}
				p := keyTestPlugin(cfg, registry)
				p.engine = engine
				rc := keyTestContext("key-a")
				defer rc.Release()
				rc.GuardrailRequest = rc.Request
				rc.Request = nil
				result := p.ProcessRequest(context.Background(), rc)
				if result.Action != pipeline.ShortCircuit || result.Error == nil {
					t.Fatalf("projection rewrite hid violation: %+v", result)
				}
				if got := string(rc.SelectedGuardrailRequest.Messages[0].Content); got != `"secret"` {
					t.Fatalf("selected original changed: %s", got)
				}
				wantLegacy := `"secret"`
				if source == "org" || source == "static" {
					wantLegacy = `"rewritten"` // dev's working-view remediation is retained
				}
				if got := string(rc.GuardrailRequest.Messages[0].Content); got != wantLegacy {
					t.Fatalf("legacy projection=%s want=%s", got, wantLegacy)
				}
				blocked := false
				for _, r := range rc.GuardrailResults {
					if r.PolicyID == "block" && r.Outcome == "blocked" {
						blocked = true
					}
				}
				if !blocked {
					t.Fatalf("missing blocker attribution: %+v", rc.GuardrailResults)
				}
				if source == "monitor" {
					observed := false
					for _, r := range rc.GuardrailResults {
						if r.PolicyID == "observe" && r.Name == "secret" && r.Outcome == "would_block" {
							observed = true
						}
					}
					if !observed {
						t.Fatal("monitor simulated a wire remediation that cannot occur")
					}
				}
			})
		}
	}
}

func TestInspectionProjectionReportsUnsupportedRemediation(t *testing.T) {
	for _, mode := range []string{"enforce", "monitor"} {
		t.Run(mode, func(t *testing.T) {
			pol := keyTestPolicy("redact", mode, "log", true)
			p := keyTestPlugin(&tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{pol}}, map[string]Guardrail{"pii": &keyTestCheck{name: "pii", mutate: true}})
			rc := keyTestContext("key-a")
			defer rc.Release()
			rc.GuardrailRequest = rc.Request
			rc.Request = nil
			if r := p.ProcessRequest(context.Background(), rc); r.Action != pipeline.Continue {
				t.Fatalf("nonblocking remediation refused: %+v", r)
			}
			if len(rc.GuardrailResults) != 1 {
				t.Fatalf("results=%+v", rc.GuardrailResults)
			}
			r := rc.GuardrailResults[0]
			if r.Outcome != "logged" || !strings.Contains(r.Message, passThroughNote) {
				t.Fatalf("unsupported wire remediation reported as success: %+v", r)
			}
			if got := string(rc.GuardrailRequest.Messages[0].Content); got != `"secret"` {
				t.Fatalf("projection changed: %s", got)
			}
		})
	}
}
