package server

import (
	"fmt"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestManagedBillabilitySupportedPaths(t *testing.T) {
	for _, id := range []string{"acme/token", "acme/request", "acme/token:nitro", "acme/mixed-endpoints", "acme/listed:free", "openrouter/free", "acme/endpoint:free"} {
		for _, surface := range managedSurfaces {
			for _, path := range []string{"plain", "prefix", "alias", "lock", "auto", "fallback"} {
				if path == "alias" && strings.Contains(id, ":") || path == "lock" && surface == "/v1/messages" {
					continue
				}
				for _, stream := range []bool{false, true} {
					t.Run(fmt.Sprintf("%s/%s/%s/stream=%t", id, surface, path, stream), func(t *testing.T) {
						cfg := managedOptIn()
						if path == "auto" {
							cfg.Routing = &tenant.RoutingConfig{Auto: &tenant.AutoRoutingConfig{Candidates: []string{id}}}
						}
						g := newManagedTestGateway(t, cfg)
						all := billabilityModels(t)
						primary := *all["acme/token"]
						primary.ID = "acme/primary"
						all[primary.ID] = &primary
						installBillabilityModels(all)(t, g)
						g.ledger.SetMicros("org-m", 1_000_000)
						free := strings.HasSuffix(id, ":free") || id == "openrouter/free"
						if free {
							zero := 0.0
							g.managed.reportedCost.Store(&zero)
						}
						name, extra := id, ""
						switch path {
						case "prefix":
							name = "openrouter/" + id
						case "alias":
							_, name, _ = strings.Cut(id, "/")
						case "auto":
							name = "auto"
						case "fallback":
							name = primary.ID
							extra = fmt.Sprintf(`,"models":[%q]`, id)
							g.managed.failFor.Store(primary.ID, 500)
						}
						var status int
						if path == "lock" {
							status, _, _ = sendManaged(t, g, surface, name, stream, false, managedLock)
						} else {
							body := fmt.Sprintf(`{"model":%q,"stream":%t,"max_tokens":64,"messages":[{"role":"user","content":"hi"}]%s}`, name, stream, extra)
							status = sendBillabilityBody(t, g, surface, body)
						}
						spent, held := settledAccount(t, g)
						recs := g.outbox("org-m")
						// Auto's token-price scorer does not rank request-only or free
						// cards. Messages now runs org auto/fallback routing, but its
						// caller-supplied models[] remains explicitly unsupported.
						if path == "auto" && (free || id == "acme/request") || surface == "/v1/messages" && path == "fallback" {
							if status < 400 || g.managed.calls() != 0 || spent != 0 || held != 0 || len(recs) != 0 {
								t.Fatalf("unsupported route: status=%d calls=%d spend=%d held=%d records=%v", status, g.managed.calls(), spent, held, recs)
							}
							return
						}
						wantCalls := 1
						if path == "fallback" {
							wantCalls = 2
						}
						wantSpend := int64(7350)
						kind := "spend"
						if free {
							wantSpend = 0
							kind = "void"
						}
						if status != http.StatusOK || g.managed.calls() != wantCalls || g.managed.lastModel() != id || spent != wantSpend || held != 0 || len(recs) != 1 {
							t.Fatalf("status=%d calls=%d sent=%s spent=%d held=%d records=%v", status, g.managed.calls(), g.managed.lastModel(), spent, held, recs)
						}
						for _, auth := range g.managed.auths {
							if auth != "Bearer fagi-openrouter-key" {
								t.Fatalf("credential=%s", auth)
							}
						}
						if recs[0]["kind"] != kind || recs[0]["cost_source"] != "provider_reported" || recs[0]["usage_estimated"] == "1" {
							t.Fatalf("records=%v", recs)
						}
					})
				}
			}
		}
	}
}
