package server

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestManagedAcceptedStreamBeforeFirstChunkIsSettled(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, tc := range []struct {
			model string
			spent int64
		}{{"acme/token", 17}, {"acme/request", 42000}, {"acme/listed:free", 0}} {
			t.Run(surface+tc.model, func(t *testing.T) {
				g := newManagedTestGateway(t, managedOptIn())
				installBillabilityModels(billabilityModels(t))(t, g)
				g.ledger.SetMicros("org-m", 1_000_000)
				accepted := make(chan struct{})
				upstream := upstreamStream(func(w http.ResponseWriter, r *http.Request, _ string) {
					w.Header().Set("Content-Type", "text/event-stream")
					fmt.Fprint(w, ": accepted\n\n")
					w.(http.Flusher).Flush()
					close(accepted)
					<-r.Context().Done()
				})
				g.managed.streamWith.Store(&upstream)
				ts := httptest.NewServer(g.srv.httpServer.Handler)
				defer ts.Close()
				ctx, cancel := context.WithCancel(context.Background())
				defer cancel()
				body := fmt.Sprintf(`{"model":%q,"stream":true,"max_tokens":64,"messages":[{"role":"user","content":"hi"}]}`, tc.model)
				req, _ := http.NewRequestWithContext(ctx, http.MethodPost, ts.URL+surface, strings.NewReader(body))
				req.Header.Set("Authorization", "Bearer "+managedOrgKey)
				req.Header.Set("Content-Type", "application/json")
				req.Header.Set("anthropic-version", "2023-06-01")
				done := make(chan struct{})
				go func() {
					defer close(done)
					resp, _ := http.DefaultClient.Do(req)
					if resp != nil {
						resp.Body.Close()
					}
				}()
				select {
				case <-accepted:
				case <-time.After(5 * time.Second):
					t.Fatal("upstream did not accept")
				}
				// Headers are flushed independently of any model chunk. Give the
				// loopback transport time to deliver them before cancellation.
				time.Sleep(100 * time.Millisecond)
				cancel()
				<-done
				ts.Close()
				spent, held := settledAccount(t, g)
				recs := g.outbox("org-m")
				if spent != tc.spent || held != 0 || g.managed.calls() != 1 || len(recs) != 1 {
					t.Fatalf("spent=%d held=%d calls=%d records=%v; want spent=%d", spent, held, g.managed.calls(), recs, tc.spent)
				}
				if tc.spent > 0 && (recs[0]["kind"] != "spend" || recs[0]["usage_estimated"] != "1" || recs[0]["cost_source"] == "unpriced") {
					t.Fatalf("records=%v", recs)
				}
			})
		}
	}
}

func TestManagedCancellationBeforeDispatchCostsNothing(t *testing.T) {
	for _, surface := range managedSurfaces {
		g := newManagedTestGateway(t, managedOptIn())
		g.ledger.SetMicros("org-m", 1_000_000)
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		req := httptest.NewRequest(http.MethodPost, surface, strings.NewReader(managedMessagesBody(decoratedModel, true, ""))).WithContext(ctx)
		req.Header.Set("Authorization", "Bearer "+managedOrgKey)
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("anthropic-version", "2023-06-01")
		g.srv.httpServer.Handler.ServeHTTP(httptest.NewRecorder(), req)
		spent, held := settledAccount(t, g)
		if g.managed.calls() != 0 || spent != 0 || held != 0 {
			t.Fatalf("%s calls=%d spent=%d held=%d", surface, g.managed.calls(), spent, held)
		}
		for _, rec := range g.outbox("org-m") {
			if rec["kind"] == "spend" {
				t.Fatalf("undispatched spend=%v", rec)
			}
		}
	}
}
