package credentials

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// Eviction orders both local flights and Redis publication across resolvers.
func TestSharedLifecycleEvictionBarrier(t *testing.T) {
	for _, mode := range []string{"local", "redis", "other-resolver"} {
		t.Run(mode, func(t *testing.T) {
			rig := newLifecycleRig(t)
			r := rig.resolver
			if mode == "local" {
				r.cache = nil
			}
			purger := r
			if mode == "other-resolver" {
				var err error
				purger, err = NewResolver(ResolverOptions{Tokens: rig.source, Cache: r.cache, Codec: testCodec(t)})
				if err != nil {
					t.Fatal(err)
				}
			}
			started, release := make(chan struct{}), make(chan struct{})
			var calls atomic.Int32
			r.tokens = lifecycleFetch(func(context.Context, Key) (Token, error) {
				if calls.Add(1) == 1 {
					close(started)
					<-release
					return Token{AccessToken: "synthetic-prior", ExpiresAt: time.Now().Add(time.Hour), CredentialVersion: 1}, nil
				}
				return Token{AccessToken: "synthetic-current", ExpiresAt: time.Now().Add(time.Hour), CredentialVersion: 2}, nil
			})
			source := r.SharedAuth("org-a", "issues")
			old := make(chan error, 1)
			go func() { _, err := source.Header(context.Background()); old <- err }()
			<-started
			if _, err := purger.PurgeCredentials(context.Background(), "org-a", "", "issues"); err != nil {
				close(release)
				t.Fatal(err)
			}
			fresh := make(chan error, 1)
			go func() {
				h, err := source.Header(context.Background())
				if err == nil && h.Get("Authorization") != "Bearer synthetic-current" {
					err = ErrTokenChannel
				}
				fresh <- err
			}()
			select {
			case err := <-fresh:
				close(release)
				if err != nil {
					t.Error("post-eviction resolution did not serve the current grant")
				}
			case <-time.After(time.Second):
				close(release)
				<-fresh
				t.Error("post-eviction resolution joined the previous generation's fetch")
			}
			if err := <-old; err == nil {
				t.Error("pre-eviction response remained publishable")
			}
			if r.cache != nil {
				tok, ok, err := r.cache.Get(context.Background(), SharedKey("org-a", "issues"))
				if err != nil || (ok && tok.CredentialVersion != 2) {
					t.Error("previous grant repopulated the shared cache")
				}
			}
		})
	}
}

func TestSharedLifecycleRevocationRemainsAuthoritative(t *testing.T) {
	r := newLifecycleRig(t).resolver
	started, release := make(chan struct{}), make(chan struct{})
	var calls atomic.Int32
	r.tokens = lifecycleFetch(func(context.Context, Key) (Token, error) {
		if calls.Add(1) != 1 {
			return Token{}, &Refusal{Reason: RefusalRevoked}
		}
		close(started)
		<-release
		return Token{AccessToken: "synthetic-prior", ExpiresAt: time.Now().Add(time.Hour), CredentialVersion: 1}, nil
	})
	done := make(chan error, 1)
	source := r.SharedAuth("org-a", "issues")
	go func() { _, err := source.Header(context.Background()); done <- err }()
	<-started
	if _, err := r.PurgeCredentials(context.Background(), "org-a", "", "issues"); err != nil {
		close(release)
		t.Fatal(err)
	}
	close(release)
	if err := <-done; err == nil {
		t.Error("revocation did not fence the outstanding response")
	}
	if _, err := source.Header(context.Background()); err == nil {
		t.Error("revoked grant remained available from the shared cache")
	}
}

func TestSharedLifecycleReconnectUsesCurrentGrant(t *testing.T) {
	s := newSharedE2E(t, nil)
	started, release := make(chan struct{}), make(chan struct{})
	var calls atomic.Int32
	s.resolver.tokens = lifecycleFetch(func(context.Context, Key) (Token, error) {
		n := calls.Add(1)
		if n == 2 {
			close(started)
			<-release
		}
		if n <= 2 {
			return Token{AccessToken: "synthetic-prior", ExpiresAt: time.Now().Add(time.Hour), CredentialVersion: 1}, nil
		}
		return Token{AccessToken: "synthetic-current", ExpiresAt: time.Now().Add(time.Hour), CredentialVersion: 2}, nil
	})
	s.waitListed(t, "org-a")
	if _, err := s.cache.Purge(context.Background(), "org-a", "", "crm"); err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { _, err := s.resolver.SharedAuth("org-a", "crm").Header(context.Background()); done <- err }()
	<-started
	if _, err := s.resolver.PurgeCredentials(context.Background(), "org-a", "", "crm"); err != nil {
		close(release)
		t.Fatal(err)
	}
	close(release)
	<-done
	s.store.Set("org-a", sharedConfig(t, s.url, signedIn("cred-current")))
	s.waitListed(t, "org-a")
	if _, d := s.call(t, orgKeyA, "crm__echo"); d != nil {
		t.Fatal("current grant was refused")
	}
	got := s.upstreamAuth()
	if len(got) != 1 || got[0] != bearerHash("synthetic-current") {
		t.Fatal("reconnected tool call did not use the current grant")
	}
}

