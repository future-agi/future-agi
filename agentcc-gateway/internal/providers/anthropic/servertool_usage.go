package anthropic

import (
	"bytes"
	"encoding/json"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// serverToolUseMarker is in every Messages body whose usage counts a server
// tool, so a body without it is not decoded a second time.
var serverToolUseMarker = []byte(`"server_tool_use"`)

// ServerToolUsage reads a native Messages body's usage.server_tool_use
// counters: web_search_requests and
// web_fetch_requests, the two the Claude API reference documents. Code
// execution has no documented counter, and this reader does not count it.
// nil when the body counts none.
func ServerToolUsage(body []byte) *models.ServerToolUsage {
	if !bytes.Contains(body, serverToolUseMarker) {
		return nil
	}
	var r struct {
		Usage struct {
			ServerToolUse struct {
				WebSearchRequests int `json:"web_search_requests"`
				WebFetchRequests  int `json:"web_fetch_requests"`
			} `json:"server_tool_use"`
		} `json:"usage"`
	}
	if json.Unmarshal(body, &r) != nil {
		return nil
	}
	u := r.Usage.ServerToolUse
	if u.WebSearchRequests <= 0 && u.WebFetchRequests <= 0 {
		return nil
	}
	return &models.ServerToolUsage{WebSearchRequests: max(u.WebSearchRequests, 0), WebFetchRequests: max(u.WebFetchRequests, 0)}
}
