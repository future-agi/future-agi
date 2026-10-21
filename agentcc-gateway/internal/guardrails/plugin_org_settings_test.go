package guardrails

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func boolPtr(b bool) *bool { return &b }

const orgSettingsTestOrg = "org-pipeline-settings"

// newOrgSettingsPlugin builds a plugin with a gateway-wide failOpen and one
// org whose guardrail config is gcfg.
func newOrgSettingsPlugin(gatewayFailOpen bool, registry map[string]Guardrail, gcfg *tenant.GuardrailConfig) *GuardrailPlugin {
	store := tenant.NewStore()
	store.Set(orgSettingsTestOrg, &tenant.OrgConfig{Guardrails: gcfg})
	p := NewPlugin(nil, registry, nil, nil, store)
	p.failOpen = gatewayFailOpen
	return p
}

func newOrgSettingsRC() *models.RequestContext {
	rc := models.AcquireRequestContext()
	rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o"}
	rc.Metadata["org_id"] = orgSettingsTestOrg
	return rc
}

func blockCheck() *tenant.GuardrailCheck {
	return &tenant.GuardrailCheck{Enabled: true, Action: "block", ConfidenceThreshold: 0.5}
}

func slowPass(name string) *mockGuardrail {
	return &mockGuardrail{name: name, stage: StagePre, result: &CheckResult{Pass: true}, delay: time.Second}
}

func TestOrgFailOpen_OrgFalseOverridesGatewayTrue(t *testing.T) {
	p := newOrgSettingsPlugin(true,
		map[string]Guardrail{"slow": slowPass("slow")},
		&tenant.GuardrailConfig{
			FailOpen:  boolPtr(false),
			TimeoutMs: 20,
			Checks:    map[string]*tenant.GuardrailCheck{"slow": blockCheck()},
		})
	rc := newOrgSettingsRC()
	defer rc.Release()

	if res := p.ProcessRequest(context.Background(), rc); res.Action != pipeline.ShortCircuit {
		t.Fatal("org fail_open=false: timed-out check should block")
	}
}

func TestOrgFailOpen_OrgTrueOverridesGatewayFalse(t *testing.T) {
	p := newOrgSettingsPlugin(false,
		map[string]Guardrail{"slow": slowPass("slow")},
		&tenant.GuardrailConfig{
			FailOpen:  boolPtr(true),
			TimeoutMs: 20,
			Checks:    map[string]*tenant.GuardrailCheck{"slow": blockCheck()},
		})
	rc := newOrgSettingsRC()
	defer rc.Release()

	if res := p.ProcessRequest(context.Background(), rc); res.Action != pipeline.Continue {
		t.Fatal("org fail_open=true: timed-out check should be let through")
	}
}

func TestOrgFailOpen_UnsetFallsBackToGateway(t *testing.T) {
	for _, gatewayFailOpen := range []bool{true, false} {
		p := newOrgSettingsPlugin(gatewayFailOpen,
			map[string]Guardrail{"slow": slowPass("slow")},
			&tenant.GuardrailConfig{
				TimeoutMs: 20,
				Checks:    map[string]*tenant.GuardrailCheck{"slow": blockCheck()},
			})
		rc := newOrgSettingsRC()
		res := p.ProcessRequest(context.Background(), rc)
		rc.Release()

		blocked := res.Action == pipeline.ShortCircuit
		if blocked == gatewayFailOpen {
			t.Errorf("gateway fail_open=%v, org unset: blocked=%v", gatewayFailOpen, blocked)
		}
	}
}

