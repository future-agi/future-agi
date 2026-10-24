package mcp

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/privacy"
)

var errClientClosed = errors.New("client closed")

// Client manages a connection to a single upstream MCP server.
type Client struct {
	serverID  string
	transport Transport                  // read with currentTransport; tests set it before use
	tmu       sync.RWMutex               // guards transport
	tools     atomic.Pointer[[]Tool]     // cached tool list (lock-free reads)
	resources atomic.Pointer[[]Resource] // cached resource list
	prompts   atomic.Pointer[[]Prompt]   // cached prompt list
	healthy   atomic.Bool
	mu        sync.Mutex // serializes connect, reconnect and close
	closed    atomic.Bool

	// life is cancelled only by Close. A transport's health loop and every
	// reconnect run under it; a stdio child lives until its transport closes.
	life         context.Context
	stopLife     context.CancelFunc
	stopMonitors context.CancelFunc // ends the current transport's health loop; under mu
	reconnecting atomic.Bool

	ids       atomic.Int64 // request ids, unique for the client's lifetime
	stderrCut atomic.Int64 // stdio stderr lines cut to the cap, across reconnects

	onToolsChanged atomic.Pointer[func(serverID string, tools []Tool)]

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

	// The stdio child's environment is built from these alone: BaseEnv names
	// copied from the host (nil = DefaultStdioBaseEnv(); empty = none), then
	// EnvFromHost names, then Env values.
	Env         map[string]string
	EnvFromHost []string
	BaseEnv     []string

	Launcher           StdioLauncher     // nil = the local launcher
	StdioGrace         time.Duration     // after stdin closes, before SIGTERM; <= 0 = 5 s
	MaxMessageBytes    int               // largest stdio message; <= 0 = 16 MiB
	StderrMaxLineBytes int               // stderr lines are cut to this; <= 0 = 1,024
	StderrRedactor     *privacy.Redactor // the operator's log redactor, applied after the credential patterns
}

// NewClient creates a new MCP client for an upstream server.
func NewClient(cfg ClientConfig) *Client {
	if cfg.PingInterval <= 0 {
		cfg.PingInterval = 30 * time.Second
	}
	if cfg.ToolsCacheTTL <= 0 {
		cfg.ToolsCacheTTL = 5 * time.Minute
	}
	if cfg.BaseEnv == nil {
		cfg.BaseEnv = DefaultStdioBaseEnv()
	}
	if cfg.Launcher == nil {
		cfg.Launcher = LocalStdioLauncher{}
	}
	if cfg.StdioGrace <= 0 {
		cfg.StdioGrace = DefaultStdioGrace
	}
	if cfg.MaxMessageBytes <= 0 {
		cfg.MaxMessageBytes = DefaultStdioMaxMessageBytes
	}
	if cfg.StderrMaxLineBytes <= 0 {
		cfg.StderrMaxLineBytes = DefaultStdioStderrMaxLineBytes
	}

	c := &Client{
		serverID: cfg.ServerID,
		cfg:      cfg,
	}
	c.life, c.stopLife = context.WithCancel(context.Background())
	return c
}

// Connect establishes the connection, performs the MCP handshake, and
// discovers tools. ctx bounds the handshake only: a stdio child keeps running
// after it ends, until Close.
func (c *Client) Connect(ctx context.Context) error {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	defer context.AfterFunc(c.life, cancel)() // Close aborts a handshake in flight

	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed.Load() {
		return errClientClosed
	}
	t, err := c.dial(ctx)
	if err != nil {
		return err
	}
	c.install(t)
	slog.Info("mcp client connected", "server", c.serverID, "tools", c.ToolCount())
	return nil
}

// dial starts a transport and runs the handshake on it; on any failure the
// transport is closed, so no stdio child outlives a failed connect.
func (c *Client) dial(ctx context.Context) (Transport, error) {
	t, err := c.createTransport(ctx)
	if err != nil {
		return nil, fmt.Errorf("create transport: %w", err)
	}
	if err := c.handshake(ctx, t); err != nil {
		t.Close()
		return nil, err
	}
	if c.closed.Load() {
		t.Close()
		return nil, errClientClosed
	}
	return t, nil
}

