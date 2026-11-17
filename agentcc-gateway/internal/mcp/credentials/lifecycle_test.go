package credentials

import (
	"context"
	"encoding/json"
	"errors"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/tenancy"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The token service and upstream are synthetic. Retained handles stand for an
// existing session; cancellation is checked independently of the next call.
type lifecycleSource struct {
	mu               sync.Mutex
	tokens           map[Key]Token
	refusals         map[Key]error
	started, release chan struct{}
}

func (s *lifecycleSource) Fetch(_ context.Context, k Key) (Token, error) {
	s.mu.Lock()
	tok, err := s.tokens[k], s.refusals[k]
	started, release := s.started, s.release
	s.mu.Unlock()
	if started != nil {
		started <- struct{}{}
		<-release
	}
	return tok, err
}
func (s *lifecycleSource) set(k Key, tok Token, err error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.tokens[k], s.refusals[k] = tok, err
}

type lifecycleClient struct {
	closed  atomic.Bool
	started chan struct{}
}

func (c *lifecycleClient) Healthy() bool { return !c.closed.Load() }
func (c *lifecycleClient) Close() error  { c.closed.Store(true); return nil }
func (c *lifecycleClient) ListTools(context.Context) ([]json.RawMessage, error) {
	return []json.RawMessage{json.RawMessage(`{"name":"search"}`)}, nil
}
func (c *lifecycleClient) ListResources(context.Context) ([]json.RawMessage, error) { return nil, nil }
func (c *lifecycleClient) ListPrompts(context.Context) ([]json.RawMessage, error)   { return nil, nil }
func (c *lifecycleClient) CallTool(ctx context.Context, _ string, args json.RawMessage, _ mcp.CallOptions) (json.RawMessage, error) {
	if string(args) == `{"wait":true}` {
		c.started <- struct{}{}
		<-ctx.Done()
		return nil, ctx.Err()
	}
	return json.RawMessage(`{}`), nil
}
func (c *lifecycleClient) ReadResource(ctx context.Context, _ string, o mcp.CallOptions) (json.RawMessage, error) {
	return c.CallTool(ctx, "", nil, o)
}
func (c *lifecycleClient) GetPrompt(ctx context.Context, _ string, a json.RawMessage, o mcp.CallOptions) (json.RawMessage, error) {
	return c.CallTool(ctx, "", a, o)
}

type lifecycleRig struct {
	resolver *Resolver
	source   *lifecycleSource
	manager  *tenancy.Manager
	fail     atomic.Bool
	started  chan struct{}
}

func newLifecycleRig(t *testing.T) *lifecycleRig {
	t.Helper()
	redis, prefix := testRedis(t)
	cache := NewTokenCache(redis, testCodec(t), CacheOptions{Prefix: prefix})
	var err error
	r := &lifecycleRig{source: &lifecycleSource{tokens: map[Key]Token{}, refusals: map[Key]error{}}, started: make(chan struct{}, 8)}
	r.resolver, err = NewResolver(ResolverOptions{Tokens: r.source, Cache: cache, Codec: testCodec(t)})
	if err != nil {
		t.Fatal(err)
	}
	store := tenant.NewStore()
	for _, org := range []string{"org-a", "org-b"} {
		var cfg tenant.OrgConfig
		if err := json.Unmarshal([]byte(`{"org_id":"`+org+`","version":1,"mcp":{"enabled":true,"servers":{"issues":{"url":"https://synthetic.example.test/mcp","credential":{"mode":"per_user"}}}}}`), &cfg); err != nil {
			t.Fatal(err)
		}
		store.Set(org, &cfg)
	}
	r.manager = tenancy.New(tenancy.Options{Store: store, MCP: config.MCPConfig{}, Deps: func() *mcp.Deps {
		return &mcp.Deps{ManagedServers: noManagedServers{}, ClientFactory: func(mcp.ClientConfig) (mcp.UpstreamClient, error) {
			if r.fail.Load() {
				return nil, errors.New("synthetic connection refused")
			}
			return &lifecycleClient{started: r.started}, nil
		}}
	}})
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		_ = r.manager.Close(ctx)
	})
	for _, k := range []Key{keyA1, keyA2, {OrgID: "org-b", UserID: "u1", ServerID: "issues"}} {
		r.source.set(k, Token{AccessToken: "synthetic-original", ExpiresAt: time.Now().Add(time.Hour), CredentialVersion: 1}, nil)
	}
	return r
}
func (r *lifecycleRig) client(t *testing.T, org, user string) mcp.UpstreamClient {
	t.Helper()
	auth, _, denial := r.resolver.resolveUser(context.Background(), oauthUser(org, user), org, "issues")
	if denial != nil {
		t.Fatal("current credential was refused", denial.Code)
	}
	_, pool, err := r.manager.Resolve(org)
	if err != nil {
		t.Fatal(err)
	}
	c, err := pool.Client("issues", user, *auth)
	if err != nil {
		t.Fatal(err)
	}
	return c
}
func lifecycleCall(c mcp.UpstreamClient) error {
	_, err := c.CallTool(context.Background(), "search", nil, mcp.CallOptions{})
	return err
}
func lifecycleRefused(t *testing.T, c mcp.UpstreamClient) {
	t.Helper()
	if lifecycleCall(c) == nil {
		t.Fatal("prior credential handle remained callable")
	}
}
func lifecycleClosed(t *testing.T, c mcp.UpstreamClient) {
	t.Helper()
	deadline := time.NewTimer(time.Second)
	defer deadline.Stop()
	tick := time.NewTicker(time.Millisecond)
	defer tick.Stop()
	for c.Healthy() {
		select {
		case <-deadline.C:
			t.Fatal("invalid credential client remained cached/open")
		case <-tick.C:
		}
	}
}
func TestPerUserLifecycleAuthoritativeState(t *testing.T) {
	for _, reason := range []string{RefusalRevoked, RefusalExpired, RefusalNeedsReauth} {
		t.Run(reason, func(t *testing.T) {
			r := newLifecycleRig(t)
			old := r.client(t, "org-a", "u1")
			other := r.client(t, "org-a", "u2")
			otherOrg := r.client(t, "org-b", "u1")
			r.source.set(keyA1, Token{}, &Refusal{Reason: reason})
			_, _, d := r.resolver.resolveUser(context.Background(), oauthUser("org-a", "u1"), "org-a", "issues")
			requireAuthRequired(t, d, authReason(reason))
			lifecycleRefused(t, old)
			lifecycleClosed(t, old)
			if lifecycleCall(other) != nil || lifecycleCall(otherOrg) != nil {
				t.Fatal("another user's credential lifetime changed")
			}
		})
	}
}
func TestPerUserLifecyclePurgeCancelsActiveSession(t *testing.T) {
	r := newLifecycleRig(t)
	old := r.client(t, "org-a", "u1")
	other := r.client(t, "org-a", "u2")
	done := make(chan error, 1)
	go func() {
		_, err := old.CallTool(context.Background(), "search", json.RawMessage(`{"wait":true}`), mcp.CallOptions{})
		done <- err
	}()
	<-r.started
	if _, err := r.resolver.PurgeCredentials(context.Background(), "org-a", "u1", "issues"); err != nil {
		t.Fatal(err)
	}
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("active call returned success after eviction")
		}
	case <-time.After(time.Second):
		t.Fatal("active session was not cancelled by Disconnect eviction")
	}
	lifecycleRefused(t, old)
	lifecycleClosed(t, old)
	if lifecycleCall(other) != nil {
		t.Fatal("another user's session was cancelled")
	}
}
func TestPerUserLifecycleDeadlineClosesIdleClient(t *testing.T) {
	r := newLifecycleRig(t)
	r.source.set(keyA1, Token{AccessToken: "synthetic-short", ExpiresAt: time.Now().Add(200 * time.Millisecond), CredentialVersion: 1}, nil)
	old := r.client(t, "org-a", "u1")
	lifecycleClosed(t, old)
	lifecycleRefused(t, old)
}
func TestPerUserLifecycleFailedReconnectWithdrawsPriorVersion(t *testing.T) {
	r := newLifecycleRig(t)
	old := r.client(t, "org-a", "u1")
	// A new grant version is a replacement even if the vendor reuses its token.
	r.source.set(keyA1, Token{AccessToken: "synthetic-original", ExpiresAt: time.Now().Add(time.Hour), CredentialVersion: 2}, nil)
	auth, _, d := r.resolver.resolveUser(context.Background(), oauthUser("org-a", "u1"), "org-a", "issues")
	if d != nil {
		t.Fatal(d.Code)
	}
	r.fail.Store(true)
	_, pool, err := r.manager.Resolve("org-a")
	if err != nil {
		t.Fatal(err)
	}
	if _, err = pool.Client("issues", "u1", *auth); err == nil {
		t.Fatal("failed reconnect reused the prior client")
	}
	lifecycleRefused(t, old)
	lifecycleClosed(t, old)
}

