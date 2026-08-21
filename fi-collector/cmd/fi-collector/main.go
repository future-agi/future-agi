// Command fi-collector — OTLP gRPC receiver → CH 25.3 spans writer.
//
// Operating modes:
//   - Standalone Docker (`docker-compose.standalone.yml`): runs as its own
//     service in front of a CH 25.3 cluster. The default.
//   - Embedded (planned): exposes a Go-API NewEmbedded() so the Django
//     `web` container can fork this in-process for single-binary deploys.
//     Out of scope for the first cut.
//
// Config priority (later overrides earlier):
//  1. Defaults coded into chwriter.New / server.New
//  2. YAML file path from --config (or /etc/fi-collector/config.yaml)
//  3. Environment overrides (FI_CH_URL, FI_GRPC_ADDR, FI_HTTP_ADDR,
//     FI_GRPC_MAX_RECV_MIB, FI_DEAD_LETTER_FILE, ...)
//
// Health surfaces (internal-only admin listener, default 127.0.0.1:9464,
// configurable via admin.addr or FI_ADMIN_ADDR):
//   - /healthz (HTTP 200 unless writer dead-letter rate > threshold)
//   - /metrics (Prometheus text exposition of writer + Go runtime stats)
//   - Structured logs on stderr (JSON lines)
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"runtime"
	"strconv"
	"syscall"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/pricing"
	"github.com/future-agi/future-agi/fi-collector/pkg/server"
	"github.com/redis/go-redis/v9"
	"gopkg.in/yaml.v3"
)

type adminConfig struct {
	Addr string `yaml:"addr"`
}

type rootConfig struct {
	Writer chwriter.Config `yaml:"writer"`
	Server server.Config   `yaml:"server"`
	Auth   auth.Config     `yaml:"auth"`
	Admin  adminConfig     `yaml:"admin"`
}

// defaultAdminAddr is the internal-only admin listener. Loopback by
// default: /healthz and /metrics are for the local host / container health
// checks, not for external scraping, so the default must not bind on
// 0.0.0.0. Deployments that need it on the wire set admin.addr (or
// FI_ADMIN_ADDR) explicitly.
const defaultAdminAddr = "127.0.0.1:9464"

// resolveAdminAddr returns the configured admin listener address, falling
// back to defaultAdminAddr when admin.addr is unset.
func resolveAdminAddr(cfg rootConfig) string {
	if cfg.Admin.Addr != "" {
		return cfg.Admin.Addr
	}
	return defaultAdminAddr
}

func main() {
	var configPath string
	flag.StringVar(&configPath, "config", "/etc/fi-collector/config.yaml", "path to YAML config")
	flag.Parse()

	log := slog.New(slog.NewJSONHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelInfo}))

	cfg := loadConfig(log, configPath)
	applyEnvOverrides(log, &cfg)

	writer, err := chwriter.New(cfg.Writer)
	if err != nil {
		log.Error("chwriter init failed", "err", err)
		os.Exit(1)
	}
	defer writer.Close()

	if !cfg.Auth.IsEnabled() {
		log.Error("FI_PG_WRITE is required — without it the collector cannot resolve API keys or project IDs")
		os.Exit(1)
	}

	var rdb *redis.Client
	if cfg.Auth.RedisAddr != "" {
		rdb = redis.NewClient(&redis.Options{Addr: cfg.Auth.RedisAddr})
		defer rdb.Close()
	} else {
		log.Warn("FI_AUTH_REDIS_ADDR not set — quota enforcement, usage metering, key-revocation and project-delete cache invalidation are disabled; auth cache entries only expire via TTL")
	}

	authenticator, err := auth.New(context.Background(), cfg.Auth, rdb, log)
	if err != nil {
		log.Error("auth init failed", "err", err)
		os.Exit(1)
	}
	defer authenticator.Close()

	var usageEmitter server.UsageEmitter = server.NoopUsageEmitter{}
	var metering server.Metering = server.NoopMetering{}
	if rdb != nil {
		usageEmitter = auth.NewUsageEmitter(rdb, authenticator.PGRead(), log)
		metering = auth.NewMetering(rdb, authenticator.PGRead(), log)
	}

	priceTable := loadPriceTable(log, os.Getenv("FI_PRICING_JSON"))
	var pricer *pricing.Pricer
	if priceTable != nil {
		var custom *pricing.CustomPricing
		if authenticator != nil && authenticator.PGRead() != nil {
			custom = pricing.NewCustomPricing(authenticator.PGRead(), 24*time.Hour, log)
		}
		pricer = pricing.New(priceTable, custom)
	}

	opts := []server.Option{server.WithLogger(log)}
	if pricer != nil {
		opts = append(opts, server.WithPricer(pricer))
	}
	srv := server.New(cfg.Server, writer, authenticator, usageEmitter, metering, opts...)

	// Admin HTTP server — internal only: /healthz (container health checks)
	// and /metrics (Prometheus text exposition). Honors admin.addr from the
	// YAML config (fallback defaultAdminAddr).
	go runAdmin(resolveAdminAddr(cfg), writer, log)

	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer cancel()

	go authenticator.WatchRevocations(ctx)

	log.Info("starting",
		"grpc_addr", cfg.Server.GRPCAddr,
		"http_addr", cfg.Server.HTTPAddr,
		"ch_url", cfg.Writer.URL,
	)
	if err := srv.Run(ctx); err != nil && ctx.Err() == nil {
		log.Error("server exited with error", "err", err)
		os.Exit(1)
	}
	log.Info("shutdown complete", "stats", writer.Snapshot())
}

