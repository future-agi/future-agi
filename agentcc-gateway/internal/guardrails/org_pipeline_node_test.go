package guardrails

import (
	"context"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// How a node runs an organization's guardrail checks, which GET
// /-/config reports as guardrail_pipeline and the console's Guardrails tab
// shows read-only: an org check's error or timeout follows the node's
// guardrails.fail_open, whatever the org's own fail_open says, and the org's
// checks run one after another, the first block stopping the rest, whatever
// the org's pipeline_mode says.

type orgNodeCountingCheck struct {
	name   string
	calls  *atomic.Int32
	delay  time.Duration
	result *CheckResult
}

func (g *orgNodeCountingCheck) Name() string { return g.name }
func (g *orgNodeCountingCheck) Stage() Stage { return StagePre }
func (g *orgNodeCountingCheck) Check(ctx context.Context, _ *CheckInput) *CheckResult {
	g.calls.Add(1)
	if g.delay > 0 {
		select {
		case <-time.After(g.delay):
		case <-ctx.Done():
			return nil
		}
	}
	return g.result
}

func orgNodePlugin(t *testing.T, nodeFailOpen bool, org *tenant.GuardrailConfig, checks ...Guardrail) *GuardrailPlugin {
	t.Helper()
	registry := map[string]Guardrail{}
	for _, g := range checks {
		registry[g.Name()] = g
	}
	store := tenant.NewStore()
	store.Set("org-th3967", &tenant.OrgConfig{Guardrails: org})
	engine := NewEngine(config.GuardrailsConfig{Enabled: true, FailOpen: nodeFailOpen, DefaultTimeout: time.Second}, registry)
	return NewPlugin(engine, registry, nil, nil, store)
}

func TestOrgCheckErrorsFollowTheNodesFailOpen(t *testing.T) {
	for _, tc := range []struct {
		name        string
		nodeOpen    bool
		orgOpen     bool
		wantBlocked bool
	}{
		{"node fail_open true, org false", true, false, false},
		{"node fail_open false, org true", false, true, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var calls atomic.Int32
			slow := &orgNodeCountingCheck{name: "slow-check", calls: &calls, delay: 300 * time.Millisecond,
				result: &CheckResult{Pass: true}}
			org := &tenant.GuardrailConfig{FailOpen: tc.orgOpen, TimeoutMs: 20, Checks: map[string]*tenant.GuardrailCheck{
				"slow-check": {Enabled: true, Action: "block", ConfidenceThreshold: 0.5},
			}}
			plugin := orgNodePlugin(t, tc.nodeOpen, org, slow)
			rc := newOrgThresholdPluginTestRequestContext()
			defer rc.Release()
			result := plugin.ProcessRequest(context.Background(), rc)
			if blocked := result.Action != pipeline.Continue; blocked != tc.wantBlocked {
				t.Fatalf("a timed-out org check: blocked %v, want %v (the node's fail_open %v decides; the org's %v is not read)",
					blocked, tc.wantBlocked, tc.nodeOpen, tc.orgOpen)
			}
		})
	}
}

func TestOrgChecksRunInTurnAndStopAtTheFirstBlock(t *testing.T) {
	for _, mode := range []string{"parallel", "sequential", ""} {
		var calls atomic.Int32
		block := &CheckResult{Pass: false, Score: 1, Message: "blocked"}
		a := &orgNodeCountingCheck{name: "check-a", calls: &calls, result: block}
		b := &orgNodeCountingCheck{name: "check-b", calls: &calls, result: block}
		org := &tenant.GuardrailConfig{PipelineMode: mode, Checks: map[string]*tenant.GuardrailCheck{
			"check-a": {Enabled: true, Action: "block", ConfidenceThreshold: 0.5},
			"check-b": {Enabled: true, Action: "block", ConfidenceThreshold: 0.5},
		}}
		plugin := orgNodePlugin(t, true, org, a, b)
		rc := newOrgThresholdPluginTestRequestContext()
		result := plugin.ProcessRequest(context.Background(), rc)
		rc.Release()
		if result.Action == pipeline.Continue {
			t.Fatalf("pipeline_mode %q: two blocking org checks let the request through", mode)
		}
		if n := calls.Load(); n != 1 {
			t.Errorf("pipeline_mode %q: %d checks ran, want 1 (the first block stops the rest)", mode, n)
		}
	}
}

// RewriteArguments keeps every byte inside the arguments' JSON value that it
// does not rewrite, white space and escapes included; white space before and
// after the value is not kept once anything is rewritten.
func TestRewriteArgumentsKeepsTheValuesBytesNotTheWhiteSpaceAroundIt(t *testing.T) {
	mask := func(a ArgumentText) ArgumentText {
		if a.Value == "123-45-6789" {
			a.Value = "***"
		}
		return a
	}
	for in, want := range map[string]string{
		"  \n{\"ssn\":\"123-45-6789\"}\n  ":                 `{"ssn":"***"}`,
		"{ \"ssn\" :\t\"123-45-6789\", \"n\":\"\\u002d\" }": "{ \"ssn\" :\t\"***\", \"n\":\"\\u002d\" }",
	} {
		got, changed := RewriteArguments(context.Background(), in, mask)
		if !changed || got != want {
			t.Errorf("RewriteArguments(%q) = %q, %v; want %q, true", in, got, changed, want)
		}
	}
	if got, changed := RewriteArguments(context.Background(), "  {\"a\":\"b\"}  ", mask); changed || got != "  {\"a\":\"b\"}  " {
		t.Errorf("nothing rewritten: %q, %v; want the arguments as given, false", got, changed)
	}
}
