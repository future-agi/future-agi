package server

import (
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/leakage"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// An A2A task through the gateway (the A2A executor serves the task's chat
// request) whose answer an organization's output check blocks fails with the
// guardrail's error, naming the check, and the answer is not in the task.
// The a2a server's own pipeline path is pinned in internal/a2a
// (TestPipelineTaskWhoseAnswerACheckWithholdsFailsWithTheGuardrailError).
func TestA2ATaskWhoseAnswerACheckWithholdsFailsWithTheGuardrailError(t *testing.T) {
	upstream := startLeakUpstream(t)
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "post-block", Key: postBlockKey, Owner: "o", KeyType: "byok",
		Metadata: map[string]string{"org_id": postBlockOrg}}}
	cfg.Guardrails = config.GuardrailsConfig{Enabled: true, DefaultTimeout: 2 * time.Second}
	cfg.A2A.Enabled = true
	cfg.A2A.Card = config.A2ACardConfig{Name: "gw", Description: "d", Version: "1.0.0"}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: upstream.URL, APIFormat: "openai", Models: []string{"gpt-4o"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	grRegistry := map[string]guardrails.Guardrail{leakageCheck: leakage.New(nil)}
	grEngine := guardrails.NewEngine(cfg.Guardrails, grRegistry)
	tenants := tenant.NewStore()
	tenants.Set(postBlockOrg, &tenant.OrgConfig{
		Providers: map[string]*tenant.ProviderConfig{"openai": {Enabled: true, APIKey: "org-key", Models: []string{"gpt-4o"}}},
		Guardrails: &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{
			leakageCheck: {Enabled: true, Action: "block", ConfidenceThreshold: 0.1},
		}},
	})
	ks := auth.NewKeyStore(cfg.Auth)
	policies := policy.NewStore()
	engine := pipeline.NewEngine(authplugin.New(ks, true), guardrails.NewPlugin(grEngine, grRegistry, nil, policies, tenants))
	srv := New(cfg, "", registry, engine, ks, grEngine, policies, nil, testModelDBPtr(), tenants, nil)
	srv.ready.Store(true)

	reply, err := sendA2ATask(srv, postBlockKey, "gpt-4o", "")
	if err != nil {
		t.Fatal(err)
	}
	var message string
	for _, part := range reply.task.Status.Message {
		message += part.Text
	}
	t.Logf("task %s: %s", reply.task.Status.State, message)
	if reply.task.Status.State != "failed" || !strings.Contains(message, "content_blocked") || !strings.Contains(message, leakageCheck) {
		t.Errorf("the task ended %q with %q; want failed with the guardrail's error naming %s", reply.task.Status.State, message, leakageCheck)
	}
	for _, a := range reply.task.Artifacts {
		for _, p := range a.Parts {
			if strings.Contains(p.Text, "DROP TABLE") {
				t.Errorf("the withheld answer is in the task's artifacts: %q", p.Text)
			}
		}
	}
}
