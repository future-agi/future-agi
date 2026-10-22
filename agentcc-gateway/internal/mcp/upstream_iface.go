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

// UpstreamClient is the interface of a connected upstream MCP server.
// Results are raw JSON so they pass through unchanged. DefaultClientFactory
// returns the built-in implementation.
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
	// per-user token), or nil.
	Auth *AuthOverride
	// Timeout bounds the call; 0 leaves the caller's deadline.
	Timeout       time.Duration
	ProgressToken string
	// Meta holds entries for the outgoing params._meta (traceparent).
	Meta map[string]json.RawMessage
	// ForwardHeaders are the configured client headers forwarded upstream.
	ForwardHeaders http.Header
}

// AuthOverride is the credential one call sends instead of the server's:
// the headers to set on the upstream request. It never prints its values.
type AuthOverride struct {
	Header http.Header
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

// ClientFactory builds a connected upstream client.
type ClientFactory func(ClientConfig) (UpstreamClient, error)

// errOptionUnsupported is returned when a call asks the built-in client for an
// option it cannot honour; the call fails rather than going out with the
// server's shared credential.
var errOptionUnsupported = errors.New("mcp: the upstream client does not support per-call credentials or forwarded headers")

// clientConnectTimeout bounds the default factory's connect, as it bounds the
// static pool's.
const clientConnectTimeout = 30 * time.Second

// DefaultClientFactory is the built-in client (NewClient and Connect) wrapped
// as an UpstreamClient. The connect context is cancelled once Connect
// returns, as in the static pool; a stdio child outlives that context.
func DefaultClientFactory(cfg ClientConfig) (UpstreamClient, error) {
	c := NewClient(cfg)
	ctx, cancel := context.WithTimeout(context.Background(), clientConnectTimeout)
	defer cancel()
	if err := c.Connect(ctx); err != nil {
		return nil, fmt.Errorf("connect mcp server %q: %w", cfg.ServerID, err)
	}
	return &legacyClient{c: c}, nil
}

// legacyClient adapts the built-in *Client to UpstreamClient. It serves the lists
// cached at connect and answers with each object's raw upstream bytes.
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
	ctx, cancel, err := applyOptions(ctx, opts)
	if err != nil {
		return nil, err
	}
	defer cancel()
	var arguments map[string]interface{}
	if err := decodeArgs(args, &arguments); err != nil {
		return nil, err
	}
	res, err := l.c.CallTool(ctx, name, arguments)
	if err != nil {
		return nil, err
	}
	return rawOf(res.Raw, res)
}

func (l *legacyClient) ReadResource(ctx context.Context, uri string, opts CallOptions) (json.RawMessage, error) {
	ctx, cancel, err := applyOptions(ctx, opts)
	if err != nil {
		return nil, err
	}
	defer cancel()
	res, err := l.c.ReadResource(ctx, uri)
	if err != nil {
		return nil, err
	}
	return rawOf(res.Raw, res)
}

func (l *legacyClient) GetPrompt(ctx context.Context, name string, args json.RawMessage, opts CallOptions) (json.RawMessage, error) {
	ctx, cancel, err := applyOptions(ctx, opts)
	if err != nil {
		return nil, err
	}
	defer cancel()
	var arguments map[string]string
	if err := decodeArgs(args, &arguments); err != nil {
		return nil, err
	}
	res, err := l.c.GetPrompt(ctx, name, arguments)
	if err != nil {
		return nil, err
	}
	return rawOf(res.Raw, res)
}

func (l *legacyClient) Healthy() bool { return l.c.Healthy() }

func (l *legacyClient) Close() error { return l.c.Close() }

// applyOptions refuses what the built-in client cannot send and applies the
// timeout. The built-in client sends neither Meta nor ProgressToken.
func applyOptions(ctx context.Context, opts CallOptions) (context.Context, context.CancelFunc, error) {
	if opts.Auth != nil || len(opts.ForwardHeaders) > 0 {
		return nil, nil, errOptionUnsupported
	}
	if opts.Timeout > 0 {
		ctx, cancel := context.WithTimeout(ctx, opts.Timeout)
		return ctx, cancel, nil
	}
	return ctx, func() {}, nil
}

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
// JSON-RPC error that the built-in client turned into an isError result).
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
