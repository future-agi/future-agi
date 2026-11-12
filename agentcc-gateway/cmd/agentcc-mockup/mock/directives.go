package mock

import (
	"errors"
	"fmt"
	"net/http"
	"strconv"
	"strings"
)

// BehaviorHeader names the per-request directives: comma-separated tokens of
// the form name[:arg], applied left to right.
const BehaviorHeader = "x-mock-behavior"

// DirectiveFunc handles a directive the mock does not know. It may edit resp
// (or set resp.Status to refuse the call) and returns an error for a
// directive it does not know either.
type DirectiveFunc func(name, arg string, req *Request, resp *Response) error

// DirectiveError refuses a call whose directives cannot be applied; the mock
// answers it with 400 and Code.
type DirectiveError struct {
	Code    string
	Message string
}

func (e *DirectiveError) Error() string { return e.Message }

// UnknownDirective is the error for a directive nobody handles.
func UnknownDirective(name string) error {
	return &DirectiveError{Code: "unknown_mock_directive", Message: fmt.Sprintf("unknown %s directive %q", BehaviorHeader, name)}
}

func invalidDirective(tok, why string) error {
	return &DirectiveError{Code: "invalid_mock_directive", Message: fmt.Sprintf("%s directive %q: %s", BehaviorHeader, tok, why)}
}

var errInvalidJSON = errors.New("body is not valid JSON")

// splitDirectives splits a header value into its trimmed, non-empty tokens.
func splitDirectives(v string) []string {
	var out []string
	for _, tok := range strings.Split(v, ",") {
		if tok = strings.TrimSpace(tok); tok != "" {
			out = append(out, tok)
		}
	}
	return out
}

// behaviorFor is the directive list for req: its model-name shortcut, then
// its own header or, for an inference call without one, the armed behaviour.
func (s *Server) behaviorFor(req *Request) []string {
	toks := modelShortcut(req.Model)
	if v := req.HTTP.Header.Get(BehaviorHeader); v != "" {
		return append(toks, splitDirectives(v)...)
	}
	if a := s.state.Armed(); a != nil && (a.Model == "" || a.Model == req.Model) {
		toks = append(toks, splitDirectives(a.Behavior)...)
	}
	return toks
}

// modelShortcut maps the mock/* model names to the directives they stand for.
// The provider prefix a gateway may leave on the name is ignored.
func modelShortcut(model string) []string {
	i := strings.LastIndex("/"+model, "/mock/")
	if i < 0 {
		return nil
	}
	name := model[i+len("mock/"):]
	switch {
	case strings.HasPrefix(name, "fail-"):
		return []string{"fail:" + strings.TrimPrefix(name, "fail-")}
	case strings.HasPrefix(name, "slow-"):
		return []string{"latency:" + strings.TrimPrefix(name, "slow-")}
	case name == "tools":
		return []string{"tools"}
	case name == "empty":
		return []string{"empty"}
	case name == "filter":
		return []string{"empty", "finish:content_filter"}
	}
	return nil // mock/ok-* and anything else succeed
}

// applyDirectives edits resp by each token in turn. Names the mock does not
// know go to hook; without one they are refused.
func (s *Server) applyDirectives(toks []string, req *Request, resp *Response) error {
	for _, tok := range toks {
		name, arg, _ := strings.Cut(tok, ":")
		if err := s.applyBuiltin(tok, name, arg, req, resp); err != errNotBuiltin {
			if err != nil {
				return err
			}
			continue
		}
		if s.hook == nil {
			return UnknownDirective(name)
		}
		if err := s.hook(name, arg, req, resp); err != nil {
			return err
		}
	}
	resp.Usage.normalize()
	return nil
}

var errNotBuiltin = errors.New("not a built-in directive")

