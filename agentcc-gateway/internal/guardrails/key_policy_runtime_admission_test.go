package guardrails

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type runtimeAdmissionCheck struct {
	name  string
	stage Stage
	calls int
}

func (g *runtimeAdmissionCheck) Name() string { return g.name }
func (g *runtimeAdmissionCheck) Stage() Stage { return g.stage }
func (g *runtimeAdmissionCheck) Check(context.Context, *CheckInput) *CheckResult {
	g.calls++
	return &CheckResult{Pass: true}
}

func TestKeyPolicyRuntimeResolvesOnceForAdmission(t *testing.T) {
	for _, resolution := range []string{"unavailable", "static", "dynamic"} {
		t.Run(resolution, func(t *testing.T) {
			policy := keyTestPolicy("one", "enforce", "block", false)
			policy.Checks["secret"].Config = map[string]interface{}{"credential": "synthetic-private-config"}
			cfg := &tenant.GuardrailConfig{FailOpen: true, KeyPolicies: []*tenant.KeyGuardrailPolicy{policy}}
			g := &runtimeAdmissionCheck{name: "secret", stage: StagePost}
			var registry map[string]Guardrail
			if resolution == "static" {
				registry = map[string]Guardrail{"secret": g}
			}
			p := keyTestPlugin(cfg, registry)
			created := 0
			p.dynamicFactory = func(string, map[string]interface{}) Guardrail {
				created++
				if resolution == "dynamic" {
					return g
				}
				return nil
			}
			rc := keyTestContext("key-a")
			defer rc.Release()
			rc.IsStream = true
			res := p.ProcessRequest(context.Background(), rc)
			if created != 1 {
				t.Fatalf("factory attempts=%d want=1", created)
			}
			if resolution == "unavailable" {
				if res.Action != pipeline.Continue {
					t.Fatalf("fail-open missing check refused: %+v", res)
				}
			} else if res.Error == nil || res.Error.Code != "stream_guardrail_unsupported" {
				t.Fatalf("post check admission=%+v", res)
			}
			if g.calls != 0 {
				t.Fatal("admission ran a post check without output")
			}
			if len(rc.GuardrailResults) != 1 {
				t.Fatalf("results=%+v", rc.GuardrailResults)
			}
			encoded, _ := json.Marshal(rc.GuardrailResults)
			if strings.Contains(string(encoded), "synthetic-private-config") {
				t.Fatal("config leaked into attribution")
			}
		})
	}
}

func TestKeyPolicyRuntimeUnavailableModesAndInvalidStage(t *testing.T) {
	for _, mode := range []string{"enforce", "monitor"} {
		for _, failOpen := range []bool{false, true} {
			for _, pipelineMode := range []string{"parallel", "sequential"} {
				for _, badStage := range []bool{false, true} {
					t.Run(fmt.Sprintf("%s/open=%v/%s/badstage=%v", mode, failOpen, pipelineMode, badStage), func(t *testing.T) {
						policy := keyTestPolicy("one", mode, "block", false)
						policy.Checks["secret"].ConfidenceThreshold = 1 // execution failure ignores score thresholds
						var registry map[string]Guardrail
						if badStage {
							registry = map[string]Guardrail{"secret": &runtimeAdmissionCheck{name: "secret", stage: Stage(55)}}
						}
						p := keyTestPlugin(&tenant.GuardrailConfig{FailOpen: failOpen, PipelineMode: pipelineMode, KeyPolicies: []*tenant.KeyGuardrailPolicy{policy}}, registry)
						rc := keyTestContext("key-a")
						defer rc.Release()
						rc.IsStream = true
						res := p.ProcessRequest(context.Background(), rc)
						blocked := mode == "enforce" && !failOpen
						if blocked {
							if res.Error == nil || res.Error.Code != "guardrail_unavailable" || res.Error.Status != 503 {
								t.Fatalf("missing unavailable refusal: %+v", res)
							}
						} else if res.Action != pipeline.Continue {
							t.Fatalf("nonblocking mode refused: %+v", res)
						}
						if len(rc.GuardrailResults) != 1 {
							t.Fatalf("attribution=%+v", rc.GuardrailResults)
						}
						if mode == "monitor" && (rc.Flags.GuardrailTriggered || !rc.Flags.GuardrailMonitored || rc.Metadata["guardrail_action"] != "") {
							t.Fatal("monitor affected client-facing flags")
						}
					})
				}
			}
		}
	}
}

func TestKeyPolicyRuntimeAdmissionUsesResolvedStage(t *testing.T) {
	for _, stage := range []Stage{StagePre, StagePost} {
		policy := keyTestPolicy("one", "enforce", "block", false)
		policy.Checks["secret"].Config = map[string]interface{}{"stage": "pre"}
		p := keyTestPlugin(&tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{policy}}, map[string]Guardrail{"secret": &runtimeAdmissionCheck{name: "secret", stage: stage}})
		rc := keyTestContext("key-a")
		rc.IsStream = true
		rc.Metadata["x-guardrail-policy"] = "disabled" // cannot weaken the key's policy
		res := p.ProcessRequest(context.Background(), rc)
		if (res.Action != pipeline.Continue) != (stage == StagePost) {
			t.Fatalf("resolved stage %v result=%+v", stage, res)
		}
		rc.Release()
	}
}