func (c *Client) handshake(ctx context.Context, t Transport) error {
	initParams := InitializeParams{
		ProtocolVersion: ProtocolVersion,
		Capabilities:    ClientCapabilities{},
		ClientInfo: Implementation{
			Name:    "agentcc-gateway",
			Version: "1.0.0",
		},
	}
	resp, err := c.request(ctx, t, MethodInitialize, initParams)
	if err != nil {
		return fmt.Errorf("initialize: %w", err)
	}
	if resp.Error != nil {
		return fmt.Errorf("initialize error: %s", resp.Error.Message)
	}

	notif := &Message{JSONRPC: "2.0", Method: MethodInitialized}
	if _, err := t.Send(ctx, notif); err != nil {
		slog.Warn("failed to send initialized notification", "server", c.serverID, "error", err)
	}

	// Discovery failures are non-fatal: a server may not have tools yet, and
	// resources and prompts are optional.
	if err := c.discoverTools(ctx, t); err != nil {
		slog.Warn("tool discovery failed", "server", c.serverID, "error", err)
	}
	if err := c.discoverResources(ctx, t); err != nil {
		slog.Debug("resource discovery failed (may not be supported)", "server", c.serverID, "error", err)
	}
	if err := c.discoverPrompts(ctx, t); err != nil {
		slog.Debug("prompt discovery failed (may not be supported)", "server", c.serverID, "error", err)
	}
	// A stdio upstream that stopped during discovery is not connected.
	return transportErr(t)
}

// transportErr is why t stopped on its own, for a transport that can tell.
func transportErr(t Transport) error {
	if s, ok := t.(interface{ Err() error }); ok {
		return s.Err()
	}
	return nil
}

// install makes t the client's transport, closes the one it replaces and
// starts t's health loop. Called with c.mu held.
func (c *Client) install(t Transport) {
	if c.stopMonitors != nil {
		c.stopMonitors()
	}
	c.tmu.Lock()
	old := c.transport
	c.transport = t
	c.tmu.Unlock()
	if old != nil && old != t {
		old.Close()
	}

	monitors, stop := context.WithCancel(c.life)
	c.stopMonitors = stop
	c.healthy.Store(true)
	go c.healthLoop(monitors, t)

	// t may have stopped between the handshake and the swap, before
	// transportStopped could see it as current.
	if err := transportErr(t); err != nil {
		c.transportStopped(t, err)
	}
}

func (c *Client) currentTransport() Transport {
	c.tmu.RLock()
	defer c.tmu.RUnlock()
	return c.transport
}

// newID returns a request id no other request of this client has used.
func (c *Client) newID() json.RawMessage {
	return json.RawMessage(strconv.FormatInt(c.ids.Add(1), 10))
}

// request sends a request with a fresh id; params may be nil.
func (c *Client) request(ctx context.Context, t Transport, method string, params interface{}) (*Message, error) {
	msg := &Message{JSONRPC: "2.0", ID: c.newID(), Method: method}
	if params != nil {
		data, err := json.Marshal(params)
		if err != nil {
			return nil, fmt.Errorf("marshal %s params: %w", method, err)
		}
		msg.Params = data
	}
	return t.Send(ctx, msg)
}

// readyTransport is the transport for a call, or an error when the client
// cannot take one.
func (c *Client) readyTransport() (Transport, error) {
	if c.closed.Load() {
		return nil, errClientClosed
	}
	t := c.currentTransport()
	if t == nil || !c.healthy.Load() {
		return nil, fmt.Errorf("server %s is unhealthy", c.serverID)
	}
	return t, nil
}

// discoverTools calls tools/list and caches the result.
func (c *Client) discoverTools(ctx context.Context, t Transport) error {
	resp, err := c.request(ctx, t, MethodToolsList, nil)
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

	if fn := c.onToolsChanged.Load(); fn != nil {
		(*fn)(c.serverID, result.Tools)
	}
	return nil
}

