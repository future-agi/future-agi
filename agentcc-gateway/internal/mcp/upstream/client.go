// Package upstream is the gateway's MCP upstream client as the pools use it:
// Factory builds connected clients for mcp.Deps.ClientFactory,
// each an mcp.UpstreamClient over mcp.Client, dialling through the server's
// Deps.Dialer (the SSRF guard) when it builds.
package upstream

import (
	"context"
	"encoding/json"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// client is one connected upstream as an mcp.UpstreamClient: lists from the
// client's cache (kept fresh by its refreshes) and calls raw, with every
// CallOptions field honoured.
type client struct {
	c *mcp.Client
}

var _ mcp.UpstreamClient = (*client)(nil)

func (u *client) ListTools(context.Context) ([]json.RawMessage, error) {
	return raws(u.c.Tools(), func(t mcp.Tool) json.RawMessage { return t.Raw })
}

func (u *client) ListResources(context.Context) ([]json.RawMessage, error) {
	return raws(u.c.Resources(), func(r mcp.Resource) json.RawMessage { return r.Raw })
}

func (u *client) ListPrompts(context.Context) ([]json.RawMessage, error) {
	return raws(u.c.Prompts(), func(p mcp.Prompt) json.RawMessage { return p.Raw })
}

func (u *client) CallTool(ctx context.Context, name string, args json.RawMessage, opts mcp.CallOptions) (json.RawMessage, error) {
	return u.c.CallToolRaw(ctx, name, args, opts)
}

func (u *client) ReadResource(ctx context.Context, uri string, opts mcp.CallOptions) (json.RawMessage, error) {
	return u.c.ReadResourceRaw(ctx, uri, opts)
}

func (u *client) GetPrompt(ctx context.Context, name string, args json.RawMessage, opts mcp.CallOptions) (json.RawMessage, error) {
	return u.c.GetPromptRaw(ctx, name, args, opts)
}

func (u *client) Healthy() bool { return u.c.Healthy() }

func (u *client) Close() error { return u.c.Close() }

// Unwrap is the mcp.Client underneath, for a pool that registers it.
func (u *client) Unwrap() *mcp.Client { return u.c }

// raws returns each item's raw upstream bytes, or the item marshalled when
// the decoder kept none.
func raws[T any](items []T, raw func(T) json.RawMessage) ([]json.RawMessage, error) {
	out := make([]json.RawMessage, 0, len(items))
	for _, it := range items {
		b := raw(it)
		if len(b) == 0 {
			var err error
			if b, err = json.Marshal(it); err != nil {
				return nil, err
			}
		}
		out = append(out, b)
	}
	return out, nil
}
