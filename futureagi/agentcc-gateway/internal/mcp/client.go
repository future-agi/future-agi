package mcp

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"sync"
	"sync/atomic"
	"time"
)

// Client manages a connection to a single upstream MCP server.
type Client struct {
	serverID  string
	transport Transport
	tools     atomic.Pointer[[]Tool]     // cached tool list (lock-free reads)
	resources atomic.Pointer[[]Resource] // cached resource list
	prompts   atomic.Pointer[[]Prompt]   // cached prompt list
	healthy   atomic.Bool
	mu        sync.Mutex // serializes reconnection
	closed    atomic.Bool

	// Callbacks.
	onToolsChanged func(serverID string, tools []Tool)

	// Config.
	cfg ClientConfig
}

// ClientConfig holds configuration for an MCP client connection.
type ClientConfig struct {
	ServerID      string
	URL           string
	Command       string   // for stdio transport
	Args          []string // for stdio transport
	TransportType string   // "http" or "stdio"
	Auth          AuthConfig
	PingInterval  time.Duration
	ToolsCacheTTL time.Duration
}

// NewClient creates a new MCP client for an upstream server.
func NewClient(cfg ClientConfig) *Client {
	if cfg.PingInterval <= 0 {
		cfg.PingInterval = 30 * time.Second
	}
	if cfg.ToolsCacheTTL <= 0 {
		cfg.ToolsCacheTTL = 5 * time.Minute
	}

	c := &Client{
		serverID: cfg.ServerID,
		cfg:      cfg,
	}
	return c
}

// Connect establishes the connection, performs the MCP handshake, and discovers tools.
func (c *Client) Connect(ctx context.Context) error {
	c.mu.Lock()
	defer c.mu.Unlock()

	// Create transport.
	transport, err := c.createTransport(ctx)
	if err != nil {
		return fmt.Errorf("create transport: %w", err)
	}
	c.transport = transport

	// Send initialize.
	initParams := InitializeParams{
		ProtocolVersion: ProtocolVersion,
		Capabilities:    ClientCapabilities{},
		ClientInfo: Implementation{
			Name:    "agentcc-gateway",
			Version: "1.0.0",
		},
	}

	paramsData, err := json.Marshal(initParams)
	if err != nil {
		return fmt.Errorf("marshal init params: %w", err)
	}

	initMsg := &Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`1`),
		Method:  MethodInitialize,
		Params:  paramsData,
	}

	resp, err := c.transport.Send(ctx, initMsg)
	if err != nil {
		return fmt.Errorf("initialize: %w", err)
	}
	if resp.Error != nil {
		return fmt.Errorf("initialize error: %s", resp.Error.Message)
	}

	// Send initialized notification.
	notif := &Message{
		JSONRPC: "2.0",
		Method:  MethodInitialized,
	}
	if _, err := c.transport.Send(ctx, notif); err != nil {
		slog.Warn("failed to send initialized notification", "server", c.serverID, "error", err)
	}

	// Discover tools.
	if err := c.discoverTools(ctx); err != nil {
		slog.Warn("tool discovery failed", "server", c.serverID, "error", err)
		// Non-fatal: server might not have tools yet.
	}

	// Discover resources (non-fatal).
	if err := c.discoverResources(ctx); err != nil {
		slog.Debug("resource discovery failed (may not be supported)", "server", c.serverID, "error", err)
	}

	// Discover prompts (non-fatal).
	if err := c.discoverPrompts(ctx); err != nil {
		slog.Debug("prompt discovery failed (may not be supported)", "server", c.serverID, "error", err)
	}

	c.healthy.Store(true)

	// Start health monitoring.
	go c.healthLoop()

	slog.Info("mcp client connected", "server", c.serverID, "tools", c.ToolCount())
	return nil
}

// discoverTools calls tools/list and caches the result.
func (c *Client) discoverTools(ctx context.Context) error {
	msg := &Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`2`),
		Method:  MethodToolsList,
	}

	resp, err := c.transport.Send(ctx, msg)
	if err != nil {
		return fmt.Errorf("tools/list: %w", err)
	}
	if resp.Error != nil {
		return fmt.Errorf("tools/list error: %s", resp.Error.Message)
	}

	var result ListToolsResult
	if err := json.Unmarshal(resp.Result, &result); err != nil {
		return fmt.Errorf("decode tools/list: %w", err)
	}

	c.tools.Store(&result.Tools)

	// Notify callback.
	if c.onToolsChanged != nil {
		c.onToolsChanged(c.serverID, result.Tools)
	}

	return nil
}

