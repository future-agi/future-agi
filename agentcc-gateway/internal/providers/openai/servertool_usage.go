package openai

import (
	"bytes"
	"encoding/json"
	"strconv"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// responsesToolUse is the part of a Responses API body that says which
// OpenAI-run tools the response used: the tools the request declared (the
// response echoes them) and the output items they produced.
type responsesToolUse struct {
	Tools  []responsesTool `json:"tools"`
	Output []struct {
		Type        string `json:"type"`
		ContainerID string `json:"container_id"`
	} `json:"output"`
}

// serverToolMarkers are the bytes a body holds when an OpenAI-run tool ran:
// the end of a web or file search call item's type, and a container id.
// Function calls ("function_call") carry none of them.
var serverToolMarkers = [...][]byte{[]byte(`web_search_call"`), []byte(`file_search_call"`), []byte(`"container_id"`)}

// mayUseServerTools reports whether body names an item ResponsesServerToolUsage
// counts, so that a body with none (a plain message, a function-calling
// turn) is not decoded a second time.
func mayUseServerTools(body []byte) bool {
	for _, m := range serverToolMarkers {
		if bytes.Contains(body, m) {
			return true
		}
	}
	return false
}

// responsesTool is one tool a Responses body declares.
type responsesTool struct {
	Type string `json:"type"`
	// Container is the code interpreter's container: an id string, or
	// {"type":"auto","memory_limit":"4g"}. Environment is the hosted
	// shell's, in the same shape.
	Container   json.RawMessage `json:"container"`
	Environment json.RawMessage `json:"environment"`
}

// ResponsesServerToolUsage counts the provider-run tools a Responses API
// body reports: each web_search_call item (as a web search
// preview call when the response's tools declare only web_search_preview,
// which OpenAI prices apart), each file_search_call item, and each
// container a call ran in (code interpreter, hosted shell), once per
// container id, at the memory_limit its tool declared. A container's
// duration is not in the body, so it is left 0 (billed at the minimum, as
// an estimate). nil when the body is not JSON or used none.
func ResponsesServerToolUsage(body []byte) *models.ServerToolUsage {
	if !mayUseServerTools(body) {
		return nil
	}
	var r responsesToolUse
	if json.Unmarshal(body, &r) != nil {
		return nil
	}
	var u models.ServerToolUsage
	preview, size := previewOnly(r.Tools), containerSize(r.Tools)
	seen := map[string]bool{}
	for _, item := range r.Output {
		switch item.Type {
		case "web_search_call":
			if preview {
				u.WebSearchPreviewRequests++
			} else {
				u.WebSearchRequests++
			}
		case "file_search_call":
			u.FileSearchRequests++
		}
		if id := item.ContainerID; id != "" && !seen[id] {
			seen[id] = true
			u.Containers = append(u.Containers, models.ServerToolContainer{ID: id, MemoryGB: size})
		}
	}
	if u.WebSearchRequests+u.WebSearchPreviewRequests+u.FileSearchRequests == 0 && len(u.Containers) == 0 {
		return nil
	}
	return &u
}

// previewOnly reports whether the response's searches ran on the preview
// tool: it declares web_search_preview and not the generally available
// web_search.
func previewOnly(tools []responsesTool) bool {
	preview := false
	for _, t := range tools {
		switch {
		case strings.HasPrefix(t.Type, "web_search_preview"):
			preview = true
		case strings.HasPrefix(t.Type, "web_search"):
			return false
		}
	}
	return preview
}

// containerSize is the memory_limit, in GB, of the container the response's
// tools declare ("4g" is 4), 0 when none says (an existing container named
// by id, or the default).
func containerSize(tools []responsesTool) int {
	for _, t := range tools {
		for _, raw := range []json.RawMessage{t.Container, t.Environment} {
			var c struct {
				MemoryLimit string `json:"memory_limit"`
			}
			if len(raw) == 0 || raw[0] != '{' || json.Unmarshal(raw, &c) != nil {
				continue
			}
			if gb, err := strconv.Atoi(strings.TrimSuffix(strings.ToLower(c.MemoryLimit), "g")); err == nil && gb > 0 {
				return gb
			}
		}
	}
	return 0
}
