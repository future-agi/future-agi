package main

// When the gateway keeps budgets and rate counters in memory (no Redis
// configured, or Redis failed its start-up check), it says so once at start
// in plain words, and never names the Redis address, password or URL.

import (
	"bytes"
	"errors"
	"log/slog"
	"path/filepath"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

const memoryWarning = "Budgets and rate limits are kept in memory: they reset when the gateway restarts and are not shared between replicas. Set AGENTCC_REDIS_ADDRESS to keep them."

const (
	syntheticRedisAddress  = "10.20.30.40:6390"
	syntheticRedisPassword = "synthetic-redis-password"
)

// captureStartLog runs fn with the default logger writing to a buffer.
func captureStartLog(t *testing.T, fn func()) string {
	t.Helper()
	var buf bytes.Buffer
	previous := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&buf, &slog.HandlerOptions{Level: slog.LevelDebug})))
	t.Cleanup(func() { slog.SetDefault(previous) })
	fn()
	return buf.String()
}

func redisConfig(enabled bool) *config.Config {
	cfg := config.DefaultConfig()
	cfg.Redis.Enabled = enabled
	if enabled {
		cfg.Redis.Address = syntheticRedisAddress
		cfg.Redis.Password = syntheticRedisPassword
	}
	return cfg
}

func assertNoRedisSecrets(t *testing.T, log string) {
	t.Helper()
	for _, secret := range []string{syntheticRedisAddress, "10.20.30.40", "6390", syntheticRedisPassword, "redis://"} {
		if strings.Contains(log, secret) {
			t.Errorf("the start-up log carries %q:\n%s", secret, log)
		}
	}
}

func TestNoRedisLogsOneMemoryWarning(t *testing.T) {
	var client *redisstate.Client
	log := captureStartLog(t, func() {
		client, _ = connectRedisState(redisConfig(false), func(redisstate.Config) (*redisstate.Client, error) {
			t.Fatal("no Redis is configured, and a connection was tried")
			return nil, nil
		})
	})
	if client != nil {
		t.Fatal("a client with no Redis configured")
	}
	if n := strings.Count(log, memoryWarning); n != 1 {
		t.Fatalf("the memory warning appears %d times, want once:\n%s", n, log)
	}
	if !strings.Contains(log, "level=WARN") {
		t.Errorf("the memory warning is not a warning:\n%s", log)
	}
}

func TestAFailedRedisCheckWarnsWithoutTheAddress(t *testing.T) {
	failed := errors.New("redis ping: dial tcp " + syntheticRedisAddress + ": connect: connection refused (redis://:" + syntheticRedisPassword + "@" + syntheticRedisAddress + ")")
	var client *redisstate.Client
	var err error
	log := captureStartLog(t, func() {
		client, err = connectRedisState(redisConfig(true), func(redisstate.Config) (*redisstate.Client, error) {
			return nil, failed
		})
	})
	if client != nil || err != nil {
		t.Fatalf("a self-hosted gateway runs on in memory after a failed check: client %v, err %v", client, err)
	}
	if n := strings.Count(log, memoryWarning); n != 1 {
		t.Fatalf("the memory warning appears %d times, want once:\n%s", n, log)
	}
	assertNoRedisSecrets(t, log)
}

func TestWithRedisThereIsNoMemoryWarning(t *testing.T) {
	connected := &redisstate.Client{}
	var client *redisstate.Client
	log := captureStartLog(t, func() {
		client, _ = connectRedisState(redisConfig(true), func(c redisstate.Config) (*redisstate.Client, error) {
			if c.Address != syntheticRedisAddress || c.Password != syntheticRedisPassword {
				t.Errorf("connected with %q, want the configured address and password", c.Address)
			}
			return connected, nil
		})
	})
	if client != connected {
		t.Fatal("the connected client was not returned")
	}
	if strings.Contains(log, memoryWarning) {
		t.Fatalf("a memory warning with Redis connected:\n%s", log)
	}
}

// A hosted gateway does not fall back: a failed check is returned, and main
// stops it.
func TestAHostedGatewayGetsTheFailedCheckBack(t *testing.T) {
	for _, name := range []string{"AGENTCC_ADMIN_TOKEN", "AGENTCC_INTERNAL_API_KEY", "AGENTCC_WEBHOOK_SECRET", "AGENTCC_REDIS_PASSWORD"} {
		t.Setenv(name, processSecret(t))
	}
	t.Setenv("AGENTCC_DEPLOYMENT_PROFILE", "hosted")
	t.Setenv("AGENTCC_HOSTED_ONEKEY_ACTIVATION", "")
	t.Setenv("AGENTCC_CONTROL_PLANE_URL", "http://backend:8000")
	t.Setenv("AGENTCC_REDIS_ADDRESS", syntheticRedisAddress)
	t.Setenv("AGENTCC_TRUSTED_PROXY_CIDR", "192.0.2.10/32")
	cfg, err := config.Load(filepath.Join(onekeyRoot, "agentcc-gateway", "config.hosted.example.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	if !cfg.IsHosted() {
		t.Fatal("the shipped hosted configuration did not load as hosted")
	}
	log := captureStartLog(t, func() {
		_, err = connectRedisState(cfg, func(redisstate.Config) (*redisstate.Client, error) {
			return nil, errors.New("redis ping: dial tcp " + syntheticRedisAddress + ": i/o timeout")
		})
	})
	if err == nil {
		t.Fatal("a hosted gateway's failed Redis check was not returned")
	}
	if strings.Contains(log, memoryWarning) {
		t.Fatalf("a hosted gateway was told it runs in memory:\n%s", log)
	}
}
