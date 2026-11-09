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
	separator        string // the gateway's legacy name separator (mcp.separator)
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
	g.separator, _ = cfg["separator"].(string)
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
	if g.isBlockedTool(toolName, serverID) {
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

// isBlockedTool reports whether a call's exposed name is blocked (bug 22).
// An entry matches the exact exposed name, or the upstream tool the name
// stands for on the calling server: a bare tool name, or
// <server_id><separator><tool> with the configured separator (default "_").
// The exposed name stands for these upstream tools on server s: the rest of
// s<separator>tool (the legacy name), of s__tool (a virtual server's), of
// s_<account>__tool (an account member s@<account>, split at the first "__"
// after the label; lane M10 rejects a label that would make this split
// ambiguous), and the name itself (flat naming). Never a bare suffix: "exec"
// does not block "github_my_exec". Some exposed names cannot be read back to
// their upstream tool: a name an override chose, a name capped with a hash
// (over 64 characters), and a name normalization changed. They match
// exactly only here, so the scanner must also judge a call by the upstream
// tool it resolved to, with BlocksUpstreamTool (lane M10's IR-9).
func (g *ToolGuard) isBlockedTool(name, serverID string) bool {
	sep := g.sep()
	for _, blocked := range g.blockedTools {
		if blocked == name {
			return true
		}
	}
	if serverID == "" {
		return false
	}
	tools := []string{name} // the upstream names the exposed name can stand for
	for _, s := range []string{"__", sep} {
		if tool, ok := strings.CutPrefix(name, serverID+s); ok && tool != "" {
			tools = append(tools, tool)
		}
	}
	if rest, ok := strings.CutPrefix(name, serverID+"_"); ok && !strings.HasPrefix(rest, "_") {
		if _, tool, ok := strings.Cut(rest, "__"); ok && tool != "" {
			tools = append(tools, tool)
		}
	}
	for _, blocked := range g.blockedTools {
		for _, tool := range tools {
			if blocked == tool || blocked == serverID+sep+tool {
				return true
			}
		}
	}
	return false
}

// BlocksUpstreamTool reports whether blocked_tools blocks the upstream tool
// a call resolved to, tool on server serverID, whatever name the call
// exposed it under (an override's, an account member's, a hash-capped or a
// normalized one): an entry that is the bare tool name,
// <server_id><separator><tool> with the configured separator, or the
// virtual-server name <server_id>__<tool>. Never a bare suffix. It only
// checks: no rate-limit count, no other guard check. It is for the scanner,
// with cc.Upstream.ServerID and cc.Tool.UpstreamName: lane M10's IR-9 adds
// that call to hook_scan_args.go.
func (g *ToolGuard) BlocksUpstreamTool(serverID, tool string) bool {
	if serverID == "" || tool == "" {
		return false
	}
	for _, blocked := range g.blockedTools {
		if blocked == tool || blocked == serverID+g.sep()+tool || blocked == serverID+"__"+tool {
			return true
		}
	}
	return false
}

// sep is the configured legacy name separator, "_" by default.
func (g *ToolGuard) sep() string {
	if g.separator == "" {
		return "_"
	}
	return g.separator
}

func (g *ToolGuard) isAllowedServer(serverID string) bool {
	for _, allowed := range g.allowedServers {
		if allowed == serverID {
			return true
		}
	}
	return false
}
