// Package servertools knows the tools a model provider runs itself, on top
// of tokens: Anthropic web search, web fetch and code execution,
// OpenAI's hosted web search, file search and containers, and Gemini
// grounding. It holds three things the cost plugin and the eligibility
// engine share, and nothing that touches a request in flight:
//
//   - tool-version detection: which provider-run tool, at which version, a
//     request carries (ParseTool, Detect);
//   - the route support table: which endpoint source can run which tool
//     version (Support, Runs, SourceOf);
//   - the fee table loader: what each tool costs per unit, read from
//     catalog/servertools/fees.json (ParseFees, Fees).
package servertools

import "github.com/futureagi/agentcc-gateway/internal/models"

// Tool is a provider-run tool family, whatever its version.
type Tool string

// The Anthropic server tools a request names by type, "<tool>_<yyyymmdd>".
const (
	WebSearch     Tool = "web_search"
	WebFetch      Tool = "web_fetch"
	CodeExecution Tool = "code_execution"
)

// anthropicTools are the families ParseTool recognises, in the order it
// tries them.
var anthropicTools = [...]Tool{WebSearch, WebFetch, CodeExecution}

// versionLen is the length of a server tool's version: a date, yyyymmdd.
const versionLen = 8

// ParseTool splits an Anthropic server tool type ("web_search_20260209")
// into its family and its version ("20260209"). ok is false for anything
// else: a function tool, a client tool such as bash_20250124, or OpenAI's
// "web_search_preview", whose suffix is no date. It allocates nothing.
func ParseTool(typ string) (tool Tool, version string, ok bool) {
	for _, t := range anthropicTools {
		prefix := len(t) + 1
		if len(typ) != prefix+versionLen || typ[:len(t)] != string(t) || typ[len(t)] != '_' {
			continue
		}
		v := typ[prefix:]
		if !isDigits(v) {
			return "", "", false
		}
		return t, v, true
	}
	return "", "", false
}

func isDigits(s string) bool {
	for i := 0; i < len(s); i++ {
		if s[i] < '0' || s[i] > '9' {
			return false
		}
	}
	return s != ""
}

// Requested is what one request carries of each server tool family: the
// latest version of each it names, "" for none. Versions are dates, so the
// latest is the greatest string.
type Requested struct {
	WebSearch, WebFetch, CodeExecution string
}

// Detect reads the server tools in req's tools array. A nil request or one
// with no server tools is the zero Requested. It allocates nothing: the
// versions are substrings of the request's own tool types.
func Detect(req *models.ChatCompletionRequest) Requested {
	var r Requested
	if req == nil {
		return r
	}
	for i := range req.Tools {
		tool, v, ok := ParseTool(req.Tools[i].Type)
		if !ok {
			continue
		}
		switch tool {
		case WebSearch:
			r.WebSearch = later(r.WebSearch, v)
		case WebFetch:
			r.WebFetch = later(r.WebFetch, v)
		case CodeExecution:
			r.CodeExecution = later(r.CodeExecution, v)
		}
	}
	return r
}

// Any reports whether the request names any server tool.
func (r Requested) Any() bool {
	return r.WebSearch != "" || r.WebFetch != "" || r.CodeExecution != ""
}

// Version is the latest version of tool the request names, "" for none.
func (r Requested) Version(tool Tool) string {
	switch tool {
	case WebSearch:
		return r.WebSearch
	case WebFetch:
		return r.WebFetch
	case CodeExecution:
		return r.CodeExecution
	}
	return ""
}

func later(a, b string) string {
	if b > a {
		return b
	}
	return a
}
