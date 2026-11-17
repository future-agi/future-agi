package auth

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

const keySyncSyntheticToken = "sk-SYNTHETIC-KEY-SYNC-NOT-A-CREDENTIAL"
const keySyncSyntheticURL = "https://SYNTHETIC-SYNC-USER:SYNTHETIC-SYNC-PASSWORD@control-plane.invalid"

type valueFreeKeyTransport func(*http.Request) (*http.Response, error)

func (f valueFreeKeyTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

type valueFreeKeyReadError struct{}

func (valueFreeKeyReadError) Read([]byte) (int, error) {
	return 0, errors.New("SYNTHETIC reader echo " + keySyncSyntheticToken)
}

// A structural interface deliberately keeps the tests-only patch buildable on
// the unfixed base: missing diagnostics must fail assertions, not compilation.
type valueFreeKeyDiagnostic interface {
	error
	Reason() string
	StatusClass() string
}

func assertValueFreeKeyText(t *testing.T, text string) {
	t.Helper()
	for _, canary := range []string{keySyncSyntheticToken, "SYNTHETIC-SYNC-USER", "SYNTHETIC-SYNC-PASSWORD", "control-plane.invalid"} {
		if strings.Contains(text, canary) {
			t.Errorf("key-sync diagnostic/log retained a synthetic credential or endpoint: %q", text)
		}
	}
}

func TestKeySyncValueFreeFailures(t *testing.T) {
	echo := `{"detail":"Bearer ` + keySyncSyntheticToken + `"}`
	pending := `{"code":"migrations_pending","detail":"Bearer ` + keySyncSyntheticToken + `"}`
	tests := []struct {
		name, baseURL, body, reason, class string
		status                             int
		transportErr                       error
		readError                          bool
		sentinel                           error
	}{
		{name: "request", baseURL: keySyncSyntheticURL + "/%", reason: "request_invalid", class: "none"},
		{name: "transport", transportErr: errors.New("SYNTHETIC transport echo " + keySyncSyntheticToken), reason: "transport_failed", class: "none"},
		{name: "canceled", transportErr: fmt.Errorf("%s: %w", keySyncSyntheticToken, context.Canceled), reason: "canceled", class: "none", sentinel: context.Canceled},
		{name: "deadline", transportErr: fmt.Errorf("%s: %w", keySyncSyntheticToken, context.DeadlineExceeded), reason: "deadline_exceeded", class: "none", sentinel: context.DeadlineExceeded},
		{name: "informational", status: 103, body: echo, reason: "http_status", class: "1xx"},
		{name: "non_ok_success", status: 204, body: echo, reason: "http_status", class: "2xx"},
		{name: "redirect_without_location", status: 302, body: echo, reason: "http_status", class: "3xx"},
		{name: "refused", status: 401, body: echo, reason: "http_status", class: "4xx"},
		{name: "rate_limited", status: 429, body: echo, reason: "http_status", class: "4xx"},
		{name: "unavailable", status: 503, body: echo, reason: "http_status", class: "5xx"},
		{name: "unrecognized_status", status: 799, body: echo, reason: "http_status", class: "other"},
		{name: "pending", status: 503, body: pending, reason: "migrations_pending", class: "5xx", sentinel: ErrAppNotMigrated},
		{name: "pending_wrong_status", status: 500, body: pending, reason: "http_status", class: "5xx"},
		{name: "pending_truncated", status: 503, body: `{"detail":"` + strings.Repeat("x", 1024) + `","code":"migrations_pending"}`, reason: "http_status", class: "5xx"},
		{name: "invalid_response", status: 200, body: `{"status":"` + keySyncSyntheticToken + `"}`, reason: "response_invalid", class: "2xx"},
		{name: "reader_error", status: 200, readError: true, reason: "response_invalid", class: "2xx"},
		{name: "false_status", status: 200, body: `{"status":false,"detail":"` + keySyncSyntheticToken + `"}`, reason: "status_false", class: "2xx"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			oldClient := keySyncHTTPClient
			t.Cleanup(func() { keySyncHTTPClient = oldClient })
			calls := 0
			keySyncHTTPClient = &http.Client{Transport: valueFreeKeyTransport(func(req *http.Request) (*http.Response, error) {
				calls++
				if req.Header.Get("Authorization") != "Bearer "+keySyncSyntheticToken {
					t.Error("sync lost the admin bearer")
				}
				if tc.transportErr != nil {
					return nil, tc.transportErr
				}
				var reader io.Reader = strings.NewReader(tc.body)
				if tc.readError {
					reader = valueFreeKeyReadError{}
				}
				return &http.Response{StatusCode: tc.status, Header: make(http.Header), Body: io.NopCloser(reader), Request: req}, nil
			})}
			baseURL := tc.baseURL
			if baseURL == "" {
				baseURL = keySyncSyntheticURL
			}
			ks := NewKeyStore(config.AuthConfig{})
			err := SyncKeysFromControlPlane(context.Background(), baseURL, keySyncSyntheticToken, ks)
			if err == nil {
				t.Fatal("expected a sync failure")
			}
			if tc.name == "request" && calls != 0 {
				t.Error("invalid request reached transport")
			}
			if tc.name != "request" && calls != 1 {
				t.Errorf("transport calls=%d, want 1", calls)
			}
			assertValueFreeKeyText(t, fmt.Sprintf("%v %+v %#v", err, err, err))
			for cause := err; cause != nil; cause = errors.Unwrap(cause) {
				assertValueFreeKeyText(t, fmt.Sprintf("%v %+v %#v", cause, cause, cause))
			}
			var diagnostic valueFreeKeyDiagnostic
			if !errors.As(err, &diagnostic) {
				t.Errorf("want finite typed key-sync diagnostic, got %T", err)
			} else {
				if diagnostic.Reason() != tc.reason || diagnostic.StatusClass() != tc.class {
					t.Errorf("diagnostic=(%q,%q), want (%q,%q)", diagnostic.Reason(), diagnostic.StatusClass(), tc.reason, tc.class)
				}
			}
			want := "key sync failed: reason=" + tc.reason + " status_class=" + tc.class
			if err.Error() != want {
				t.Errorf("diagnostic=%q, want %q", err.Error(), want)
			}
			for _, sentinel := range []error{ErrAppNotMigrated, context.Canceled, context.DeadlineExceeded} {
				if errors.Is(err, sentinel) != (tc.sentinel == sentinel) {
					t.Errorf("incorrect sentinel identity for %v", sentinel)
				}
			}
			if tc.transportErr != nil && errors.Is(err, tc.transportErr) {
				t.Error("raw transport cause was retained")
			}
			if ks.AppHasNoKeyTable() != (tc.sentinel == ErrAppNotMigrated) {
				t.Error("migration latch differs from latest answer")
			}
			if ks.KeyListLoaded() || ks.Count() != 0 {
				t.Error("failed sync changed loaded-list state")
			}
		})
	}
}

