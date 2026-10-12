package main

import (
	"context"
	"log/slog"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"
	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"github.com/future-agi/future-agi/fi-collector/pkg/server"
	"github.com/redis/go-redis/v9"
)

// Commercial quotas (Future AGI Cloud's free-tier hard caps and budget
// pauses) stay on unless COMMERCIAL_QUOTAS_ENABLED turns them off, so Cloud's
// collector needs no new setting; every shipped self-hosted manifest sets it
// to false (TH-8084, B1).
func TestCommercialQuotasAreOnUnlessDisabled(t *testing.T) {
	for _, tc := range []struct {
		value string
		want  bool
	}{
		{"", true}, {"true", true}, {"TRUE", true}, {"1", true}, {"yes", true}, {"on", true},
		{"false", false}, {"False", false}, {"0", false}, {"no", false}, {"off", false}, {" OFF ", false},
	} {
		clearCollectorEnv(t)
		t.Setenv("COMMERCIAL_QUOTAS_ENABLED", tc.value)
		cfg := rootConfig{}
		if err := applyEnvOverrides(slog.Default(), &cfg); err != nil {
			t.Fatal(err)
		}
		if cfg.Auth.CommercialQuotasOn() != tc.want {
			t.Errorf("COMMERCIAL_QUOTAS_ENABLED=%q: quotas %v, want %v", tc.value, cfg.Auth.CommercialQuotasOn(), tc.want)
		}
	}
}

// A typo must not silently pick a side: the collector refuses to start.
func TestCommercialQuotasRejectsUnknownValues(t *testing.T) {
	for _, bad := range []string{"flase", "disabled", "2"} {
		clearCollectorEnv(t)
		t.Setenv("COMMERCIAL_QUOTAS_ENABLED", bad)
		if err := applyEnvOverrides(slog.Default(), &rootConfig{}); err == nil {
			t.Errorf("COMMERCIAL_QUOTAS_ENABLED=%q must be rejected", bad)
		}
	}
}

// staleCloudRedis seeds the keys a self-hosted install can carry over from
// Cloud: a free plan, a usage counter far past the free allowance, and a
// budget pause.
func staleCloudRedis(t *testing.T) *redis.Client {
	t.Helper()
	mr := miniredis.RunT(t)
	rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	t.Cleanup(func() { rdb.Close() })
	period := time.Now().UTC().Format("2006-01")
	mr.Set("plan:org-stale", "free")
	mr.Set("usage:org-stale:tracing_events:"+period, "999999999")
	mr.Set("pause:org-stale:tracing_events", "1")
	return rdb
}

func TestCommercialQuotasOffIgnoresStaleCloudKeys(t *testing.T) {
	off := false
	m := newMetering(auth.Config{CommercialQuotas: &off}, staleCloudRedis(t), nil, slog.Default())
	if _, ok := m.(server.NoopMetering); !ok {
		t.Fatalf("COMMERCIAL_QUOTAS_ENABLED=false must not construct metering, got %T", m)
	}
	if r := m.CheckUsage(context.Background(), "org-stale", "tracing_event", 1); !r.Allowed {
		t.Fatalf("stale usage:/pause: keys capped a self-hosted install: %+v", r)
	}
}

func TestCommercialQuotasUnsetKeepsCloudMetering(t *testing.T) {
	m := newMetering(auth.Config{}, staleCloudRedis(t), nil, slog.Default())
	if _, ok := m.(*auth.Metering); !ok {
		t.Fatalf("unset COMMERCIAL_QUOTAS_ENABLED must keep Cloud metering, got %T", m)
	}
	if r := m.CheckUsage(context.Background(), "org-stale", "tracing_event", 1); r.Allowed {
		t.Fatal("Cloud metering must still enforce the free-tier cap")
	}
}

func TestNoRedisMeansNoMetering(t *testing.T) {
	if _, ok := newMetering(auth.Config{}, nil, nil, slog.Default()).(server.NoopMetering); !ok {
		t.Fatal("without Redis the collector must not meter")
	}
}