func TestKeyPolicyRuntimeAdmissionDoesNotChangeSpeech(t *testing.T) {
	for _, endpoint := range []string{"speech_relay", "transcription_relay"} {
		policy := keyTestPolicy("one", "enforce", "block", false)
		p := keyTestPlugin(&tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{policy}}, nil)
		p.dynamicFactory = func(string, map[string]interface{}) Guardrail {
			t.Error("HTTP admission resolved speech policy")
			return nil
		}
		rc := keyTestContext("key-a")
		rc.IsStream = true
		rc.EndpointType = endpoint
		if res := p.ProcessRequest(context.Background(), rc); res.Action != pipeline.Continue {
			t.Fatalf("speech admission changed: %+v", res)
		}
		rc.Release()
	}
}

func TestKeyPolicyRuntimeStaticFallbackStillExecutes(t *testing.T) {
	policy := keyTestPolicy("one", "enforce", "block", false)
	p := keyTestPlugin(&tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{policy}}, map[string]Guardrail{"secret": &keyTestCheck{name: "secret"}})
	p.dynamicFactory = func(string, map[string]interface{}) Guardrail { return nil }
	rc := keyTestContext("key-a")
	defer rc.Release()
	if res := p.ProcessRequest(context.Background(), rc); res.Error == nil || res.Error.Code != "content_blocked" {
		t.Fatalf("static fallback did not enforce: %+v", res)
	}
}

// Request-scoped resolution must not be reused after a key/org change, even if
// an internal caller supplies the original context with a different identity.
func TestKeyPolicyRuntimeResolutionScope(t *testing.T) {
	policy := keyTestPolicy("one", "enforce", "block", false)
	cfg := &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{policy}}
	p := keyTestPlugin(cfg, nil)
	rc := keyTestContext("key-a")
	defer rc.Release()
	ctx := p.withResolvedKeyPolicies(context.Background(), rc)
	if got := p.keyPoliciesForInvocation(ctx, "org", "key-b", cfg); len(got.policies) != 0 {
		t.Fatal("resolution crossed key identity")
	}
	if got := p.keyPoliciesForInvocation(ctx, "other", "key-a", &tenant.GuardrailConfig{}); len(got.policies) != 0 {
		t.Fatal("resolution crossed org identity")
	}
	if got := p.keyPoliciesForInvocation(ctx, "org", "key-a", cfg); len(got.policies) != 1 {
		t.Fatal("selected key resolution missing")
	}
}

func TestKeyPolicyRuntimeAdmissionKeepsResolvedSnapshot(t *testing.T) {
	policy := keyTestPolicy("one", "enforce", "block", false)
	p := keyTestPlugin(&tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{policy}}, map[string]Guardrail{"secret": &runtimeAdmissionCheck{name: "secret", stage: StagePost}})
	rc := keyTestContext("key-a")
	defer rc.Release()
	rc.IsStream = true
	ctx := p.withResolvedKeyPolicies(context.Background(), rc)
	p.tenantStore.Set("org", &tenant.OrgConfig{})
	if res := p.checkKeyPolicyStreamAdmission(ctx, rc); res.Error == nil || res.Error.Code != "stream_guardrail_unsupported" {
		t.Fatalf("admission discarded resolved snapshot: %+v", res)
	}
	// The subsequent invocation sees the corrected configuration.
	if res := p.ProcessRequest(context.Background(), rc); res.Action != pipeline.Continue {
		t.Fatalf("new invocation kept stale policy: %+v", res)
	}
}

func TestKeyPolicyRuntimeResolutionSkipsDisabledAndUnselected(t *testing.T) {
	for _, scope := range []string{"disabled", "nil-check", "unselected", "missing-key", "missing-org", "other-org"} {
		t.Run(scope, func(t *testing.T) {
			policy := keyTestPolicy("one", "enforce", "block", false)
			if scope == "disabled" {
				policy.Checks["secret"].Enabled = false
			}
			if scope == "nil-check" {
				policy.Checks["secret"] = nil
			}
			p := keyTestPlugin(&tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{policy}}, nil)
			p.dynamicFactory = func(string, map[string]interface{}) Guardrail { t.Error("resolved an inapplicable check"); return nil }
			rc := keyTestContext("key-a")
			defer rc.Release()
			rc.IsStream = true
			switch scope {
			case "unselected":
				rc.Metadata["auth_key_id"] = "key-b"
			case "missing-key":
				delete(rc.Metadata, "auth_key_id")
			case "missing-org":
				delete(rc.Metadata, "org_id")
			case "other-org":
				rc.Metadata["org_id"] = "other"
			}
			completeKeyTestContext(rc)
			if res := p.ProcessRequest(context.Background(), rc); res.Action != pipeline.Continue || len(rc.GuardrailResults) != 0 {
				t.Fatalf("inapplicable check affected request: %+v", res)
			}
		})
	}
}
