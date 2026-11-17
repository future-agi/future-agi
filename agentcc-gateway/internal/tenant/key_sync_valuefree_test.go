package tenant

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
)

const operatorSyncSyntheticToken = "sk-SYNTHETIC-OPERATOR-SYNC-NOT-A-CREDENTIAL"
const operatorSyncSyntheticURL = "https://SYNTHETIC-OPERATOR-USER:SYNTHETIC-OPERATOR-PASSWORD@synthetic-control-plane.invalid"

type operatorKeySyncTransport func(*http.Request) (*http.Response, error)

func (f operatorKeySyncTransport) RoundTrip(req *http.Request) (*http.Response, error) { return f(req) }

// The real JSON handler writes bytes unchanged. Cancel only after its target
// failure has been emitted, so shutdown suppression cannot make a canary pass.
type operatorKeySyncLogs struct {
	mu             sync.Mutex
	bytes          bytes.Buffer
	stop           context.CancelFunc
	message, level string
}

func (w *operatorKeySyncLogs) Write(p []byte) (int, error) {
	w.mu.Lock()
	n, err := w.bytes.Write(p)
	w.mu.Unlock()
	var record struct {
		Message string `json:"msg"`
		Level   string `json:"level"`
	}
	if json.Unmarshal(p, &record) == nil && record.Message == w.message && record.Level == w.level {
		w.stop()
	}
	return n, err
}
func (w *operatorKeySyncLogs) text() string {
	w.mu.Lock()
	defer w.mu.Unlock()
	return w.bytes.String()
}

func TestControlPlaneKeySyncValueFreeOperatorLogs(t *testing.T) {
	for _, phase := range []string{"startup_info", "startup_warn", "periodic_info_then_warn"} {
		for _, failure := range []string{"http_error", "transport_error", "migrations_pending", "decode_error"} {
			t.Run(phase+"/"+failure, func(t *testing.T) {
				// Both sync clients use DefaultTransport. Intercept every request with a
				// synthetic in-process fixture; neither DNS nor a hosted service is used.
				originalTransport := http.DefaultTransport
				originalLogger := slog.Default()
				originalWarnAfter := startupSyncWarnAfter
				t.Cleanup(func() {
					http.DefaultTransport = originalTransport
					slog.SetDefault(originalLogger)
					startupSyncWarnAfter = originalWarnAfter
				})
				var keyCalls atomic.Int32
				http.DefaultTransport = operatorKeySyncTransport(func(req *http.Request) (*http.Response, error) {
					body := `{"status":true,"result":{}}`
					status := http.StatusOK
					switch req.URL.Path {
					case "/agentcc/org-configs/bulk/": // isolate the key producer, not the sibling org producer
					case "/agentcc/api-keys/bulk/":
						keyCalls.Add(1)
						if req.Header.Get("Authorization") != "Bearer "+operatorSyncSyntheticToken {
							t.Error("admin bearer not sent")
						}
						switch failure {
						case "http_error":
							status = http.StatusServiceUnavailable
							body = `{"detail":"` + req.Header.Get("Authorization") + `"}`
						case "transport_error":
							return nil, errors.New("SYNTHETIC transport echo " + req.URL.String() + " " + req.Header.Get("Authorization"))
						case "migrations_pending":
							status = http.StatusServiceUnavailable
							body = `{"code":"migrations_pending","detail":"` + req.Header.Get("Authorization") + `"}`
						case "decode_error":
							// A malformed response does not depend on optional schema fields.
							body = `{"status":` + operatorSyncSyntheticToken + `}`
						}
					default:
						t.Errorf("unexpected synthetic sync path %q", req.URL.Path)
						return nil, errors.New("unexpected synthetic route")
					}
					return &http.Response{StatusCode: status, Header: make(http.Header), Body: io.NopCloser(strings.NewReader(body)), Request: req}, nil
				})
				ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				defer cancel()
				logs := &operatorKeySyncLogs{stop: cancel, level: "INFO", message: "control plane not ready yet, retrying startup sync"}
				startupSyncWarnAfter = time.Hour
				interval := time.Duration(0)
				startup := true
				switch phase {
				case "startup_warn":
					startupSyncWarnAfter = 0
					logs.level = "WARN"
					logs.message = "control plane sync still failing: the gateway serves only its config.yaml keys until keys load from the app; still retrying"
				case "periodic_info_then_warn":
					startup = false
					interval = time.Millisecond
					logs.level = "WARN"
					logs.message = "periodic key sync failed"
				}
				slog.SetDefault(slog.New(slog.NewJSONHandler(logs, &slog.HandlerOptions{Level: slog.LevelDebug})))
				ks := auth.NewKeyStore(config.AuthConfig{})
				RunControlPlaneSync(ctx, startup, interval, operatorSyncSyntheticURL, operatorSyncSyntheticToken, NewStore(), ks)
				if errors.Is(ctx.Err(), context.DeadlineExceeded) {
					t.Fatal("never reached the target operator log")
				}
				text := logs.text()
				for _, canary := range []string{operatorSyncSyntheticToken, "SYNTHETIC-OPERATOR-USER", "SYNTHETIC-OPERATOR-PASSWORD", "synthetic-control-plane.invalid"} {
					if strings.Contains(text, canary) {
						t.Errorf("operator log leaked synthetic credential/endpoint: %s", text)
					}
				}
				reason, class := "http_status", "5xx"
				switch failure {
				case "transport_error":
					reason, class = "transport_failed", "none"
				case "migrations_pending":
					reason = "migrations_pending"
				case "decode_error":
					reason, class = "response_invalid", "2xx"
				}
				want := "key sync failed: reason=" + reason + " status_class=" + class
				found, foundInfo := false, false
				for _, line := range strings.Split(strings.TrimSpace(text), "\n") {
					var record struct {
						Message string `json:"msg"`
						Level   string `json:"level"`
						Error   string `json:"error"`
					}
					if err := json.Unmarshal([]byte(line), &record); err != nil {
						t.Fatalf("invalid operator JSON: %v", err)
					}
					if record.Message == logs.message {
						if record.Error != want {
							t.Errorf("operator error=%q, want %q", record.Error, want)
						}
						if record.Level == logs.level {
							found = true
						}
						if record.Level == "INFO" {
							foundInfo = true
						}
					}
				}
				if !found {
					t.Error("target failure log missing")
				}
				if phase == "periodic_info_then_warn" && (!foundInfo || keyCalls.Load() < 2) {
					t.Error("periodic INFO-to-WARN path was not exercised")
				}
				if keyCalls.Load() == 0 {
					t.Error("key sync never ran")
				}
				if ks.KeyListLoaded() {
					t.Error("failure incorrectly marked key list loaded")
				}
				if ks.AppHasNoKeyTable() != (failure == "migrations_pending") {
					t.Error("migration semantics changed")
				}
			})
		}
	}
}