// CallTool forwards a tool call to the upstream server.
func (c *Client) CallTool(ctx context.Context, name string, args map[string]interface{}) (*ToolCallResult, error) {
	if c.closed.Load() {
		return nil, fmt.Errorf("client closed")
	}
	if !c.healthy.Load() {
		return nil, fmt.Errorf("server %s is unhealthy", c.serverID)
	}

	params := ToolCallParams{
		Name:      name,
		Arguments: args,
	}
	paramsData, err := json.Marshal(params)
	if err != nil {
		return nil, fmt.Errorf("marshal tool call params: %w", err)
	}

	// Use a unique ID based on timestamp.
	id := fmt.Sprintf("%d", time.Now().UnixNano())

	msg := &Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`"` + id + `"`),
		Method:  MethodToolsCall,
		Params:  paramsData,
	}

	resp, err := c.transport.Send(ctx, msg)
	if err != nil {
		return nil, fmt.Errorf("tools/call: %w", err)
	}
	if resp.Error != nil {
		return &ToolCallResult{
			Content: []ContentPart{{Type: "text", Text: resp.Error.Message}},
			IsError: true,
		}, nil
	}

	var result ToolCallResult
	if err := json.Unmarshal(resp.Result, &result); err != nil {
		return nil, fmt.Errorf("decode tools/call result: %w", err)
	}
	return &result, nil
}

// Tools returns the cached tool list.
func (c *Client) Tools() []Tool {
	p := c.tools.Load()
	if p == nil {
		return nil
	}
	return *p
}

// ToolCount returns the number of cached tools.
func (c *Client) ToolCount() int {
	p := c.tools.Load()
	if p == nil {
		return 0
	}
	return len(*p)
}

// Resources returns the cached resource list.
func (c *Client) Resources() []Resource {
	p := c.resources.Load()
	if p == nil {
		return nil
	}
	return *p
}

// Prompts returns the cached prompt list.
func (c *Client) Prompts() []Prompt {
	p := c.prompts.Load()
	if p == nil {
		return nil
	}
	return *p
}

// discoverResources calls resources/list and caches the result.
func (c *Client) discoverResources(ctx context.Context) error {
	msg := &Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`3`),
		Method:  MethodResourcesList,
	}
	resp, err := c.transport.Send(ctx, msg)
	if err != nil {
		return err
	}
	if resp.Error != nil {
		return fmt.Errorf("resources/list error: %s", resp.Error.Message)
	}
	var result ListResourcesResult
	if err := json.Unmarshal(resp.Result, &result); err != nil {
		return fmt.Errorf("decode resources/list: %w", err)
	}
	c.resources.Store(&result.Resources)
	return nil
}

// discoverPrompts calls prompts/list and caches the result.
func (c *Client) discoverPrompts(ctx context.Context) error {
	msg := &Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`4`),
		Method:  MethodPromptsList,
	}
	resp, err := c.transport.Send(ctx, msg)
	if err != nil {
		return err
	}
	if resp.Error != nil {
		return fmt.Errorf("prompts/list error: %s", resp.Error.Message)
	}
	var result ListPromptsResult
	if err := json.Unmarshal(resp.Result, &result); err != nil {
		return fmt.Errorf("decode prompts/list: %w", err)
	}
	c.prompts.Store(&result.Prompts)
	return nil
}

// ReadResource reads a specific resource from the upstream server.
func (c *Client) ReadResource(ctx context.Context, uri string) (*ReadResourceResult, error) {
	if c.closed.Load() || !c.healthy.Load() {
		return nil, fmt.Errorf("server %s is unavailable", c.serverID)
	}
	params := ReadResourceParams{URI: uri}
	paramsData, _ := json.Marshal(params)
	id := fmt.Sprintf("%d", time.Now().UnixNano())
	msg := &Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`"` + id + `"`),
		Method:  MethodResourcesRead,
		Params:  paramsData,
	}
	resp, err := c.transport.Send(ctx, msg)
	if err != nil {
		return nil, err
	}
	if resp.Error != nil {
		return nil, fmt.Errorf("resources/read: %s", resp.Error.Message)
	}
	var result ReadResourceResult
	if err := json.Unmarshal(resp.Result, &result); err != nil {
		return nil, fmt.Errorf("decode resources/read: %w", err)
	}
	return &result, nil
}

// GetPrompt gets a prompt from the upstream server.
func (c *Client) GetPrompt(ctx context.Context, name string, arguments map[string]string) (*GetPromptResult, error) {
	if c.closed.Load() || !c.healthy.Load() {
		return nil, fmt.Errorf("server %s is unavailable", c.serverID)
	}
	params := GetPromptParams{Name: name, Arguments: arguments}
	paramsData, _ := json.Marshal(params)
	id := fmt.Sprintf("%d", time.Now().UnixNano())
	msg := &Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`"` + id + `"`),
		Method:  MethodPromptsGet,
		Params:  paramsData,
	}
	resp, err := c.transport.Send(ctx, msg)
	if err != nil {
		return nil, err
	}
	if resp.Error != nil {
		return nil, fmt.Errorf("prompts/get: %s", resp.Error.Message)
	}
	var result GetPromptResult
	if err := json.Unmarshal(resp.Result, &result); err != nil {
		return nil, fmt.Errorf("decode prompts/get: %w", err)
	}
	return &result, nil
}

