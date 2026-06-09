package mcpsec

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"regexp"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// toolGuardRateCounter tracks per-tool call counts in time buckets.
type toolGuardRateCounter struct {
	count  atomic.Int64
	bucket atomic.Int64 // unix minute
}

// ToolGuard implements mcp.ToolCallGuard using the MCPSec policy.
// It checks blocked tools, allowed servers, injection patterns,
// custom injection patterns, and per-tool rate limits
// directly on MCP tool call requests and results.
type ToolGuard struct {
	blockedTools     []string
	allowedServers   []string
	validateInputs   bool
	validateOutputs  bool
	customPatterns   []*regexp.Regexp
	toolRateLimits   map[string]int // tool name → max calls per minute
	toolRateCounters sync.Map       // tool name → *toolGuardRateCounter
}

// ToolGuardConfig holds typed configuration for the ToolGuard.
type ToolGuardConfig struct {
	BlockedTools    []string
	AllowedServers  []string
	ValidateInputs  bool
	ValidateOutputs bool
}

// NewToolGuard creates a ToolGuard from typed config.
func NewToolGuard(cfg map[string]interface{}) *ToolGuard {
	g := &ToolGuard{
		validateInputs:  true,
		validateOutputs: true,
	}

	// Handle blocked_tools — can be []string or []interface{}.
	g.blockedTools = extractStringSlice(cfg, "blocked_tools")
	g.allowedServers = extractStringSlice(cfg, "allowed_servers")

	if v, ok := cfg["validate_inputs"]; ok {
		if b, ok := v.(bool); ok {
			g.validateInputs = b
		}
	}
	if v, ok := cfg["validate_outputs"]; ok {
		if b, ok := v.(bool); ok {
			g.validateOutputs = b
		}
	}

	// Parse custom injection patterns.
	for _, raw := range extractStringSlice(cfg, "custom_patterns") {
		re, err := regexp.Compile(raw)
		if err != nil {
			slog.Warn("mcpsec toolguard: invalid custom pattern, skipping", "pattern", raw, "error", err)
			continue
		}
		g.customPatterns = append(g.customPatterns, re)
	}

	// Parse per-tool rate limits.
	if v, ok := cfg["tool_rate_limits"]; ok {
		switch m := v.(type) {
		case map[string]interface{}:
			g.toolRateLimits = make(map[string]int, len(m))
			for tool, limit := range m {
				switch n := limit.(type) {
				case float64:
					g.toolRateLimits[tool] = int(n)
				case int:
					g.toolRateLimits[tool] = n
				}
			}
		case map[string]int:
			g.toolRateLimits = m
		}
	}

	return g
}

// extractStringSlice handles both []string and []interface{} from config maps.
func extractStringSlice(cfg map[string]interface{}, key string) []string {
	v, ok := cfg[key]
	if !ok || v == nil {
		return nil
	}
	// Direct []string (from typed config).
	if ss, ok := v.([]string); ok {
		return ss
	}
	// []interface{} (from JSON/YAML config).
	if arr, ok := v.([]interface{}); ok {
		result := make([]string, 0, len(arr))
		for _, item := range arr {
			if s, ok := item.(string); ok && s != "" {
				result = append(result, s)
			}
		}
		return result
	}
	// Comma-separated string.
	if s, ok := v.(string); ok && s != "" {
		var result []string
		for _, p := range strings.Split(s, ",") {
			if t := strings.TrimSpace(p); t != "" {
				result = append(result, t)
			}
		}
		return result
	}
	return nil
}

// CheckPre validates a tool call before forwarding to upstream.
func (g *ToolGuard) CheckPre(toolName, serverID string, arguments map[string]interface{}) string {
	// Check allowed servers.
	if len(g.allowedServers) > 0 && !g.isAllowedServer(serverID) {
		return fmt.Sprintf("server %q not in allowed list", serverID)
	}

	// Check blocked tools (match both namespaced and original name).
	if g.isBlockedTool(toolName) {
		return fmt.Sprintf("tool %q is blocked", toolName)
	}

	// Check per-tool rate limit.
	if !g.checkToolRateLimit(toolName) {
		return fmt.Sprintf("rate limit exceeded for tool %q", toolName)
	}

	// Check injection in arguments.
	if g.validateInputs && len(arguments) > 0 {
		argsJSON, _ := json.Marshal(arguments)
		s := string(argsJSON)
		if containsInjection(s) {
			return fmt.Sprintf("injection pattern detected in arguments for %q", toolName)
		}
		if g.containsCustomInjection(s) {
			return fmt.Sprintf("custom injection pattern detected in arguments for %q", toolName)
		}
	}

	return ""
}

// CheckPost validates a tool call result after receiving from upstream.
func (g *ToolGuard) CheckPost(toolName, serverID string, result *mcp.ToolCallResult) string {
	if !g.validateOutputs || result == nil {
		return ""
	}

	// Check content parts for injection patterns.
	for _, part := range result.Content {
		if part.Type == "text" {
			if containsInjection(part.Text) {
				return fmt.Sprintf("injection pattern detected in output of %q", toolName)
			}
			if g.containsCustomInjection(part.Text) {
				return fmt.Sprintf("custom injection pattern detected in output of %q", toolName)
			}
		}
	}

	return ""
}

// containsCustomInjection checks custom user-defined patterns.
func (g *ToolGuard) containsCustomInjection(s string) bool {
	for _, pattern := range g.customPatterns {
		if pattern.MatchString(s) {
			return true
		}
	}
	return false
}

// checkToolRateLimit checks if a tool has exceeded its per-minute rate limit.
func (g *ToolGuard) checkToolRateLimit(toolName string) bool {
	if g.toolRateLimits == nil {
		return true
	}
	limit, ok := g.toolRateLimits[toolName]
	if !ok || limit <= 0 {
		return true
	}

	currentMinute := time.Now().Unix() / 60

	val, _ := g.toolRateCounters.LoadOrStore(toolName, &toolGuardRateCounter{})
	counter := val.(*toolGuardRateCounter)

	// If we've moved to a new minute, reset the counter.
	if counter.bucket.Load() != currentMinute {
		counter.count.Store(1)
		counter.bucket.Store(currentMinute)
		return true
	}

	newCount := counter.count.Add(1)
	return newCount <= int64(limit)
}

func (g *ToolGuard) isBlockedTool(name string) bool {
	for _, blocked := range g.blockedTools {
		if blocked == name {
			return true
		}
		// Also match without namespace prefix (e.g., "exec_command" matches "server_exec_command").
		if strings.HasSuffix(name, "_"+blocked) {
			return true
		}
	}
	return false
}

func (g *ToolGuard) isAllowedServer(serverID string) bool {
	for _, allowed := range g.allowedServers {
		if allowed == serverID {
			return true
		}
	}
	return false
}