type lifecycleFetch func(context.Context, Key) (Token, error)

func (f lifecycleFetch) Fetch(ctx context.Context, k Key) (Token, error) { return f(ctx, k) }

// These fixtures return stored state; unlike Django they never proactively refresh.
func (f lifecycleFetch) FetchCurrent(ctx context.Context, k Key) (Token, error) { return f(ctx, k) }
func (s *lifecycleSource) FetchCurrent(ctx context.Context, k Key) (Token, error) {
	return s.Fetch(ctx, k)
}

func TestPerUserLifecycleRefreshedGrantRevalidatesAfterPurge(t *testing.T) {
	r := newLifecycleRig(t)
	old := r.client(t, "org-a", "u1")
	var calls atomic.Int32
	r.resolver.tokens = lifecycleFetch(func(ctx context.Context, k Key) (Token, error) {
		if calls.Add(1) == 1 {
			if _, err := r.resolver.PurgeCredentials(ctx, k.OrgID, k.UserID, k.ServerID); err != nil {
				return Token{}, err
			}
		}
		return Token{AccessToken: "synthetic-refreshed", ExpiresAt: time.Now().Add(time.Hour), CredentialVersion: 2}, nil
	})
	fresh := r.client(t, "org-a", "u1")
	if calls.Load() != 2 {
		t.Fatal("the grant was not revalidated after in-fetch eviction")
	}
	if lifecycleCall(fresh) != nil {
		t.Fatal("a successfully refreshed grant required another sign-in")
	}
	lifecycleRefused(t, old)
	lifecycleClosed(t, old)
}

