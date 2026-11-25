package middleware

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// A self-hosted evaluation reaches the managed gateway with a licence token,
// and the licence's per-minute limit refuses it here, before any plugin. The
// evaluator times its one retry from Retry-After, so the refusal has to say
// when the next minute's window opens.
func TestLicenseAuthRateLimitRefusalCarriesRetryAfter(t *testing.T) {
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		addr = "localhost:6379"
	}
	client, err := redisstate.NewClient(redisstate.Config{Address: addr, Timeout: time.Second})
	if err != nil {
		t.Skipf("skipping: Redis not available at %s: %v", addr, err)
	}
	defer client.Close()

	privateKey, publicPEM := testRSAKeyPair(t)
	token := signTestLicenseToken(t, privateKey, licenseClaims{
		LicenseID:  fmt.Sprintf("lic_retry_after_%d", time.Now().UnixNano()),
		InstanceID: "inst_test",
		Scope:      "enterprise",
		Services:   []string{"turing"},
		Models:     []string{"turing_small"},
		ExpiresAt:  time.Now().Add(time.Hour).Unix(),
		JTI:        "tok_retry_after",
	})
	handler := LicenseAuth(
		config.LicenseAuthConfig{Enabled: true, PublicKey: publicPEM, RateLimitRPM: 1},
		redisstate.NewLicenseStore(client),
	)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}))
	send := func() *httptest.ResponseRecorder {
		req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(`{"model":"turing_small"}`))
		req.Header.Set("Authorization", "Bearer "+token)
		rr := httptest.NewRecorder()
		handler.ServeHTTP(rr, req)
		return rr
	}

	// One request a minute: the next one in the same minute is refused. A
	// probe that straddles a minute boundary proves nothing, so try again.
	for attempt := 0; attempt < 5; attempt++ {
		before := time.Now()
		rr := send()
		after := time.Now()
		if rr.Code == http.StatusNoContent {
			continue // this minute still had room
		}
		if rr.Code != http.StatusTooManyRequests {
			t.Fatalf("unexpected status %d: %s", rr.Code, rr.Body.String())
		}
		if before.Unix()/60 != after.Unix()/60 {
			continue
		}

		header := rr.Header().Get("Retry-After")
		wait, err := strconv.Atoi(header)
		if err != nil {
			t.Fatalf("licence rate-limit 429 has no usable Retry-After (%q); body %s", header, rr.Body.String())
		}
		if wait < 1 || wait > 60 {
			t.Fatalf("Retry-After = %d, want 1-60 for a one-minute window", wait)
		}
		nextWindow := time.Unix((before.Unix()/60+1)*60, 0)
		if retryAt := after.Add(time.Duration(wait) * time.Second); retryAt.Before(nextWindow) {
			t.Fatalf("Retry-After = %d sends the retry to %s, before the window reopens at %s",
				wait, retryAt.Format(time.RFC3339Nano), nextWindow.Format(time.RFC3339Nano))
		}
		return
	}
	t.Fatal("no refusal observed within one minute")
}