func TestKeySyncValueFreeProducerLogs(t *testing.T) {
	for _, tc := range []struct {
		name, body string
		count      int
		warning    bool
	}{
		{name: "empty", body: `{"status":true,"result":[]}`},
		{name: "malformed_expiry", body: fmt.Sprintf(`{"status":true,"result":[{"id":"bad","key_hash":%q,"expires_at":%q},{"id":"good","key_hash":%q}]}`, HashKey("SYNTHETIC-BAD-KEY"), keySyncSyntheticToken, HashKey("SYNTHETIC-GOOD-KEY")), count: 1, warning: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			oldClient, oldLogger := keySyncHTTPClient, slog.Default()
			t.Cleanup(func() { keySyncHTTPClient = oldClient; slog.SetDefault(oldLogger) })
			keySyncHTTPClient = &http.Client{Transport: valueFreeKeyTransport(func(req *http.Request) (*http.Response, error) {
				return &http.Response{StatusCode: 200, Header: make(http.Header), Body: io.NopCloser(strings.NewReader(tc.body)), Request: req}, nil
			})}
			var logs bytes.Buffer
			slog.SetDefault(slog.New(slog.NewJSONHandler(&logs, &slog.HandlerOptions{Level: slog.LevelDebug})))
			ks := NewKeyStore(config.AuthConfig{})
			if err := SyncKeysFromControlPlane(context.Background(), keySyncSyntheticURL, keySyncSyntheticToken, ks); err != nil {
				t.Fatal(err)
			}
			assertValueFreeKeyText(t, logs.String())
			if !ks.KeyListLoaded() || ks.Count() != tc.count {
				t.Errorf("loaded=%v count=%d", ks.KeyListLoaded(), ks.Count())
			}
			if tc.warning && !strings.Contains(logs.String(), "reason=key_invalid status_class=2xx") {
				t.Error("missing finite malformed-key warning")
			}
		})
	}
}