func (s *Server) applyBuiltin(tok, name, arg string, req *Request, resp *Response) error {
	switch name {
	case "fail":
		return s.applyFail(tok, arg, resp)
	case "latency":
		return setInt(tok, arg, &resp.LatencyMs)
	case "chunk_delay":
		return setInt(tok, arg, &resp.ChunkDelayMs)
	case "stream_abort_after":
		return setInt(tok, arg, &resp.AbortAfter)
	case "no_usage":
		resp.NoUsage = true
	case "usage_cost":
		f, err := strconv.ParseFloat(arg, 64)
		if err != nil || f < 0 {
			return invalidDirective(tok, "want a non-negative USD amount")
		}
		resp.CostUSD, resp.ReportCost = &f, true
	case "cached_tokens", "cache_read":
		return setInt(tok, arg, &resp.Usage.CachedTokens)
	case "cache_write_tokens", "cache_creation":
		return setInt(tok, arg, &resp.Usage.CacheWriteTokens)
	case "reasoning_tokens":
		return setInt(tok, arg, &resp.Usage.ReasoningTokens)
	case "finish":
		if arg == "" {
			return invalidDirective(tok, "want a finish reason")
		}
		resp.FinishReason = arg
	case "empty":
		resp.Content, resp.ToolCalls, resp.Usage.Completion = "", nil, 0
	case "tools":
		resp.Content, resp.ToolCalls = "", defaultToolCalls(req)
	case "served":
		if arg == "" {
			return invalidDirective(tok, "want a provider slug")
		}
		resp.ServedProvider = arg
	default:
		return errNotBuiltin
	}
	return nil
}

// applyFail handles fail:<status>[:n=<k>]. With n, only the first k calls
// carrying this exact token fail; later ones succeed. Counts restart on
// /__reset and when the token is armed again.
func (s *Server) applyFail(tok, arg string, resp *Response) error {
	statusText, opt, hasOpt := strings.Cut(arg, ":")
	status, err := strconv.Atoi(statusText)
	if err != nil || status < 400 || status > 599 {
		return invalidDirective(tok, "want fail:<4xx|5xx>[:n=<k>]")
	}
	if hasOpt {
		nText, ok := strings.CutPrefix(opt, "n=")
		n, err := strconv.Atoi(nText)
		if !ok || err != nil || n < 1 {
			return invalidDirective(tok, "want n=<k> with k >= 1")
		}
		if s.state.countUse(tok) > n {
			return nil
		}
	}
	resp.Status = status
	resp.ErrorCode = strconv.Itoa(status)
	resp.ErrorMessage = fmt.Sprintf("mock upstream failure (%s)", tok)
	return nil
}

func setInt(tok, arg string, dst *int) error {
	n, err := strconv.Atoi(arg)
	if err != nil || n < 0 {
		return invalidDirective(tok, "want a non-negative integer")
	}
	*dst = n
	return nil
}

// defaultToolCalls is one call to the first tool the request offers.
func defaultToolCalls(req *Request) []ToolCall {
	name := "get_weather"
	if tools, ok := req.JSON["tools"].([]any); ok && len(tools) > 0 {
		if t, ok := tools[0].(map[string]any); ok {
			if n := toolName(t); n != "" {
				name = n
			}
		}
	}
	return []ToolCall{{ID: "call_mock_1", Name: name, Arguments: `{"query":"mock"}`}}
}

// toolName reads a tool's name in the chat ({function: {name}}), Responses
// and Anthropic ({name}) shapes.
func toolName(t map[string]any) string {
	if fn, ok := t["function"].(map[string]any); ok {
		if n, ok := fn["name"].(string); ok {
			return n
		}
	}
	n, _ := t["name"].(string)
	return n
}

// directiveErrorStatus is the status a directive error is answered with.
func directiveErrorStatus(err error) (int, string) {
	var de *DirectiveError
	if errors.As(err, &de) {
		return http.StatusBadRequest, de.Code
	}
	return http.StatusBadRequest, "invalid_mock_directive"
}
