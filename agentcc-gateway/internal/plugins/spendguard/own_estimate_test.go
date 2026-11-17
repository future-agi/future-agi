package spendguard

import (
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestOwnEstimateMinuteRefusal(t *testing.T) {
	for _, key := range []string{"", "private-key-id"} {
		for _, estimate := range []int64{10_001, 90_000} {
			t.Run(key+"/"+strings.Repeat("x", int(estimate/10_000)), func(t *testing.T) {
				store, rdb, prefix := testRedis(t)
				g, _ := guardWith(store, config.SpendGuardConfig{MaxUSDPerMinute: 1}, map[string]*tenant.OrgConfig{"private-org-id": capped(0.01, 0)})
				apiErr := g.Admit(bg, "private-org-id", key, estimate, "never-fits")
				if apiErr == nil {
					t.Fatal("oversized estimate admitted")
				}
				w := writeErr(apiErr)
				want := "This request's worst-case cost exceeds the per-minute spending limit. Lower max_tokens to fit within the limit."
				if w.Code != http.StatusTooManyRequests || apiErr.Code != "managed_spend_velocity" || apiErr.Message != want {
					t.Fatalf("own-estimate refusal: %d %s", w.Code, w.Body)
				}
				meta, ok := apiErr.Metadata.(map[string]any)
				if !ok || len(meta) != 1 || meta["reason"] != "request_exceeds_minute_cap" {
					t.Fatalf("fixed reason only, no org/amount/reset data: %#v", apiErr.Metadata)
				}
				if w.Header().Get("Retry-After") != "" {
					t.Fatalf("never-fitting request advertised retry: %s", w.Header())
				}
				for _, private := range []string{"private-org-id", "private-key-id", "$", "0.01", "0.09"} {
					if strings.Contains(w.Body.String(), private) {
						t.Fatalf("value leaked: %s", w.Body)
					}
				}
				if got := bucket(t, rdb, orgMinute(prefix, "private-org-id", t0)); got != 0 {
					t.Fatalf("refusal counted %d", got)
				}
				if err := g.Admit(bg, "private-org-id", key, 10_000, "exact-cap"); err != nil {
					t.Fatalf("exact cap must fit: %v", err)
				}
			})
		}
	}
}

func TestOwnEstimateWindowFullUnchanged(t *testing.T) {
	store, _, _ := testRedis(t)
	g, _ := guardWith(store, config.SpendGuardConfig{MaxUSDPerMinute: 0.01}, nil)
	if err := g.Admit(bg, "org-window", "", 8_000, "first"); err != nil {
		t.Fatal(err)
	}
	apiErr := g.Admit(bg, "org-window", "", 3_000, "second")
	if apiErr == nil {
		t.Fatal("full window admitted")
	}
	w := writeErr(apiErr)
	reset := t0.Truncate(time.Minute).Add(time.Minute).Format(time.RFC3339)
	want := "Your organization's managed spend is at its $0.010000 per-minute cap ($0.008000 counted, including requests in flight). Retry after " + reset + ", or use your own provider keys."
	if apiErr.Message != want || apiErr.Code != "managed_spend_velocity" || apiErr.Type != models.ErrTypeRateLimit || w.Header().Get("Retry-After") != "50" {
		t.Fatalf("ordinary refusal changed: %s %s", w.Header(), w.Body)
	}
	checkErrorContract(t, w)
}

func TestOwnEstimateHourRefusal(t *testing.T) {
	for _, minute := range []float64{0, 1} {
		for _, key := range []string{"", "private-key-id"} {
			store, rdb, prefix := testRedis(t)
			g, _ := guardWith(store, config.SpendGuardConfig{MaxUSDPerMinute: minute, MaxUSDPerHour: 1}, map[string]*tenant.OrgConfig{"private-org-id": capped(0, 0.01)})
			apiErr := g.Admit(bg, "private-org-id", key, 20_000, "never-fits-hour")
			if apiErr == nil {
				t.Fatal("oversized hour estimate admitted")
			}
			w := writeErr(apiErr)
			meta, ok := apiErr.Metadata.(map[string]any)
			if w.Code != http.StatusTooManyRequests || apiErr.Code != "managed_spend_velocity" || !ok || len(meta) != 1 || meta["reason"] != "request_exceeds_hour_cap" {
				t.Fatalf("own-hour refusal must be value-free: %d %s", w.Code, w.Body)
			}
			if w.Header().Get("Retry-After") != "" || strings.Contains(apiErr.Message, "Retry after") {
				t.Fatalf("never-fitting request advertised retry: %s %s", w.Header(), w.Body)
			}
			for _, private := range []string{"private-org-id", "private-key-id", "$", "0.01", "0.02"} {
				if strings.Contains(w.Body.String(), private) {
					t.Fatalf("value leaked: %s", w.Body)
				}
			}
			if got := bucket(t, rdb, orgMinute(prefix, "private-org-id", t0)); got != 0 {
				t.Fatalf("refusal counted %d", got)
			}
			if err := g.Admit(bg, "private-org-id", key, 10_000, "exact-hour-cap"); err != nil {
				t.Fatalf("exact cap must fit: %v", err)
			}
			window := g.Admit(bg, "private-org-id", key, 1, "ordinary-window-full")
			if window == nil || window.RetryAfterSec <= 0 {
				t.Fatalf("ordinary full hour lost retry: %+v", window)
			}
		}
	}
}
