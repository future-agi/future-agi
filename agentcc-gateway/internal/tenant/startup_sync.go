package tenant

import (
	"context"
	"errors"
	"log/slog"
	"math/rand/v2"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
)

// Startup sync schedule. The control plane is usually still starting when the
// gateway boots (a first boot can spend minutes on migrations), so failed
// attempts are expected: they log at INFO, and only an outage longer than
// startupSyncWarnAfter logs a WARN, repeated at most every
// startupSyncWarnEvery. Each retry waits within 10% of the backoff (see
// jittered). Variables so tests can shorten them.
var (
	startupSyncFirstRetry = 2 * time.Second
	startupSyncMaxRetry   = 30 * time.Second
	startupSyncWarnAfter  = 2 * time.Minute
	startupSyncWarnEvery  = 5 * time.Minute
	startupSyncTimeout    = 10 * time.Second // per request
)

// syncOnStartup loads org configs and API keys from the control plane,
// retrying with capped exponential backoff until both have loaded or ctx ends,
// and marks each half in loaded as it loads. It does not give up on its own:
// until it succeeds, the gateway serves only its config.yaml keys. Reports
// whether the sync completed.
func syncOnStartup(ctx context.Context, baseURL, adminToken string, store *Store, keyStore *auth.KeyStore, loaded *syncLoaded) bool {
	start := time.Now()
	var lastWarn time.Time
	wait := startupSyncFirstRetry
	orgsSynced := false
	keysSynced := keyStore == nil

	for attempt := 1; ; attempt++ {
		var errs []error
		if !orgsSynced {
			reqCtx, cancel := context.WithTimeout(ctx, startupSyncTimeout)
			if err := SyncFromControlPlane(reqCtx, baseURL, adminToken, store); err != nil {
				errs = append(errs, err)
			} else {
				orgsSynced = true
				loaded.orgs.Store(true)
			}
			cancel()
		}
		if !keysSynced {
			reqCtx, cancel := context.WithTimeout(ctx, startupSyncTimeout)
			if err := auth.SyncKeysFromControlPlane(reqCtx, baseURL, adminToken, keyStore); err != nil {
				errs = append(errs, err)
			} else {
				keysSynced = true
				loaded.keys.Store(true)
			}
			cancel()
		}

		if orgsSynced && keysSynced {
			keyCount := 0
			if keyStore != nil {
				keyCount = keyStore.Count()
			}
			slog.Info("control plane startup sync succeeded",
				"attempts", attempt, "after", time.Since(start).Round(time.Second).String(),
				"orgs", store.Count(), "keys", keyCount)
			return true
		}
		if ctx.Err() != nil {
			return false
		}

		err := errors.Join(errs...)
		sleep := jittered(wait)
		failingFor := time.Since(start)
		if failingFor >= startupSyncWarnAfter && (lastWarn.IsZero() || time.Since(lastWarn) >= startupSyncWarnEvery) {
			lastWarn = time.Now()
			slog.Warn("control plane sync still failing: "+startupSyncImpact(orgsSynced, keysSynced)+"; still retrying",
				"failing_for", failingFor.Round(time.Second).String(), "attempts", attempt,
				"need_orgs", !orgsSynced, "need_keys", !keysSynced, "error", err)
		} else {
			slog.Info("control plane not ready yet, retrying startup sync",
				"attempt", attempt, "retry_in", sleep.Round(time.Millisecond).String(),
				"need_orgs", !orgsSynced, "need_keys", !keysSynced, "error", err)
		}

		timer := time.NewTimer(sleep)
		select {
		case <-ctx.Done():
			timer.Stop()
			return false
		case <-timer.C:
		}
		wait = min(wait*2, startupSyncMaxRetry)
	}
}

// startupSyncImpact says what the gateway lacks while the startup sync fails.
func startupSyncImpact(orgsSynced, keysSynced bool) string {
	switch {
	case !orgsSynced && !keysSynced:
		return "the gateway serves only its config.yaml keys until keys and org settings load from the app"
	case !keysSynced:
		return "the gateway serves only its config.yaml keys until keys load from the app"
	default:
		return "the gateway runs without org settings until they load from the app"
	}
}

// jittered returns d spread by up to 10% either way, so gateway replicas
// started together do not retry the control plane in lockstep.
func jittered(d time.Duration) time.Duration {
	spread := d / 5
	if spread <= 0 {
		return d
	}
	return d - spread/2 + rand.N(spread)
}

// RunControlPlaneSync runs the startup sync when startup is set, and the
// periodic re-sync every interval (0 = none), until ctx ends. The periodic
// re-sync starts right away, so what has loaded is re-synced even while the
// rest keeps failing. Next to a startup sync it logs a half's failures at INFO
// until that half first loads, in either sync: until then the startup sync is
// the one that warns about a long outage. Blocks; run it in a goroutine.
func RunControlPlaneSync(ctx context.Context, startup bool, interval time.Duration, baseURL, adminToken string, store *Store, keyStore *auth.KeyStore) {
	if !startup {
		StartPeriodicSync(ctx, interval, baseURL, adminToken, store, keyStore)
		return
	}
	loaded := &syncLoaded{}
	periodic := make(chan struct{})
	go func() {
		defer close(periodic)
		runPeriodicSync(ctx, interval, baseURL, adminToken, store, keyStore, loaded)
	}()
	syncOnStartup(ctx, baseURL, adminToken, store, keyStore, loaded)
	<-periodic
}
