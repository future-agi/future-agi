package main

import (
	"bytes"
	"context"
	"io"
	"log/slog"
	"os"
	"strings"
	"sync"
	"testing"

	"github.com/future-agi/future-agi/fi-collector/pkg/trajectory"
	"gopkg.in/yaml.v3"
)

func noEnv(string) string { return "" }

// guardedBuffer is a log sink safe for the gate's background goroutine.
type guardedBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (b *guardedBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *guardedBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.String()
}

func TestRedisClientUsesTLSForRedissURL(t *testing.T) {
	c, err := newRedisClient("rediss://cache.example.internal:6380/0", "from-env", noEnv)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	o := c.Options()
	if o.TLSConfig == nil || o.TLSConfig.ServerName != "cache.example.internal" {
		t.Fatalf("rediss:// must connect with TLS for the URL's host: %+v", o.TLSConfig)
	}
	if o.Addr != "cache.example.internal:6380" || o.Password != "from-env" {
		t.Fatalf("addr %q, password from FI_AUTH_REDIS_PASSWORD expected", o.Addr)
	}
	// FI_REDIS_TLS=true with host:port does the same.
	c2, err := newRedisClient("cache.example.internal:6380", "", func(k string) string {
		if k == "FI_REDIS_TLS" {
			return "true"
		}
		return ""
	})
	if err != nil {
		t.Fatal(err)
	}
	defer c2.Close()
	if c2.Options().TLSConfig == nil || c2.Options().TLSConfig.ServerName != "cache.example.internal" {
		t.Fatal("FI_REDIS_TLS=true must enable TLS")
	}
}

func TestRedisClientPlainForRedisURL(t *testing.T) {
	for _, addr := range []string{"redis://cache:6379/0", "cache:6379"} {
		c, err := newRedisClient(addr, "", noEnv)
		if err != nil {
			t.Fatal(err)
		}
		if c.Options().TLSConfig != nil || c.Options().Addr != "cache:6379" {
			t.Fatalf("%s: plain Redis expected, got %+v", addr, c.Options())
		}
		c.Close()
	}
	// A URL password wins over the separate variable.
	c, err := newRedisClient("redis://:urlpass@cache:6379/0", "envpass", noEnv)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	if c.Options().Password != "urlpass" {
		t.Fatal("the URL's password must win")
	}
}

func TestRedisClientErrorsNeverEchoTheAddress(t *testing.T) {
	_, err := newRedisClient("rediss://:sup3rsecret@cache:notaport/x", "", noEnv)
	if err == nil || strings.Contains(err.Error(), "sup3rsecret") {
		t.Fatalf("invalid URL error must not include the value: %v", err)
	}
	if _, err := newRedisClient("cache:6379", "", func(string) string { return "sometimes" }); err == nil {
		t.Fatal("an invalid FI_REDIS_TLS must be rejected")
	}
}

func TestDedupRejectedWithAsyncInsert(t *testing.T) {
	for _, mode := range []string{"on", "canary"} {
		t.Setenv("FI_TRAJECTORY_DEDUP_MODE", mode)
		cfg := rootConfig{}
		cfg.Writer.AsyncInsert = true
		cfg.Observed.Mode = "disabled"
		err := applyEnvOverrides(slog.New(slog.NewTextHandler(io.Discard, nil)), &cfg)
		if err == nil || !strings.Contains(err.Error(), "synchronous") {
			t.Fatalf("mode %s with async_insert must be rejected, got %v", mode, err)
		}
	}
	t.Setenv("FI_TRAJECTORY_DEDUP_MODE", "off")
	cfg := rootConfig{}
	cfg.Writer.AsyncInsert = true
	cfg.Observed.Mode = "disabled"
	if err := applyEnvOverrides(slog.New(slog.NewTextHandler(io.Discard, nil)), &cfg); err != nil {
		t.Fatalf("async_insert with dedup off stays allowed: %v", err)
	}
}

// TestTrajectoryOptionsDormantBuildsNothing: with dedup off (the default),
// or with only the billing flag set, nothing trajectory-related is wired: no
// seen store, no gate, so no metadata queries and no gate-key reads.
func TestTrajectoryOptionsDormantBuildsNothing(t *testing.T) {
	logs := &guardedBuffer{}
	log := slog.New(slog.NewJSONHandler(logs, nil))
	for _, env := range []map[string]string{
		{},
		{"FI_BILL_DEDUPED_BYTES": "true"},
		{"FI_TRAJECTORY_DEDUP_MODE": "canary"}, // canary with no project
		{"FI_TRAJECTORY_DEDUP_MODE": "off", "FI_TRAJECTORY_DEDUP_PROJECTS": "11111111-1111-4111-8111-111111111111"},
	} {
		cfg, err := trajectory.ConfigFromEnv(trajectory.Config{}, func(k string) string { return env[k] })
		if err != nil {
			t.Fatal(err)
		}
		if opts := trajectoryOptions(context.Background(), log, cfg, nil, nil, nil); opts != nil {
			t.Fatalf("%v: dedup off must wire nothing, got %d options", env, len(opts))
		}
	}
	if logs.String() != "" {
		t.Fatalf("dormant wiring must not log: %s", logs.String())
	}
	// Active dedup does wire the store and the gate (the gate cannot open
	// without Redis, which is logged).
	cfg, err := trajectory.ConfigFromEnv(trajectory.Config{}, func(k string) string {
		return map[string]string{"FI_TRAJECTORY_DEDUP_MODE": "on"}[k]
	})
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if opts := trajectoryOptions(ctx, log, cfg, nil, nil, nil); len(opts) != 2 {
		t.Fatalf("active dedup wires the store and the gate, got %d options", len(opts))
	}
	if !strings.Contains(logs.String(), "redis_not_configured") {
		t.Fatalf("dedup without Redis must say the gate stays closed: %s", logs.String())
	}
}

// TestShippedCollectorYAMLTrajectoryDefaults: the shipped config leaves dedup
// off and the billing flag unset, so turning dedup on later bills stored
// bytes unless an operator sets the kill switch explicitly.
func TestShippedCollectorYAMLTrajectoryDefaults(t *testing.T) {
	raw, err := os.ReadFile("../../config/collector.yaml")
	if err != nil {
		t.Fatal(err)
	}
	var cfg rootConfig
	if err := yaml.Unmarshal(raw, &cfg); err != nil {
		t.Fatal(err)
	}
	if err := cfg.Trajectory.Validate(); err != nil {
		t.Fatalf("shipped trajectory config is invalid: %v", err)
	}
	if cfg.Trajectory.BillDedupedBytes != nil {
		t.Fatal("bill_deduped_bytes must be left unset in the shipped config: an explicit false is the kill switch")
	}
	if cfg.Trajectory.CompactSpanAttrs || cfg.Trajectory.Normalize().Active() {
		t.Fatal("the shipped config must keep trajectory dedup off")
	}
	n := cfg.Trajectory.Normalize()
	if n.ItemsMaxRetries != trajectory.DefaultItemsMaxRetries || n.ItemsInsertTimeout != trajectory.DefaultItemsInsertTimeout ||
		n.MaxReuseAge != trajectory.DefaultMaxReuseAge || !n.SeenRedisEnabled() {
		t.Fatalf("shipped knobs differ from the defaults: %+v", n)
	}
}
