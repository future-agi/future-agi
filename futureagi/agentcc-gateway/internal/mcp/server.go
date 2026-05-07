package mcp

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"strings"
	"sync"
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

// KeyAuthenticator looks up an API key from a raw bearer token.
// Returns allowed tools, denied tools, and whether the key is valid.
type KeyAuthenticator interface {
	AuthenticateKey(rawKey string) (allowed []string, denied []string, valid bool)
}

// contextKey is an unexported type for context keys in this package.
type contextKey int

const (
	ctxKeyAllowedTools contextKey = iota
	ctxKeyDeniedTools
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
func (s *Server) Close() {
	s.sessions.Close()

	s.clientsMu.Lock()
	for _, client := range s.clients {
		client.Close()
	}
	s.clients = make(map[string]*Client)
	s.clientsMu.Unlock()

	s.sseMu.Lock()
	for id, ch := range s.sseClients {
		close(ch)
		delete(s.sseClients, id)
	}
	s.sseMu.Unlock()
}

// HandlePost handles POST /mcp — the main JSON-RPC endpoint.
func (s *Server) HandlePost(w http.ResponseWriter, r *http.Request) {
	// Parse the JSON-RPC message.
	var msg Message
	if err := json.NewDecoder(r.Body).Decode(&msg); err != nil {
		writeJSONRPCError(w, nil, ErrCodeParse, "invalid JSON: "+err.Error())
		return
	}

	if msg.JSONRPC != "2.0" {
		writeJSONRPCError(w, msg.ID, ErrCodeInvalidRequest, "expected jsonrpc 2.0")
		return
	}

	// Per-key tool filtering: authenticate bearer token and inject allowed/denied tools into context.
	req := r
	if s.keyAuth != nil {
		if rawKey := extractBearerToken(r); rawKey != "" {
			allowed, denied, valid := s.keyAuth.AuthenticateKey(rawKey)
			if !valid {
				writeJSONRPCError(w, msg.ID, ErrCodeInvalidRequest, "invalid or inactive API key")
				return
			}
			ctx := r.Context()
			if len(allowed) > 0 {
				ctx = context.WithValue(ctx, ctxKeyAllowedTools, allowed)
			}
			if len(denied) > 0 {
				ctx = context.WithValue(ctx, ctxKeyDeniedTools, denied)
			}
			req = r.WithContext(ctx)
		}
	}

	// Route by method.
	switch msg.Method {
	case MethodInitialize:
		s.handleInitialize(w, req, &msg)
	case MethodInitialized:
		// Notification — acknowledge but no response.
		w.WriteHeader(http.StatusAccepted)
	case MethodToolsList:
		s.handleToolsList(w, req, &msg)
	case MethodToolsCall:
		s.handleToolsCall(w, req, &msg)
	case MethodResourcesList:
		s.handleResourcesList(w, req, &msg)
	case MethodResourcesRead:
		s.handleResourcesRead(w, req, &msg)
	case MethodPromptsList:
		s.handlePromptsList(w, req, &msg)
	case MethodPromptsGet:
		s.handlePromptsGet(w, req, &msg)
	case MethodPing:
		s.handlePing(w, &msg)
	case MethodCancelled:
		w.WriteHeader(http.StatusAccepted)
	default:
		writeJSONRPCError(w, msg.ID, ErrCodeMethodNotFound, "unknown method: "+msg.Method)
	}
}

// HandleSSE handles GET /mcp — opens an SSE stream for server-to-client notifications.
func (s *Server) HandleSSE(w http.ResponseWriter, r *http.Request) {
	// Validate session.
	sessionID := r.Header.Get("MCP-Session-Id")
	if sessionID == "" {
		http.Error(w, "missing MCP-Session-Id", http.StatusBadRequest)
		return
	}
	session := s.sessions.Get(sessionID)
	if session == nil {
		http.Error(w, "invalid or expired session", http.StatusNotFound)
		return
	}

	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming not supported", http.StatusInternalServerError)
		return
	}

	// Set SSE headers.
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.WriteHeader(http.StatusOK)
	flusher.Flush()

	// Register SSE channel.
	ch := make(chan []byte, 32)
	s.sseMu.Lock()
	s.sseClients[sessionID] = ch
	s.sseMu.Unlock()

	defer func() {
		s.sseMu.Lock()
		delete(s.sseClients, sessionID)
		s.sseMu.Unlock()
	}()

	// Stream events.
	keepalive := time.NewTicker(15 * time.Second)
	defer keepalive.Stop()

	for {
		select {
		case data, ok := <-ch:
			if !ok {
				return
			}
			fmt.Fprintf(w, "data: %s\n\n", data)
			flusher.Flush()

		case <-keepalive.C:
			// SSE comment as keepalive.
			fmt.Fprint(w, ": keepalive\n\n")
			flusher.Flush()
			// Touch session to keep it alive.
			session.Touch()

		case <-r.Context().Done():
			return
		}
	}
}