// loadPriceTable resolves the token-pricing table. FI_PRICING_JSON is
// best-effort: a bad override file must not silently disable pricing for
// every span, so a failed override load falls back to the embedded snapshot
// (with a warn log — pricing still works — rather than an error log) rather
// than returning nil. Only a failure of the embedded snapshot itself
// (near-impossible — it's compiled in) leaves pricing disabled and logs at
// Error.
func loadPriceTable(log *slog.Logger, path string) *pricing.Table {
	table, err := pricing.LoadTable(path)
	if err != nil && path != "" {
		// Pricing still works on this path — the embedded snapshot load
		// below succeeds — so Warn, not Error; Error is reserved for the
		// double-failure case below.
		log.Warn("FI_PRICING_JSON override load failed; falling back to embedded pricing snapshot",
			"env", "FI_PRICING_JSON", "path", path, "err", err)
		table, err = pricing.LoadTable("")
	}
	if err != nil {
		log.Error("pricing table load failed; token-based cost disabled", "err", err)
	}
	if table != nil && table.Skipped > 0 {
		log.Warn("pricing table loaded with skipped entries", "skipped", table.Skipped)
	}
	return table
}

func loadConfig(log *slog.Logger, path string) rootConfig {
	cfg := rootConfig{}
	b, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			log.Warn("config file not found — using defaults + env overrides", "path", path)
			return cfg
		}
		log.Error("read config failed", "path", path, "err", err)
		os.Exit(1)
	}
	if err := yaml.Unmarshal(b, &cfg); err != nil {
		log.Error("parse config failed", "err", err)
		os.Exit(1)
	}
	return cfg
}

// applyEnvOverrides — surgical, only the fields ops most often need to
// override at runtime without baking a new image.
func applyEnvOverrides(log *slog.Logger, c *rootConfig) {
	if v := os.Getenv("FI_CH_URL"); v != "" {
		c.Writer.URL = v
	}
	if v := os.Getenv("FI_CH_DATABASE"); v != "" {
		c.Writer.Database = v
	}
	if v := os.Getenv("FI_CH_USERNAME"); v != "" {
		c.Writer.Username = v
	}
	if v := os.Getenv("FI_CH_PASSWORD"); v != "" {
		c.Writer.Password = v
	}
	if v := os.Getenv("FI_GRPC_ADDR"); v != "" {
		c.Server.GRPCAddr = v
	}
	if v := os.Getenv("FI_HTTP_ADDR"); v != "" {
		// `FI_HTTP_ADDR=disable` (or `off`) turns the OTLP/HTTP listener
		// off entirely. Useful when deploying behind an external HTTP
		// gateway that strips OTLP/HTTP at the edge. The string `disable`
		// is more obvious in compose env lines than an empty value, which
		// docker compose silently swallows.
		switch v {
		case "disable", "off":
			c.Server.HTTPAddr = ""
		default:
			c.Server.HTTPAddr = v
		}
	}
	if v := os.Getenv("FI_GRPC_MAX_RECV_MIB"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			c.Server.GRPCMaxRecvMiB = n
		} else {
			// Silent fallback here would reproduce the silent-loss failure
			// mode this knob exists to fix — an operator must see it.
			log.Warn("ignoring invalid FI_GRPC_MAX_RECV_MIB", "value", v)
		}
	}
	if v := os.Getenv("FI_DEAD_LETTER_FILE"); v != "" {
		c.Writer.DeadLetterFile = v
	}
	if v := os.Getenv("FI_ADMIN_ADDR"); v != "" {
		// Already referenced by docker-compose.yml and
		// docker-compose.standalone.yml; wire it up so it is not silently
		// ignored like admin.addr used to be.
		c.Admin.Addr = v
	}
	// Auth overrides (auth is active when PG_WRITE is set)
	if v := os.Getenv("FI_PG_WRITE"); v != "" {
		c.Auth.PGWrite = v
	}
	if v := os.Getenv("FI_PG_READ"); v != "" {
		c.Auth.PGRead = v
	}
	if v := os.Getenv("FI_AUTH_REDIS_ADDR"); v != "" {
		c.Auth.RedisAddr = v
	}
}

