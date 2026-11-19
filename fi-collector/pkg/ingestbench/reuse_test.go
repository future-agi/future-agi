package ingestbench

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// A previous execution need not have delivered any of its leading traces.
// Refuse sequential reuse even if only later identities survived; do not let
// their timestamps inside the verification slack certify a second delivery.
func TestReusePreflightFailureSendsNoLoad(t *testing.T) {
	for _, body := range []string{"", "{}", `{"rows":0} {"rows":1}`, "query failed"} {
		t.Run(body, func(t *testing.T) {
			c := mockRunConfig()
			store := &mockStorage{cfg: c}
			ch := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				b, _ := io.ReadAll(r.Body)
				if strings.Contains(string(b), "SELECT count() AS rows FROM (SELECT 1") {
					for _, setting := range []string{"timeout_overflow_mode", "read_overflow_mode", "read_overflow_mode_leaf", "result_overflow_mode"} {
						if r.URL.Query().Get(setting) != "throw" {
							t.Errorf("%s must fail closed", setting)
						}
					}
					if body == "query failed" {
						w.WriteHeader(http.StatusInternalServerError)
					}
					_, _ = io.WriteString(w, body)
					return
				}
				r.Body = io.NopCloser(strings.NewReader(string(b)))
				store.serve(w, r)
			}))
			defer ch.Close()
			var sent atomic.Int64
			collector := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { sent.Add(1) }))
			defer collector.Close()
			c.ClickHouseURL, c.Endpoint = ch.URL, collector.URL
			r, err := Run(context.Background(), c, Options{})
			if err == nil || !strings.Contains(err.Error(), "verification preflight") || r.Counters.Attempted != 0 || sent.Load() != 0 {
				t.Fatalf("preflight error sent load: err=%v attempted=%d requests=%d", err, r.Counters.Attempted, sent.Load())
			}
		})
	}
}

func TestReusePreflightRejectsOnlyLaterStoredTraces(t *testing.T) {
	c := mockRunConfig()
	c.Rate, c.SpansPerTrace = 68, 1
	c.DirectProbes.Enabled = false
	store := &mockStorage{cfg: c}
	for seq := int64(64); seq < 68; seq++ {
		store.rows = append(store.rows, storedRow(c, seq, time.Now().Add(-10*time.Second)))
	}
	ch := httptest.NewServer(http.HandlerFunc(store.serve))
	defer ch.Close()
	var sent atomic.Int64
	collector := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		sent.Add(1)
		w.Header().Set("Content-Type", "application/x-protobuf")
		w.WriteHeader(http.StatusOK)
	}))
	defer collector.Close()
	c.ClickHouseURL, c.Endpoint = ch.URL, collector.URL
	r, err := Run(context.Background(), c, Options{})
	if err == nil || !strings.Contains(err.Error(), "already has") || r.Verification.PreexistingRunRows == 0 || r.Counters.Attempted != 0 || sent.Load() != 0 {
		t.Fatalf("reused later-only namespace: err=%v preexisting=%d attempted=%d requests=%d", err, r.Verification.PreexistingRunRows, r.Counters.Attempted, sent.Load())
	}
}
