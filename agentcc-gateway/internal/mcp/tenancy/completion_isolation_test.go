package tenancy

import (
	"context"
	"encoding/json"
	"net/http"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

type completionBarrierClient struct {
	mcp.UpstreamClient
	armed *atomic.Bool
}

func (c *completionBarrierClient) CallTool(ctx context.Context, n string, a json.RawMessage, o mcp.CallOptions) (json.RawMessage, error) {
	raw, err := c.UpstreamClient.CallTool(ctx, n, a, o)
	c.armed.Store(true)
	return raw, err
}

func TestCredentialLifecycleCompletionCheckPreservesSiblingProgress(t *testing.T) {
	r := newRig(t, Options{})
	r.factory.upstream(urlA, "search")
	var armed atomic.Bool
	entered, release := make(chan struct{}), make(chan struct{})
	var once sync.Once
	d := *r.deps.Load()
	d.ClientFactory = func(cfg mcp.ClientConfig) (mcp.UpstreamClient, error) {
		c, err := r.factory.build(cfg)
		if cfg.Auth.Token == "synthetic-blocked" && err == nil {
			return &completionBarrierClient{UpstreamClient: c, armed: &armed}, nil
		}
		return c, err
	}
	r.deps.Store(&d)
	r.push("org-a", 1, `{"enabled":true,"servers":{"github":{"url":"`+urlA+`","credential":{"mode":"per_user"}}}}`)
	_, pool := r.resolve("org-a")
	check := func(context.Context) error {
		if armed.Load() {
			once.Do(func() { close(entered) })
			<-release
		}
		return nil
	}
	a, err := pool.Client("github", "one", mcp.AuthOverride{Header: http.Header{"Authorization": {"Bearer synthetic-blocked"}}, Check: check})
	if err != nil {
		t.Fatal(err)
	}
	b, err := pool.Client("github", "two", mcp.AuthOverride{Header: http.Header{"Authorization": {"Bearer synthetic-other"}}})
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		select {
		case <-release:
		default:
			close(release)
		}
	}()
	done := make(chan error, 1)
	go func() { done <- credentialCall(a) }()
	select {
	case <-entered:
	case <-time.After(time.Second):
		t.Fatal("completion check did not reach its Redis stand-in")
	}
	sibling := make(chan error, 1)
	go func() { sibling <- credentialCall(b) }()
	select {
	case err := <-sibling:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(time.Second):
		t.Fatal("one completion's epoch I/O blocked an unrelated member")
	}
	armed.Store(false)
	close(release)
	if err := <-done; err != nil {
		t.Fatal(err)
	}
}
