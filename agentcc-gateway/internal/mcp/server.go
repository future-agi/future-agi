package mcp

import (
	"context"
	"encoding/json"
	"sync"
	"sync/atomic"
	"time"
)

// ToolCallGuard validates MCP tool calls before and after execution.
type ToolCallGuard interface {
	// CheckPre validates a tool call before forwarding to upstream.
	// Returns an error message if blocked, or "" if allowed.
	CheckPre(toolName, serverID string, arguments map[string]interface{}) string

	// CheckPost validates a tool call result after receiving from upstream.
	// Returns an error message if the result should be blocked, or "" if allowed.
	CheckPost(toolName, serverID string, result *ToolCallResult) string
}

// KeyAuthenticator looks up an API key from a raw bearer token. valid is
// false for an unknown or inactive key.
type KeyAuthenticator interface {
	AuthenticateKey(rawKey string) (grant KeyGrant, valid bool)
}

// KeyGrant is what per-key auth knows about a valid key: who it is, which owns
// the sessions it opens, and which tools it may use.
type KeyGrant struct {
	KeyID        string
	OrgID        string // "" for a key with no org
	AllowedTools []string
	DeniedTools  []string
}

// contextKey is an unexported type for context keys in this package.
type contextKey int

const (
	ctxKeyAllowedTools contextKey = iota
	ctxKeyDeniedTools
	ctxKeySessionOwner
)

// Server is the MCP server handler for Agentcc.
// It serves as a gateway: agents connect to Agentcc via MCP, and Agentcc
// aggregates tools from all upstream MCP servers.
type Server struct {
	registry    *Registry
	sessions    *SessionManager
	clients     map[string]*Client // server ID → client
	clientsMu   sync.RWMutex
	depth       *DepthTracker
	toolTimeout time.Duration
	serverInfo  Implementation
	sseClients  map[string]chan []byte // session ID → SSE channel
	sseMu       sync.RWMutex
	guard       ToolCallGuard    // optional pre/post tool call guardrail
	keyAuth     KeyAuthenticator // optional per-key tool filtering

	// depsPtr holds the chain's late-bound dependencies (SetDeps, Deps).
	depsPtr atomic.Pointer[Deps]
	// hookObserver sees every chain hook as it runs: a test seam, nil in a
	// running gateway (observeHook).
	hookObserver func(hook string, cc *CallContext)
}

// ServerConfig holds configuration for the MCP server.
type ServerConfig struct {
	MaxAgentDepth   int
	ToolCallTimeout time.Duration
	SessionTTL      time.Duration
	Separator       string
}

// SetGuard attaches a tool call guardrail to the MCP server.
func (s *Server) SetGuard(guard ToolCallGuard) {
	s.guard = guard
}

// SetKeyAuth attaches a per-key tool authenticator to the MCP server.
func (s *Server) SetKeyAuth(auth KeyAuthenticator) {
	s.keyAuth = auth
}

// NewServer creates a new MCP server handler.
func NewServer(cfg ServerConfig) *Server {
	if cfg.ToolCallTimeout <= 0 {
		cfg.ToolCallTimeout = 30 * time.Second
	}
	if cfg.Separator == "" {
		cfg.Separator = "_"
	}

	return &Server{
		registry:    NewRegistry(cfg.Separator),
		sessions:    NewSessionManager(cfg.SessionTTL),
		clients:     make(map[string]*Client),
		depth:       NewDepthTracker(cfg.MaxAgentDepth),
		toolTimeout: cfg.ToolCallTimeout,
		serverInfo: Implementation{
			Name:    "agentcc-gateway",
			Version: "1.0.0",
		},
		sseClients: make(map[string]chan []byte),
	}
}

// RegisterClient adds an upstream MCP client and registers its tools, resources, and prompts.
func (s *Server) RegisterClient(client *Client) {
	s.clientsMu.Lock()
	s.clients[client.ServerID()] = client
	s.clientsMu.Unlock()

	// Register tools in the aggregated catalog.
	tools := client.Tools()
	s.registry.RegisterServer(client.ServerID(), tools)

	// Register resources and prompts.
	if resources := client.Resources(); len(resources) > 0 {
		s.registry.RegisterResources(client.ServerID(), resources)
	}
	if prompts := client.Prompts(); len(prompts) > 0 {
		s.registry.RegisterPrompts(client.ServerID(), prompts)
	}

	// Listen for tool list changes.
	client.SetOnToolsChanged(func(serverID string, tools []Tool) {
		s.registry.RegisterServer(serverID, tools)
		s.notifyToolsChanged()
	})
}

// UnregisterClient removes an upstream MCP client and its tools.
func (s *Server) UnregisterClient(serverID string) {
	s.clientsMu.Lock()
	client, ok := s.clients[serverID]
	if ok {
		delete(s.clients, serverID)
	}
	s.clientsMu.Unlock()

	s.registry.UnregisterServer(serverID)

	if client != nil {
		client.Close()
	}
	s.notifyToolsChanged()
}

// Registry returns the tool registry.
func (s *Server) Registry() *Registry {
	return s.registry
}

// Close shuts down the server and all upstream connections.
// Shutdown is Close bounded by ctx, for the gateway's shutdown: it returns
// when every client is closed, or with
// ctx.Err() when ctx ends first, leaving the rest to finish in the
// background.
func (s *Server) Shutdown(ctx context.Context) error {
	done := make(chan struct{})
	go func() {
		s.Close()
		close(done)
	}()
	select {
	case <-done:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

// Close closes every session, upstream client and stream. It waits for each
// client's Close, so stdio children have run their shutdown ladder when it
// returns.
func (s *Server) Close() {
	s.sessions.Close()

	// Take the clients out under the lock, then close them in parallel with
	// it released: a stdio client's Close can take its whole shutdown ladder
	// (stdin close, grace, SIGTERM, wait, SIGKILL), and one after another
	// they would hold clientsMu for the sum.
	s.clientsMu.Lock()
	clients := make([]*Client, 0, len(s.clients))
	for _, client := range s.clients {
		clients = append(clients, client)
	}
	s.clients = make(map[string]*Client)
	s.clientsMu.Unlock()
	var wg sync.WaitGroup
	for _, client := range clients {
		wg.Add(1)
		go func(c *Client) {
			defer wg.Done()
			c.Close()
		}(client)
	}
	wg.Wait()

	s.sseMu.Lock()
	for id, ch := range s.sseClients {
		close(ch)
		delete(s.sseClients, id)
	}
	s.sseMu.Unlock()
}

// notifyToolsChanged sends a tools/list_changed notification to all connected SSE clients.
func (s *Server) notifyToolsChanged() {
	notif, err := NewNotification(MethodToolsListChanged, nil)
	if err != nil {
		return
	}
	data, err := json.Marshal(notif)
	if err != nil {
		return
	}

	s.sseMu.RLock()
	defer s.sseMu.RUnlock()

	for _, ch := range s.sseClients {
		select {
		case ch <- data:
		default:
			// Channel full, skip this client.
		}
	}
}
