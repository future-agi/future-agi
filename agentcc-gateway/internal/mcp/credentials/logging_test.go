package credentials

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// A token never reaches a log line, an error, a denial or a printed value:
// across a fetch, a cached call, a refusal and a failing channel.
func TestTokenNeverLogged(t *testing.T) {
	var logs bytes.Buffer
	prev := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&logs, &slog.HandlerOptions{Level: slog.LevelDebug})))
	t.Cleanup(func() { slog.SetDefault(prev) })

	redis, prefix := testRedis(t)
	d := newFakeDjango(t)
	clock := newTestClock()
	r, _ := testResolver(t, d, redis, prefix, clock)
	const value = "m25-test-token-must-not-print"
	d.give(keyA1, value, clock.now().Add(time.Hour), 1)
	ctx := context.Background()
	var seen []string
	for i := 0; i < 2; i++ {
		auth, ref, den := r.resolveUser(ctx, oauthUser("org-a", "u1"), "org-a", "issues")
		if den != nil || bearerOf(auth) != value {
			t.Fatalf("call %d: %v", i, den)
		}
		seen = append(seen, fmt.Sprint(auth), fmt.Sprintf("%v %+v %#v", *auth, *auth, *auth), fmt.Sprintf("%+v", ref))
	}
	tok, _ := tokenClientFor(t, d).Fetch(ctx, keyA1)
	slog.Info("a token value", "token", tok)
	seen = append(seen, fmt.Sprint(tok), fmt.Sprintf("%+v %#v", tok, tok))

	d.refuse(keyA2, RefusalRevoked, testConnectHost+ConnectStartPath("issues"))
	_, _, den := r.resolveUser(ctx, oauthUser("org-a", "u2"), "org-a", "issues")
	raw, _ := json.Marshal(den.Object())
	seen = append(seen, string(raw), den.Error())

	d.failWith(500)
	r.cache.Purge(ctx, "org-a", "u1", "issues")
	_, _, den = r.resolveUser(ctx, oauthUser("org-a", "u1"), "org-a", "issues")
	raw, _ = json.Marshal(den.Object())
	seen = append(seen, string(raw), den.Error())
	_, err := tokenClientFor(t, d).Fetch(ctx, keyA1)
	seen = append(seen, err.Error())

	for _, s := range append(seen, logs.String()) {
		if strings.Contains(s, value) || strings.Contains(s, testDjangoSecret) {
			t.Fatalf("a token or the secret was printed: %q", s)
		}
	}
	if !strings.Contains(logs.String(), "mcp per-user credential unavailable") {
		t.Fatalf("the failing channel was not logged: %s", logs.String())
	}
}

// The resolver answers mcp.CredentialResolver and mcp.CredentialPurger.
var (
	_ mcp.CredentialResolver = (*Resolver)(nil)
	_ mcp.CredentialPurger   = (*Resolver)(nil)
	_ TokenSource            = (*TokenClient)(nil)
)
