package gauth

import (
	"crypto/rand"
	"crypto/rsa"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// tokenServer is a stand-in for Google's token endpoint. It answers with fail's status, or
// with a numbered token, after delay.
type tokenServer struct {
	*httptest.Server
	hits  atomic.Int64
	fail  atomic.Int64 // 0 = succeed, else the status to return
	delay time.Duration
}

func newTokenServer(t *testing.T, delay time.Duration) *tokenServer {
	t.Helper()
	ts := &tokenServer{delay: delay}
	ts.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := ts.hits.Add(1)
		time.Sleep(ts.delay)
		if status := ts.fail.Load(); status != 0 {
			w.WriteHeader(int(status))
			return
		}
		fmt.Fprintf(w, `{"access_token":"tok-%d","expires_in":3600}`, n)
	}))
	t.Cleanup(ts.Close)
	return ts
}

func newTestProvider(t *testing.T, tokenURL string) *TokenProvider {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	return &TokenProvider{clientEmail: "test@example.iam.gserviceaccount.com", privateKey: key, tokenURL: tokenURL, scope: ScopeCloudPlatform}
}

// When Google's token endpoint is failing, the requests queued behind a failed exchange must
// share its result rather than each repeat the exchange while holding the provider's lock.
func TestTokenFailedExchangeIsSharedByWaitingRequests(t *testing.T) {
	ts := newTokenServer(t, 200*time.Millisecond)
	ts.fail.Store(http.StatusInternalServerError)
	tp := newTestProvider(t, ts.URL)

	const callers = 20
	var wg sync.WaitGroup
	start := time.Now()
	for i := 0; i < callers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if tok := tp.Token(); tok != "" {
				t.Errorf("Token() = %q while the token endpoint fails", tok)
			}
		}()
	}
	wg.Wait()

	if hits := ts.hits.Load(); hits != 1 {
		t.Errorf("token endpoint hit %d times by %d concurrent requests, want 1", hits, callers)
	}
	if elapsed := time.Since(start); elapsed > time.Second {
		t.Errorf("%d requests took %v to get past one failed exchange", callers, elapsed)
	}

	// The failure is not remembered for long: once the endpoint recovers, so does the provider.
	ts.fail.Store(0)
	time.Sleep(1100 * time.Millisecond)
	if tok := tp.Token(); tok == "" {
		t.Error("Token() still empty after the token endpoint recovered")
	}
}

// Requests in flight when a token is rejected each get a 401. Only the first should discard the
// token; the rest must not throw away the fresh token fetched in the meantime.
func TestInvalidateIgnoresAnAlreadyReplacedToken(t *testing.T) {
	ts := newTokenServer(t, 0)
	tp := newTestProvider(t, ts.URL)

	first := tp.Token()
	tp.Invalidate(first)
	second := tp.Token()
	if second == first || second == "" {
		t.Fatalf("after invalidating %q, Token() = %q, want a new token", first, second)
	}

	tp.Invalidate(first) // a late 401 for a request that was sent with the old token
	if got := tp.Token(); got != second {
		t.Errorf("Token() = %q after a stale invalidation, want %q kept", got, second)
	}
	if hits := ts.hits.Load(); hits != 2 {
		t.Errorf("token endpoint hit %d times, want 2", hits)
	}
}
