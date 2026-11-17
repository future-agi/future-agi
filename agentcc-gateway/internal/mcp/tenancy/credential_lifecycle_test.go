package tenancy

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

func credentialConfig(state, value, extra string) string {
	return fmt.Sprintf(`{"enabled":true,"servers":{"github":{"url":%q,"credential":{"mode":"shared","credential_id":"credential-one","state":%q,"secret":{"type":"bearer","value":%q}%s}}}}`, urlA, state, value, extra)
}

func requireCredentialRefusal(t *testing.T, err error) {
	t.Helper()
	var d *mcp.Denial
	if !errors.As(err, &d) || d.Code != "auth_required" || d.Type != "auth_required" || !d.InBand() {
		t.Fatal("call must return the in-band auth_required refusal")
	}
}

func credentialCall(c mcp.UpstreamClient) error {
	_, err := c.CallTool(context.Background(), "search", json.RawMessage(`{}`), mcp.CallOptions{})
	return err
}

func TestCredentialLifecycleLapsedSession(t *testing.T) {
	for _, state := range []string{"revoked", "expired", "disabled", "superseded", "needs_reauth"} {
		t.Run(state, func(t *testing.T) {
			r := newRig(t, Options{})
			u := r.factory.upstream(urlA, "search")
			r.push("org-a", 1, credentialConfig("active", "synthetic-first", ""))
			r.push("org-b", 1, credentialConfig("active", "synthetic-other", ""))
			_, a := r.resolve("org-a")
			_, b := r.resolve("org-b")
			old, err := a.Client("github", "", mcp.AuthOverride{})
			if err != nil {
				t.Fatal(err)
			}
			other, err := b.Client("github", "", mcp.AuthOverride{})
			if err != nil {
				t.Fatal(err)
			}
			if err := credentialCall(old); err != nil {
				t.Fatal(err)
			}
			r.push("org-a", 2, credentialConfig(state, "", ""))
			// No Resolve: a retained session/client must observe the write itself.
			requireCredentialRefusal(t, credentialCall(old))
			_, err = a.Client("github", "", mcp.AuthOverride{})
			requireCredentialRefusal(t, err)
			eventually(t, "lapsed client closed", func() bool { return !old.Healthy() })
			if err := credentialCall(other); err != nil {
				t.Fatal("other org lost its client")
			}
			if got := len(u.called()); got != 2 {
				t.Fatalf("upstream calls = %d, want 2", got)
			}
			if got := r.factory.totalBuilds(); got != 2 {
				t.Fatalf("lapsed credential caused a connection: builds=%d", got)
			}
		})
	}
}

func TestCredentialLifecycleExpiryDeadline(t *testing.T) {
	r := newRig(t, Options{})
	u := r.factory.upstream(urlA, "search")
	expires := time.Now().Add(250 * time.Millisecond).UTC().Format(time.RFC3339Nano)
	r.push("org-a", 1, credentialConfig("active", "synthetic-first", `,"expires_at":"`+expires+`"`))
	_, pool := r.resolve("org-a")
	c, err := pool.Client("github", "", mcp.AuthOverride{})
	if err != nil {
		t.Fatal(err)
	}
	eventually(t, "expiry closes the idle upstream without another request", func() bool { return !c.Healthy() })
	requireCredentialRefusal(t, credentialCall(c))
	if len(u.called()) != 0 {
		t.Fatal("expired credential reached upstream")
	}
}

func TestCredentialLifecycleFailedReplacement(t *testing.T) {
	r := newRig(t, Options{RetryInitial: time.Hour, RetryMax: time.Hour})
	u := r.factory.upstream(urlA, "search")
	r.push("org-a", 1, credentialConfig("active", "synthetic-first", ""))
	_, pool := r.resolve("org-a")
	old, err := pool.Client("github", "", mcp.AuthOverride{})
	if err != nil {
		t.Fatal(err)
	}
	d := *r.deps.Load()
	d.ClientFactory = func(mcp.ClientConfig) (mcp.UpstreamClient, error) {
		return nil, errors.New("synthetic authentication refused")
	}
	r.deps.Store(&d)
	r.push("org-a", 2, credentialConfig("active", "synthetic-second", ""))
	r.resolve("org-a")
	requireCredentialRefusal(t, credentialCall(old))
	_, err = pool.Client("github", "", mcp.AuthOverride{})
	requireCredentialRefusal(t, err)
	eventually(t, "superseded client closed", func() bool { return !old.Healthy() })
	if len(u.called()) != 0 {
		t.Fatal("replacement failure used the old credential")
	}
}

