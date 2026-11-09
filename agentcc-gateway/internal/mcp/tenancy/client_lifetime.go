package tenancy

import (
	"context"
	"encoding/json"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// timeoutClient applies the server's timeout_ms to calls that set none.
type timeoutClient struct {
	mcp.UpstreamClient
	timeout time.Duration
	entry   *entry
	userKey string
	user    bool
	// grant is present on a shared OAuth connection pinned during construction.
	server  string
	grant   *mcp.AuthOverride
	life    context.Context
	cleanup func()
}

func withTimeout(c mcp.UpstreamClient, d time.Duration) mcp.UpstreamClient {
	if d <= 0 {
		return c
	}
	return &timeoutClient{UpstreamClient: c, timeout: d}
}

// Upstream is the client the timeout wraps, so a hook can reach the
// mcp.Client under a pool client (callClient, at the W3b merge).
func (t *timeoutClient) Upstream() mcp.UpstreamClient { return t.UpstreamClient }

func (t *timeoutClient) opts(o mcp.CallOptions) mcp.CallOptions {
	if o.Timeout == 0 {
		o.Timeout = t.timeout
	}
	return o
}

func (t *timeoutClient) CallTool(ctx context.Context, name string, args json.RawMessage, o mcp.CallOptions) (json.RawMessage, error) {
	ctx, done, err := t.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer done()
	raw, err := t.UpstreamClient.CallTool(ctx, name, args, t.opts(o))
	if err = t.finish(ctx, err); err != nil {
		return nil, err
	}
	return raw, nil
}

func (t *timeoutClient) ReadResource(ctx context.Context, uri string, o mcp.CallOptions) (json.RawMessage, error) {
	ctx, done, err := t.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer done()
	raw, err := t.UpstreamClient.ReadResource(ctx, uri, t.opts(o))
	if err = t.finish(ctx, err); err != nil {
		return nil, err
	}
	return raw, nil
}

func (t *timeoutClient) GetPrompt(ctx context.Context, name string, args json.RawMessage, o mcp.CallOptions) (json.RawMessage, error) {
	ctx, done, err := t.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer done()
	raw, err := t.UpstreamClient.GetPrompt(ctx, name, args, t.opts(o))
	if err = t.finish(ctx, err); err != nil {
		return nil, err
	}
	return raw, nil
}
