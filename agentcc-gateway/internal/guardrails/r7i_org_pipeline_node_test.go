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

// How a node runs an organization's guardrail checks: an org check's error
// or timeout follows the org's own fail_open when the org set one, whatever
// the node's guardrails.fail_open says; an org that set none follows the
// node's, as v1.58.0 ran every org check. With the org's pipeline_mode
// "sequential", or with none set, its checks run one after another, the
// first block stopping the rest.

type r7iCountingCheck struct {
	name   string
	calls  *atomic.Int32
	delay  time.Duration
	result *CheckResult
}

func (g *r7iCountingCheck) Name() string { return g.name }
func (g *r7iCountingCheck) Stage() Stage { return StagePre }
func (g *r7iCountingCheck) Check(ctx context.Context, _ *CheckInput) *CheckResult {
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

func r7iOrgPlugin(t *testing.T, nodeFailOpen bool, org *tenant.GuardrailConfig, checks ...Guardrail) *GuardrailPlugin {
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

func TestR7iOrgCheckErrorsFollowTheNodesFailOpen(t *testing.T) {
	yes, no := true, false
	for _, tc := range []struct {
		name        string
		nodeOpen    bool
		orgOpen     *bool // nil: the org set none
		wantBlocked bool
	}{
		{"node fail_open true, org false", true, &no, true},
		{"node fail_open false, org true", false, &yes, false},
		{"node fail_open true, org true", true, &yes, false},
		{"node fail_open false, org false", false, &no, true},
		{"node fail_open true, org unset", true, nil, false},
		{"node fail_open false, org unset", false, nil, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var calls atomic.Int32
			slow := &r7iCountingCheck{name: "slow-check", calls: &calls, delay: 300 * time.Millisecond,
				result: &CheckResult{Pass: true}}
			org := &tenant.GuardrailConfig{FailOpen: tc.orgOpen, TimeoutMs: 20, Checks: map[string]*tenant.GuardrailCheck{
				"slow-check": {Enabled: true, Action: "block", ConfidenceThreshold: 0.5},
			}}
			plugin := r7iOrgPlugin(t, tc.nodeOpen, org, slow)
			rc := newOrgThresholdPluginTestRequestContext()
			defer rc.Release()
			result := plugin.ProcessRequest(context.Background(), rc)
			if blocked := result.Action != pipeline.Continue; blocked != tc.wantBlocked {
				t.Fatalf("a timed-out org check: blocked %v, want %v (an org fail_open it set decides; set none, the node's %v does)",
					blocked, tc.wantBlocked, tc.nodeOpen)
			}
		})
	}
}

func TestR7iOrgChecksRunInTurnAndStopAtTheFirstBlock(t *testing.T) {
	// "" is an org that set no pipeline_mode: its checks run in turn too.
	for _, mode := range []string{"sequential", "Sequential", ""} {
		var calls atomic.Int32
		block := &CheckResult{Pass: false, Score: 1, Message: "blocked"}
		a := &r7iCountingCheck{name: "check-a", calls: &calls, result: block}
		b := &r7iCountingCheck{name: "check-b", calls: &calls, result: block}
		org := &tenant.GuardrailConfig{PipelineMode: mode, Checks: map[string]*tenant.GuardrailCheck{
			"check-a": {Enabled: true, Action: "block", ConfidenceThreshold: 0.5},
			"check-b": {Enabled: true, Action: "block", ConfidenceThreshold: 0.5},
		}}
		plugin := r7iOrgPlugin(t, true, org, a, b)
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
func TestR7iRewriteArgumentsKeepsTheValuesBytesNotTheWhiteSpaceAroundIt(t *testing.T) {
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
