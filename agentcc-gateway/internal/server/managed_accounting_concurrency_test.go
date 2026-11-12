package server

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// Eight requests compete for exactly two request fees. Real upstream usage
// before an error consumes credit, and every retry competes atomically for
// another hold. Neither the memory nor Redis ledger may dispatch a third call.
func TestManagedAccountingConcurrentRetryFunding(t *testing.T) {
	for _, backend := range []string{"memory", "redis"} {
		t.Run(backend, func(t *testing.T) {
			g := newManagedTestGateway(t, managedOptIn())
			installBillabilityModels(billabilityModels(t))(t, g)
			if backend == "redis" {
				prefix := fmt.Sprintf("test-billfix-%d:", time.Now().UnixNano())
				client := r7AgentRedis(t, prefix)
				ledger := creditsplugin.NewOrgLedger(redisstate.NewManagedCreditStore(client, prefix))
				g.ledger = ledger
				g.credits.SetOrgLedger(ledger, true)
				g.srv.EnableManaged(ManagedSettings{Catalog: g.srv.handlers.catalog, Ledger: ledger, MarkupPercent: 5, RequireCredits: true})
				if _, err := ledger.Grant(context.Background(), "org-m", billing.GrantPush{GrantedTotalMicros: 84000, Version: 1}); err != nil {
					t.Fatal(err)
				}
			} else {
				g.ledger.SetMicros("org-m", 84000)
			}
			up := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
				w.Header().Set("Content-Type", "text/event-stream")
				writeManagedChunk(w, model, `"choices":[],"usage":{"prompt_tokens":0,"completion_tokens":0,"cost":0.04}`)
				fmt.Fprint(w, "data: {\"error\":{\"message\":\"upstream unavailable\",\"code\":502}}\n\n")
			})
			g.managed.streamWith.Store(&up)
			ts := httptest.NewServer(g.srv.httpServer.Handler)
			defer ts.Close()
			client := &http.Client{Timeout: 10 * time.Second}
			start := make(chan struct{})
			var workers sync.WaitGroup
			for i := 0; i < 8; i++ {
				workers.Add(1)
				go func() {
					defer workers.Done()
					<-start
					req, _ := http.NewRequest(http.MethodPost, ts.URL+"/v1/messages", strings.NewReader(managedMessagesBody("acme/request", true, "")))
					req.Header.Set("Authorization", "Bearer "+managedOrgKey)
					req.Header.Set("Content-Type", "application/json")
					req.Header.Set("anthropic-version", "2023-06-01")
					resp, err := client.Do(req)
					if err != nil {
						t.Error(err)
						return
					}
					io.Copy(io.Discard, resp.Body)
					resp.Body.Close()
					if resp.StatusCode < 400 {
						t.Errorf("upstream failure became success: %d", resp.StatusCode)
					}
				}()
			}
			close(start)
			workers.Wait()
			ts.Close()
			spent, held := settledAccount(t, g)
			account := g.account(t, "org-m")
			if g.managed.calls() != 2 || spent != 84000 || held != 0 || account.AvailableMicros() != 0 {
				t.Fatalf("calls=%d spent=%d held=%d available=%d", g.managed.calls(), spent, held, account.AvailableMicros())
			}
		})
	}
}
