package openai

import (
	"context"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestResponsesBindingIncludesEndpointAndEffectiveAccount(t *testing.T) {
	binding := func(id string, cfg config.ProviderConfig, ctx context.Context) string {
		t.Helper()
		p, err := New(id, cfg)
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { _ = p.Close() })
		bound, ok := any(p).(interface{ ResponsesBinding(context.Context) string })
		if !ok {
			t.Fatal("Responses provider does not expose a continuation binding")
		}
		value := bound.ResponsesBinding(ctx)
		if value == "" || strings.Contains(value, "fixture") {
			t.Fatal("binding must be nonempty and opaque")
		}
		return value
	}
	ctx := context.Background()
	cfg := config.ProviderConfig{BaseURL: "https://responses.example.invalid", APIKey: "account-fixture-a"}
	want := binding("openai", cfg, ctx)
	if got := binding("openai", cfg, ctx); got != want {
		t.Error("recreating the same provider changed the continuation binding")
	}
	for _, change := range []string{"provider", "host", "path", "credential", "context-credential", "project", "organization"} {
		t.Run(change, func(t *testing.T) {
			changed, id, callCtx := cfg, "openai", ctx
			switch change {
			case "provider":
				id = "other"
			case "host":
				changed.BaseURL = "https://other.example.invalid"
			case "path":
				prefix := "/other-account"
				changed.APIPathPrefix = &prefix
			case "credential":
				changed.APIKey = "account-fixture-b"
			case "context-credential":
				callCtx = models.WithInclusiveCredential(ctx, id, "account-fixture-b")
			case "project":
				changed.Headers = map[string]string{"OpenAI-Project": "project-fixture"}
			case "organization":
				changed.Headers = map[string]string{"OpenAI-Organization": "organization-fixture"}
			}
			if got := binding(id, changed, callCtx); got == want {
				t.Error("different upstream binding accepted the same continuation scope")
			}
		})
	}
}
