package mcp

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"slices"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp/upstream/wire"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
)

var errClientClosed = errors.New("client closed")

// Client manages a connection to a single upstream MCP server (plan M13):
// the connection's lifecycle, then the protocol on it (era detection, the
// legacy session or the stateless 2026 era, every list page, list refreshes)
// and the calls, raw for pass-through.
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
	stopMonitors context.CancelFunc // ends the current transport's loops; under mu
	reconnecting atomic.Bool

	// The reconnect backoff persists across reconnects (M5 review P3): a
	// server that dies right after each handshake is retried ever more
	// slowly, and the delay resets only after a connection has lasted
	// reconnectResetAfter.
	reconnectBase time.Duration // the first delay; tests shorten it
	backoffMu     sync.Mutex
	backoff       time.Duration // the next delay; 0 = reconnectBase
	installedAt   time.Time     // when the current transport was installed

	ids       atomic.Int64 // request ids, unique for the client's lifetime
	stderrCut atomic.Int64 // stdio stderr lines cut to the cap, across reconnects

	era      atomic.Pointer[string]       // the negotiated protocol version
	settings atomic.Pointer[CallSettings] // the server's per-call settings
	tokens   *wire.Tokens                 // progress tokens of the calls in flight
	reinitMu sync.Mutex                   // one re-initialize at a time
	listMu   sync.Mutex                   // one list refresh at a time
	refresh  chan struct{}                // a coalesced list refresh request

	onToolsChanged atomic.Pointer[func(serverID string, tools []Tool)]

	cfg ClientConfig
}

// ClientConfig holds configuration for an MCP client connection.
type ClientConfig struct {
	// Lifetime, when set, owns construction, background I/O and the connection.
	// Unlike Connect's context it remains attached after the handshake.
	Lifetime      context.Context
	ServerID      string
	URL           string   // the endpoint, used exactly as configured
	Command       string   // for stdio transport
	Args          []string // for stdio transport
	TransportType string   // "http" (Streamable HTTP), "sse" (2024-11-05 HTTP+SSE) or "stdio"
	Auth          AuthConfig
	PingInterval  time.Duration
	// ToolsCacheTTL is how often the lists are refreshed besides on the
	// upstream's list_changed (tools_cache_ttl_seconds).
	ToolsCacheTTL time.Duration

	// Era pins the upstream's MCP revision: "" or "auto" detects it
	// (server/discover, then initialize); 2026-07-28 is the stateless era;
	// any other revision initializes with it. "sse" is always 2024-11-05.
	Era string
	// Dialer opens HTTP connections: the factory sets M17's SSRF guard from
	// Deps.Dialer; nil is a plain net.Dialer.
	Dialer Dialer
	// Call are the server's per-call settings.
	Call CallSettings

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

// CallSettings are an upstream server's per-call settings.
type CallSettings struct {
	// Timeout bounds one call (the server's timeout_ms); 0 takes the
	// caller's CallOptions.Timeout (tool_call_timeout).
	Timeout time.Duration
	// ForwardHeaders are the inbound headers copied upstream
	// (forward_headers); config.ForwardHeaderRefused names never are.
	ForwardHeaders []string
	// MaxResultBytes bounds one answer (mcp.max_result_bytes); 0 takes
	// DefaultMaxResultBytes.
	MaxResultBytes int64
}

// DefaultMaxResultBytes is mcp.max_result_bytes' default.
const DefaultMaxResultBytes = 8 << 20

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
		serverID:      cfg.ServerID,
		cfg:           cfg,
		reconnectBase: time.Second,
		tokens:        wire.NewTokens(),
		refresh:       make(chan struct{}, 1),
	}
	c.SetCallSettings(cfg.Call)
	parent := cfg.Lifetime
	if parent == nil {
		parent = context.Background()
	}
	c.life, c.stopLife = context.WithCancel(parent)
	if cfg.Lifetime != nil {
		context.AfterFunc(c.life, func() { _ = c.Close() })
	}
	return c
}

// SetCallSettings replaces the server's per-call settings; calls already
// sent keep theirs.
func (c *Client) SetCallSettings(s CallSettings) {
	if s.MaxResultBytes <= 0 {
		s.MaxResultBytes = DefaultMaxResultBytes
	}
	s.ForwardHeaders = append([]string(nil), s.ForwardHeaders...)
	c.settings.Store(&s)
}