// Three checks each taking ~100ms: parallel should finish in roughly one
// check's time, sequential in roughly three.
func TestOrgPipelineMode_ParallelVsSequentialLatency(t *testing.T) {
	const delay = 100 * time.Millisecond
	registry := map[string]Guardrail{}
	checks := map[string]*tenant.GuardrailCheck{}
	for _, n := range []string{"a", "b", "c"} {
		registry[n] = &mockGuardrail{name: n, stage: StagePre, result: &CheckResult{Pass: true}, delay: delay}
		checks[n] = blockCheck()
	}

	run := func(mode string) time.Duration {
		p := newOrgSettingsPlugin(true, registry, &tenant.GuardrailConfig{PipelineMode: mode, Checks: checks})
		rc := newOrgSettingsRC()
		defer rc.Release()
		start := time.Now()
		if res := p.ProcessRequest(context.Background(), rc); res.Action != pipeline.Continue {
			t.Fatalf("mode %q: passing checks should continue", mode)
		}
		return time.Since(start)
	}

	if d := run("parallel"); d >= 2*delay {
		t.Errorf("parallel took %v, want < %v", d, 2*delay)
	}
	if d := run(""); d >= 2*delay {
		t.Errorf("default (unset) mode took %v, want parallel (< %v)", d, 2*delay)
	}
	if d := run("sequential"); d < 3*delay {
		t.Errorf("sequential took %v, want >= %v", d, 3*delay)
	}
}

// In parallel mode a fast block must not wait for slow checks, and the block
// is attributed to the check that blocked.
func TestOrgPipelineMode_ParallelBlockShortCircuits(t *testing.T) {
	registry := map[string]Guardrail{
		"a-slow":  &mockGuardrail{name: "a-slow", stage: StagePre, result: &CheckResult{Pass: true}, delay: 2 * time.Second},
		"b-block": &mockGuardrail{name: "b-block", stage: StagePre, result: &CheckResult{Pass: false, Score: 0.9, Message: "bad"}},
	}
	p := newOrgSettingsPlugin(false, registry, &tenant.GuardrailConfig{
		PipelineMode: "parallel",
		Checks: map[string]*tenant.GuardrailCheck{
			"a-slow":  blockCheck(),
			"b-block": blockCheck(),
		},
	})
	rc := newOrgSettingsRC()
	defer rc.Release()

	start := time.Now()
	res := p.ProcessRequest(context.Background(), rc)
	if res.Action != pipeline.ShortCircuit {
		t.Fatal("expected block")
	}
	if d := time.Since(start); d > 500*time.Millisecond {
		t.Errorf("parallel block waited %v for slow check", d)
	}
	if rc.Metadata["guardrail_name"] != "b-block" {
		t.Errorf("guardrail_name = %q, want b-block", rc.Metadata["guardrail_name"])
	}
	// The cancelled slow check must not be reported (it would look like a
	// fail-closed timeout).
	for _, r := range rc.GuardrailResults {
		if r.Name == "a-slow" {
			t.Errorf("cancelled check a-slow should not be in results: %+v", r)
		}
	}
}

// A warn that triggers before a block must not be named in the block error.
func TestOrgPipelineMode_SequentialBlockMessageNamesBlocker(t *testing.T) {
	registry := map[string]Guardrail{
		"a-warn":  &mockGuardrail{name: "a-warn", stage: StagePre, result: &CheckResult{Pass: false, Score: 0.9, Message: "meh"}},
		"b-block": &mockGuardrail{name: "b-block", stage: StagePre, result: &CheckResult{Pass: false, Score: 0.9, Message: "bad"}},
	}
	p := newOrgSettingsPlugin(true, registry, &tenant.GuardrailConfig{
		PipelineMode: "sequential",
		Checks: map[string]*tenant.GuardrailCheck{
			"a-warn":  {Enabled: true, Action: "warn", ConfidenceThreshold: 0.5},
			"b-block": blockCheck(),
		},
	})
	rc := newOrgSettingsRC()
	defer rc.Release()

	res := p.ProcessRequest(context.Background(), rc)
	if res.Action != pipeline.ShortCircuit || res.Error == nil {
		t.Fatal("expected block")
	}
	if !strings.Contains(res.Error.Message, "b-block") {
		t.Errorf("block message %q should name b-block", res.Error.Message)
	}
}
