package server

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// closedAddr is a loopback address nothing listens on: a port the system
// handed out and that was closed again.
func closedAddr(t *testing.T) string {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	addr := ln.Addr().String()
	ln.Close()
	return "http://" + addr
}

// TestRotationValidationNotReached: a validation call
// that got no answer (nothing listening, or the provider's timeout) says so
// in the 422 answer, and never reads as the provider refusing the key; a
// provider that answered 401 reads as a refusal.
func TestRotationValidationNotReached(t *testing.T) {
	slow := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.Copy(io.Discard, r.Body)
		time.Sleep(2 * time.Second)
	}))
	defer slow.Close()
	refusing := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.Copy(io.Discard, r.Body)
		w.WriteHeader(http.StatusUnauthorized)
		io.WriteString(w, `{"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key"}}`)
	}))
	defer refusing.Close()

	for _, tc := range []struct {
		name, format, url string
		timeout           time.Duration
		want, notWant     string
	}{
		{"anthropic, nothing listening", "anthropic", closedAddr(t), 0, "got no answer from the provider", "refused the call"},
		{"openai, nothing listening", "openai", closedAddr(t), 0, "got no answer from the provider", "refused the call"},
		{"anthropic, the provider's timeout", "anthropic", slow.URL, 200 * time.Millisecond, "got no answer from the provider", "refused the call"},
		{"anthropic, 401", "anthropic", refusing.URL, 0, "the provider refused the call: status 401", "no answer"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			srv := rotServer(t, "http://127.0.0.1:9", 20*time.Millisecond, func(c *config.Config) {
				c.Providers["p"] = config.ProviderConfig{
					BaseURL: tc.url, APIKey: rotOld, APIFormat: tc.format, Models: []string{"m-rot"},
					DefaultTimeout: tc.timeout,
					Rotation:       config.RotationConfig{Enabled: true, DrainPeriod: 20 * time.Millisecond},
				}
			})
			if w := rotDo(srv, http.MethodPost, "/-/admin/providers/p/rotate", `{"new_key":"`+rotNew+`"}`, rotAdmin); w.Code != http.StatusOK {
				t.Fatalf("rotate -> %d %s", w.Code, w.Body.String())
			}
			w := rotDo(srv, http.MethodPost, "/-/admin/providers/p/rotate/promote", "", rotAdmin)
			body := w.Body.String()
			if w.Code != http.StatusUnprocessableEntity {
				t.Fatalf("promote -> %d %s; want 422", w.Code, body)
			}
			if !strings.Contains(body, tc.want) || strings.Contains(body, tc.notWant) {
				t.Errorf("422 answer %s; want it to say %q and not %q", body, tc.want, tc.notWant)
			}
			if strings.Contains(body, rotNew[:8]) {
				t.Errorf("422 answer %s carries part of the new key", body)
			}
		})
	}
}

// TestKeyFreeErrorNoAnswer: every error shape a provider adapter returns for
// a call that got no answer (a dial error as provider_0, a deadline as
// gateway_timeout, a transport error) reads as no answer and keeps no key.
func TestKeyFreeErrorNoAnswer(t *testing.T) {
	const key = "sk-key-free-0123456789abcdef"
	for name, err := range map[string]error{
		"provider_0":      models.ErrUpstreamProvider(0, "anthropic: request failed: dial tcp 127.0.0.1:1: connect: connection refused"),
		"gateway_timeout": models.ErrGatewayTimeout("anthropic: request timed out"),
		// What http.Client.Do returns (the openai and azure models list
		// hand it back as it is).
		"transport": &url.Error{Op: "Get", URL: "http://127.0.0.1:1/v1/models?k=" + key,
			Err: errors.New("dial tcp 127.0.0.1:1: connect: connection refused")},
		"deadline": fmt.Errorf("list models: %w", context.DeadlineExceeded),
	} {
		got := keyFreeError(err, key).Error()
		if !strings.Contains(got, "got no answer from the provider") || strings.Contains(got, "refused the call") || strings.Contains(got, key) {
			t.Errorf("%s: %q; want \"got no answer from the provider\", no refusal, no key", name, got)
		}
	}
	if got := keyFreeError(&models.APIError{Status: 401, Type: "authentication_error", Code: "invalid_api_key", Message: "bad key " + key}, key).Error(); !strings.HasPrefix(got, "the provider refused the call: status 401") || strings.Contains(got, key) {
		t.Errorf("401: %q; want a refusal naming status 401, no key", got)
	}
}
