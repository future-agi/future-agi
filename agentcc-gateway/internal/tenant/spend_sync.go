package tenant

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"sync/atomic"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
)

// httpClient is reused across calls to keep TCP connections alive.
var httpClient = &http.Client{Timeout: 30 * time.Second}

// SpendSummary mirrors the Django spend-summary endpoint response.
type SpendSummary struct {
	Period      string                     `json:"period"`
	PeriodStart string                     `json:"period_start"`
	Orgs        map[string]OrgSpendSummary `json:"orgs"`
}

// OrgSpendSummary holds aggregated spend for a single org.
type OrgSpendSummary struct {
	TotalSpend float64            `json:"total_spend"`
	PerKey     map[string]float64 `json:"per_key"`
	PerUser    map[string]float64 `json:"per_user"`
	PerModel   map[string]float64 `json:"per_model"`
}

// SyncSpendFromControlPlane fetches the current-period spend summary
// from the Django backend so the gateway can seed its budget counters.
func SyncSpendFromControlPlane(ctx context.Context, baseURL, adminToken, period string) (*SpendSummary, error) {
	if baseURL == "" {
		return nil, nil
	}

	endpoint := baseURL + "/agentcc/spend-summary/?period=" + period

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return nil, fmt.Errorf("building spend sync request: %w", err)
	}
	if adminToken != "" {
		req.Header.Set("Authorization", "Bearer "+adminToken)
	}

	resp, err := httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("spend sync: control plane unreachable: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 1024))
		return nil, fmt.Errorf("spend sync: status %d: %s", resp.StatusCode, body)
	}

	var envelope struct {
		Status bool         `json:"status"`
		Result SpendSummary `json:"result"`
	}
	if err := json.NewDecoder(io.LimitReader(resp.Body, 10<<20)).Decode(&envelope); err != nil {
		return nil, fmt.Errorf("spend sync: parsing response: %w", err)
	}
	if !envelope.Status {
		return nil, fmt.Errorf("spend sync: response status=false")
	}

	slog.Info("spend sync completed", "orgs", len(envelope.Result.Orgs), "period", envelope.Result.Period)
	return &envelope.Result, nil
}

// StartPeriodicSync runs SyncFromControlPlane (and optionally key sync)
// on a timer. It blocks until ctx is cancelled — call it in a goroutine.
func StartPeriodicSync(ctx context.Context, interval time.Duration, baseURL, adminToken string, store *Store, keyStore *auth.KeyStore) {
	runPeriodicSync(ctx, interval, baseURL, adminToken, store, keyStore, nil)
}

// runPeriodicSync is StartPeriodicSync. The org and key syncs each log a
// failure at INFO unless it is the second or later in a row, when it is WARN:
// one missed tick is a backend restart (Standalone stops the API before the
// gateway; a Distributed or Helm rollout), two are an outage. With loaded
// (shared with the startup sync), a half's failures are all INFO until that
// half has loaded, here or in the startup sync; without it, both halves count
// as loaded from the start. A sync cut off by ctx ending (shutdown) did not
// fail and is not logged.
func runPeriodicSync(ctx context.Context, interval time.Duration, baseURL, adminToken string, store *Store, keyStore *auth.KeyStore, loaded *syncLoaded) {
	if interval <= 0 || baseURL == "" {
		return
	}
	if loaded == nil {
		loaded = &syncLoaded{}
		loaded.orgs.Store(true)
		loaded.keys.Store(true)
	}
	orgs := periodicFailures{loaded: &loaded.orgs}
	keys := periodicFailures{loaded: &loaded.keys}

	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	slog.Info("periodic control plane sync started", "interval", interval.String())

	for {
		select {
		case <-ctx.Done():
			slog.Info("periodic control plane sync stopped")
			return
		case <-ticker.C:
			syncCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
			if err := SyncFromControlPlane(syncCtx, baseURL, adminToken, store); err != nil {
				orgs.failed(ctx, "periodic sync failed", err)
			} else {
				orgs.succeeded()
				slog.Debug("periodic sync completed", "orgs", store.Count())
			}
			if keyStore != nil {
				if err := auth.SyncKeysFromControlPlane(syncCtx, baseURL, adminToken, keyStore); err != nil {
					keys.failed(ctx, "periodic key sync failed", err)
				} else {
					keys.succeeded()
				}
			}
			cancel()
		}
	}
}

// syncLoaded records which halves of the control plane sync have loaded. The
// startup and periodic syncs share one, so a half the startup sync loaded
// counts as loaded for the periodic sync too.
type syncLoaded struct {
	orgs, keys atomic.Bool
}

// periodicFailures tracks one half of the periodic sync: whether it has
// loaded, and how many times in a row it has failed.
type periodicFailures struct {
	loaded *atomic.Bool
	inARow int
}

// failed counts a failure and logs it as msg, unless ctx has ended.
func (f *periodicFailures) failed(ctx context.Context, msg string, err error) {
	if ctx.Err() != nil {
		return
	}
	f.inARow++
	level := slog.LevelInfo
	if f.loaded.Load() && f.inARow >= 2 {
		level = slog.LevelWarn
	}
	slog.Log(ctx, level, msg, "error", err, "failures_in_a_row", f.inARow)
}

func (f *periodicFailures) succeeded() {
	f.loaded.Store(true)
	f.inARow = 0
}
