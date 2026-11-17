package mcp

import (
	"bytes"
	"context"
	"encoding/json"
	"sort"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/futureagi/agentcc-gateway/internal/mcp/vserver"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// hookResolveMetaTool is step 5 of the tools/call chain (chain.go).
const hookResolveMetaTool = "resolveMetaTool"

// ViaCallTool is a tool-call record's via for a call made through the
// call_tool meta-tool (mcp-tool-call-v1).
const ViaCallTool = "call_tool"

// metaCallKey keys a call's metaCall in its context.
type metaCallKey struct{}

// metaCall is what step 5 decided for a meta-tool call.
type metaCall struct {
	// via is ViaCallTool once a call_tool call was rewritten to its target.
	via string
	// answered is set once the gateway answered the call itself
	// (search_tools, describe_tool); cc.Result is the answer.
	answered bool
}

// resolveMetaTool serves the meta-tools of a search-mode virtual server.
//
// search_tools {query, limit} and describe_tool {name} read the listing:
// what a tools/list with the call's context would offer in direct mode
// (offeredTools), so they find and describe exactly the tools the caller
// may see, and a tool it cannot see is described as a missing one. The
// gateway answers them itself (answerInGateway); no upstream is called.
//
// call_tool {name, arguments} becomes a call of the named tool with
// those arguments, here, before any tool is resolved: steps 6 to 20 then
// judge, run, bill and record the real tool exactly as a direct call of it,
// so the access rules (an app's tool posture included), the key's lists,
// the request's narrowing, the tool policy, pins, approvals, limits,
// credentials, guardrails and the record all apply to it, and a tool the
// caller cannot see answers as a missing one. Its arguments are refused as
// invalid_arguments unless they are a tool name and an object (or no)
// arguments.
//
// Any other name passes through, and on a virtual server in direct mode, an
// org's compatibility view and the legacy view the meta-tool names are not
// tools at all: no upstream tool is ever exposed under one (vserver
// reserves them), so calling one answers as a missing tool.
// Keep observeHook as its first statement.
func resolveMetaTool(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookResolveMetaTool)
	v := callViewOf(cc)
	if v == nil || !v.searchMode() {
		return nil
	}
	switch cc.Tool.ExposedName {
	case vserver.MetaCallTool:
		return enterCallTool(cc)
	case vserver.MetaSearchTools:
		return searchTools(s, cc, v)
	case vserver.MetaDescribeTool:
		return describeTool(s, cc, v)
	}
	return nil
}

// enterCallTool rewrites a call_tool call into a call of its target: the
// exposed name, the raw arguments and the decoded ones (which the default
// validator, the guard and the upstream call read), and marks the call as
// made via call_tool.
func enterCallTool(cc *CallContext) *Denial {
	var p struct {
		Name      string          `json:"name"`
		Arguments json.RawMessage `json:"arguments"`
	}
	if len(cc.Args) == 0 || json.Unmarshal(cc.Args, &p) != nil || p.Name == "" {
		return metaArgumentsInvalid(`call_tool needs {"name": "<tool>", "arguments": {...}}`)
	}
	args := p.Arguments
	if isJSONNull(args) {
		args = nil
	}
	var decoded map[string]interface{}
	if args != nil && json.Unmarshal(args, &decoded) != nil {
		return metaArgumentsInvalid("call_tool arguments must be an object")
	}
	cc.Tool.ExposedName = p.Name
	cc.Args = args
	cc.legacy.arguments = decoded
	cc.ctx = context.WithValue(cc.Context(), metaCallKey{}, &metaCall{via: ViaCallTool})
	return nil
}

// --- search_tools and describe_tool ---

// search_tools' bounds.
const (
	searchDefaultLimit  = 10
	searchMaxLimit      = 20
	searchMaxQueryBytes = 1024
	// searchDescriptionRunes caps a description in a search result;
	// describe_tool gives the whole definition.
	searchDescriptionRunes = 300
)

// searchHit is one tool in a search_tools result.
type searchHit struct {
	Name        string `json:"name"`
	Title       string `json:"title,omitempty"`
	Description string `json:"description"`
}

