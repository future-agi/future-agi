package redisstate

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"strings"
	"sync"
	"testing"

	"github.com/redis/go-redis/v9"
)

// Embedding the interface lets this fixture implement only PUBLISH. No socket,
// Redis server, credentials, or other client operations are used by this test.
type revocationResultRedis struct {
	redis.UniversalClient
	err     error
	calls   int
	channel string
	keyID   interface{}
}

func (r *revocationResultRedis) Publish(_ context.Context, channel string, message interface{}) *redis.IntCmd {
	r.calls++
	r.channel, r.keyID = channel, message
	return redis.NewIntResult(1, r.err)
}

// Serial: captures the process-wide logger, as does the retained r2 contract.
func TestKeyRevocationPublishResult(t *testing.T) {
	detail := strings.Join([]string{"synthetic", "private", "transport", "detail"}, "-")
	for _, name := range []string{"nil_client", "nil_broadcaster", "transport_failure", "success"} {
		t.Run(name, func(t *testing.T) {
			var logs revocationResultLogBuffer
			previous := slog.Default()
			slog.SetDefault(slog.New(slog.NewJSONHandler(&logs, nil)))
			t.Cleanup(func() { slog.SetDefault(previous) })

			rdb := &revocationResultRedis{}
			b := NewKeyRevocationBroadcaster(nil)
			switch name {
			case "nil_broadcaster":
				b = nil
			case "transport_failure", "success":
				b = NewKeyRevocationBroadcaster(&Client{rdb: rdb, breaker: newCircuitBreaker(3, 0)})
				if name == "transport_failure" {
					rdb.err = errors.New(detail)
				}
			}
			const keyID = "synthetic-r3-revocation"
			err := b.PublishWithError(context.Background(), keyID)
			wantFailure := name != "success"
			if (err != nil) != wantFailure {
				t.Fatalf("publish failure = %t, want %t", err != nil, wantFailure)
			}
			if wantFailure {
				var failure *RevocationPublishError
				if !errors.As(err, &failure) || err.Error() != "revocation not broadcast" || errors.Unwrap(err) != nil {
					t.Error("failure must have the typed, value-free publish contract without an underlying error")
				}
			}
			if name == "transport_failure" || name == "success" {
				if rdb.calls != 1 || rdb.channel != keyRevocationChannel || rdb.keyID != keyID {
					t.Error("PUBLISH must target the revocation channel with the requested key exactly once")
				}
			} else if rdb.calls != 0 {
				t.Error("missing-client publish must not call Redis")
			}
			captured := logs.snapshot()
			if bytes.Contains(captured, []byte(detail)) {
				t.Error("publish log leaked the synthetic underlying error detail")
			}
			reported := 0
			for _, line := range bytes.Split(captured, []byte("\n")) {
				if len(line) == 0 {
					continue
				}
				var record struct {
					Level   string `json:"level"`
					Message string `json:"msg"`
					KeyID   string `json:"key_id"`
					Error   string `json:"error"`
				}
				if json.Unmarshal(line, &record) != nil {
					t.Error("invalid structured log")
					continue
				}
				if record.KeyID == keyID && record.Level == "WARN" && strings.Contains(record.Message, "revocation") && record.Error == "revocation not broadcast" {
					reported++
				}
			}
			wantReports := 0
			if wantFailure {
				wantReports = 1
			}
			if reported != wantReports {
				t.Errorf("value-free warning count = %d, want %d", reported, wantReports)
			}
		})
	}
}

type revocationResultLogBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (b *revocationResultLogBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *revocationResultLogBuffer) snapshot() []byte {
	b.mu.Lock()
	defer b.mu.Unlock()
	return bytes.Clone(b.buf.Bytes())
}
