// Package mcpsec implements a guardrail that enforces MCP (Model Context Protocol)
// security policies. It validates MCP tool calls against server allowlists,
// blocks specific tools, validates inputs for injection patterns, and enforces
// rate limits on MCP tool calls per request.
package mcpsec

import (
	"context"
	"fmt"
	"log/slog"
	"regexp"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

// Common injection patterns to detect in MCP tool arguments.
var injectionPatterns = []*regexp.Regexp{
	regexp.MustCompile(`(?i)\bexec\s*\(`),
	regexp.MustCompile(`(?i)\beval\s*\(`),
	regexp.MustCompile(`(?i)\bsystem\s*\(`),
	regexp.MustCompile(`;\s*rm\s+`),
	regexp.MustCompile(`\|\s*sh\b`),
	regexp.MustCompile(`(?i)\bdrop\s+table\b`),
	regexp.MustCompile(`(?i)\bdelete\s+from\b`),
	regexp.MustCompile(`<script[^>]*>`),
}

// toolRateCounter tracks per-tool call counts in time buckets for rate limiting.
type toolRateCounter struct {
	count  atomic.Int64
	bucket atomic.Int64 // unix minute
}

// MCPSecGuardrail enforces MCP security policies.
type MCPSecGuardrail struct {
	name               string
	allowedServers     []string
	blockedTools       []string
	validateInputs     bool
	validateOutputs    bool
	maxCallsPerRequest int
	customPatterns     []*regexp.Regexp
	toolRateLimits     map[string]int // tool name → max calls per minute
	toolRateCounters   sync.Map       // tool name → *toolRateCounter
}

// New creates a MCPSecGuardrail from config.
func New(name string, cfg map[string]interface{}) *MCPSecGuardrail {
	g := &MCPSecGuardrail{
		name:               name,
		validateInputs:     true,
		validateOutputs:    true,
		maxCallsPerRequest: 10,
	}

	// Parse allowed_servers from comma-separated string or string slice.
	g.allowedServers = parseStringList(cfg, "allowed_servers")
	g.blockedTools = parseStringList(cfg, "blocked_tools")

	if v, ok := cfg["validate_inputs"]; ok {
		switch b := v.(type) {
		case bool:
			g.validateInputs = b
		case string:
			g.validateInputs = b == "true"
		}
	}
	if v, ok := cfg["validate_outputs"]; ok {
		switch b := v.(type) {
		case bool:
			g.validateOutputs = b
		case string:
			g.validateOutputs = b == "true"
		}
	}
	if v, ok := cfg["max_calls_per_request"]; ok {
		switch n := v.(type) {
		case float64:
			g.maxCallsPerRequest = int(n)
		case int:
			g.maxCallsPerRequest = n
		}
	}

	// Parse custom injection patterns.
	for _, raw := range parseStringList(cfg, "custom_patterns") {
		re, err := regexp.Compile(raw)
		if err != nil {
			slog.Warn("mcpsec: invalid custom pattern, skipping", "pattern", raw, "error", err)
			continue
		}
		g.customPatterns = append(g.customPatterns, re)
	}

	// Parse per-tool rate limits (map of tool name → max calls/min).
	if v, ok := cfg["tool_rate_limits"]; ok {
		if m, ok := v.(map[string]interface{}); ok {
			g.toolRateLimits = make(map[string]int, len(m))
			for tool, limit := range m {
				switch n := limit.(type) {
				case float64:
					g.toolRateLimits[tool] = int(n)
				case int:
					g.toolRateLimits[tool] = n
				}
			}
		}
	}

	return g
}

func (g *MCPSecGuardrail) Name() string           { return g.name }
func (g *MCPSecGuardrail) Stage() guardrails.Stage { return guardrails.StagePre }

// IsMCPSecConfig returns true if the config specifies an MCP security guardrail.
func IsMCPSecConfig(cfg map[string]interface{}) bool {
	if cfg == nil {
		return false
	}
	provider, _ := cfg["provider"].(string)
	return provider == "mcp_security"
}

// Check evaluates MCP tool calls against security policies.
func (g *MCPSecGuardrail) Check(ctx context.Context, input *guardrails.CheckInput) *guardrails.CheckResult {
	if input == nil {
		return &guardrails.CheckResult{Pass: true}
	}

	var violations []string

	// Check request tools and tool calls.
	if input.Request != nil {
		// Check tool definitions in the request.
		for _, tool := range input.Request.Tools {
			name := tool.Function.Name
			if g.isBlockedTool(name) {
				violations = append(violations, fmt.Sprintf("blocked tool: %s", name))
			}
		}

		// Check tool calls in messages (for multi-turn with tool results).
		callCount := 0
		for _, msg := range input.Request.Messages {
			for _, tc := range msg.ToolCalls {
				callCount++
				if g.isBlockedTool(tc.Function.Name) {
					violations = append(violations, fmt.Sprintf("blocked tool call: %s", tc.Function.Name))
				}
				if g.validateInputs && containsInjection(tc.Function.Arguments) {
					violations = append(violations, fmt.Sprintf("injection detected in tool args: %s", tc.Function.Name))
				}
				if g.validateInputs && g.containsCustomInjection(tc.Function.Arguments) {
					violations = append(violations, fmt.Sprintf("custom injection pattern in tool args: %s", tc.Function.Name))
				}
				if !g.checkToolRateLimit(tc.Function.Name) {
					violations = append(violations, fmt.Sprintf("rate limit exceeded for tool: %s", tc.Function.Name))
				}
			}
		}

		// Check max calls per request.
		if g.maxCallsPerRequest > 0 && callCount > g.maxCallsPerRequest {
			violations = append(violations, fmt.Sprintf("too many tool calls: %d > %d", callCount, g.maxCallsPerRequest))
		}
	}

	// Check response tool calls.
	if input.Response != nil {
		for _, choice := range input.Response.Choices {
			for _, tc := range choice.Message.ToolCalls {
				if g.isBlockedTool(tc.Function.Name) {
					violations = append(violations, fmt.Sprintf("blocked response tool call: %s", tc.Function.Name))
				}
				if g.validateOutputs && containsInjection(tc.Function.Arguments) {
					violations = append(violations, fmt.Sprintf("injection in response tool args: %s", tc.Function.Name))
				}
				if g.validateOutputs && g.containsCustomInjection(tc.Function.Arguments) {
					violations = append(violations, fmt.Sprintf("custom injection in response tool args: %s", tc.Function.Name))
				}
			}
		}
	}

	if len(violations) == 0 {
		return &guardrails.CheckResult{Pass: true, Score: 0.0, Message: "MCP security check passed"}
	}

	return &guardrails.CheckResult{
		Pass:    false,
		Score:   1.0,
		Message: fmt.Sprintf("MCP security violations: %s", strings.Join(violations, "; ")),
		Details: map[string]interface{}{
			"violations": violations,
		},
	}
}

// isBlockedTool checks if a tool name is in the blocked list.
func (g *MCPSecGuardrail) isBlockedTool(name string) bool {
	for _, blocked := range g.blockedTools {
		if blocked == name {
			return true
		}
	}
	return false
}

// containsInjection checks if a string contains built-in injection patterns.
func containsInjection(s string) bool {
	for _, pattern := range injectionPatterns {
		if pattern.MatchString(s) {
			return true
		}
	}
	return false
}

// containsCustomInjection checks custom user-defined patterns.
func (g *MCPSecGuardrail) containsCustomInjection(s string) bool {
	for _, pattern := range g.customPatterns {
		if pattern.MatchString(s) {
			return true
		}
	}
	return false
}

// checkToolRateLimit checks if a tool has exceeded its per-minute rate limit.
// Returns true if the call should be allowed.
func (g *MCPSecGuardrail) checkToolRateLimit(toolName string) bool {
	if g.toolRateLimits == nil {
		return true
	}
	limit, ok := g.toolRateLimits[toolName]
	if !ok || limit <= 0 {
		return true
	}

	currentMinute := time.Now().Unix() / 60

	val, _ := g.toolRateCounters.LoadOrStore(toolName, &toolRateCounter{})
	counter := val.(*toolRateCounter)

	// If we've moved to a new minute, reset the counter.
	if counter.bucket.Load() != currentMinute {
		counter.count.Store(1)
		counter.bucket.Store(currentMinute)
		return true
	}

	newCount := counter.count.Add(1)
	return newCount <= int64(limit)
}

// parseStringList extracts a string list from config (comma-separated string or string slice).
func parseStringList(cfg map[string]interface{}, key string) []string {
	if v, ok := cfg[key].(string); ok && v != "" {
		var result []string
		for _, p := range strings.Split(v, ",") {
			if t := strings.TrimSpace(p); t != "" {
				result = append(result, t)
			}
		}
		return result
	}
	if arr, ok := cfg[key].([]interface{}); ok {
		var result []string
		for _, item := range arr {
			if s, ok := item.(string); ok && s != "" {
				result = append(result, s)
			}
		}
		return result
	}
	return nil
}