// --- Method Handlers ---

func (s *Server) handleInitialize(w http.ResponseWriter, r *http.Request, msg *Message) {
	var params InitializeParams
	if err := json.Unmarshal(msg.Params, &params); err != nil {
		writeJSONRPCError(w, msg.ID, ErrCodeInvalidParams, "invalid initialize params")
		return
	}

	// Create session.
	session := s.sessions.Create(params.ClientInfo, params.Capabilities)

	caps := ServerCapabilities{
		Tools: &ToolsCapability{ListChanged: true},
	}
	if s.registry.ResourceCount() > 0 {
		caps.Resources = &ResourcesCapability{ListChanged: true}
	}
	if s.registry.PromptCount() > 0 {
		caps.Prompts = &PromptsCapability{ListChanged: true}
	}

	result := InitializeResult{
		ProtocolVersion: ProtocolVersion,
		Capabilities:    caps,
		ServerInfo:      s.serverInfo,
		Instructions: fmt.Sprintf(
			"Agentcc MCP gateway with %d tools, %d resources, %d prompts from %d servers",
			s.registry.ToolCount(),
			s.registry.ResourceCount(),
			s.registry.PromptCount(),
			len(s.registry.ServerIDs()),
		),
	}

	// Set session ID in response header.
	w.Header().Set("MCP-Session-Id", session.ID)
	writeJSONRPCResult(w, msg.ID, result)

	slog.Info("mcp session created",
		"session", session.ID,
		"client", params.ClientInfo.Name,
		"version", params.ClientInfo.Version,
	)
}

func (s *Server) handleToolsList(w http.ResponseWriter, r *http.Request, msg *Message) {
	session := s.validateSession(w, r, msg)
	if session == nil {
		return
	}

	tools := s.registry.ListMCPTools()

	// Per-key filtering: only return tools the key is allowed to use.
	tools = filterToolsByKey(r.Context(), tools)

	result := ListToolsResult{Tools: tools}
	writeJSONRPCResult(w, msg.ID, result)
}