func TestPerUserLifecyclePurgeOrdersConcurrentFetch(t *testing.T) {
	r := newLifecycleRig(t)
	r.source.started, r.source.release = make(chan struct{}, 1), make(chan struct{})
	done := make(chan *mcp.Denial, 1)
	go func() {
		_, _, d := r.resolver.resolveUser(context.Background(), oauthUser("org-a", "u1"), "org-a", "issues")
		done <- d
	}()
	<-r.source.started
	if _, err := r.resolver.PurgeCredentials(context.Background(), "org-a", "u1", "issues"); err != nil {
		t.Fatal(err)
	}
	r.source.set(keyA1, Token{}, &Refusal{Reason: RefusalRevoked})
	close(r.source.release)
	if d := <-done; d == nil {
		t.Fatal("pre-purge fetch published a credential after eviction")
	}
	r.source.mu.Lock()
	r.source.started = nil
	r.source.release = nil
	r.source.mu.Unlock()
	r.source.set(keyA1, Token{AccessToken: "synthetic-new", ExpiresAt: time.Now().Add(time.Hour), CredentialVersion: 2}, nil)
	fresh := r.client(t, "org-a", "u1")
	if lifecycleCall(fresh) != nil {
		t.Fatal("fresh reconnect did not become usable")
	}
}
