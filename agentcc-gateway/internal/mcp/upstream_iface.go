package mcp

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"sort"
	"strings"
	"time"
)

// UpstreamClient is a connected upstream MCP server, as the pools (M9) and
// the upstream hook (M13) use it. Results are raw JSON so they pass through
// unchanged. Frozen after W2b: M13 replaces the implementation behind
// Deps.ClientFactory, never this interface.
type UpstreamClient interface {
	ListTools(ctx context.Context) ([]json.RawMessage, error)
	CallTool(ctx context.Context, name string, args json.RawMessage, opts CallOptions) (json.RawMessage, error)
	ListResources(ctx context.Context) ([]json.RawMessage, error)
	ReadResource(ctx context.Context, uri string, opts CallOptions) (json.RawMessage, error)
	ListPrompts(ctx context.Context) ([]json.RawMessage, error)
	GetPrompt(ctx context.Context, name string, args json.RawMessage, opts CallOptions) (json.RawMessage, error)
	Healthy() bool
	Close() error
}

// CallOptions are the per-call settings of an upstream request.
type CallOptions struct {
	// Auth replaces the server's configured credential for this call (a
	// per-user token, M25), or nil.
	Auth *AuthOverride
	// Timeout bounds the call; 0 leaves the caller's deadline.
	Timeout       time.Duration
	ProgressToken string
	// Meta entries are added to the outgoing params._meta (traceparent, M27).
	Meta map[string]json.RawMessage
	// ForwardHeaders are the configured client headers forwarded upstream.
	ForwardHeaders http.Header
}

// AuthOverride is the credential one call sends instead of the server's:
// the headers to set on the upstream request. It never prints its values.
type AuthOverride struct {
	Header http.Header
	// Lifetime belongs to the resolved vault version, not to the request.
	// Expiry, refusal, replacement and cache purge cancel it for every handle.
	Lifetime  context.Context
	ExpiresAt time.Time
	Version   int
	// Check fences each dispatch against invalidation on any replica.
	Check func(context.Context) error
}

// String names the override's headers and hides their values.
func (a AuthOverride) String() string {
	names := make([]string, 0, len(a.Header))
	for k := range a.Header {
		names = append(names, k)
	}
	sort.Strings(names)
	return "AuthOverride{" + strings.Join(names, ",") + "}"
}

// GoString hides the values from %#v as well.
func (a AuthOverride) GoString() string { return a.String() }

// ClientFactory builds a connected upstream client. Pools build every client
// through Deps.ClientFactory (M9).
type ClientFactory func(ClientConfig) (UpstreamClient, error)

// errOptionUnsupported is returned when a call asks today's client for an
// option it cannot honour; the call fails rather than going out with the
// server's shared credential.
var errOptionUnsupported = errors.New("mcp: the upstream client does not support per-call credentials or forwarded headers")

// clientConnectTimeout bounds the default factory's connect, as the static
// pool's connect has always been bounded.
const clientConnectTimeout = 30 * time.Second

// DefaultClientFactory is today's client (NewClient and Connect) wrapped as an
// UpstreamClient. The connect context is cancelled once Connect returns, as
// the static pool has always done (M5 makes that harmless for stdio).
func DefaultClientFactory(cfg ClientConfig) (UpstreamClient, error) {
	c := NewClient(cfg)
	ctx, cancel := context.WithTimeout(context.Background(), clientConnectTimeout)
	defer cancel()
	if err := c.Connect(ctx); err != nil {
		_ = c.Close()
		return nil, fmt.Errorf("connect mcp server %q: %w", cfg.ServerID, err)
	}
	return &legacyClient{c: c}, nil
}

// legacyClient adapts *Client to UpstreamClient. Lists use the connect cache;
// operations use the raw methods so pool lifetime guards can delegate without
// losing call options, raw arguments, results or upstream errors.
type legacyClient struct {
	c *Client
}

func (l *legacyClient) ListTools(context.Context) ([]json.RawMessage, error) {
	return rawEach(l.c.Tools(), func(t Tool) json.RawMessage { return t.Raw })
}

func (l *legacyClient) ListResources(context.Context) ([]json.RawMessage, error) {
	return rawEach(l.c.Resources(), func(r Resource) json.RawMessage { return r.Raw })
}

func (l *legacyClient) ListPrompts(context.Context) ([]json.RawMessage, error) {
	return rawEach(l.c.Prompts(), func(p Prompt) json.RawMessage { return p.Raw })
}

func (l *legacyClient) CallTool(ctx context.Context, name string, args json.RawMessage, opts CallOptions) (json.RawMessage, error) {
	return l.c.CallToolRaw(ctx, name, args, opts)
}

func (l *legacyClient) ReadResource(ctx context.Context, uri string, opts CallOptions) (json.RawMessage, error) {
	return l.c.ReadResourceRaw(ctx, uri, opts)
}

func (l *legacyClient) GetPrompt(ctx context.Context, name string, args json.RawMessage, opts CallOptions) (json.RawMessage, error) {
	return l.c.GetPromptRaw(ctx, name, args, opts)
}

func (l *legacyClient) Healthy() bool { return l.c.Healthy() }

func (l *legacyClient) Close() error { return l.c.Close() }

// decodeArgs decodes a raw arguments object; empty or null leaves v unset.
func decodeArgs(args json.RawMessage, v any) error {
	if len(args) == 0 || string(args) == "null" {
		return nil
	}
	if err := json.Unmarshal(args, v); err != nil {
		return fmt.Errorf("decode arguments: %w", err)
	}
	return nil
}

// rawOf returns raw when the decoder kept it, else v marshalled (an upstream
// JSON-RPC error that today's client turned into an isError result).
func rawOf(raw json.RawMessage, v any) (json.RawMessage, error) {
	if len(raw) > 0 {
		return raw, nil
	}
	return json.Marshal(v)
}

func rawEach[T any](items []T, raw func(T) json.RawMessage) ([]json.RawMessage, error) {
	out := make([]json.RawMessage, 0, len(items))
	for _, it := range items {
		b, err := rawOf(raw(it), it)
		if err != nil {
			return nil, err
		}
		out = append(out, b)
	}
	return out, nil
}
