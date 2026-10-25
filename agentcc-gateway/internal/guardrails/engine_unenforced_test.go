package guardrails

import (
	"context"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
)

// On an answer the gateway cannot withhold (models.NoChatAnswer), the post
// stage records a rule in block mode that triggers as what is done, log,
// and the rules after it still run; RunPost blocks and stops there. Under
// X-Guardrail-Policy strict every rule blocks, and is logged the same way;
// disabled runs none.
func TestEngine_RunPostUnenforcedLogsWhatItCannotBlock(t *testing.T) {
	fails := func(name string) *mockGuardrail {
		return &mockGuardrail{name: name, stage: StagePost, result: &CheckResult{Pass: false, Score: 0.95, Message: name + " failed"}}
	}
	registry := map[string]Guardrail{"blocker": fails("blocker"), "warner": fails("warner")}
	cfg := config.GuardrailsConfig{Enabled: true, FailOpen: true, DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{
			{Name: "blocker", Stage: "post", Mode: "sync", Action: "block", Threshold: 0.5},
			{Name: "warner", Stage: "post", Mode: "sync", Action: "warn", Threshold: 0.5},
		}}
	engine := NewEngine(cfg, registry)
	input := &CheckInput{Metadata: map[string]string{}}

	enforced := engine.RunPost(context.Background(), input, nil, policy.RequestPolicyNone)
	if !enforced.Blocked || len(enforced.Triggered) != 1 {
		t.Fatalf("RunPost: blocked %v, %d triggered; want a block that stops the stage", enforced.Blocked, len(enforced.Triggered))
	}

	got := engine.RunPostUnenforced(context.Background(), input, nil, policy.RequestPolicyNone)
	if got.Blocked || len(got.Triggered) != 2 || got.Triggered[0].Action != ActionLog || got.Triggered[1].Action != ActionWarn || len(got.Warnings) != 1 {
		t.Errorf("RunPostUnenforced: blocked %v, triggered %+v, warnings %v; want blocker logged, then warner's warning", got.Blocked, got.Triggered, got.Warnings)
	}

	strict := engine.RunPostUnenforced(context.Background(), input, nil, policy.RequestPolicyStrict)
	if strict.Blocked || len(strict.Triggered) != 2 || strict.Triggered[0].Action != ActionLog || strict.Triggered[1].Action != ActionLog {
		t.Errorf("strict: blocked %v, triggered %+v; want both logged", strict.Blocked, strict.Triggered)
	}
	if off := engine.RunPostUnenforced(context.Background(), input, nil, policy.RequestPolicyDisabled); len(off.Triggered) != 0 {
		t.Errorf("disabled: %d triggered, want none", len(off.Triggered))
	}
}