func (s *Server) handleToolsCall(w http.ResponseWriter, r *http.Request, msg *Message) {
	session := s.validateSession(w, r, msg)
	if session == nil {
		return
	}

	var params ToolCallParams
	if err := json.Unmarshal(msg.Params, &params); err != nil {
		writeJSONRPCError(w, msg.ID, ErrCodeInvalidParams, "invalid tool call params")
		return
	}

	// Check agent depth.
	depth := s.depth.ExtractDepth(r)
	if err := s.depth.CheckDepth(depth); err != nil {
		writeJSONRPCError(w, msg.ID, ErrCodeInvalidRequest, err.Error())
		return
	}

	// Resolve tool in registry.
	regTool, ok := s.registry.ResolveTool(params.Name)
	if !ok {
		writeJSONRPCError(w, msg.ID, ErrCodeMethodNotFound, "tool not found: "+params.Name)
		return
	}

	// Per-key filtering: check if this tool is allowed for the authenticated key.
	if reason := checkToolKeyAccess(r.Context(), params.Name); reason != "" {
		writeJSONRPCError(w, msg.ID, ErrCodeInvalidRequest, reason)
		return
	}

	// Validate arguments against input schema if available.
	if len(regTool.Tool.InputSchema) > 0 && len(params.Arguments) > 0 {
		if errMsg := validateArgsAgainstSchema(regTool.Tool.InputSchema, params.Arguments); errMsg != "" {
			writeJSONRPCError(w, msg.ID, ErrCodeInvalidParams, errMsg)
			return
		}
	}

	// Find the upstream client.
	s.clientsMu.RLock()
	client, ok := s.clients[regTool.Server]
	s.clientsMu.RUnlock()
	if !ok || !client.Healthy() {
		writeJSONRPCError(w, msg.ID, ErrCodeInternal,
			fmt.Sprintf("upstream server %q is unavailable", regTool.Server))
		return
	}

	// Pre-call guardrail check.
	if s.guard != nil {
		if errMsg := s.guard.CheckPre(params.Name, regTool.Server, params.Arguments); errMsg != "" {
			slog.Warn("mcp tool call blocked (pre)",
				"tool", params.Name,
				"server", regTool.Server,
				"reason", errMsg,
			)
			writeJSONRPCError(w, msg.ID, ErrCodeInvalidRequest, "guardrail: "+errMsg)
			return
		}
	}

	// Forward tool call to upstream with the original (un-namespaced) tool name.
	start := time.Now()
	ctx, cancel := context.WithTimeout(r.Context(), s.toolTimeout)
	defer cancel()

	result, err := client.CallTool(ctx, regTool.OrigName, params.Arguments)
	duration := time.Since(start)

	// Record stats.
	regTool.Stats.RecordCall(duration, err != nil || (result != nil && result.IsError))

	if err != nil {
		slog.Error("mcp tool call failed",
			"tool", params.Name,
			"server", regTool.Server,
			"duration", duration,
			"error", err,
		)
		writeJSONRPCError(w, msg.ID, ErrCodeInternal, "tool call failed: "+err.Error())
		return
	}

	// Post-call guardrail check.
	if s.guard != nil {
		if errMsg := s.guard.CheckPost(params.Name, regTool.Server, result); errMsg != "" {
			slog.Warn("mcp tool call blocked (post)",
				"tool", params.Name,
				"server", regTool.Server,
				"reason", errMsg,
			)
			writeJSONRPCError(w, msg.ID, ErrCodeInvalidRequest, "guardrail: "+errMsg)
			return
		}
	}

	// Add deprecation warning if tool is deprecated.
	if vi, ok := s.registry.GetToolVersion(params.Name); ok && vi.Deprecated {
		warning := fmt.Sprintf("DEPRECATED: tool %q is deprecated", params.Name)
		if vi.DeprecationMessage != "" {
			warning = fmt.Sprintf("DEPRECATED: %s", vi.DeprecationMessage)
		}
		if vi.ReplacedBy != "" {
			warning += fmt.Sprintf(". Use %q instead", vi.ReplacedBy)
		}
		result.Content = append(result.Content, ContentPart{
			Type: "text",
			Text: warning,
		})
	}

	slog.Info("mcp tool call",
		"tool", params.Name,
		"server", regTool.Server,
		"duration", duration,
		"is_error", result.IsError,
	)

	writeJSONRPCResult(w, msg.ID, result)
}

func (s *Server) handleResourcesList(w http.ResponseWriter, r *http.Request, msg *Message) {
	session := s.validateSession(w, r, msg)
	if session == nil {
		return
	}
	resources := s.registry.ListResources()
	writeJSONRPCResult(w, msg.ID, ListResourcesResult{Resources: resources})
}

