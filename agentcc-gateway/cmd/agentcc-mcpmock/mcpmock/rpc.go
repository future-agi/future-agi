package mcpmock

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"strconv"
	"strings"
)

// streamer carries what a call sends besides its result: notifications on the
// request's own stream, and (legacy only) requests to the client whose
// answers it waits for. It is nil when the answer is a plain JSON body.
type streamer interface {
	send(msg *Message) error
	ask(ctx context.Context, req *Message) (*Message, error)
}

// call is one request's context: the revision it is served under, its 2026
// metadata or its legacy session, its stream, and its cost-log entry.
type call struct {
	version   string
	stateless bool
	meta      requestMeta
	caps      json.RawMessage // the client's capabilities (initialize or _meta)
	stream    streamer
	charge    *Charge
}

// handle answers one request. initialize and subscriptions/listen are the
// transports' own (they open a session or a stream).
func (m *Mock) handle(ctx context.Context, c *call, msg *Message) (any, *RPCError) {
	switch msg.Method {
	case "ping", "logging/setLevel":
		if c.stateless {
			return nil, unknownMethod(c, msg.Method)
		}
		return map[string]any{}, nil
	case "server/discover":
		if !c.stateless {
			return nil, unknownMethod(c, msg.Method)
		}
		return m.finish(c, m.discoverResult(), true), nil
	case "tools/list":
		return m.list(c, msg.Params, "tools", toolDefs(m.listedTools()))
	case "resources/list":
		defs := make([]json.RawMessage, 0)
		for _, r := range m.state.resourceList() {
			defs = append(defs, r.Def)
		}
		return m.list(c, msg.Params, "resources", defs)
	case "prompts/list":
		defs := make([]json.RawMessage, 0)
		for _, p := range m.state.promptList() {
			defs = append(defs, p.Def)
		}
		return m.list(c, msg.Params, "prompts", defs)
	case "tools/call":
		return m.callTool(ctx, c, msg)
	case "resources/read":
		return m.readResource(c, msg.Params)
	case "prompts/get":
		return m.getPrompt(c, msg.Params)
	}
	return nil, unknownMethod(c, msg.Method)
}

// unknownMethod is -32601, answered with HTTP 404 in 2026.
func unknownMethod(c *call, method string) *RPCError {
	status := 0
	if c.stateless {
		status = 404
	}
	return rpcErr(status, CodeMethodNotFound, "method not found: %s", method)
}

// initializeResult answers a legacy initialize, negotiating the revision.
func (m *Mock) initializeResult(params json.RawMessage) (string, json.RawMessage, map[string]any, *RPCError) {
	var p struct {
		ProtocolVersion string          `json:"protocolVersion"`
		Capabilities    json.RawMessage `json:"capabilities"`
	}
	if err := json.Unmarshal(params, &p); err != nil || p.ProtocolVersion == "" {
		return "", nil, nil, rpcErr(0, CodeInvalidParams, "initialize needs params with a protocolVersion")
	}
	version, ok := m.cfg.Era.negotiate(p.ProtocolVersion)
	if !ok {
		return "", nil, nil, rpcErr(0, CodeMethodNotFound, "this %s mock has no initialize", m.cfg.Era.Name)
	}
	return version, p.Capabilities, map[string]any{
		"protocolVersion": version,
		"capabilities":    m.serverCapabilities(),
		"serverInfo":      m.serverInfo(),
		"instructions":    "agentcc-mcpmock: a test upstream; nothing it says is real.",
	}, nil
}

func (m *Mock) serverInfo() map[string]any {
	return map[string]any{"name": m.cfg.ServerName, "version": "1.0.0"}
}

func (m *Mock) serverCapabilities() map[string]any {
	return map[string]any{
		"tools":     map[string]any{"listChanged": true},
		"resources": map[string]any{"listChanged": true},
		"prompts":   map[string]any{"listChanged": true},
		"logging":   map[string]any{},
	}
}