func lifecycleRequest(t *testing.T, f *e2e, bearer, method string, params any) []byte {
	t.Helper()
	post := func(session, method string, params any) *httptest.ResponseRecorder {
		body, err := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
		if err != nil {
			t.Fatal(err)
		}
		req := httptest.NewRequest(http.MethodPost, "/mcp", bytes.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Accept", "application/json, text/event-stream")
		req.Header.Set("Authorization", "Bearer "+bearer)
		if session != "" {
			req.Header.Set("MCP-Session-Id", session)
		}
		out := httptest.NewRecorder()
		f.srv.HandlePost(out, req)
		return out
	}
	init := post("", "initialize", map[string]any{"protocolVersion": mcp.ProtocolVersion, "capabilities": map[string]any{}, "clientInfo": map[string]any{"name": "lifecycle", "version": "1"}})
	session := init.Header().Get("MCP-Session-Id")
	if session == "" {
		t.Fatal("synthetic session initialization failed")
	}
	return post(session, method, params).Body.Bytes()
}

func TestPerUserLifecycleReadsRevalidateAfterListing(t *testing.T) {
	cases := []struct {
		method string
		params any
	}{
		{"resources/read", map[string]any{"uri": "mcp://issues/mock://docs/readme"}},
		{"prompts/get", map[string]any{"name": "issues__greet", "arguments": map[string]any{"who": "lifecycle"}}},
	}
	for _, tc := range cases {
		for _, reason := range []string{RefusalRevoked, RefusalExpired, RefusalNeedsReauth} {
			t.Run(tc.method+"/"+reason, func(t *testing.T) {
				f := newE2EPool(t, false)
				f.django.give(keyA1, "synthetic-prior", f.clock.now().Add(time.Hour), 1)
				f.django.give(keyA2, "synthetic-other", f.clock.now().Add(time.Hour), 1)
				if _, d := f.call(t, callerA1, "issues__echo"); d != nil {
					t.Fatal("initial listing failed")
				}
				f.django.refuse(keyA1, reason, testConnectHost+ConnectStartPath("issues"))
				if _, err := f.resolver.PurgeCredentials(context.Background(), "org-a", "u1", "issues"); err != nil {
					t.Fatal(err)
				}
				before := len(f.mock.State().ReceivedLast(0, tc.method))
				response := lifecycleRequest(t, f, callerA1, tc.method, tc.params)
				if len(f.mock.State().ReceivedLast(0, tc.method)) != before {
					t.Error("refused personal read reached upstream")
				}
				if !bytes.Contains(response, []byte("auth_required")) {
					t.Error("personal read did not return auth_required")
				}
				response = lifecycleRequest(t, f, callerA2, tc.method, tc.params)
				if bytes.Contains(response, []byte("auth_required")) || len(f.mock.State().ReceivedLast(0, tc.method)) != before+1 {
					t.Error("unaffected user's read was refused")
				}
				f.django.give(keyA1, "synthetic-current", f.clock.now().Add(time.Hour), 2)
				response = lifecycleRequest(t, f, callerA1, tc.method, tc.params)
				if bytes.Contains(response, []byte("auth_required")) || len(f.mock.State().ReceivedLast(0, tc.method)) != before+2 {
					t.Error("replacement personal grant did not restore reads")
				}
			})
		}
	}
}

// FetchCurrent checks authoritative state without another refresh/eviction.
// Extra methods compile on the red baseline, which incorrectly calls Fetch twice.
type shortRefreshSource struct {
	resolver          *Resolver
	refreshes, checks atomic.Int32
	ttl               time.Duration
}

func (s *shortRefreshSource) Fetch(ctx context.Context, k Key) (Token, error) {
	s.refreshes.Add(1)
	if _, err := s.resolver.PurgeCredentials(ctx, k.OrgID, k.UserID, k.ServerID); err != nil {
		return Token{}, err
	}
	return s.current(), nil
}
func (s *shortRefreshSource) FetchCurrent(context.Context, Key) (Token, error) {
	s.checks.Add(1)
	return s.current(), nil
}
func (s *shortRefreshSource) current() Token {
	return Token{AccessToken: "synthetic-refreshed", ExpiresAt: time.Now().Add(s.ttl), CredentialVersion: 2}
}
func TestPerUserLifecycleShortRefreshIsBounded(t *testing.T) {
	for _, ttl := range []time.Duration{90 * time.Second, 2 * time.Minute} {
		t.Run(ttl.String(), func(t *testing.T) {
			r := newLifecycleRig(t)
			old := r.client(t, "org-a", "u1")
			source := &shortRefreshSource{resolver: r.resolver, ttl: ttl}
			r.resolver.tokens = source
			auth, _, d := r.resolver.resolveUser(context.Background(), oauthUser("org-a", "u1"), "org-a", "issues")
			if d != nil || auth == nil {
				t.Fatal("valid short-lived refreshed grant was refused")
			}
			if source.refreshes.Load() != 1 || source.checks.Load() != 1 {
				t.Fatal("refresh/revalidation was not bounded to one each")
			}
			if !auth.ExpiresAt.After(time.Now()) || auth.Lifetime.Err() != nil {
				t.Fatal("refreshed lifetime is not usable")
			}
			lifecycleRefused(t, old)
		})
	}
}
