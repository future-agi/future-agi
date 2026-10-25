package mcpmock

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"strings"
	"sync/atomic"
	"time"
)

// envNamesTool is the stdio-only tool that reports the names (never the
// values) of the environment variables the mock process sees.
const envNamesTool = "env_names"

var envNamesDef = json.RawMessage(`{"name":"env_names","description":"Names of the environment variables this stdio mock sees (never their values).","inputSchema":{"type":"object","properties":{}}}`)

// requestStateTTL bounds a 2026 input_required round trip.
const requestStateTTL = 5 * time.Minute

// listedTools is the tool set as listed: the fixtures (or the replaced
// set), plus env_names in stdio mode.
func (m *Mock) listedTools() []Tool {
	tools := m.state.Tools()
	if m.cfg.Stdio {
		tools = append(tools, Tool{Name: envNamesTool, Def: envNamesDef})
	}
	return tools
}

func toolDefs(tools []Tool) []json.RawMessage {
	defs := make([]json.RawMessage, 0, len(tools))
	for _, t := range tools {
		defs = append(defs, t.Def)
	}
	return defs
}

func (m *Mock) findTool(name string) (Tool, bool) {
	if m.cfg.Stdio && name == envNamesTool {
		return Tool{Name: envNamesTool, Def: envNamesDef}, true
	}
	return m.state.tool(name)
}

type toolCallParams struct {
	Name      string          `json:"name"`
	Arguments json.RawMessage `json:"arguments"`
	Meta      struct {
		ProgressToken json.RawMessage `json:"progressToken"`
	} `json:"_meta"`
	InputResponses map[string]json.RawMessage `json:"inputResponses"`
	RequestState   string                     `json:"requestState"`
}

// callTool answers tools/call. The cost-log entry is written first, at
// receipt; the transport marks it completed once the result is delivered.
func (m *Mock) callTool(ctx context.Context, c *call, msg *Message) (any, *RPCError) {
	var p toolCallParams
	if err := json.Unmarshal(msg.Params, &p); err != nil || p.Name == "" {
		return nil, rpcErr(0, CodeInvalidParams, "tools/call needs params with a name")
	}
	tool, ok := m.findTool(p.Name)
	if !ok {
		return nil, rpcErr(0, CodeInvalidParams, "unknown tool: %s", p.Name)
	}
	b := m.state.Behavior()
	price := tool.PriceUSD
	if b.CostUSD != nil {
		price = *b.CostUSD
	}
	firstRound := b.Elicit && c.stateless && len(p.InputResponses) == 0 && p.RequestState == ""
	c.charge = m.state.charge(p.Name, price, msg.ID, p.Meta.ProgressToken, firstRound)

	if m.state.takeFailure() {
		return nil, rpcErr(502, CodeInternal, "mcpmock: injected failure (fail_next)")
	}
	if b.LatencyMS > 0 {
		select {
		case <-time.After(time.Duration(b.LatencyMS) * time.Millisecond):
		case <-ctx.Done():
			return nil, rpcErr(0, CodeInternal, "mcpmock: the client went away")
		}
	}
	var answer json.RawMessage
	if b.Elicit {
		var rerr *RPCError
		if c.stateless {
			if firstRound {
				return m.finish(c, m.inputRequired(p), false), nil
			}
			answer, rerr = m.openInput(p)
		} else {
			answer, rerr = m.elicitLegacy(ctx, c, p.Name)
		}
		if rerr != nil {
			return nil, rerr
		}
	}
	m.sendExtras(c, p.Meta.ProgressToken, b)
	return m.finish(c, m.toolResult(tool, p.Arguments, answer, b), false), nil
}