// newAdminMux builds the admin HTTP mux: /healthz (container health
// checks) and /metrics (Prometheus text exposition of writer + Go runtime
// stats). Extracted from runAdmin so tests can exercise the handlers
// without binding a socket.
func newAdminMux(w *chwriter.Writer, log *slog.Logger) *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(rw http.ResponseWriter, r *http.Request) {
		s := w.Snapshot()
		denom := s.BatchesInserted + s.BatchesFailed
		if denom > 100 && s.BatchesFailed*2 > denom {
			rw.WriteHeader(503)
			_ = json.NewEncoder(rw).Encode(map[string]any{"status": "unhealthy", "stats": s})
			return
		}
		rw.WriteHeader(200)
		_ = json.NewEncoder(rw).Encode(map[string]any{"status": "ok", "stats": s})
	})
	mux.HandleFunc("/metrics", func(rw http.ResponseWriter, r *http.Request) {
		writeMetrics(rw, w)
	})
	return mux
}

// writeMetrics renders a minimal Prometheus text exposition (format 0.0.4)
// of the writer's lifetime stats plus basic Go runtime gauges. The
// fi-collector deliberately stays stdlib-only (no prometheus client
// dependency), so this is the honest, dependency-free surface the config
// comment promises at /metrics.
func writeMetrics(rw http.ResponseWriter, w *chwriter.Writer) {
	rw.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
	s := w.Snapshot()
	var ms runtime.MemStats
	runtime.ReadMemStats(&ms)

	write := func(name, help, typ string, value uint64) {
		fmt.Fprintf(rw, "# HELP %s %s\n# TYPE %s %s\n%s %d\n", name, help, name, typ, name, value)
	}
	write("fi_collector_batches_inserted_total", "Batches inserted into ClickHouse.", "counter", s.BatchesInserted)
	write("fi_collector_rows_inserted_total", "Rows inserted into ClickHouse.", "counter", s.RowsInserted)
	write("fi_collector_batches_retried_total", "Batches that needed at least one retry.", "counter", s.BatchesRetried)
	write("fi_collector_rows_dead_lettered_total", "Rows persisted to the dead-letter file.", "counter", s.RowsDeadLettered)
	write("fi_collector_batches_failed_total", "Batches that exhausted their retry budget.", "counter", s.BatchesFailed)
	write("fi_collector_curated_batches_inserted_total", "Curated-dimension batches inserted.", "counter", s.CuratedBatchesInserted)
	write("fi_collector_curated_batches_failed_total", "Curated-dimension batches failed.", "counter", s.CuratedBatchesFailed)

	fmt.Fprintf(rw, "# HELP go_goroutines Number of goroutines that currently exist.\n# TYPE go_goroutines gauge\ngo_goroutines %d\n", runtime.NumGoroutine())
	fmt.Fprintf(rw, "# HELP go_memstats_alloc_bytes Number of bytes allocated and still in use.\n# TYPE go_memstats_alloc_bytes gauge\ngo_memstats_alloc_bytes %d\n", ms.Alloc)
	fmt.Fprintf(rw, "# HELP go_memstats_heap_objects Number of allocated objects.\n# TYPE go_memstats_heap_objects gauge\ngo_memstats_heap_objects %d\n", ms.HeapObjects)
}

// runAdmin serves /healthz and /metrics for container health checks and
// local scraping.
func runAdmin(addr string, w *chwriter.Writer, log *slog.Logger) {
	srv := &http.Server{Addr: addr, Handler: newAdminMux(w, log), ReadHeaderTimeout: 5 * time.Second}
	if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Warn("admin server stopped", "err", err)
	}
}
