package redisstate_test

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// This contract intentionally has no Redis dependency and must not skip. A
// missing client must not panic or silently drop a revocation publish attempt.
// Keep this test serial: it temporarily replaces the process-wide slog logger.
func TestKeyRevocationPublishWithoutRedis(t *testing.T) {
	var logs revocationLogBuffer
	previous := slog.Default()
	slog.SetDefault(slog.New(slog.NewJSONHandler(&logs, nil)))
	t.Cleanup(func() { slog.SetDefault(previous) })

	const keyID = "synthetic-no-redis-revocation"
	broadcaster := redisstate.NewKeyRevocationBroadcaster(nil)
	panicked := false
	func() {
		defer func() {
			if recovered := recover(); recovered != nil {
				panicked = true
				t.Errorf("revocation publish without Redis panicked: %v", recovered)
			}
		}()
		broadcaster.Publish(context.Background(), keyID)
	}()

	// Match only this synthetic key's structured warning, so unrelated
	// background logs cannot satisfy the reporting assertion. Do not print
	// the captured process log: it may include unrelated request details.
	reported := false
	for _, line := range bytes.Split(logs.snapshot(), []byte("\n")) {
		if len(line) == 0 {
			continue
		}
		var record struct {
			Level   string `json:"level"`
			Message string `json:"msg"`
			KeyID   string `json:"key_id"`
			Error   string `json:"error"`
		}
		if err := json.Unmarshal(line, &record); err != nil {
			t.Error("revocation log capture contains invalid structured JSON")
			continue
		}
		if record.KeyID == keyID && (record.Level == "WARN" || record.Level == "ERROR") &&
			strings.Contains(strings.ToLower(record.Message), "revocation") && record.Error != "" {
			reported = true
		}
	}
	t.Logf("no-Redis publish: panicked=%t failure_reported=%t", panicked, reported)
	if !reported {
		t.Error("revocation publish without Redis must report a structured warning or error with key_id and error")
	}
}

// JSONHandler serializes its writes; the extra lock also protects snapshots
// from any background goroutine logging while this test owns the default logger.
type revocationLogBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (b *revocationLogBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *revocationLogBuffer) snapshot() []byte {
	b.mu.Lock()
	defer b.mu.Unlock()
	return bytes.Clone(b.buf.Bytes())
}
