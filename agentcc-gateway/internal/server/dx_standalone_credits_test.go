package server

import (
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
)

func TestDXR2StandaloneCreditsGuidance(t *testing.T) {
	// These are the actual mounts of docker-compose.yml and the Standalone
	// E2E stack. Neither install includes the cloud credit checkout module.
	for _, path := range []string{"../../config.example.yaml", "../../../e2e/stack/gateway.e2e.yaml", "../../../e2e/stack/gateway.r7alt.yaml"} {
		t.Run(path, func(t *testing.T) {
			cfg, err := config.Load(path)
			if err != nil {
				t.Fatal(err)
			}
			if !cfg.Managed.GrantOnlyCredits {
				t.Error("Standalone config must explicitly select operator-granted credit")
			}
			g := newManagedTestGateway(t, managedOptIn())
			settings := creditsplugin.DefaultSettings()
			settings.GrantOnlyCredits = cfg.Managed.GrantOnlyCredits
			g.credits.SetSettings(settings)
			g.grant(t, "org-m", 0)
			w := g.chat(t, chatBody("anthropic/claude-sonnet-5.5"), nil)
			got := errorObject(t, w)
			want := "Your organization has no Future AGI credit. Credit purchase is not available on this self-hosted install, so credit is granted by its operator: ask them to grant Future AGI credit, or use your own provider keys on the Gateway Providers page."
			if w.Code != http.StatusPaymentRequired || got["code"] != "managed_credits_exhausted" || got["type"] != "payment_required" || got["message"] != want {
				t.Fatalf("Standalone answer %d %s; want 402 managed_credits_exhausted payment_required %q", w.Code, w.Body.String(), want)
			}
		})
	}
}