// searchTools answers search_tools {query, limit}: the offered tools ranked
// by the query's words (rankTools), at most limit of them (default 10, at
// most 20).
func searchTools(s *Server, cc *CallContext, v *callView) *Denial {
	var p struct {
		Query string `json:"query"`
		Limit *int   `json:"limit"`
	}
	if !decodeMetaArgs(cc.Args, &p) {
		return metaArgumentsInvalid(`search_tools needs {"query": "<words>", "limit": 1 to 20}`)
	}
	if len(p.Query) > searchMaxQueryBytes {
		return metaArgumentsInvalid("search_tools query is longer than 1024 bytes")
	}
	terms := searchTerms(p.Query)
	if len(terms) == 0 {
		return metaArgumentsInvalid("search_tools needs a query of one or more words")
	}
	limit := searchDefaultLimit
	if p.Limit != nil {
		if *p.Limit < 1 || *p.Limit > searchMaxLimit {
			return metaArgumentsInvalid("search_tools limit must be from 1 to 20")
		}
		limit = *p.Limit
	}
	offered, d := offeredTools(s, cc, v)
	if d != nil {
		return d
	}
	hits := []searchHit{}
	var ranked []Tool
	if hybrid := s.search.Load(); hybrid != nil {
		ranked = hybrid.rank(cc, offered, p.Query, limit)
	} else {
		ranked = rankTools(offered, terms, strings.ToLower(strings.TrimSpace(p.Query)), limit)
	}
	for _, t := range ranked {
		hits = append(hits, searchHit{Name: t.Name, Title: t.Title, Description: capRunes(t.Description, searchDescriptionRunes)})
	}
	body, err := json.Marshal(struct {
		Tools []searchHit `json:"tools"`
	}{hits})
	if err != nil {
		return metaAnswerFailed()
	}
	return answerInGateway(cc, string(body), body)
}

// describeTool answers describe_tool {name}: the offered tool's definition,
// exactly as a direct-mode list sends it. A tool the caller is not offered
// answers as a direct call of a tool it cannot see: -32602 Unknown tool.
func describeTool(s *Server, cc *CallContext, v *callView) *Denial {
	var p struct {
		Name string `json:"name"`
	}
	if !decodeMetaArgs(cc.Args, &p) || p.Name == "" {
		return metaArgumentsInvalid(`describe_tool needs {"name": "<tool>"}`)
	}
	offered, d := offeredTools(s, cc, v)
	if d != nil {
		return d
	}
	for _, t := range offered {
		if t.Name != p.Name {
			continue
		}
		def := definition(t.Raw, t, t.Name, t.GatewayMeta)
		structured, err := json.Marshal(struct {
			Tool json.RawMessage `json:"tool"`
		}{def})
		if err != nil {
			return metaAnswerFailed()
		}
		return answerInGateway(cc, string(def), structured)
	}
	// authorizeTool's answer for a tool the caller cannot see.
	return &Denial{Type: models.ErrTypeInvalidRequest, Code: "tool_not_found", Message: "Unknown tool: " + p.Name, RPCCode: ErrCodeInvalidParams}
}

// offeredTools is what a tools/list with the call's context would offer in
// direct mode: the tools/list steps from assembleList up to, not including,
// toolset (members, access rules, pins, the description scan), then
// the step profile. Running the list's own steps keeps search and describe
// exactly in step with the list as those steps change.
func offeredTools(s *Server, cc *CallContext, v *callView) ([]Tool, *Denial) {
	saved := cc.ToolList
	defer func() { cc.ToolList = saved }()
	cc.ToolList = nil
	listing := false
	for _, st := range toolsListChain {
		if st.name == hookToolset {
			break
		}
		if st.name == hookAssembleList {
			listing = true
		}
		if !listing {
			continue
		}
		if d := st.run(s, cc); d != nil {
			return nil, d
		}
	}
	offered, _ := v.stepNarrowed(cc, cc.ToolList)
	return offered, nil
}

// searchTerms are the query's words, lower-cased, each once, in order: runs
// of letters and digits.
func searchTerms(query string) []string {
	seen := map[string]bool{}
	var terms []string
	for _, w := range strings.FieldsFunc(strings.ToLower(query), func(r rune) bool {
		return !unicode.IsLetter(r) && !unicode.IsDigit(r)
	}) {
		if !seen[w] {
			seen[w] = true
			terms = append(terms, w)
		}
	}
	return terms
}