func (s *Server) handleResourcesRead(w http.ResponseWriter, r *http.Request, msg *Message) {
	session := s.validateSession(w, r, msg)
	if session == nil {
		return
	}

	var params ReadResourceParams
	if err := json.Unmarshal(msg.Params, &params); err != nil {
		writeJSONRPCError(w, msg.ID, ErrCodeInvalidParams, "invalid resources/read params")
		return
	}

	// Resolve resource to find the upstream server.
	regRes, ok := s.registry.ResolveResource(params.URI)
	if !ok {
		writeJSONRPCError(w, msg.ID, ErrCodeMethodNotFound, "resource not found: "+params.URI)
		return
	}

	s.clientsMu.RLock()
	client, ok := s.clients[regRes.Server]
	s.clientsMu.RUnlock()
	if !ok || !client.Healthy() {
		writeJSONRPCError(w, msg.ID, ErrCodeInternal,
			fmt.Sprintf("upstream server %q is unavailable", regRes.Server))
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), s.toolTimeout)
	defer cancel()

	result, err := client.ReadResource(ctx, params.URI)
	if err != nil {
		writeJSONRPCError(w, msg.ID, ErrCodeInternal, "resources/read failed: "+err.Error())
		return
	}
	writeJSONRPCResult(w, msg.ID, result)
}

func (s *Server) handlePromptsList(w http.ResponseWriter, r *http.Request, msg *Message) {
	session := s.validateSession(w, r, msg)
	if session == nil {
		return
	}
	prompts := s.registry.ListPrompts()
	writeJSONRPCResult(w, msg.ID, ListPromptsResult{Prompts: prompts})
}

func (s *Server) handlePromptsGet(w http.ResponseWriter, r *http.Request, msg *Message) {
	session := s.validateSession(w, r, msg)
	if session == nil {
		return
	}

	var params GetPromptParams
	if err := json.Unmarshal(msg.Params, &params); err != nil {
		writeJSONRPCError(w, msg.ID, ErrCodeInvalidParams, "invalid prompts/get params")
		return
	}

	// Resolve prompt to find the upstream server and original name.
	regPrompt, ok := s.registry.ResolvePrompt(params.Name)
	if !ok {
		writeJSONRPCError(w, msg.ID, ErrCodeMethodNotFound, "prompt not found: "+params.Name)
		return
	}

	s.clientsMu.RLock()
	client, ok := s.clients[regPrompt.Server]
	s.clientsMu.RUnlock()
	if !ok || !client.Healthy() {
		writeJSONRPCError(w, msg.ID, ErrCodeInternal,
			fmt.Sprintf("upstream server %q is unavailable", regPrompt.Server))
		return
	}

	// Forward with original (un-namespaced) prompt name.
	origName := regPrompt.Prompt.Name
	// Strip the server prefix to get original name.
	if idx := len(regPrompt.Server) + len(s.registry.Separator()); idx < len(origName) {
		origName = origName[idx:]
	}

	ctx, cancel := context.WithTimeout(r.Context(), s.toolTimeout)
	defer cancel()

	result, err := client.GetPrompt(ctx, origName, params.Arguments)
	if err != nil {
		writeJSONRPCError(w, msg.ID, ErrCodeInternal, "prompts/get failed: "+err.Error())
		return
	}
	writeJSONRPCResult(w, msg.ID, result)
}

func (s *Server) handlePing(w http.ResponseWriter, msg *Message) {
	writeJSONRPCResult(w, msg.ID, struct{}{})
}

