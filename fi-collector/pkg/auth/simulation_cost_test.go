package auth_test

import (
	"context"
	"log/slog"
	"testing"
	"time"

	chexp "github.com/future-agi/future-agi/fi-collector/exporter/clickhouse25exporter"
	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"github.com/future-agi/future-agi/fi-collector/pkg/pricing"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

func TestPostgresSimulationRoutedObserveSpanUsesPerMillionRate(t *testing.T) {
	pool, key, secret := auth.NewSimulationCostFixture(t)
	ctx := context.Background()
	log := slog.Default()
	dsn := pool.Config().ConnString()
	a, err := auth.New(ctx, auth.Config{PGRead: dsn, PGWrite: dsn}, nil, log)
	if err != nil {
		t.Fatal(err)
	}
	defer a.Close()
	a.CheckRoutingColumns(ctx)
	ck := auth.CacheKey(key, secret)
	pricer := pricing.New(&pricing.Table{}, pricing.NewCustomPricing(pool, time.Hour, log))
	// After the cold lookup, exercise both the cached route and a fresh lookup
	// of the same existing hidden project.
	for _, path := range []string{"created", "cached", "existing"} {
		if path == "existing" {
			a.InvalidateKey(ck)
		}
		result, err := a.Authenticate(ctx, key, secret)
		if err != nil {
			t.Fatal(err)
		}
		req := auth.WithResolvedContext(ctx, result, ck)
		traces := ptrace.NewTraces()
		rs := traces.ResourceSpans().AppendEmpty()
		span := rs.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
		span.SetTraceID([16]byte{1})
		span.SetSpanID([8]byte{2})
		span.Attributes().PutStr("gen_ai.request.model", "custom-chat")
		span.Attributes().PutInt("gen_ai.usage.input_tokens", 1_000_000)
		span.Attributes().PutStr("user.id", "fixture-user")
		out, err := a.RouteSimulation(req, ck, traces, result)
		if err != nil || out.Hidden == "" {
			t.Fatalf("%s route: %+v, err=%v", path, out, err)
		}
		if dropped, err := auth.StampResourceAttrs(req, a, ck, traces, result); err != nil || dropped != 0 {
			t.Fatalf("%s stamp: dropped=%d err=%v", path, dropped, err)
		}
		rows, _, err := chexp.ConvertWithIdentities(req, traces, pricer)
		if err != nil || len(rows) != 1 {
			t.Fatalf("%s convert: rows=%d err=%v", path, len(rows), err)
		}
		if cost := rows[0]["cost"]; cost != 2.50 {
			t.Errorf("%s simulation cost = %v; want 2.50 USD", path, cost)
		}
		if rows[0]["end_user_id"] != nil {
			t.Errorf("%s: pricing changed end-user linkage", path)
		}
	}
}
