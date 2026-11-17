package tenancy

import (
	"errors"
	"net/http"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

func TestCredentialLifecycleEmptyUserOverrideBuildsNothing(t *testing.T) {
	r := newRig(t, Options{})
	r.factory.upstream(urlA, "search")
	r.push("org-a", 1, `{"enabled":true,"servers":{"github":{"url":"`+urlA+`","credential":{"mode":"per_user"}}}}`)
	_, pool := r.resolve("org-a")
	builds := 0
	d := *r.deps.Load()
	d.ClientFactory = func(cfg mcp.ClientConfig) (mcp.UpstreamClient, error) {
		builds++
		return r.factory.build(cfg)
	}
	r.deps.Store(&d)
	for _, auth := range []mcp.AuthOverride{{}, {Header: http.Header{"Authorization": {""}}}} {
		_, err := pool.Client("github", "user-one", auth)
		requireCredentialRefusal(t, err)
	}
	if builds != 0 {
		t.Fatal("missing authorization constructed a personal client")
	}
}

func TestCredentialLifecycleUserReplacementHasNoFallback(t *testing.T) {
	r := newRig(t, Options{})
	r.factory.upstream(urlA, "search")
	r.push("org-a", 1, `{"enabled":true,"servers":{"github":{"url":"`+urlA+`","credential":{"mode":"per_user"}}}}`)
	_, pool := r.resolve("org-a")
	old, err := pool.Client("github", "user-one", mcp.AuthOverride{Header: http.Header{"Authorization": {"Bearer synthetic-first"}}})
	if err != nil {
		t.Fatal(err)
	}
	other, err := pool.Client("github", "user-two", mcp.AuthOverride{Header: http.Header{"Authorization": {"Bearer synthetic-other"}}})
	if err != nil {
		t.Fatal(err)
	}
	d := *r.deps.Load()
	d.ClientFactory = func(mcp.ClientConfig) (mcp.UpstreamClient, error) {
		return nil, errors.New("synthetic authentication refused")
	}
	r.deps.Store(&d)
	_, err = pool.Client("github", "user-one", mcp.AuthOverride{Header: http.Header{"Authorization": {"Bearer synthetic-second"}}})
	requireCredentialRefusal(t, err)
	requireCredentialRefusal(t, credentialCall(old))
	if old.Healthy() {
		t.Fatal("superseded user client remained open")
	}
	if err = credentialCall(other); err != nil {
		t.Fatal("another user lost its client")
	}
}

func TestCredentialLifecycleUserReconnectOrdering(t *testing.T) {
	r := newRig(t, Options{})
	u := r.factory.upstream(urlA, "search")
	r.push("org-a", 1, `{"enabled":true,"servers":{"github":{"url":"`+urlA+`","credential":{"mode":"per_user"}}}}`)
	_, pool := r.resolve("org-a")
	started, release := make(chan struct{}), make(chan struct{})
	late := &fakeClient{up: u}
	d := *r.deps.Load()
	d.ClientFactory = func(cfg mcp.ClientConfig) (mcp.UpstreamClient, error) {
		if cfg.Auth.Token == "synthetic-first" {
			close(started)
			<-release
			return late, nil
		}
		return r.factory.build(cfg)
	}
	r.deps.Store(&d)
	done := make(chan error, 1)
	go func() {
		_, err := pool.Client("github", "user-one", mcp.AuthOverride{Header: http.Header{"Authorization": {"Bearer synthetic-first"}}})
		done <- err
	}()
	<-started
	type result struct {
		c   mcp.UpstreamClient
		err error
	}
	freshDone := make(chan result, 1)
	go func() {
		c, err := pool.Client("github", "user-one", mcp.AuthOverride{Header: http.Header{"Authorization": {"Bearer synthetic-second"}}})
		freshDone <- result{c, err}
	}()
	var fresh result
	select {
	case fresh = <-freshDone:
		close(release)
	case <-time.After(time.Second):
		close(release)
		<-done
		<-freshDone
		t.Fatal("new credential waited for a superseded connection")
	}
	if fresh.err != nil {
		t.Fatal("latest reconnect failed")
	}
	requireCredentialRefusal(t, <-done)
	eventually(t, "late user connection closed", late.closed.Load)
	if err := credentialCall(fresh.c); err != nil {
		t.Fatal("latest user credential was replaced by an older build")
	}
}