// validateSession checks the MCP-Session-Id header and returns the session,
// or writes an error response and returns nil.
func (s *Server) validateSession(w http.ResponseWriter, r *http.Request, msg *Message) *Session {
	sessionID := r.Header.Get("MCP-Session-Id")
	if sessionID == "" {
		writeJSONRPCError(w, msg.ID, ErrCodeInvalidRequest, "missing MCP-Session-Id header")
		return nil
	}
	session := s.sessions.Get(sessionID)
	if session == nil {
		// Per MCP spec, expired/invalid session → HTTP 404 so client re-initializes.
		w.WriteHeader(http.StatusNotFound)
		return nil
	}
	return session
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

// --- JSON-RPC Response Helpers ---

func writeJSONRPCResult(w http.ResponseWriter, id json.RawMessage, result interface{}) {
	resp, err := NewResponse(id, result)
	if err != nil {
		writeJSONRPCError(w, id, ErrCodeInternal, "failed to marshal result")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

func writeJSONRPCError(w http.ResponseWriter, id json.RawMessage, code int, message string) {
	resp := NewErrorResponse(id, code, message)
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

// --- Admin API Helpers ---

// ClientStatus returns status info for an upstream client.
type ClientStatus struct {
	ServerID  string `json:"server_id"`
	Healthy   bool   `json:"healthy"`
	ToolCount int    `json:"tool_count"`
}

// ClientStatuses returns the status of all upstream connections.
func (s *Server) ClientStatuses() []ClientStatus {
	s.clientsMu.RLock()
	defer s.clientsMu.RUnlock()

	statuses := make([]ClientStatus, 0, len(s.clients))
	for id, client := range s.clients {
		statuses = append(statuses, ClientStatus{
			ServerID:  id,
			Healthy:   client.Healthy(),
			ToolCount: client.ToolCount(),
		})
	}
	return statuses
}

// SessionCount returns the number of active downstream sessions.
func (s *Server) SessionCount() int {
	return s.sessions.Count()
}

// TestToolResult is the result of a tool execution playground test.
type TestToolResult struct {
	Content       []ContentPart `json:"content,omitempty"`
	IsError       bool          `json:"is_error"`
	DurationMs    float64       `json:"duration_ms"`
	GuardrailPre  string        `json:"guardrail_pre"`  // "pass", "blocked", or "skipped"
	GuardrailPost string        `json:"guardrail_post"` // "pass", "blocked", or "skipped"
	Error         string        `json:"error,omitempty"`
	Server        string        `json:"server,omitempty"`
}

// TestTool executes a tool call for the admin playground (no MCP session required).
func (s *Server) TestTool(ctx context.Context, toolName string, arguments map[string]interface{}) TestToolResult {
	// Resolve tool.
	regTool, ok := s.registry.ResolveTool(toolName)
	if !ok {
		return TestToolResult{Error: "tool not found: " + toolName, GuardrailPre: "skipped", GuardrailPost: "skipped"}
	}

	result := TestToolResult{Server: regTool.Server, GuardrailPre: "skipped", GuardrailPost: "skipped"}

	// Pre-call guardrail check.
	if s.guard != nil {
		if errMsg := s.guard.CheckPre(toolName, regTool.Server, arguments); errMsg != "" {
			result.GuardrailPre = "blocked"
			result.Error = "guardrail blocked: " + errMsg
			return result
		}
		result.GuardrailPre = "pass"
	}

	// Find upstream client.
	s.clientsMu.RLock()
	client, ok := s.clients[regTool.Server]
	s.clientsMu.RUnlock()
	if !ok || !client.Healthy() {
		result.Error = fmt.Sprintf("upstream server %q is unavailable", regTool.Server)
		return result
	}

	// Call upstream.
	start := time.Now()
	callCtx, cancel := context.WithTimeout(ctx, s.toolTimeout)
	defer cancel()

	callResult, err := client.CallTool(callCtx, regTool.OrigName, arguments)
	result.DurationMs = float64(time.Since(start).Microseconds()) / 1000.0

	if err != nil {
		result.Error = "tool call failed: " + err.Error()
		result.IsError = true
		return result
	}

	result.Content = callResult.Content
	result.IsError = callResult.IsError

	// Post-call guardrail check.
	if s.guard != nil {
		if errMsg := s.guard.CheckPost(toolName, regTool.Server, callResult); errMsg != "" {
			result.GuardrailPost = "blocked"
			result.Error = "guardrail blocked output: " + errMsg
			return result
		}
		result.GuardrailPost = "pass"
	}

	return result
}

// --- Per-Key Tool Filtering Helpers ---

// extractBearerToken extracts the bearer token from the Authorization header.
func extractBearerToken(r *http.Request) string {
	auth := r.Header.Get("Authorization")
	if auth == "" {
		return ""
	}
	const prefix = "Bearer "
	if len(auth) > len(prefix) && strings.EqualFold(auth[:len(prefix)], prefix) {
		return strings.TrimSpace(auth[len(prefix):])
	}
	return ""
}

// filterToolsByKey filters the tool list based on per-key allowed/denied tools in context.
func filterToolsByKey(ctx context.Context, tools []Tool) []Tool {
	allowed, _ := ctx.Value(ctxKeyAllowedTools).([]string)
	denied, _ := ctx.Value(ctxKeyDeniedTools).([]string)

	if len(allowed) == 0 && len(denied) == 0 {
		return tools
	}

	filtered := make([]Tool, 0, len(tools))
	for _, tool := range tools {
		if len(allowed) > 0 && !stringInSlice(tool.Name, allowed) {
			continue
		}
		if len(denied) > 0 && stringInSlice(tool.Name, denied) {
			continue
		}
		filtered = append(filtered, tool)
	}
	return filtered
}

// checkToolKeyAccess checks if a tool is accessible for the authenticated key.
// Returns an error message if blocked, or "" if allowed.
func checkToolKeyAccess(ctx context.Context, toolName string) string {
	allowed, _ := ctx.Value(ctxKeyAllowedTools).([]string)
	denied, _ := ctx.Value(ctxKeyDeniedTools).([]string)

	if len(allowed) > 0 && !stringInSlice(toolName, allowed) {
		return fmt.Sprintf("tool %q not in allowed list for this API key", toolName)
	}
	if len(denied) > 0 && stringInSlice(toolName, denied) {
		return fmt.Sprintf("tool %q is denied for this API key", toolName)
	}
	return ""
}

// stringInSlice checks if a string is present in a slice.
func stringInSlice(s string, list []string) bool {
	for _, item := range list {
		if item == s {
			return true
		}
	}
	return false
}

// validateArgsAgainstSchema validates tool call arguments against a JSON Schema.
// Checks required fields and basic type validation.
func validateArgsAgainstSchema(schemaRaw json.RawMessage, args map[string]interface{}) string {
	var schema struct {
		Type       string                            `json:"type"`
		Required   []string                          `json:"required"`
		Properties map[string]map[string]interface{} `json:"properties"`
	}
	if err := json.Unmarshal(schemaRaw, &schema); err != nil {
		return "" // Can't parse schema, skip validation
	}

	// Check required fields.
	for _, req := range schema.Required {
		if _, ok := args[req]; !ok {
			return fmt.Sprintf("missing required argument: %q", req)
		}
	}

	// Check types of provided arguments.
	for name, val := range args {
		prop, ok := schema.Properties[name]
		if !ok {
			continue // Extra args are allowed
		}
		expectedType, _ := prop["type"].(string)
		if expectedType == "" {
			continue
		}
		if !matchesJSONType(val, expectedType) {
			return fmt.Sprintf("argument %q: expected type %q", name, expectedType)
		}
	}

	return ""
}

// matchesJSONType checks if a Go value matches a JSON Schema type.
func matchesJSONType(val interface{}, jsonType string) bool {
	switch jsonType {
	case "string":
		_, ok := val.(string)
		return ok
	case "number":
		switch val.(type) {
		case float64, int, int64, float32:
			return true
		}
		return false
	case "integer":
		switch v := val.(type) {
		case float64:
			return v == float64(int64(v))
		case int, int64:
			return true
		}
		return false
	case "boolean":
		_, ok := val.(bool)
		return ok
	case "array":
		_, ok := val.([]interface{})
		return ok
	case "object":
		_, ok := val.(map[string]interface{})
		return ok
	case "null":
		return val == nil
	}
	return true // Unknown type, allow
}
