package mcp

import (
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-mcpmock/mcpmock"
)

// The listing taken when a catalog provider is added sends the key as the
// pool will. For a key whose provider declares a prefix other than "Bearer "
// in Authorization (PagerDuty's "Token token="), Django puts the record's
// prefix before the key in the probe's api_key value, and the probe sends
// an api_key header value exactly as given.

const probePrefixKey = "pd-probe-test-value-51b8"

// TestProbeSendsADeclaredPrefixKeyAsGiven: every request of the listing
// carries "Authorization: Token token=<key>", once, never "Bearer", never in
// the query, and neither the result nor the gateway log holds the key.
func TestProbeSendsADeclaredPrefixKeyAsGiven(t *testing.T) {
	const want = "Token token=" + probePrefixKey
	logs := &lockedBuffer{}
	old := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(logs, &slog.HandlerOptions{Level: slog.LevelDebug})))
	defer slog.SetDefault(old)

	fix, err := mcpmock.LoadFixtures(mcpFixtures)
	if err != nil {
		t.Fatal(err)
	}
	era, err := mcpmock.ParseEra("2025-06-18")
	if err != nil {
		t.Fatal(err)
	}
	m, err := mcpmock.New(mcpmock.Config{Era: era, Auth: "none", Path: "/mcp", ServerName: "pd-probe"}, fix)
	if err != nil {
		t.Fatal(err)
	}
	var mu sync.Mutex
	var seen [][]string
	var leaked bool
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got := r.Header.Values("Authorization")
		mu.Lock()
		seen = append(seen, got)
		leaked = leaked || strings.Contains(r.URL.RawQuery, probePrefixKey)
		mu.Unlock()
		if len(got) != 1 || got[0] != want {
			http.Error(w, "missing or wrong key", http.StatusUnauthorized)
			return
		}
		m.Handler().ServeHTTP(w, r)
	}))
	defer srv.Close()

	s := probeServer(t, hostOf(t, srv.URL))
	code, raw, res := probe(t, s, probeBody(map[string]any{"org_id": "org-a", "url": srv.URL + "/mcp", "transport": "http",
		"auth": map[string]any{"type": "api_key", "header": "Authorization", "value": want}}))
	if code != http.StatusOK || !res.OK || res.ToolCount == nil || *res.ToolCount == 0 {
		t.Fatalf("status %d, body %s; want the listing the key unlocks", code, raw)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(seen) < 2 || leaked {
		t.Fatalf("the upstream saw %d requests, the key in a query %t", len(seen), leaked)
	}
	for i, got := range seen {
		if len(got) != 1 || got[0] != want {
			t.Errorf("request %d: Authorization has %d value(s), Token token=<key> %t", i, len(got), len(got) == 1 && got[0] == want)
		}
	}
	if strings.Contains(raw, probePrefixKey) || strings.Contains(logs.String(), probePrefixKey) {
		t.Fatal("the probe's result or the gateway log carries the key")
	}
	if !strings.Contains(logs.String(), "mcp probe") {
		t.Fatal("the probe logged nothing; the check would pass vacuously")
	}
}

// TestProbeRefusesAControlCharacterInTheCredential: an inline credential
// whose value holds CR, LF, NUL, a tab or DEL is refused as a bad request
// before anything is dialed, and the refusal never echoes the value. Go's
// transport would refuse CR and LF itself, as an unreachable upstream, but
// a tab would reach the header.
func TestProbeRefusesAControlCharacterInTheCredential(t *testing.T) {
	var reached atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		reached.Add(1)
		w.WriteHeader(http.StatusUnauthorized)
	}))
	defer srv.Close()
	s := probeServer(t, hostOf(t, srv.URL))
	for _, value := range []string{
		"Token token=" + probePrefixKey + "\r\nX-Injected: 1",
		"Token token=" + probePrefixKey + "\n",
		"Token\ttoken=" + probePrefixKey,
		probePrefixKey + "\x00",
		probePrefixKey + "\x7f",
	} {
		for _, auth := range []map[string]any{
			{"type": "api_key", "header": "Authorization", "value": value},
			{"type": "api_key", "header": "X-Api-Key", "value": value},
			{"type": "bearer", "value": value},
		} {
			code, raw, _ := probe(t, s, probeBody(map[string]any{"org_id": "org-a", "url": srv.URL + "/mcp", "auth": auth}))
			if code != http.StatusBadRequest || !strings.Contains(raw, "invalid_probe_request") || strings.Contains(raw, probePrefixKey) {
				t.Errorf("%s %v with a control character: status %d (%s), want 400 invalid_probe_request without the value",
					auth["type"], auth["header"], code, strings.ReplaceAll(raw, probePrefixKey, "<key>"))
			}
		}
	}
	if n := reached.Load(); n != 0 {
		t.Fatalf("%d probes with a control character in the credential reached the upstream", n)
	}
}
