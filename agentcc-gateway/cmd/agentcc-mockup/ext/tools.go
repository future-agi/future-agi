package ext

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"strconv"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-mockup/mock"
)

// maxStreamTools bounds stream_tools:<n>.
const maxStreamTools = 64

// streamTools handles stream_tools:<n>: the answer is n tool calls and no
// text. Streamed, each call goes out as its head and then its argument
// deltas, in every route's stream shape; unstreamed, the body lists them.
// Call i (from 1) has id call_mock_<i>, calls the request's tools in turn
// (get_weather when it offers none), and has arguments {"query":"mock-<i>"}
// unless a later tool_args sets them.
func streamTools(arg string, req *mock.Request, resp *mock.Response) error {
	n, err := strconv.Atoi(arg)
	if err != nil || n < 1 || n > maxStreamTools {
		return invalidDirective("stream_tools:"+arg, fmt.Sprintf("want a tool-call count from 1 to %d", maxStreamTools))
	}
	names := offeredToolNames(req)
	calls := make([]mock.ToolCall, 0, n)
	for i := 1; i <= n; i++ {
		calls = append(calls, mock.ToolCall{
			ID:        fmt.Sprintf("call_mock_%d", i),
			Name:      names[(i-1)%len(names)],
			Arguments: fmt.Sprintf(`{"query":"mock-%d"}`, i),
		})
	}
	resp.Content, resp.ToolCalls = "", calls
	return nil
}

// toolArgs handles tool_args:<base64 json>: every tool call in the answer
// gets the decoded JSON as its arguments, verbatim. It applies to the calls
// the directives before it made (tools, stream_tools, or a mock/tools model).
// Standard and URL-safe base64 are accepted, padded or not.
func toolArgs(arg string, resp *mock.Response) error {
	tok := "tool_args:" + arg
	raw, err := decodeBase64(arg)
	if err != nil {
		return invalidDirective(tok, "want base64 of a JSON value")
	}
	raw = bytes.TrimSpace(raw)
	if !json.Valid(raw) {
		return invalidDirective(tok, fmt.Sprintf("the decoded arguments are not JSON: %q", raw))
	}
	if len(resp.ToolCalls) == 0 {
		return invalidDirective(tok, "the answer has no tool calls; put tools or stream_tools:<n> before it")
	}
	for i := range resp.ToolCalls {
		resp.ToolCalls[i].Arguments = string(raw)
	}
	return nil
}

func decodeBase64(s string) ([]byte, error) {
	var err error
	for _, enc := range []*base64.Encoding{base64.StdEncoding, base64.RawStdEncoding, base64.URLEncoding, base64.RawURLEncoding} {
		var b []byte
		if b, err = enc.DecodeString(s); err == nil {
			return b, nil
		}
	}
	return nil, err
}

// offeredToolNames are the names of the tools the request offers, in order,
// read in the chat ({function: {name}}), Responses and Anthropic ({name})
// shapes; ["get_weather"] when it offers none.
func offeredToolNames(req *mock.Request) []string {
	tools, _ := req.JSON["tools"].([]any)
	var names []string
	for _, t := range tools {
		tool, _ := t.(map[string]any)
		fn, _ := tool["function"].(map[string]any)
		name, _ := fn["name"].(string)
		if name == "" {
			name, _ = tool["name"].(string)
		}
		if name != "" {
			names = append(names, name)
		}
	}
	if len(names) == 0 {
		return []string{"get_weather"}
	}
	return names
}