// sendExtras sends the armed progress and log notifications on the call's
// own stream. Without a stream (a JSON answer) there is nowhere to send them.
// A 2026 request gets log messages only when its _meta asks for a logLevel.
func (m *Mock) sendExtras(c *call, progressToken json.RawMessage, b Behavior) {
	if c.stream == nil {
		return
	}
	if len(progressToken) > 0 {
		for i := 1; i <= b.Progress; i++ {
			_ = c.stream.send(notification("notifications/progress",
				map[string]any{"progressToken": progressToken, "progress": i, "total": b.Progress}))
		}
	}
	if c.stateless && c.meta.LogLevel == "" {
		return
	}
	for i := 1; i <= b.LogMessages; i++ {
		_ = c.stream.send(notification("notifications/message",
			map[string]any{"level": "info", "logger": "mcpmock", "data": fmt.Sprintf("log message %d", i)}))
	}
}

// toolResult builds the CallToolResult: the fixture's canned one, else the
// tool name and arguments as text; then the armed is_error, result_bytes and
// cost_usd. env_names reports the process's variable names.
func (m *Mock) toolResult(tool Tool, args, answer json.RawMessage, b Behavior) map[string]any {
	result := map[string]any{}
	if len(tool.Result) > 0 {
		_ = json.Unmarshal(tool.Result, &result)
	}
	if tool.Name == envNamesTool {
		names := envNames()
		text, _ := json.Marshal(names)
		result["content"] = []any{map[string]any{"type": "text", "text": string(text)}}
		result["structuredContent"] = map[string]any{"names": names}
	}
	if _, ok := result["content"]; !ok {
		if len(args) == 0 {
			args = json.RawMessage(`{}`)
		}
		text := fmt.Sprintf("%s ok: %s", tool.Name, args)
		if len(answer) > 0 {
			text += fmt.Sprintf(" (input: %s)", answer)
		}
		result["content"] = []any{map[string]any{"type": "text", "text": text}}
	}
	if b.ResultBytes > 0 {
		result["content"] = []any{map[string]any{"type": "text", "text": strings.Repeat("x", b.ResultBytes)}}
	}
	if b.IsError {
		result["isError"] = true
	}
	if b.CostUSD != nil {
		meta, _ := result["_meta"].(map[string]any)
		if meta == nil {
			meta = map[string]any{}
		}
		meta[metaCostUSD] = *b.CostUSD
		result["_meta"] = meta
	}
	return result
}

func envNames() []string {
	names := []string{}
	for _, kv := range os.Environ() {
		if name, _, ok := strings.Cut(kv, "="); ok && name != "" {
			names = append(names, name)
		}
	}
	sort.Strings(names)
	return names
}

func elicitParams(tool string, withMode bool) map[string]any {
	p := map[string]any{
		"message": fmt.Sprintf("mcpmock: confirm the call to %s", tool),
		"requestedSchema": map[string]any{
			"type":       "object",
			"properties": map[string]any{"confirm": map[string]any{"type": "boolean"}},
			"required":   []string{"confirm"},
		},
	}
	if withMode {
		p["mode"] = "form"
	}
	return p
}

// elicitLegacy asks a legacy client through elicitation/create on the call's
// stream and waits for its answer (2025-06-18 and later, when the client
// declared the capability).
func (m *Mock) elicitLegacy(ctx context.Context, c *call, tool string) (json.RawMessage, *RPCError) {
	if !atLeast(c.version, V0618) {
		return nil, rpcErr(0, CodeInternal, "mcpmock: elicitation needs 2025-06-18 or later, not %s", c.version)
	}
	var caps map[string]json.RawMessage
	_ = json.Unmarshal(c.caps, &caps)
	if _, ok := caps["elicitation"]; !ok {
		return nil, rpcErr(0, CodeInternal, "mcpmock: the client did not declare the elicitation capability")
	}
	if c.stream == nil {
		return nil, rpcErr(0, CodeInternal, "mcpmock: elicitation needs a streamed answer (Accept text/event-stream)")
	}
	id, _ := json.Marshal(fmt.Sprintf("mcpmock-elicit-%d", atomic.AddInt64(&m.elicitSeq, 1)))
	raw, _ := json.Marshal(elicitParams(tool, atLeast(c.version, V1125)))
	actx, cancel := context.WithTimeout(ctx, m.cfg.ElicitTimeout)
	defer cancel()
	resp, err := c.stream.ask(actx, &Message{JSONRPC: "2.0", ID: id, Method: "elicitation/create", Params: raw})
	if err != nil {
		return nil, rpcErr(0, CodeInternal, "mcpmock: elicitation got no answer: %v", err)
	}
	if resp.Error != nil {
		return nil, rpcErr(0, CodeInternal, "mcpmock: elicitation refused: %s", resp.Error.Message)
	}
	return checkAccepted(resp.Result)
}