// ServerID returns the server identifier.
func (c *Client) ServerID() string {
	return c.serverID
}

// Healthy returns whether the upstream server is healthy.
func (c *Client) Healthy() bool {
	return c.healthy.Load() && !c.closed.Load()
}

// SetOnToolsChanged sets a callback for when the tool list changes.
func (c *Client) SetOnToolsChanged(fn func(serverID string, tools []Tool)) {
	c.onToolsChanged = fn
}

// Close disconnects from the upstream server.
func (c *Client) Close() error {
	if c.closed.Swap(true) {
		return nil
	}
	c.healthy.Store(false)
	if c.transport != nil {
		return c.transport.Close()
	}
	return nil
}

// healthLoop periodically pings the upstream server.
func (c *Client) healthLoop() {
	ticker := time.NewTicker(c.cfg.PingInterval)
	defer ticker.Stop()

	failures := 0
	for range ticker.C {
		if c.closed.Load() {
			return
		}

		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		msg := &Message{
			JSONRPC: "2.0",
			ID:      json.RawMessage(`"ping"`),
			Method:  MethodPing,
		}

		_, err := c.transport.Send(ctx, msg)
		cancel()

		if err != nil {
			failures++
			slog.Warn("mcp ping failed", "server", c.serverID, "failures", failures, "error", err)
			if failures >= 3 {
				c.healthy.Store(false)
				slog.Error("mcp server marked unhealthy", "server", c.serverID)
				go c.reconnect()
				return
			}
		} else {
			failures = 0
			c.healthy.Store(true)
		}
	}
}

// reconnect attempts to re-establish the connection with exponential backoff.
func (c *Client) reconnect() {
	backoff := time.Second
	maxBackoff := 60 * time.Second

	for attempt := 1; !c.closed.Load(); attempt++ {
		slog.Info("mcp reconnecting", "server", c.serverID, "attempt", attempt, "backoff", backoff)
		time.Sleep(backoff)

		if c.closed.Load() {
			return
		}

		c.mu.Lock()
		// Close old transport.
		if c.transport != nil {
			c.transport.Close()
		}

		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		transport, err := c.createTransport(ctx)
		if err != nil {
			cancel()
			c.mu.Unlock()
			slog.Warn("mcp reconnect transport failed", "server", c.serverID, "error", err)
			backoff = min(backoff*2, maxBackoff)
			continue
		}
		c.transport = transport

		// Re-initialize.
		initParams := InitializeParams{
			ProtocolVersion: ProtocolVersion,
			Capabilities:    ClientCapabilities{},
			ClientInfo:      Implementation{Name: "agentcc-gateway", Version: "1.0.0"},
		}
		paramsData, _ := json.Marshal(initParams)
		initMsg := &Message{
			JSONRPC: "2.0",
			ID:      json.RawMessage(`1`),
			Method:  MethodInitialize,
			Params:  paramsData,
		}
		resp, err := c.transport.Send(ctx, initMsg)
		if err != nil || resp.Error != nil {
			cancel()
			c.mu.Unlock()
			slog.Warn("mcp reconnect init failed", "server", c.serverID, "error", err)
			backoff = min(backoff*2, maxBackoff)
			continue
		}

		// Send initialized notification.
		notif := &Message{JSONRPC: "2.0", Method: MethodInitialized}
		c.transport.Send(ctx, notif)

		// Re-discover tools, resources, prompts.
		c.discoverTools(ctx)
		c.discoverResources(ctx)
		c.discoverPrompts(ctx)
		cancel()
		c.mu.Unlock()

		c.healthy.Store(true)
		slog.Info("mcp reconnected", "server", c.serverID, "tools", c.ToolCount())

		// Restart health loop.
		go c.healthLoop()
		return
	}
}

func (c *Client) createTransport(ctx context.Context) (Transport, error) {
	switch c.cfg.TransportType {
	case "stdio":
		t := NewStdioTransport(c.cfg.Command, c.cfg.Args)
		if err := t.Start(ctx); err != nil {
			return nil, err
		}
		return t, nil
	default: // "http" or empty
		return NewHTTPTransport(c.cfg.URL, c.cfg.Auth), nil
	}
}
