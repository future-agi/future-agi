package guardrails

import (
	"context"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A check that rewrites the request and fails (here it
// panics) before it finishes is refused like one that times out: 403
// remediation_unavailable naming the check, with fail_open on or off, and the
// request stays as the client sent it.

type failingRewriter struct{ name string }

func (g failingRewriter) Name() string          { return g.name }
func (g failingRewriter) Stage() Stage          { return StagePre }
func (g failingRewriter) RewritesRequest() bool { return true }
func (g failingRewriter) Check(context.Context, *CheckInput) *CheckResult {
	panic("rewriter failed")
}

func TestPlugin_ARewriteThatFailsIsRefused(t *testing.T) {
	for _, failOpen := range []bool{true, false} {
		for _, passThrough := range []bool{false, true} {
			name := map[bool]string{false: "chat", true: "passed through"}[passThrough]
			name += map[bool]string{true: " / fail_open", false: " / fail closed"}[failOpen]

			t.Run("config rule / "+name, func(t *testing.T) {
				g := failingRewriter{name: "failing-rewriter"}
				engine := NewEngine(config.GuardrailsConfig{FailOpen: failOpen, DefaultTimeout: time.Second,
					Rules: []config.GuardrailRuleConfig{{Name: g.name, Stage: "pre", Mode: "sync", Action: "log"}}},
					map[string]Guardrail{g.name: g})
				plugin := NewPlugin(engine, map[string]Guardrail{g.name: g}, nil, nil, nil)
				rc := raceContext(passThrough)
				defer rc.Release()
				r := plugin.ProcessRequest(context.Background(), rc)
				assertRewriteRefused(t, r.Error, g.name)
				assertAsSent(t, rc)
			})

			t.Run("org check / "+name, func(t *testing.T) {
				g := failingRewriter{name: "failing-org-rewriter"}
				engine := NewEngine(config.GuardrailsConfig{FailOpen: failOpen, DefaultTimeout: time.Second}, nil)
				tenants := tenant.NewStore()
				tenants.Set("org-race", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{
					Checks: map[string]*tenant.GuardrailCheck{g.name: {Enabled: true, Action: "log"}}}})
				plugin := NewPlugin(engine, map[string]Guardrail{g.name: g}, nil, nil, tenants)
				rc := raceContext(passThrough)
				defer rc.Release()
				r := plugin.ProcessRequest(context.Background(), rc)
				assertRewriteRefused(t, r.Error, g.name)
				assertAsSent(t, rc)
			})
		}
	}
}