func checkAccepted(raw json.RawMessage) (json.RawMessage, *RPCError) {
	var res struct {
		Action  string          `json:"action"`
		Content json.RawMessage `json:"content"`
	}
	if err := json.Unmarshal(raw, &res); err != nil || res.Action != "accept" {
		return nil, rpcErr(0, CodeInvalidParams, "mcpmock: the input was not accepted (action %q)", res.Action)
	}
	return res.Content, nil
}

// inputRequired is a 2026 InputRequiredResult (MRTR): one form elicitation
// under the key "confirm", and the mock's own sealed requestState. Its shape
// is not checked against the published schema.
func (m *Mock) inputRequired(p toolCallParams) map[string]any {
	return map[string]any{
		"resultType": "input_required",
		"inputRequests": map[string]any{
			"confirm": map[string]any{"method": "elicitation/create", "params": elicitParams(p.Name, true)},
		},
		"requestState": m.sealState(p.Name, p.Arguments, time.Now().Add(requestStateTTL)),
	}
}

// openInput checks a 2026 retry: the requestState must be the mock's own,
// untampered, unexpired and for the same tool and arguments.
func (m *Mock) openInput(p toolCallParams) (json.RawMessage, *RPCError) {
	if !m.stateValid(p.RequestState, p.Name, p.Arguments, time.Now()) {
		return nil, rpcErr(0, CodeInvalidParams, "mcpmock: requestState is missing, tampered, expired or for another call")
	}
	raw, ok := p.InputResponses["confirm"]
	if !ok {
		return nil, rpcErr(0, CodeInvalidParams, "mcpmock: inputResponses has no \"confirm\" answer")
	}
	return checkAccepted(raw)
}

func argsHash(args json.RawMessage) string {
	var v any
	_ = json.Unmarshal(args, &v)
	canon, _ := json.Marshal(v) // map keys sort, so equal arguments hash equal
	return hashHex(string(canon))
}

func (m *Mock) sealState(tool string, args json.RawMessage, exp time.Time) string {
	body := fmt.Sprintf("%s|%s|%d", tool, argsHash(args), exp.Unix())
	mac := hmac.New(sha256.New, m.stateKey)
	mac.Write([]byte(body))
	return base64.RawURLEncoding.EncodeToString([]byte(body)) + "." + hex.EncodeToString(mac.Sum(nil))
}

func (m *Mock) stateValid(token, tool string, args json.RawMessage, now time.Time) bool {
	enc, sig, ok := strings.Cut(token, ".")
	if !ok {
		return false
	}
	body, err := base64.RawURLEncoding.DecodeString(enc)
	if err != nil {
		return false
	}
	mac := hmac.New(sha256.New, m.stateKey)
	mac.Write(body)
	if !hmac.Equal([]byte(hex.EncodeToString(mac.Sum(nil))), []byte(sig)) {
		return false
	}
	parts := strings.Split(string(body), "|")
	if len(parts) != 3 || parts[0] != tool || parts[1] != argsHash(args) {
		return false
	}
	var exp int64
	_, err = fmt.Sscan(parts[2], &exp)
	return err == nil && now.Unix() <= exp
}