// discoverResult is the 2026 server/discover result. Its shape is not
// checked against the published schema.
func (m *Mock) discoverResult() map[string]any {
	caps := m.serverCapabilities()
	caps["extensions"] = map[string]any{}
	return map[string]any{
		"supportedVersions": []string{V2026},
		"capabilities":      caps,
	}
}

// finish decorates a result for the revision it is served under. A 2026
// result carries resultType (complete unless set), serverInfo and, for a
// cacheable list, read or discover, ttlMs and cacheScope.
func (m *Mock) finish(c *call, result map[string]any, cacheable bool) map[string]any {
	if !c.stateless {
		return result
	}
	if _, ok := result["resultType"]; !ok {
		result["resultType"] = "complete"
	}
	result["serverInfo"] = m.serverInfo()
	if cacheable {
		scope := "public"
		if m.cfg.Auth != "none" {
			scope = "private"
		}
		result["ttlMs"], result["cacheScope"] = 60000, scope
	}
	return result
}

// list answers a list method, a page at a time when behavior.page_size is set.
func (m *Mock) list(c *call, params json.RawMessage, key string, defs []json.RawMessage) (any, *RPCError) {
	var p struct {
		Cursor string `json:"cursor"`
	}
	_ = json.Unmarshal(params, &p)
	start := 0
	if p.Cursor != "" {
		off, ok := decodeCursor(p.Cursor)
		if !ok || off > len(defs) {
			return nil, rpcErr(0, CodeInvalidParams, "invalid cursor")
		}
		start = off
	}
	end := len(defs)
	if size := m.state.Behavior().PageSize; size > 0 && start+size < end {
		end = start + size
	}
	result := map[string]any{key: defs[start:end]}
	if end < len(defs) {
		result["nextCursor"] = encodeCursor(end)
	}
	return m.finish(c, result, true), nil
}

func encodeCursor(off int) string {
	return base64.RawURLEncoding.EncodeToString([]byte("mcpmock:" + strconv.Itoa(off)))
}

func decodeCursor(cur string) (int, bool) {
	raw, err := base64.RawURLEncoding.DecodeString(cur)
	if err != nil || !strings.HasPrefix(string(raw), "mcpmock:") {
		return 0, false
	}
	off, err := strconv.Atoi(strings.TrimPrefix(string(raw), "mcpmock:"))
	return off, err == nil && off >= 0
}

func (m *Mock) readResource(c *call, params json.RawMessage) (any, *RPCError) {
	var p struct {
		URI string `json:"uri"`
	}
	_ = json.Unmarshal(params, &p)
	for _, r := range m.state.resourceList() {
		if r.URI == p.URI {
			content := map[string]any{"uri": r.URI, "text": r.Text}
			if r.Mime != "" {
				content["mimeType"] = r.Mime
			}
			return m.finish(c, map[string]any{"contents": []any{content}}, true), nil
		}
	}
	code := -32002 // resource not found before 2026-07-28
	if c.stateless {
		code = CodeInvalidParams
	}
	return nil, rpcErr(0, code, "resource not found: %s", p.URI)
}

func (m *Mock) getPrompt(c *call, params json.RawMessage) (any, *RPCError) {
	var p struct {
		Name string `json:"name"`
	}
	_ = json.Unmarshal(params, &p)
	for _, pr := range m.state.promptList() {
		if pr.Name == p.Name {
			messages := pr.Messages
			if len(messages) == 0 {
				messages = json.RawMessage(`[]`)
			}
			return m.finish(c, map[string]any{"messages": messages}, false), nil
		}
	}
	return nil, rpcErr(0, CodeInvalidParams, "unknown prompt: %s", p.Name)
}

// requestName is the name a 2026 Mcp-Name header must mirror, and what
// /__received shows: the tool or prompt name, or the resource uri.
func requestName(method string, params json.RawMessage) string {
	var p struct {
		Name string `json:"name"`
		URI  string `json:"uri"`
	}
	_ = json.Unmarshal(params, &p)
	switch method {
	case "tools/call", "prompts/get":
		return p.Name
	case "resources/read":
		return p.URI
	}
	return ""
}
