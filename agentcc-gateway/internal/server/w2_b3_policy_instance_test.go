package server

import (
	"encoding/json"
	"fmt"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/providers/gemini"
	"github.com/futureagi/agentcc-gateway/internal/providers/openai"
)

// A resolved model fallback's pointer takes precedence over the primary's
// provider ID and org markers, even when both instances use the same ID.
func TestW2B3ResolvedFallbackInstance(t *testing.T) {
	srv, _ := w2B3SelectedServer(t, true, false)
	h := srv.handlers
	org := h.tenantStore.Get("org-w2-b3")
	router, err := openai.New("custom", config.ProviderConfig{APIFormat: "openai", Type: "openrouter", BaseURL: "https://openrouter.invalid/api"})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = router.Close() })
	native, err := gemini.New("custom", config.ProviderConfig{APIFormat: "gemini", BaseURL: "https://native.invalid"})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = native.Close() })
	for _, selected := range []struct {
		name   string
		p      providers.Provider
		router bool
	}{{"router", router, true}, {"native", native, false}} {
		for _, routerHistory := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/router-history=%t", selected.name, routerHistory), func(t *testing.T) {
				call := `{"id":"call_weather::sig::synthetic-signature","type":"function","function":{"name":"weather","arguments":"{}"}}`
				if routerHistory {
					call = `{"id":"call_weather","type":"function","function":{"name":"weather","arguments":"{}"},"extra_content":{"google":{"thought_signature":"synthetic-signature"}}}`
				}
				var req models.ChatCompletionRequest
				if err := json.Unmarshal([]byte(`{"model":"gemini-3.5-flash","messages":[{"role":"assistant","tool_calls":[`+call+`]}]}`), &req); err != nil {
					t.Fatal(err)
				}
				rc := models.AcquireRequestContext()
				defer rc.Release()
				rc.Model, rc.Provider, rc.Request = req.Model, "custom", &req
				rc.Metadata["key_type"], rc.Metadata["org_id"] = "internal", "org-w2-b3"
				rc.Metadata["billing_mode"] = billingBYOK
				got := h.fallbackHopAllowed(rc, org, "custom", req.Model, selected.p)
				if got != (routerHistory == selected.router) {
					t.Fatalf("resolved fallback allowed=%t; selected=%s router-history=%t", got, selected.name, routerHistory)
				}
			})
		}
	}
}

func TestW2B3CandidateInstance(t *testing.T) {
	for _, native := range []bool{false, true} {
		for _, actualOrg := range []bool{false, true} {
			t.Run(fmt.Sprintf("native=%t/org=%t", native, actualOrg), func(t *testing.T) {
				srv, _ := w2B3SelectedServer(t, native, actualOrg)
				rc := models.AcquireRequestContext()
				defer rc.Release()
				rc.Model = "auto"
				rc.Metadata["key_type"], rc.Metadata["org_id"] = "internal", "org-w2-b3"
				org := srv.handlers.tenantStore.Get("org-w2-b3")
				got := srv.handlers.aggregatorProvider(rc, org, "custom", "gemini-3.5-flash", "gemini-3.5-flash", true)
				if got != !native {
					t.Fatalf("candidate router=%t, want %t", got, !native)
				}
			})
		}
	}
}
