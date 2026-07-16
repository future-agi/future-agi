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
//     FI_DEAD_LETTER_FILE)
//
// Health surfaces:
//   - /healthz (HTTP 200 unless writer dead-letter rate > threshold)
//   - Structured logs on stderr (JSON lines)
package main

import (
	"context"
	"encoding/json"
	"flag"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/pricing"
	"github.com/future-agi/future-agi/fi-collector/pkg/server"
	"github.com/redis/go-redis/v9"
	"gopkg.in/yaml.v3"
)

type rootConfig struct {
	Writer chwriter.Config `yaml:"writer"`
	Server server.Config   `yaml:"server"`
	Auth   auth.Config     `yaml:"auth"`
}

func main() {
	var configPath string
	flag.StringVar(&configPath, "config", "/etc/fi-collector/config.yaml", "path to YAML config")
	flag.Parse()

	log := slog.New(slog.NewJSONHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelInfo}))

	cfg := loadConfig(log, configPath)
	applyEnvOverrides(&cfg)

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
		log.Warn("FI_AUTH_REDIS_ADDR not set — quota enforcement and usage metering are disabled")
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

	// Admin HTTP server — internal only, health check endpoint.
	go runAdmin(":9464", writer, log)

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
// (with an error log) rather than returning nil. Only a failure of the
// embedded snapshot itself (near-impossible — it's compiled in) leaves
// pricing disabled.
func loadPriceTable(log *slog.Logger, path string) *pricing.Table {
	table, err := pricing.LoadTable(path)
	if err != nil && path != "" {
		log.Error("FI_PRICING_JSON override load failed; falling back to embedded pricing snapshot",
			"env", "FI_PRICING_JSON", "path", path, "err", err)
		table, err = pricing.LoadTable("")
	}
	if err != nil {
		log.Error("pricing table load failed; token-based cost disabled", "err", err)
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
func applyEnvOverrides(c *rootConfig) {
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
	if v := os.Getenv("FI_DEAD_LETTER_FILE"); v != "" {
		c.Writer.DeadLetterFile = v
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

// runAdmin serves /healthz for container health checks.
func runAdmin(addr string, w *chwriter.Writer, log *slog.Logger) {
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
	srv := &http.Server{Addr: addr, Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Warn("admin server stopped", "err", err)
	}
}