// CallTool forwards a tool call to the upstream server.
func (c *Client) CallTool(ctx context.Context, name string, args map[string]interface{}) (*ToolCallResult, error) {
	t, err := c.readyTransport()
	if err != nil {
		return nil, err
	}
	resp, err := c.request(ctx, t, MethodToolsCall, ToolCallParams{Name: name, Arguments: args})
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
func (c *Client) discoverResources(ctx context.Context, t Transport) error {
	resp, err := c.request(ctx, t, MethodResourcesList, nil)
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
func (c *Client) discoverPrompts(ctx context.Context, t Transport) error {
	resp, err := c.request(ctx, t, MethodPromptsList, nil)
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
	t, err := c.readyTransport()
	if err != nil {
		return nil, fmt.Errorf("server %s is unavailable", c.serverID)
	}
	resp, err := c.request(ctx, t, MethodResourcesRead, ReadResourceParams{URI: uri})
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
	t, err := c.readyTransport()
	if err != nil {
		return nil, fmt.Errorf("server %s is unavailable", c.serverID)
	}
	resp, err := c.request(ctx, t, MethodPromptsGet, GetPromptParams{Name: name, Arguments: arguments})
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

// StderrLinesCut is how many stdio stderr lines were cut to the cap, across
// every reconnect.
func (c *Client) StderrLinesCut() int64 {
	return c.stderrCut.Load()
}

// SetOnToolsChanged sets a callback for when the tool list changes.
func (c *Client) SetOnToolsChanged(fn func(serverID string, tools []Tool)) {
	c.onToolsChanged.Store(&fn)
}

// Close disconnects from the upstream server. For a stdio server it returns
// once the child has gone (the transport's shutdown ladder).
func (c *Client) Close() error {
	if c.closed.Swap(true) {
		return nil
	}
	c.healthy.Store(false)
	if c.stopLife != nil {
		c.stopLife() // ends the health loop and any reconnect, aborts a handshake
	}
	c.mu.Lock()
	c.tmu.Lock()
	t := c.transport
	c.tmu.Unlock()
	c.mu.Unlock()
	if t != nil {
		return t.Close()
	}
	return nil
}

// transportStopped is a stdio transport's report that it stopped on its own
// (the child exited, or sent a message over the limit). Only the current
// transport counts; one still being dialled fails its handshake instead.
func (c *Client) transportStopped(t Transport, cause error) {
	if c.closed.Load() || c.currentTransport() != t {
		return
	}
	c.healthy.Store(false)
	slog.Warn("mcp upstream stopped; reconnecting", "server", c.serverID, "error", cause)
	c.startReconnect()
}

func (c *Client) startReconnect() {
	if c.reconnecting.CompareAndSwap(false, true) {
		go c.reconnect()
	}
}

// healthLoop pings t until its monitors end; three failures in a row mark
// the client unhealthy and start a reconnect.
func (c *Client) healthLoop(ctx context.Context, t Transport) {
	ticker := time.NewTicker(c.cfg.PingInterval)
	defer ticker.Stop()

	failures := 0
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
		pingCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
		_, err := c.request(pingCtx, t, MethodPing, nil)
		cancel()
		if ctx.Err() != nil {
			return
		}
		if err == nil {
			failures = 0
			continue
		}
		failures++
		slog.Warn("mcp ping failed", "server", c.serverID, "failures", failures, "error", err)
		if failures >= 3 {
			c.healthy.Store(false)
			slog.Error("mcp server marked unhealthy", "server", c.serverID)
			c.startReconnect()
			return
		}
	}
}

// reconnect re-establishes the connection with exponential backoff until it
// succeeds or the client closes.
func (c *Client) reconnect() {
	backoff := time.Second
	maxBackoff := 60 * time.Second

	for attempt := 1; ; attempt++ {
		slog.Info("mcp reconnecting", "server", c.serverID, "attempt", attempt, "backoff", backoff)
		timer := time.NewTimer(backoff)
		select {
		case <-c.life.Done():
			timer.Stop()
			c.reconnecting.Store(false)
			return
		case <-timer.C:
		}
		if c.redial() {
			return
		}
		backoff = min(backoff*2, maxBackoff)
	}
}

// redial replaces the transport; false means try again later. The 30 s
// context bounds the handshake only, as Connect's does.
func (c *Client) redial() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed.Load() {
		c.reconnecting.Store(false)
		return true
	}
	if c.stopMonitors != nil {
		c.stopMonitors()
	}
	// Close the old transport first, so its stdio server is gone before the
	// new one starts.
	if old := c.currentTransport(); old != nil {
		old.Close()
	}

	ctx, cancel := context.WithTimeout(c.life, 30*time.Second)
	defer cancel()
	t, err := c.dial(ctx)
	if err != nil {
		slog.Warn("mcp reconnect failed", "server", c.serverID, "error", err)
		return false
	}
	// Cleared before install, so a transport dead on arrival reconnects again.
	c.reconnecting.Store(false)
	c.install(t)
	slog.Info("mcp reconnected", "server", c.serverID, "tools", c.ToolCount())
	return true
}

func (c *Client) createTransport(ctx context.Context) (Transport, error) {
	switch c.cfg.TransportType {
	case "stdio":
		return c.startStdio(ctx)
	default: // "http" or empty
		return NewHTTPTransport(c.cfg.URL, c.cfg.Auth), nil
	}
}

func (c *Client) startStdio(ctx context.Context) (*StdioTransport, error) {
	cfg := c.cfg
	spec := StdioSpec{
		ServerID: c.serverID,
		Command:  cfg.Command,
		Args:     cfg.Args,
		Env:      buildStdioEnv(cfg.BaseEnv, cfg.EnvFromHost, cfg.Env),
	}
	t := newStdioTransport(spec, stdioOptions{
		launcher:        cfg.Launcher,
		grace:           cfg.StdioGrace,
		termWait:        stdioTermWait,
		maxMessageBytes: cfg.MaxMessageBytes,
		stderrMaxLine:   cfg.StderrMaxLineBytes,
		stderrRedactor:  cfg.StderrRedactor,
		stderrCut:       &c.stderrCut,
	})
	t.opts.onDead = func(err error) { c.transportStopped(t, err) }
	if err := t.Start(ctx); err != nil {
		return nil, err
	}
	return t, nil
}
