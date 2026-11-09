package common

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/netguard"
)

func TestR5ExactErrorCopyAndClassification(t *testing.T) {
	key := "opaque-credential-abcdefghijklmnopqrstuvwxyz"
	original := &models.APIError{Status: 429, UpstreamStatus: 429, ProviderStatus: 429, Message: "before " + key + " after", Code: key, Type: key, ProviderRejected: true, RetryAfterSec: 7, BillingEvidence: &models.ProviderBillingEvidence{}}
	got := ExactAPIError(original, key)
	if got == original || original.Code != key || original.Message != "before "+key+" after" {
		t.Fatal("changed original")
	}
	if got.Message != "before [REDACTED] after" || got.Code != "[REDACTED]" || got.Type != "[REDACTED]" {
		t.Fatal("diagnostic fields not scrubbed")
	}
	if got.Status != 429 || got.UpstreamStatus != 429 || got.ProviderStatus != 429 || got.BillingEvidence != original.BillingEvidence || !got.ProviderRejected || got.RetryAfterSec != 7 {
		t.Fatal("lost status/billing/retry evidence")
	}
	wrapped := ExactError(fmt.Errorf("outer %s: %w", key, original), key)
	var api *models.APIError
	if !errors.As(wrapped, &api) || api.Message != got.Message || strings.Contains(wrapped.Error(), key) {
		t.Fatal("wrapped APIError leaked or lost classification")
	}
	if errors.Unwrap(wrapped) != nil {
		t.Fatal("raw error exposed by unwrap")
	}
	timeout := ExactError(fmt.Errorf("dial %s: %w", key, context.DeadlineExceeded), key)
	if !errors.Is(timeout, context.DeadlineExceeded) || !timedOut(timeout) || strings.Contains(timeout.Error(), key) {
		t.Fatal("timeout classification or scrub failed")
	}
}

func TestR5ActualHeadersAndHeldValues(t *testing.T) {
	req, _ := http.NewRequest("GET", "https://example.invalid", nil)
	req.Header.Set("Authorization", "Bearer dispatched-token-not-refreshed")
	req.Header.Set("X-Custom-Secret", "inherited-opaque-secret")
	req.Header.Set("Cookie", "session=session-cookie-value")
	req.Header.Set("X-Version", "2026-11-09")
	req.URL.User = url.UserPassword("user", "url-credential-value")
	keys := ErrorCredentials(req, "instance-held-value")
	for _, key := range []string{"dispatched-token-not-refreshed", "inherited-opaque-secret", "session-cookie-value", "url-credential-value", "instance-held-value"} {
		got := ExactError(errors.New("before "+key+" after"), keys...)
		if got.Error() != "before [REDACTED] after" {
			t.Errorf("header credential not scrubbed: %q", got.Error())
		}
	}
	if got := ExactError(errors.New("version 2026-11-09; other-owner-value"), keys...).Error(); got != "version 2026-11-09; other-owner-value" {
		t.Fatal("ordinary settings changed")
	}
	req.Header.Set("Authorization", "Bearer later-token")
	if got := ExactError(errors.New("dispatched-token-not-refreshed"), keys...).Error(); got != "[REDACTED]" {
		t.Fatal("request credential snapshot mutated")
	}
}

// Classification survives the scrub where it carries no text, and a typed
// error that embeds the request URL is not handed back to errors.As.
func TestR5ExactErrorKeepsGuardRefusalAndHidesURLError(t *testing.T) {
	key := "query-credential-abcdefghijklmnopqrstuvwxyz"
	blocked := &netguard.BlockedError{}
	urlErr := &url.Error{Op: "Get", URL: "http://example.invalid/?key=" + key, Err: blocked}
	wrapped := ExactError(fmt.Errorf("downloading image: %w", urlErr), key)
	var got *netguard.BlockedError
	if !errors.As(wrapped, &got) || got != blocked {
		t.Fatal("guard refusal classification lost")
	}
	var leaked *url.Error
	if errors.As(wrapped, &leaked) {
		t.Fatal("url.Error with the request URL is reachable through errors.As")
	}
	if strings.Contains(wrapped.Error(), key) {
		t.Fatal("credential in wrapped text")
	}
}
