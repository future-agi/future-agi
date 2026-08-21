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
// Health surfaces:
//   - /healthz (HTTP 200 unless writer dead-letter rate > threshold)
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
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"github.com/future-agi/future-agi/fi-collector/pkg/catalogkafka"
	"github.com/future-agi/future-agi/fi-collector/pkg/catalogwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/pricing"
	"github.com/future-agi/future-agi/fi-collector/pkg/server"
	"github.com/redis/go-redis/v9"
	"gopkg.in/yaml.v3"
)

type rootConfig struct {
	Writer  chwriter.Config             `yaml:"writer"`
	Server  server.Config               `yaml:"server"`
	Auth    auth.Config                 `yaml:"auth"`
	Catalog catalogwriter.RuntimeConfig `yaml:"catalog"`
}

func main() {
	var configPath string
	flag.StringVar(&configPath, "config", "/etc/fi-collector/config.yaml", "path to YAML config")
	flag.Parse()

	log := slog.New(slog.NewJSONHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelInfo}))

	cfg := loadConfig(log, configPath)
	if err := applyEnvOverrides(log, &cfg); err != nil {
		log.Error("invalid environment override", "err", err)
		os.Exit(1)
	}

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

	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer cancel()

	opts := []server.Option{server.WithLogger(log)}
	if pricer != nil {
		opts = append(opts, server.WithPricer(pricer))
	}
	mode, err := cfg.Catalog.SelectedMode()
	if err != nil {
		log.Error("catalog mode validation failed", "err", err)
		os.Exit(1)
	}
	var catalogWAL *catalogwriter.Writer
	var catalogSubmitter *catalogwriter.AsyncSubmitter
	var stopCatalogSubmit context.CancelFunc
	var catalogPublisher *catalogkafka.SpoolPublisher
	var kafkaProducer *catalogkafka.Producer
	if mode == catalogwriter.ModeDirect && sameClickHouseIdentity(
		cfg.Writer.Username, cfg.Catalog.ClickHouse.Username,
	) {
		log.Error("catalog ClickHouse user must be separate from canonical span writer")
		os.Exit(1)
	}
	switch mode {
	case catalogwriter.ModeDisabled:
	case catalogwriter.ModeDirect:
		catalogWAL, catalogPublisher, err = newDirectCatalogWriter(cfg.Catalog)
		if err != nil {
			log.Error("direct catalog writer init failed", "err", err)
			os.Exit(1)
		}
	case catalogwriter.ModeKafka:
		kafkaProducer, err = catalogkafka.NewFranzProducer(catalogkafka.FranzProducerConfig{
			Brokers: cfg.Catalog.Kafka.Brokers,
			Topic:   cfg.Catalog.Kafka.Topic,
		})
		if err != nil {
			log.Error("Kafka catalog producer init failed", "err", err)
			os.Exit(1)
		}
		catalogWAL, catalogPublisher, err = newKafkaCatalogWriter(cfg.Catalog, kafkaProducer)
		if err != nil {
			kafkaProducer.Close()
			log.Error("Kafka catalog writer init failed", "err", err)
			os.Exit(1)
		}
	}
	if catalogWAL != nil {
		catalogSubmitter, err = catalogwriter.NewAsyncSubmitter(catalogWAL, 64, 64)
		if err != nil {
			if kafkaProducer != nil {
				kafkaProducer.Close()
			}
			log.Error("catalog submitter init failed", "mode", mode, "err", err)
			os.Exit(1)
		}
		// The signal context reaches Server.Run first. Keep WAL ownership alive
		// until Server.shutdown has performed its final canonical-span drain;
		// otherwise that final batch could race a stopped submitter.
		catalogSubmitCtx, stopSubmit := context.WithCancel(context.Background())
		stopCatalogSubmit = stopSubmit
		catalogSubmitter.Run(catalogSubmitCtx)
		opts = append(opts, server.WithAttributeCatalogWriter(&catalogwriter.AttributeCatalogWriter{
			Writer: catalogWAL, Submitter: catalogSubmitter,
		}))
	}
	srv := server.New(cfg.Server, writer, authenticator, usageEmitter, metering, opts...)
	var catalogReplayDone chan struct{}
	if catalogWAL != nil {
		catalogReplayDone = make(chan struct{})
		go func() {
			defer close(catalogReplayDone)
			switch mode {
			case catalogwriter.ModeDirect:
				runDirectCatalogReplay(
					ctx, catalogWAL, catalogPublisher, cfg.Catalog.ReplayInterval, log,
				)
			case catalogwriter.ModeKafka:
				runKafkaCatalogReplay(
					ctx, catalogWAL, catalogPublisher, cfg.Catalog.ReplayInterval, log,
				)
			}
		}()
		go logCatalogSubmissionGaps(ctx, catalogSubmitter, log)
	}

	// Admin HTTP server — internal only, health check endpoint.
	go runAdmin(":9464", writer, log)

	go authenticator.WatchRevocations(ctx)

	log.Info("starting",
		"grpc_addr", cfg.Server.GRPCAddr,
		"http_addr", cfg.Server.HTTPAddr,
		"ch_url", cfg.Writer.URL,
	)
	runErr := srv.Run(ctx)
	unexpectedExit := runErr != nil && ctx.Err() == nil
	if unexpectedExit {
		log.Error("server exited with error; draining catalog lifecycle", "err", runErr)
	}
	if catalogSubmitter != nil {
		stopCatalogSubmit()
		catalogSubmitter.Wait()
		drainCatalogSubmissionGaps(catalogSubmitter, log)
	}
	// A listener/runtime error is not driven by the signal context. Cancel only
	// after the final server drain has transferred every accepted job to the
	// durable WAL, then stop the replay worker before closing its destination.
	if unexpectedExit {
		cancel()
	}
	if catalogReplayDone != nil {
		<-catalogReplayDone
	}
	if kafkaProducer != nil {
		kafkaProducer.Close()
	}
	log.Info("shutdown complete", "stats", writer.Snapshot())
	if unexpectedExit {
		os.Exit(1)
	}
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
func applyEnvOverrides(log *slog.Logger, c *rootConfig) error {
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
	if v := os.Getenv("FI_CATALOG_MODE"); v != "" {
		c.Catalog.Mode = catalogwriter.Mode(v)
	}
	if v := os.Getenv("FI_CATALOG_ENVIRONMENT"); v != "" {
		c.Catalog.Environment = v
	}
	if v := os.Getenv("FI_CATALOG_EPOCH"); v != "" {
		epoch, err := strconv.ParseUint(v, 10, 16)
		if err != nil || epoch == 0 {
			return fmt.Errorf("FI_CATALOG_EPOCH must be a non-zero UInt16")
		}
		c.Catalog.CatalogEpoch = uint16(epoch)
	}
	if v := os.Getenv("FI_CATALOG_PRODUCER_STREAM_ID"); v != "" {
		c.Catalog.ProducerStream = v
	}
	if v := os.Getenv("FI_CATALOG_SPOOL_DIR"); v != "" {
		c.Catalog.SpoolDir = v
	}
	if v := os.Getenv("FI_CATALOG_REPLAY_INTERVAL"); v != "" {
		interval, err := time.ParseDuration(v)
		if err != nil || interval <= 0 {
			return fmt.Errorf("FI_CATALOG_REPLAY_INTERVAL must be a positive duration")
		}
		c.Catalog.ReplayInterval = interval
	}
	if v := os.Getenv("FI_CATALOG_CH_URL"); v != "" {
		c.Catalog.ClickHouse.URL = v
	}
	if v := os.Getenv("FI_CATALOG_CH_DATABASE"); v != "" {
		c.Catalog.ClickHouse.Database = v
	}
	if v := os.Getenv("FI_CATALOG_CH_USERNAME"); v != "" {
		c.Catalog.ClickHouse.Username = v
	}
	if v := os.Getenv("FI_CATALOG_CH_PASSWORD"); v != "" {
		c.Catalog.ClickHouse.Password = v
	}
	if v := os.Getenv("FI_CATALOG_KAFKA_BROKERS"); v != "" {
		c.Catalog.Kafka.Brokers = splitNonempty(v)
	}
	if v := os.Getenv("FI_CATALOG_KAFKA_TOPIC"); v != "" {
		c.Catalog.Kafka.Topic = v
	}
	return c.Catalog.ValidateMode()
}

func splitNonempty(value string) []string {
	parts := strings.Split(value, ",")
	out := make([]string, 0, len(parts))
	for _, part := range parts {
		if trimmed := strings.TrimSpace(part); trimmed != "" {
			out = append(out, trimmed)
		}
	}
	return out
}

func sameClickHouseIdentity(canonical, catalog string) bool {
	if canonical == "" {
		canonical = "default"
	}
	if catalog == "" {
		catalog = "default"
	}
	return canonical == catalog
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
