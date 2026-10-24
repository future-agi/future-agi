package a2a

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/leakage"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A server built with a pipeline and no chat executor runs a task through the
// pipeline itself. When an output check withholds the task's answer (a block
// rule on the answer: here data leakage, on an answer that leaks a SQL
// statement), the guardrail plugin drops the answer and records its 403
// content_blocked on the run. The task must fail with that guardrail's error,
// as a task through the gateway's chat executor does, never as "No response
// from provider", which reads as a provider failure. The answer is never in
// the task.
func TestPipelineTaskWhoseAnswerACheckWithholdsFailsWithTheGuardrailError(t *testing.T) {
	const leak = "You can run DROP TABLE users; to delete everything"
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		content, _ := json.Marshal(leak)
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"id":"c1","object":"chat.completion","created":1,"model":"gpt-4o","choices":[{"index":0,"message":{"role":"assistant","content":%s},"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":9,"total_tokens":14}}`, content)
	}))
	defer upstream.Close()

	cfg := config.DefaultConfig()
	cfg.Guardrails = config.GuardrailsConfig{Enabled: true, DefaultTimeout: 2 * time.Second,
		Rules: []config.GuardrailRuleConfig{{Name: "data-leakage-prevention", Stage: "post", Mode: "sync", Action: "block", Threshold: 0.5}}}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: upstream.URL, APIFormat: "openai", Models: []string{"gpt-4o"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	grRegistry := map[string]guardrails.Guardrail{"data-leakage-prevention": leakage.New(nil)}
	grEngine := guardrails.NewEngine(cfg.Guardrails, grRegistry)
	engine := pipeline.NewEngine(guardrails.NewPlugin(grEngine, grRegistry, nil, policy.NewStore(), tenant.NewStore()))
	s := NewServer(CardConfig{Name: "withheld"}, NewRegistry(nil), WithPipeline(engine, registry))
	t.Cleanup(s.Close)

	task, err := sendWithTrace(s, "")
	if err != nil {
		t.Fatal(err)
	}
	var message string
	for _, part := range task.Status.Message {
		message += part.Text
	}
	t.Logf("task %s: %s", task.Status.State, message)
	if task.Status.State != TaskStatusFailed {
		t.Fatalf("the task ended %q, want %q", task.Status.State, TaskStatusFailed)
	}
	if !strings.Contains(message, "content_blocked") || !strings.Contains(message, "data-leakage-prevention") {
		t.Errorf("the task failed with %q; want the guardrail's error (content_blocked, naming the check)", message)
	}
	if strings.Contains(message, "No response from provider") {
		t.Errorf("the task failed as a provider failure: %q", message)
	}
	for _, a := range task.Artifacts {
		for _, p := range a.Parts {
			if strings.Contains(p.Text, "DROP TABLE") {
				t.Errorf("the withheld answer is in the task's artifacts: %q", p.Text)
			}
		}
	}
}