// CallSettings returns the server's per-call settings.
func (c *Client) CallSettings() CallSettings { return *c.settings.Load() }

// Connect establishes the connection, performs the MCP handshake, and
// discovers tools. ctx bounds the handshake only: a stdio child keeps running
// after it ends, until Close.
func (c *Client) Connect(ctx context.Context) error {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	defer context.AfterFunc(c.life, cancel)() // Close aborts a handshake in flight
	if c.life.Err() != nil {
		cancel()
	}

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
	slog.Info("mcp client connected", "server", c.serverID, "era", c.Era(), "tools", c.ToolCount())
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

// handshake settles the era (client_era.go) and lists everything.
func (c *Client) handshake(ctx context.Context, t Transport) error {
	if err := c.negotiate(ctx, t); err != nil {
		return err
	}
	// Discovery failures are non-fatal: a server may not have tools yet, and
	// resources and prompts are optional.
	c.refreshLists(ctx, t)
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
// starts t's loops: health, list refresh and the notification stream.
// Called with c.mu held.
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
	c.backoffMu.Lock()
	c.installedAt = time.Now()
	c.backoffMu.Unlock()
	c.healthy.Store(true)
	go c.healthLoop(monitors, t)
	go c.refreshLoop(monitors, t)
	go c.listenLoop(monitors, t)

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

// newID returns a request id no other request of this client has used: the
// id an upstream sees is always the client's own, never a caller's.
func (c *Client) newID() json.RawMessage {
	return json.RawMessage(strconv.FormatInt(c.ids.Add(1), 10))
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

// CallTool forwards a tool call to the upstream server. An upstream JSON-RPC
// error comes back as an isError result carrying its message.
func (c *Client) CallTool(ctx context.Context, name string, args map[string]interface{}) (*ToolCallResult, error) {
	t, err := c.readyTransport()
	if err != nil {
		return nil, err
	}
	params, err := json.Marshal(ToolCallParams{Name: name, Arguments: args})
	if err != nil {
		return nil, err
	}
	resp, err := c.exchange(ctx, t, MethodToolsCall, params, sendOptions{}, mirrorInput{schema: c.toolSchema(name)})
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

// ReadResource reads a specific resource from the upstream server.
func (c *Client) ReadResource(ctx context.Context, uri string) (*ReadResourceResult, error) {
	t, err := c.readyTransport()
	if err != nil {
		return nil, fmt.Errorf("server %s is unavailable", c.serverID)
	}
	params, _ := json.Marshal(ReadResourceParams{URI: uri})
	resp, err := c.exchange(ctx, t, MethodResourcesRead, params, sendOptions{}, mirrorInput{})
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
	params, _ := json.Marshal(GetPromptParams{Name: name, Arguments: arguments})
	resp, err := c.exchange(ctx, t, MethodPromptsGet, params, sendOptions{}, mirrorInput{})
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

// toolSchema is the cached inputSchema of an upstream tool, or nil.
func (c *Client) toolSchema(name string) json.RawMessage {
	for _, t := range c.Tools() {
		if t.Name == name {
			return t.InputSchema
		}
	}
	return nil
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

// SetOnToolsChanged sets a callback for when the tool list changes: a
// refresh (list_changed, the TTL, a reconnect) that found a different list.
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
		c.stopLife() // ends the loops and any reconnect, aborts a handshake
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

// transportStopped is a stdio or HTTP+SSE transport's report that it stopped
// on its own (the child exited, the stream ended, a message over the limit).
// Only the current transport counts; one still being dialled fails its
// handshake instead.
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

// healthLoop checks t until its monitors end (ping, or server/discover on
// the 2026 era, which has no ping); three failures in a row mark the client
// unhealthy and start a reconnect.
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
		err := c.ping(pingCtx, t)
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

// Reconnect backoff bounds.
const (
	reconnectMaxBackoff = 60 * time.Second
	reconnectResetAfter = time.Minute
)

// nextBackoff is the delay before the next reconnect attempt: it doubles on
// every attempt, across reconnects, up to reconnectMaxBackoff, and starts
// again from reconnectBase only once a connection lasted reconnectResetAfter.
func (c *Client) nextBackoff() time.Duration {
	c.backoffMu.Lock()
	defer c.backoffMu.Unlock()
	if c.backoff == 0 || (!c.installedAt.IsZero() && time.Since(c.installedAt) >= reconnectResetAfter) {
		c.backoff = c.reconnectBase
	}
	c.installedAt = time.Time{}
	d := c.backoff
	c.backoff = min(2*d, reconnectMaxBackoff)
	return d
}

// reconnect re-establishes the connection with exponential backoff until it
// succeeds or the client closes.
func (c *Client) reconnect() {
	for attempt := 1; ; attempt++ {
		backoff := c.nextBackoff()
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
	maxBytes := c.CallSettings().MaxResultBytes
	switch c.cfg.TransportType {
	case "stdio":
		return c.startStdio(ctx)
	case "sse":
		t := newSSETransport(c.cfg.URL, c.cfg.Auth, c.cfg.Dialer, maxBytes)
		t.onMessage = c.handleMessage
		t.onDead = func(err error) { c.transportStopped(t, err) }
		if err := t.Start(ctx); err != nil {
			return nil, err
		}
		return t, nil
	default: // "http" or empty
		t := newHTTPTransport(c.cfg.URL, c.cfg.Auth, c.cfg.Dialer, maxBytes)
		t.onMessage = c.handleMessage
		return t, nil
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

// --- The protocol: era, session, requests --------------------------------------

// gatewayClientInfo is how the gateway introduces itself upstream.
var gatewayClientInfo = Implementation{Name: "agentcc-gateway", Version: "1.0.0"}

// List-change notifications besides tools' (types.go names that one).
const (
	methodResourcesListChanged = "notifications/resources/list_changed"
	methodPromptsListChanged   = "notifications/prompts/list_changed"
)

// Era is the protocol version negotiated with the upstream ("" before the
// handshake). It is cached per server and re-probed on -32022.
func (c *Client) Era() string {
	if v := c.era.Load(); v != nil {
		return *v
	}
	return ""
}

func (c *Client) is2026() bool { return c.Era() == wire.Version2026 }

// useEra records the negotiated version; an HTTP transport sends it as
// MCP-Protocol-Version on every later request.
func (c *Client) useEra(t Transport, v string) {
	c.era.Store(&v)
	if h, ok := t.(*HTTPTransport); ok {
		h.setVersion(v)
	}
}

// negotiate settles the era: a configured one skips detection; otherwise
// server/discover with the 2026 _meta, and any answer that is not 2026-shaped
// falls back to initialize with the newest 2025 revision. transport "sse" is
// always 2024-11-05.
func (c *Client) negotiate(ctx context.Context, t Transport) error {
	era := c.cfg.Era
	switch {
	case c.cfg.TransportType == "sse":
		return c.initialize(ctx, t, wire.VersionHTTPSSE)
	case era == wire.Version2026:
		c.useEra(t, era)
		return nil
	case era != "" && era != "auto":
		return c.initialize(ctx, t, era)
	case c.discover(ctx, t):
		c.useEra(t, wire.Version2026)
		return nil
	}
	return c.initialize(ctx, t, ProtocolVersion)
}

// discoverTimeout bounds the era probe, so an older server that never
// answers an unknown method leaves the connect time for initialize.
const discoverTimeout = 10 * time.Second

// discover asks server/discover as a 2026-07-28 client would, and reports
// whether the answer was 2026-shaped.
func (c *Client) discover(ctx context.Context, t Transport) bool {
	ctx, cancel := context.WithTimeout(ctx, discoverTimeout)
	defer cancel()
	info, _ := json.Marshal(gatewayClientInfo)
	params, err := wire.SetMeta(nil, wire.RequestMeta(info))
	if err != nil {
		return false
	}
	msg := &Message{JSONRPC: "2.0", ID: c.newID(), Method: MethodServerDiscover, Params: params}
	h := http.Header{}
	h.Set("MCP-Protocol-Version", wire.Version2026)
	h.Set(wire.HeaderMethod, MethodServerDiscover)
	resp, err := c.send(ctx, t, msg, sendOptions{header: h})
	return err == nil && resp != nil && resp.Error == nil && wire.Supports2026(resp.Result)
}

// initialize runs the legacy handshake with version and records the version
// the server chose; on HTTP it starts a new session (the initialize carries
// no session id, and the answer's replaces the old one, which requests in
// flight keep until then).
func (c *Client) initialize(ctx context.Context, t Transport, version string) error {
	params, _ := json.Marshal(InitializeParams{ProtocolVersion: version, Capabilities: ClientCapabilities{}, ClientInfo: gatewayClientInfo})
	resp, err := c.send(ctx, t, &Message{JSONRPC: "2.0", ID: c.newID(), Method: MethodInitialize, Params: params}, sendOptions{})
	if err != nil {
		return fmt.Errorf("initialize: %w", err)
	}
	if resp.Error != nil {
		return fmt.Errorf("initialize error: %s", resp.Error.Message)
	}
	var result InitializeResult
	_ = json.Unmarshal(resp.Result, &result)
	negotiated := result.ProtocolVersion
	if negotiated == "" {
		negotiated = version
	}
	if negotiated == wire.Version2026 || !slices.Contains(config.MCPProtocolVersions(), negotiated) {
		return fmt.Errorf("initialize: the server chose protocol version %q, which the gateway does not speak", negotiated)
	}
	c.useEra(t, negotiated)
	if _, err := c.send(ctx, t, &Message{JSONRPC: "2.0", Method: MethodInitialized}, sendOptions{}); err != nil {
		slog.Warn("failed to send initialized notification", "server", c.serverID, "error", err)
	}
	return nil
}

// mirrorInput is what a 2026 tools/call's Mcp-Param-* headers are built
// from: the upstream tool's inputSchema and the parameters the client sent
// as Mcp-Param-* headers.
type mirrorInput struct {
	schema  json.RawMessage
	inbound http.Header
}

// exchange sends one request and returns its response. A legacy session the
// upstream ended (HTTP 404, answered at the session check, before any call
// runs) is re-initialized and the request retried once; a 2026 request
// refused with -32022 re-probes the era and is retried once.
func (c *Client) exchange(ctx context.Context, t Transport, method string, params json.RawMessage, opts sendOptions, mirror mirrorInput) (*Message, error) {
	for attempt := 0; ; attempt++ {
		msg, msgOpts, err := c.request(method, params, opts, mirror)
		if err != nil {
			return nil, err
		}
		sid := sessionOf(t)
		resp, err := c.send(ctx, t, msg, msgOpts)
		if attempt > 0 || err == nil {
			return resp, err
		}
		switch {
		case errors.Is(err, errSessionNotFound):
			slog.Info("mcp upstream session ended; re-initializing", "server", c.serverID, "method", method)
			if rerr := c.reinitialize(ctx, t, sid); rerr != nil {
				return nil, fmt.Errorf("%w (re-initialize: %v)", err, rerr)
			}
		case c.is2026() && unsupportedVersion(err):
			slog.Info("mcp upstream refused 2026-07-28; detecting its era again", "server", c.serverID)
			if rerr := c.reprobe(ctx, t); rerr != nil {
				return nil, fmt.Errorf("%w (era probe: %v)", err, rerr)
			}
		default:
			return nil, err
		}
	}
}

// request builds one request with a fresh id. On the 2026 era its params
// carry the per-request _meta, and opts the mirrored headers.
func (c *Client) request(method string, params json.RawMessage, opts sendOptions, mirror mirrorInput) (*Message, sendOptions, error) {
	if c.is2026() {
		info, _ := json.Marshal(gatewayClientInfo)
		var err error
		if params, err = wire.SetMeta(params, wire.RequestMeta(info)); err != nil {
			return nil, opts, fmt.Errorf("%s params: %w", method, err)
		}
		h := opts.header.Clone()
		if h == nil {
			h = http.Header{}
		}
		for k, vs := range wire.Mirrored(method, params, mirror.schema, mirror.inbound) {
			h[k] = vs
		}
		opts.header = h
	}
	return &Message{JSONRPC: "2.0", ID: c.newID(), Method: method, Params: params}, opts, nil
}

// sessionOf is t's legacy session id, or "".
func sessionOf(t Transport) string {
	if h, ok := t.(*HTTPTransport); ok {
		return h.session()
	}
	return ""
}

// unsupportedVersion reports a 2026-07-28 refusal of the protocol version.
func unsupportedVersion(err error) bool {
	var se *HTTPStatusError
	return errors.As(err, &se) && se.RPC != nil && se.RPC.Code == wire.CodeUnsupportedVersion
}

// send sends msg on t with opts (headers only where the transport has
// them). A request whose context ends before its answer is cancelled
// upstream with its own id (notifications/cancelled), except on the 2026 era,
// where closing the request is the cancellation. ctx's dispatch notice
// (WithDispatchNotice) runs once the request's bytes are written: HTTP
// requests are traced here, the stdio transport calls it after its write.
func (c *Client) send(ctx context.Context, t Transport, msg *Message, opts sendOptions) (*Message, error) {
	if c.life.Err() != nil {
		return nil, c.life.Err()
	}
	var resp *Message
	var err error
	ctx = withWroteRequestNotice(ctx)
	if os, ok := t.(optionSender); ok {
		resp, err = os.sendWith(ctx, msg, opts)
	} else {
		if opts.auth != nil {
			return nil, errOptionUnsupported
		}
		resp, err = t.Send(ctx, msg)
	}
	if err != nil && msg.IsRequest() && ctx.Err() != nil && !c.is2026() {
		c.cancelUpstream(t, msg.ID, ctx.Err())
	}
	return resp, err
}

// cancelUpstream tells the upstream to stop the request id (best effort).
func (c *Client) cancelUpstream(t Transport, id json.RawMessage, cause error) {
	// A caller timeout may send cancellation, but an evicted connection may
	// not authenticate a new request, even a best-effort cancellation notice.
	if c.life.Err() != nil {
		return
	}
	params, _ := json.Marshal(map[string]any{"requestId": id, "reason": cause.Error()})
	ctx, cancel := context.WithTimeout(c.life, 2*time.Second)
	defer cancel()
	if _, err := t.Send(ctx, &Message{JSONRPC: "2.0", Method: MethodCancelled, Params: params}); err != nil {
		slog.Debug("mcp cancel notification not sent", "server", c.serverID, "error", err)
	}
}

// reinitialize starts a new legacy session after the upstream ended the one
// staleSID names; when another request already replaced it, it does nothing.
func (c *Client) reinitialize(ctx context.Context, t Transport, staleSID string) error {
	if _, ok := t.(*HTTPTransport); !ok {
		return errSessionNotFound
	}
	c.reinitMu.Lock()
	defer c.reinitMu.Unlock()
	if sessionOf(t) != staleSID {
		return nil
	}
	return c.initialize(ctx, t, c.Era())
}

// reprobe detects the era again, as a fresh connection would (the
// configured era, if any, is not re-tried: it is what was refused).
func (c *Client) reprobe(ctx context.Context, t Transport) error {
	c.reinitMu.Lock()
	defer c.reinitMu.Unlock()
	if !c.is2026() {
		return nil // another request already moved to the older era
	}
	if c.discover(ctx, t) {
		return errors.New("server/discover still lists 2026-07-28")
	}
	return c.initialize(ctx, t, ProtocolVersion)
}

// ping checks the upstream is answering: ping, or server/discover on the
// 2026 era, which removed ping. A JSON-RPC or HTTP error answer still means
// the server is there.
func (c *Client) ping(ctx context.Context, t Transport) error {
	method := MethodPing
	if c.is2026() {
		method = MethodServerDiscover
	}
	_, err := c.exchange(ctx, t, method, nil, sendOptions{}, mirrorInput{})
	var se *HTTPStatusError
	if errors.As(err, &se) {
		return nil
	}
	return err
}

// --- Lists ---------------------------------------------------------------------

// listAll reads every page of one list (wire.All's caps), logging a capped
// listing.
func (c *Client) listAll(ctx context.Context, t Transport, method, key string) ([]json.RawMessage, error) {
	l, err := wire.All(ctx, func(ctx context.Context, cursor string) ([]json.RawMessage, string, error) {
		var params json.RawMessage
		if cursor != "" {
			params, _ = json.Marshal(map[string]string{"cursor": cursor})
		}
		resp, err := c.exchange(ctx, t, method, params, sendOptions{}, mirrorInput{})
		if err != nil {
			return nil, "", fmt.Errorf("%s: %w", method, err)
		}
		if resp.Error != nil {
			return nil, "", fmt.Errorf("%s error: %s", method, resp.Error.Message)
		}
		var page map[string]json.RawMessage
		if err := json.Unmarshal(resp.Result, &page); err != nil {
			return nil, "", fmt.Errorf("decode %s: %w", method, err)
		}
		var items []json.RawMessage
		var next string
		if err := json.Unmarshal(page[key], &items); err != nil && page[key] != nil {
			return nil, "", fmt.Errorf("decode %s: %w", method, err)
		}
		_ = json.Unmarshal(page["nextCursor"], &next)
		return items, next, nil
	})
	if l.Capped {
		slog.Warn("mcp upstream list capped", "server", c.serverID, "method", method,
			"pages", l.Pages, "items", len(l.Items), "max_pages", wire.MaxPages, "max_items", wire.MaxItems)
	}
	return l.Items, err
}

// decodeList decodes raw items into T (whose decoder keeps Raw).
func decodeList[T any](items []json.RawMessage) ([]T, error) {
	out := make([]T, 0, len(items))
	for _, raw := range items {
		var v T
		if err := json.Unmarshal(raw, &v); err != nil {
			return nil, err
		}
		out = append(out, v)
	}
	return out, nil
}

// refreshLists lists tools, resources and prompts again; a failure keeps the
// list held before.
func (c *Client) refreshLists(ctx context.Context, t Transport) {
	c.listMu.Lock()
	defer c.listMu.Unlock()
	if err := c.discoverTools(ctx, t); err != nil {
		slog.Warn("tool discovery failed", "server", c.serverID, "error", err)
	}
	if err := c.discoverResources(ctx, t); err != nil {
		slog.Debug("resource discovery failed (may not be supported)", "server", c.serverID, "error", err)
	}
	if err := c.discoverPrompts(ctx, t); err != nil {
		slog.Debug("prompt discovery failed (may not be supported)", "server", c.serverID, "error", err)
	}
}

// discoverTools lists every tool page and caches the list; a list that
// differs from the one held emits the change event.
func (c *Client) discoverTools(ctx context.Context, t Transport) error {
	raw, err := c.listAll(ctx, t, MethodToolsList, "tools")
	if err != nil {
		return err
	}
	tools, err := decodeList[Tool](raw)
	if err != nil {
		return fmt.Errorf("decode tools/list: %w", err)
	}
	old := c.tools.Swap(&tools)
	if old != nil && sameRaw(*old, tools, func(t Tool) []byte { return t.Raw }) {
		return nil
	}
	if fn := c.onToolsChanged.Load(); fn != nil {
		(*fn)(c.serverID, tools)
	}
	return nil
}

// sameRaw reports whether two lists hold the same items, byte for byte.
func sameRaw[T any](a, b []T, raw func(T) []byte) bool {
	return slices.EqualFunc(a, b, func(x, y T) bool { return string(raw(x)) == string(raw(y)) })
}

// discoverResources lists every resource page and caches the list.
func (c *Client) discoverResources(ctx context.Context, t Transport) error {
	raw, err := c.listAll(ctx, t, MethodResourcesList, "resources")
	if err != nil {
		return err
	}
	resources, err := decodeList[Resource](raw)
	if err != nil {
		return fmt.Errorf("decode resources/list: %w", err)
	}
	c.resources.Store(&resources)
	return nil
}

// discoverPrompts lists every prompt page and caches the list.
func (c *Client) discoverPrompts(ctx context.Context, t Transport) error {
	raw, err := c.listAll(ctx, t, MethodPromptsList, "prompts")
	if err != nil {
		return err
	}
	prompts, err := decodeList[Prompt](raw)
	if err != nil {
		return fmt.Errorf("decode prompts/list: %w", err)
	}
	c.prompts.Store(&prompts)
	return nil
}

// requestRefresh asks for one list refresh; requests coalesce.
func (c *Client) requestRefresh() {
	select {
	case c.refresh <- struct{}{}:
	default:
	}
}

// refreshLoop refreshes the lists every ToolsCacheTTL and on request (an
// upstream list_changed), until t's monitors end.
func (c *Client) refreshLoop(ctx context.Context, t Transport) {
	ttl := time.NewTicker(c.cfg.ToolsCacheTTL)
	defer ttl.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ttl.C:
		case <-c.refresh:
		}
		rctx, cancel := context.WithTimeout(ctx, 30*time.Second)
		c.refreshLists(rctx, t)
		cancel()
	}
}

// listen stream backoff bounds.
const (
	listenBaseBackoff = time.Second
	listenMaxBackoff  = 30 * time.Second
)

// listenLoop keeps the upstream's notification stream open on an HTTP
// transport: a legacy session's GET stream, or 2026's subscriptions/listen.
// Its messages go to handleMessage. A stream that ends is reopened with
// backoff, and the lists are refreshed then, for anything missed; an upstream
// without such a stream leaves the TTL refresh alone.
func (c *Client) listenLoop(ctx context.Context, t Transport) {
	h, ok := t.(*HTTPTransport)
	if !ok {
		return
	}
	backoff := listenBaseBackoff
	for ctx.Err() == nil {
		sid := h.session()
		var listen *Message
		var header http.Header
		switch {
		case c.is2026():
			params, _ := json.Marshal(map[string]any{"notifications": []string{
				MethodToolsListChanged, methodResourcesListChanged, methodPromptsListChanged}})
			msg, opts, err := c.request(MethodSubscriptionsListen, params, sendOptions{}, mirrorInput{})
			if err != nil {
				return
			}
			listen, header = msg, opts.header
		case sid == "":
			return // a legacy server without a session has no GET stream
		}
		opened := time.Now()
		err := h.stream(ctx, listen, header, c.handleMessage)
		switch {
		case ctx.Err() != nil:
			return
		case errors.Is(err, errStreamUnsupported):
			slog.Debug("mcp upstream has no notification stream; refreshing on the TTL", "server", c.serverID, "error", err)
			return
		case errors.Is(err, errSessionNotFound):
			if rerr := c.reinitialize(ctx, t, sid); rerr != nil {
				slog.Warn("mcp re-initialize for the notification stream failed", "server", c.serverID, "error", rerr)
			}
		}
		if time.Since(opened) > listenMaxBackoff {
			backoff = listenBaseBackoff
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(backoff):
		}
		backoff = min(2*backoff, listenMaxBackoff)
		c.requestRefresh()
	}
}

// handleMessage takes the upstream's messages that answer no request: a list
// change refreshes the lists, progress goes to the call whose token it
// carries (anything else is dropped), and a server request is answered.
func (c *Client) handleMessage(m *Message) {
	switch {
	case m.Method == MethodToolsListChanged || m.Method == methodResourcesListChanged || m.Method == methodPromptsListChanged:
		c.requestRefresh()
	case m.Method == MethodProgress:
		if sink, params, ok := c.tokens.Route(m.Params); ok {
			sink(params)
		}
	case m.IsRequest():
		c.answerServerRequest(m)
	}
}

// answerServerRequest answers a legacy server's own request: ping, and a
// refusal for everything else (elicitation and sampling are M28's), so the
// server never waits on the gateway.
func (c *Client) answerServerRequest(m *Message) {
	t := c.currentTransport()
	if t == nil {
		return
	}
	resp := NewErrorResponse(m.ID, ErrCodeMethodNotFound, "the gateway does not serve "+m.Method)
	if m.Method == MethodPing {
		resp, _ = NewResponse(m.ID, struct{}{})
	}
	go func() {
		ctx, cancel := context.WithTimeout(c.life, 10*time.Second)
		defer cancel()
		if _, err := t.Send(ctx, resp); err != nil {
			slog.Debug("mcp answer to a server request not sent", "server", c.serverID, "method", m.Method, "error", err)
		}
	}()
}

// --- Raw calls: the factory's UpstreamClient and the upstream hook ---------------

// UpstreamError is a JSON-RPC error an upstream answered a call with; Code
// is the upstream's own, kept for MC.3's upstream_error.
type UpstreamError struct {
	Code    int
	Message string
	Data    json.RawMessage
}

func (e *UpstreamError) Error() string {
	return fmt.Sprintf("upstream JSON-RPC error %d: %s", e.Code, e.Message)
}

// ErrResultTooLarge is an upstream answer over mcp.max_result_bytes.
var ErrResultTooLarge = wire.ErrTooLarge

type notificationSinkKey struct{}

// WithUpstreamNotifications returns ctx carrying fn: the upstream's
// notifications for a call made with ctx (its progress, with the caller's own
// progressToken restored) go to fn, from the goroutine reading the upstream,
// until the call returns. Without it they are dropped.
func WithUpstreamNotifications(ctx context.Context, fn func(*Message)) context.Context {
	return context.WithValue(ctx, notificationSinkKey{}, fn)
}

// CallToolRaw calls one upstream tool and returns its result as the
// upstream sent it. opts.ForwardHeaders carries the client's headers: only
// the server's forward_headers are forwarded, and an Mcp-Param-{Name} entry
// names a parameter the client sent, regenerated from the arguments.
func (c *Client) CallToolRaw(ctx context.Context, name string, args json.RawMessage, opts CallOptions) (json.RawMessage, error) {
	params := map[string]json.RawMessage{"name": mustJSON(name)}
	if len(args) > 0 && string(args) != "null" {
		params["arguments"] = args
	}
	return c.callRaw(ctx, MethodToolsCall, params, c.toolSchema(name), opts)
}

// ReadResourceRaw reads one upstream resource, raw.
func (c *Client) ReadResourceRaw(ctx context.Context, uri string, opts CallOptions) (json.RawMessage, error) {
	return c.callRaw(ctx, MethodResourcesRead, map[string]json.RawMessage{"uri": mustJSON(uri)}, nil, opts)
}

// GetPromptRaw gets one upstream prompt, raw.
func (c *Client) GetPromptRaw(ctx context.Context, name string, args json.RawMessage, opts CallOptions) (json.RawMessage, error) {
	params := map[string]json.RawMessage{"name": mustJSON(name)}
	if len(args) > 0 && string(args) != "null" {
		params["arguments"] = args
	}
	return c.callRaw(ctx, MethodPromptsGet, params, nil, opts)
}

func mustJSON(v any) json.RawMessage {
	b, _ := json.Marshal(v)
	return b
}

// callRaw sends one call: bounded by the server's timeout (else
// opts.Timeout, the caller's tool_call_timeout) and the caller's deadline;
// with a gateway progress token in place of the caller's; with opts.Meta in
// _meta; with the per-call credential, the forwarded headers and the answer
// bound.
func (c *Client) callRaw(ctx context.Context, method string, params map[string]json.RawMessage, schema json.RawMessage, opts CallOptions) (json.RawMessage, error) {
	t, err := c.readyTransport()
	if err != nil {
		return nil, err
	}
	settings := c.CallSettings()
	if timeout := firstSet(settings.Timeout, opts.Timeout); timeout > 0 {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, timeout)
		defer cancel()
	}
	meta := make(map[string]json.RawMessage, len(opts.Meta)+1)
	for k, v := range opts.Meta {
		meta[k] = v
	}
	if opts.ProgressToken != "" {
		sink, _ := ctx.Value(notificationSinkKey{}).(func(*Message))
		token, release := c.tokens.Issue(progressTokenJSON(opts.ProgressToken), func(p json.RawMessage) {
			if sink != nil {
				sink(&Message{JSONRPC: "2.0", Method: MethodProgress, Params: p})
			}
		})
		defer release()
		meta[wire.MetaProgressToken] = token
	}
	body, err := json.Marshal(params)
	if err != nil {
		return nil, err
	}
	if body, err = wire.SetMeta(body, meta); err != nil {
		return nil, err
	}
	so := sendOptions{auth: opts.Auth, header: wire.Forwarded(opts.ForwardHeaders, settings.ForwardHeaders), maxBytes: settings.MaxResultBytes}
	resp, err := c.exchange(ctx, t, method, body, so, mirrorInput{schema: schema, inbound: opts.ForwardHeaders})
	if err != nil {
		var se *HTTPStatusError
		if errors.As(err, &se) && se.RPC != nil {
			return nil, &UpstreamError{Code: se.RPC.Code, Message: se.RPC.Message, Data: se.RPC.Data}
		}
		return nil, err
	}
	if resp.Error != nil {
		return nil, &UpstreamError{Code: resp.Error.Code, Message: resp.Error.Message, Data: resp.Error.Data}
	}
	if int64(len(resp.Result)) > settings.MaxResultBytes {
		return nil, fmt.Errorf("%w: %d bytes", ErrResultTooLarge, len(resp.Result))
	}
	return resp.Result, nil
}

// firstSet is the first of a and b that is set.
func firstSet(a, b time.Duration) time.Duration {
	if a > 0 {
		return a
	}
	return b
}

// progressTokenJSON is a caller's progressToken as JSON: CallOptions carries
// its JSON text (a number, or a quoted string); bare text is a string.
func progressTokenJSON(s string) json.RawMessage {
	var v any
	if json.Unmarshal([]byte(s), &v) == nil {
		switch v.(type) {
		case string, float64:
			return json.RawMessage(s)
		}
	}
	return mustJSON(s)
}
