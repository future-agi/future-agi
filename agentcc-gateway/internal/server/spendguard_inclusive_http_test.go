package server

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	"github.com/futureagi/agentcc-gateway/internal/plugins/spendguard"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

func TestSpendGuardInclusiveHTTP(t *testing.T) {
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Skip("TEST_REDIS_ADDR is required for the real spend guard")
	}
	host, _, err := net.SplitHostPort(addr)
	if err != nil || (host != "localhost" && !net.ParseIP(host).IsLoopback()) {
		t.Fatal("spend guard test requires loopback Redis")
	}
	for _, tc := range []struct {
		name        string
		minute      float64
		established bool
		status      int
		reason      string
		router      bool
	}{
		{"minute-356", 356, true, http.StatusTooManyRequests, "request_exceeds_minute_cap", false},
		{"minute-357", 357, true, http.StatusOK, "", false},
		{"hour-356", 357, true, http.StatusTooManyRequests, "request_exceeds_hour_cap", false},
		{"fresh-org-25", 357, false, http.StatusTooManyRequests, "new_org_daily_cap", false},
		{"router-minute-356", 356, true, http.StatusForbidden, "", true},
		{"router-minute-357", 357, true, http.StatusForbidden, "", true},
		{"router-fresh-org-25", 357, false, http.StatusForbidden, "", true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			g, owner, _ := cat2OperatorGateway(t, true)
			model := "openrouter/auto"
			if !tc.router {
				unionSpendFixture(t, g)
				model = unionSpendModel
			}
			prefix := fmt.Sprintf("test:cat2-sg:%d:", time.Now().UnixNano())
			store := redisstate.NewSpendGuardStore(r7AgentRedis(t, prefix), prefix)
			hour := 3570.0
			if tc.name == "hour-356" {
				hour = 356
			}
			guard := spendguard.NewGuard(store, config.SpendGuardConfig{
				MaxUSDPerMinute: tc.minute, MaxUSDPerHour: hour, NewOrgDailyCapUSD: 25,
			})
			if tc.established {
				v, err := store.Admit(t.Context(), redisstate.SpendGuardAdmission{
					Org: "org-m", HoldID: "first-seen", EstimateMicros: 1,
					Limits: redisstate.SpendGuardLimits{NewOrgDailyMicros: 25_000_000},
					Now:    time.Now().Add(-8 * 24 * time.Hour),
				})
				if err != nil || v.Outcome != redisstate.SpendGuardAdmitted {
					t.Fatalf("established org: %+v %v", v, err)
				}
			}
			h := g.srv.handlers
			est := catalogueSpendEstimator(g)
			var estimate, estimates atomic.Int64
			sg := spendguard.New(guard, func(rc *models.RequestContext) int64 {
				v := est.Estimate(rc)
				estimate.Store(v)
				estimates.Add(1)
				return v
			})
			cost := costplugin.New(true, h.ModelDB, g.tenants, nil)
			cost.SetManagedMarkup(5)
			cost.SetPricer(costplugin.NewPricer(h.ModelDB, func() models.PriceLookup { return h.Deps().Prices }))
			if !(cost.Priority() < sg.Priority() && sg.Priority() < g.credits.Priority()) {
				t.Fatal("production order must be cost, spendguard, credits")
			}
			*g.srv.engine = *pipeline.NewEngine(cost, sg, g.credits)
			if h.engine.PluginCount() != 3 {
				t.Fatal("HTTP handlers did not receive the real plugin pipeline")
			}
			var held atomic.Int64
			answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, served string) {
				account, err := owner.Account(context.Background(), "org-m")
				if err != nil {
					t.Error(err)
				}
				held.Store(account.HeldMicros)
				r601Response("0.0000095", false)(w, r, served)
			})
			g.managed.responseWith.Store(&answer)
			before, err := owner.Account(context.Background(), "org-m")
			if err != nil {
				t.Fatal(err)
			}
			beforeEvents := len(owner.Outbox("org-m"))
			srv := httptest.NewServer(g.srv.httpServer.Handler)
			defer srv.Close()
			body, _ := json.Marshal(catalogueSpendRequest(model, 64, 65536, 1))
			req, _ := http.NewRequest(http.MethodPost, srv.URL+"/v1/chat/completions", strings.NewReader(string(body)))
			req.Header.Set("Authorization", "Bearer "+managedOrgKey)
			req.Header.Set("Content-Type", "application/json")
			resp, err := srv.Client().Do(req)
			if err != nil {
				t.Fatal(err)
			}
			defer resp.Body.Close()
			b, err := io.ReadAll(resp.Body)
			if err != nil {
				t.Fatal(err)
			}
			wantCalls, wantEstimate := int64(1), unionSpendBound
			if tc.router {
				wantCalls, wantEstimate = 0, 0
			}
			if estimates.Load() != wantCalls || estimate.Load() != wantEstimate {
				t.Errorf("guard calls=%d estimate=%d want=%d/%d", estimates.Load(), estimate.Load(), wantCalls, wantEstimate)
			}
			if resp.StatusCode != tc.status {
				t.Errorf("status=%d want=%d body=%s", resp.StatusCode, tc.status, b)
			}
			if tc.status == http.StatusOK {
				if g.managed.calls() != 1 {
					t.Fatalf("loopback provider calls=%d want=1", g.managed.calls())
				}
				if held.Load() != unionSpendBound {
					t.Errorf("in-flight credit hold=%d want exact bound=%d", held.Load(), unionSpendBound)
				}
				after, err := owner.Account(context.Background(), "org-m")
				if err != nil || after.HeldMicros != 0 || after.SpentMicros-before.SpentMicros != 11 || len(owner.Outbox("org-m")) != beforeEvents+1 {
					t.Errorf("settlement differs from exact 11-micro single debit: %+v error=%v", after, err)
				}
				return
			}
			after, err := owner.Account(context.Background(), "org-m")
			if err != nil || !reflect.DeepEqual(before, after) || len(owner.Outbox("org-m")) != beforeEvents || g.managed.calls() != 0 {
				t.Errorf("refusal changed ledger or reached provider: before=%+v after=%+v events=%d calls=%d err=%v",
					before, after, len(owner.Outbox("org-m")), g.managed.calls(), err)
			}
			var doc struct {
				Error struct {
					Code     string         `json:"code"`
					Message  string         `json:"message"`
					Metadata map[string]any `json:"metadata"`
				} `json:"error"`
			}
			if err := json.Unmarshal(b, &doc); err != nil {
				t.Fatal(err)
			}
			if tc.router {
				if doc.Error.Code != "managed_router_unbounded" || held.Load() != 0 || estimates.Load() != 0 || resp.Header.Get("Retry-After") != "" {
					t.Errorf("router must refuse before estimator/reserves: %s", b)
				}
				return
			}
			if doc.Error.Code != "managed_spend_velocity" || !reflect.DeepEqual(doc.Error.Metadata, map[string]any{"reason": tc.reason}) || resp.Header.Get("Retry-After") != "" {
				t.Errorf("wrong value-free refusal: %s", b)
			}
			for _, field := range []string{"required_usd", "available_usd", "cap_usd", "counted_usd", "estimate_micros"} {
				if _, found := doc.Error.Metadata[field]; found {
					t.Errorf("refusal leaks amount field %s", field)
				}
			}
			if strings.ContainsAny(doc.Error.Message, "$0123456789") {
				t.Errorf("own-request refusal must be value-free: %s", doc.Error.Message)
			}
		})
	}
}