type activeCredentialClient struct {
	*fakeClient
	started chan struct{}
}

func (c *activeCredentialClient) CallTool(ctx context.Context, _ string, _ json.RawMessage, _ mcp.CallOptions) (json.RawMessage, error) {
	close(c.started)
	select {
	case <-ctx.Done():
		return nil, ctx.Err()
	case <-time.After(3 * time.Second):
		return nil, errors.New("active call was not cancelled")
	}
}

func TestCredentialLifecycleRevokeCancelsActiveCall(t *testing.T) {
	r := newRig(t, Options{})
	u := r.factory.upstream(urlA, "search")
	c := &activeCredentialClient{fakeClient: &fakeClient{up: u}, started: make(chan struct{})}
	d := *r.deps.Load()
	d.ClientFactory = func(mcp.ClientConfig) (mcp.UpstreamClient, error) { return c, nil }
	r.deps.Store(&d)
	r.push("org-a", 1, credentialConfig("active", "synthetic-first", ""))
	_, pool := r.resolve("org-a")
	client, err := pool.Client("github", "", mcp.AuthOverride{})
	if err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { done <- credentialCall(client) }()
	<-c.started
	r.push("org-a", 2, credentialConfig("revoked", "", ""))
	requireCredentialRefusal(t, <-done)
	eventually(t, "active upstream closed", c.closed.Load)
}

func TestCredentialLifecycleReconnectOrdering(t *testing.T) {
	for _, revoke := range []bool{true, false} {
		t.Run(fmt.Sprintf("revoke_%t", revoke), func(t *testing.T) {
			r := newRig(t, Options{InitialWait: -1})
			u := r.factory.upstream(urlA, "search")
			started, release := make(chan struct{}), make(chan struct{})
			var once sync.Once
			t.Cleanup(func() { once.Do(func() { close(release) }) })
			late := &fakeClient{up: u}
			d := *r.deps.Load()
			d.ClientFactory = func(cfg mcp.ClientConfig) (mcp.UpstreamClient, error) {
				if cfg.Auth.Token == "synthetic-second" {
					close(started)
					<-release
					return late, nil
				}
				return r.factory.build(cfg)
			}
			r.deps.Store(&d)
			r.push("org-a", 1, credentialConfig("active", "synthetic-first", ""))
			_, pool := r.resolve("org-a")
			eventually(t, "first connection", func() bool { s, _ := pool.Server("github"); return s.Healthy })
			old, err := pool.Client("github", "", mcp.AuthOverride{})
			if err != nil {
				t.Fatal(err)
			}
			r.push("org-a", 2, credentialConfig("active", "synthetic-second", ""))
			r.resolve("org-a")
			<-started
			if revoke {
				r.push("org-a", 3, credentialConfig("revoked", "", ""))
			} else {
				r.push("org-a", 3, credentialConfig("active", "synthetic-third", ""))
			}
			r.resolve("org-a")
			once.Do(func() { close(release) })
			eventually(t, "late superseded connection closed", late.closed.Load)
			requireCredentialRefusal(t, credentialCall(old))
			if revoke {
				_, err = pool.Client("github", "", mcp.AuthOverride{})
				requireCredentialRefusal(t, err)
			} else {
				eventually(t, "latest replacement usable", func() bool { s, _ := pool.Server("github"); return s.Healthy })
				fresh, err := pool.Client("github", "", mcp.AuthOverride{})
				if err != nil {
					t.Fatal(err)
				}
				if err = credentialCall(fresh); err != nil {
					t.Fatal("latest credential refused")
				}
			}
		})
	}
}

func TestCredentialLifecycleInvalidConfigCannotKeepCredential(t *testing.T) {
	r := newRig(t, Options{})
	r.factory.upstream(urlA, "search")
	r.push("org-a", 1, credentialConfig("active", "synthetic-first", ""))
	_, pool := r.resolve("org-a")
	old, err := pool.Client("github", "", mcp.AuthOverride{})
	if err != nil {
		t.Fatal(err)
	}
	r.push("org-a", 2, `{"enabled":true,"servers":{"github":{"url":"not-a-url","credential":{"mode":"shared","state":"revoked"}}}}`)
	r.resolve("org-a")
	requireCredentialRefusal(t, credentialCall(old))
}
