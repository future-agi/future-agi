package mcp

import (
	"context"
	"fmt"
	"time"
)

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