// rankTools returns at most limit of list's tools that match the terms,
// best first: each term found in a tool's exposed name or title scores 3,
// in its description 1, and a name equal to the whole query 100. Ties go
// by exposed name, byte order, so the answer depends on nothing but the
// list and the query.
func rankTools(list []Tool, terms []string, whole string, limit int) []Tool {
	type hit struct {
		t     Tool
		score int
	}
	var hits []hit
	for _, t := range list {
		name, title, desc := strings.ToLower(t.Name), strings.ToLower(t.Title), strings.ToLower(t.Description)
		score := 0
		if name == whole {
			score += 100
		}
		for _, term := range terms {
			if strings.Contains(name, term) || strings.Contains(title, term) {
				score += 3
			}
			if strings.Contains(desc, term) {
				score++
			}
		}
		if score > 0 {
			hits = append(hits, hit{t, score})
		}
	}
	sort.Slice(hits, func(i, j int) bool {
		if hits[i].score != hits[j].score {
			return hits[i].score > hits[j].score
		}
		return hits[i].t.Name < hits[j].t.Name
	})
	out := make([]Tool, 0, min(limit, len(hits)))
	for _, h := range hits[:min(limit, len(hits))] {
		out = append(out, h.t)
	}
	return out
}

// capRunes cuts s to at most n runes, ending a cut one with "…".
func capRunes(s string, n int) string {
	if utf8.RuneCountInString(s) <= n {
		return s
	}
	r := []rune(s)
	return string(r[:n-1]) + "…"
}

// answerInGateway makes the call's result the gateway's own answer: one
// text part and the structured content, as a tool's result. The chain then
// skips the steps that judge and run a member's tool (answeredByGateway)
// and answers the client with cc.Result; the record and the evidence block
// still describe the call.
func answerInGateway(cc *CallContext, text string, structured json.RawMessage) *Denial {
	res, err := json.Marshal(struct {
		Content           []ContentPart   `json:"content"`
		StructuredContent json.RawMessage `json:"structuredContent"`
	}{[]ContentPart{{Type: "text", Text: text}}, structured})
	if err != nil {
		return metaAnswerFailed()
	}
	cc.Result = res
	if m := metaCallOf(cc); m != nil {
		m.answered = true
	} else {
		cc.ctx = context.WithValue(cc.Context(), metaCallKey{}, &metaCall{answered: true})
	}
	return nil
}

// answeredByGateway reports whether a step answered the call itself
// (search_tools, describe_tool), so the steps that judge and run a member's
// tool do not apply to it.
func answeredByGateway(cc *CallContext) bool {
	m := metaCallOf(cc)
	return m != nil && m.answered
}

func metaAnswerFailed() *Denial {
	return rpcDenial(ErrCodeInternal, "internal_error", "failed to build the meta-tool answer")
}

// decodeMetaArgs decodes a meta-tool's arguments into dst; absent or null
// arguments decode as {}.
func decodeMetaArgs(args json.RawMessage, dst any) bool {
	if len(args) == 0 || isJSONNull(args) {
		args = json.RawMessage(`{}`)
	}
	return json.Unmarshal(args, dst) == nil
}

// metaCallOf returns the call's metaCall, or nil.
func metaCallOf(cc *CallContext) *metaCall {
	m, _ := cc.Context().Value(metaCallKey{}).(*metaCall)
	return m
}

// callVia is the record's via: ViaCallTool for a call made through
// call_tool, else "".
func callVia(cc *CallContext) string {
	if m := metaCallOf(cc); m != nil {
		return m.via
	}
	return ""
}

// metaArgumentsInvalid refuses a meta-tool call's own arguments, with the
// code and JSON-RPC error the argument check gives any tool (validateArgs).
func metaArgumentsInvalid(message string) *Denial {
	return rpcDenial(ErrCodeInvalidParams, "invalid_arguments", message)
}

func isJSONNull(raw json.RawMessage) bool {
	return bytes.Equal(bytes.TrimSpace(raw), []byte("null"))
}
