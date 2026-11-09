// Package record makes cassettes: an MCP client that records a server's
// initialize (or server/discover), its lists and chosen tool calls, and a
// loopback HTTP proxy that records REST request/response pairs. Neither
// writes anything: the caller saves through cassette.Save, which scrubs and
// verifies before a byte touches disk. Both refuse to start without a
// test-account label.
package record

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"time"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-cassette/cassette"
	"github.com/futureagi/agentcc-gateway/internal/mcp/upstream/wire"
)

// newestLegacy is the revision an initialize asks for when no era is given.
const newestLegacy = "2025-11-25"

// maxPages bounds one list's pagination.
const maxPages = 100

// recordableEras are the revisions the recorder speaks over Streamable HTTP.
var recordableEras = map[string]bool{"": true, "auto": true, wire.Version2026: true,
	"2025-11-25": true, "2025-06-18": true, "2025-03-26": true}

// Call is one chosen tools/call.
type Call struct {
	Name      string          `json:"name"`
	Arguments json.RawMessage `json:"arguments,omitempty"`
}

// MCPOptions configure an MCP recording.
type MCPOptions struct {
	URL         string
	TestAccount string
	// Era is "auto" (or ""): server/discover, then initialize, as the
	// gateway's client does; "2026-07-28": stateless only; any other
	// revision: initialize with it.
	Era         string
	Credentials http.Header // sent on every request; never recorded
	Calls       []Call
	Timeout     time.Duration // per request; 0 means 60 s
	HTTPClient  *http.Client  // nil: a client that follows no redirect
}

// MCP records an MCP server: its initialize or server/discover, every page
// of tools/list, resources/list and prompts/list (results or errors, as the
// gateway's client lists all three), then each chosen tools/call. The
// cassette it returns is not scrubbed yet.
func MCP(ctx context.Context, o MCPOptions) (*cassette.Cassette, error) {
	if err := cassette.ValidateTestAccount(o.TestAccount); err != nil {
		return nil, err
	}
	if err := CheckUpstream(o.URL, false); err != nil {
		return nil, err
	}
	if !recordableEras[o.Era] {
		return nil, fmt.Errorf("era %q: want auto, 2026-07-28, 2025-11-25, 2025-06-18 or 2025-03-26 "+
			"(2024-11-05's HTTP+SSE transport is not recorded)", o.Era)
	}
	for i, call := range o.Calls {
		if call.Name == "" {
			return nil, fmt.Errorf("call %d has no tool name", i)
		}
	}
	c := &rpcClient{url: o.URL, hc: o.HTTPClient, creds: o.Credentials, timeout: o.Timeout,
		schemas: map[string]json.RawMessage{}}
	if c.hc == nil {
		c.hc = noRedirectClient()
	}
	if c.timeout <= 0 {
		c.timeout = 60 * time.Second
	}
	r := &recording{rec: &cassette.MCPRecording{}}
	if err := r.connect(ctx, c, o.Era); err != nil {
		return nil, err
	}
	defer c.end(context.WithoutCancel(ctx))
	for _, list := range []string{"tools/list", "resources/list", "prompts/list"} {
		if err := r.list(ctx, c, list); err != nil {
			return nil, err
		}
	}
	for _, call := range o.Calls {
		params, err := json.Marshal(call)
		if err != nil {
			return nil, err
		}
		if _, err := r.exchange(ctx, c, "tools/call", params); err != nil {
			return nil, err
		}
	}
	r.rec.ProtocolVersion = c.version
	return &cassette.Cassette{Format: cassette.Format, Kind: cassette.KindMCP, TestAccount: o.TestAccount,
		RecordedAt: time.Now().UTC().Format(time.RFC3339), MCP: r.rec}, nil
}

type recording struct {
	rec *cassette.MCPRecording
}

// exchange sends one request and records it with its answer.
func (r *recording) exchange(ctx context.Context, c *rpcClient, method string, params json.RawMessage) (reply, error) {
	rep, err := c.call(ctx, method, params)
	if err != nil {
		return rep, err
	}
	r.rec.Exchanges = append(r.rec.Exchanges, cassette.MCPExchange{Method: method, Params: params,
		Result: rep.result, Error: rep.rpcErr, HTTPStatus: rep.status})
	return rep, nil
}

// connect settles the era: server/discover first unless an older revision
// is named, then initialize.
func (r *recording) connect(ctx context.Context, c *rpcClient, era string) error {
	if era == "" || era == "auto" || era == wire.Version2026 {
		c.stateless = true
		rep, err := c.call(ctx, "server/discover", nil)
		if err == nil && len(rep.rpcErr) == 0 && wire.Supports2026(rep.result) {
			c.version = wire.Version2026
			r.rec.Exchanges = append(r.rec.Exchanges, cassette.MCPExchange{Method: "server/discover", Result: rep.result})
			return nil
		}
		c.stateless = false
		if era == wire.Version2026 {
			return errors.New("the server does not serve 2026-07-28 (server/discover)")
		}
		era = newestLegacy
	}
	params, _ := json.Marshal(map[string]any{"protocolVersion": era, "capabilities": map[string]any{},
		"clientInfo": clientInfo})
	rep, err := r.exchange(ctx, c, "initialize", params)
	if err != nil {
		return err
	}
	var init struct {
		ProtocolVersion string `json:"protocolVersion"`
	}
	if len(rep.rpcErr) > 0 || json.Unmarshal(rep.result, &init) != nil || init.ProtocolVersion == "" {
		return fmt.Errorf("initialize failed (JSON-RPC error %d)", errorCode(rep.rpcErr))
	}
	c.version = init.ProtocolVersion
	return c.notify(ctx, "notifications/initialized")
}

// list records every page of one list method. An error answer is recorded
// too (a server without resources says so), and ends that list.
func (r *recording) list(ctx context.Context, c *rpcClient, method string) error {
	cursor := ""
	for page := 0; page < maxPages; page++ {
		var params json.RawMessage
		if cursor != "" {
			params, _ = json.Marshal(map[string]string{"cursor": cursor})
		}
		rep, err := r.exchange(ctx, c, method, params)
		if err != nil || len(rep.rpcErr) > 0 {
			return err
		}
		var res struct {
			Tools []struct {
				Name        string          `json:"name"`
				InputSchema json.RawMessage `json:"inputSchema"`
			} `json:"tools"`
			NextCursor string `json:"nextCursor"`
		}
		if err := json.Unmarshal(rep.result, &res); err != nil {
			return fmt.Errorf("%s: the result is not an object", method)
		}
		for _, t := range res.Tools {
			c.schemas[t.Name] = t.InputSchema
		}
		if res.NextCursor == "" {
			return nil
		}
		cursor = res.NextCursor
	}
	return fmt.Errorf("%s: more than %d pages", method, maxPages)
}

func noRedirectClient() *http.Client {
	return &http.Client{CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
}
