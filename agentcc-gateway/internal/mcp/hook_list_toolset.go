package mcp

import (
	"encoding/json"
	"unicode/utf8"

	"github.com/futureagi/agentcc-gateway/internal/mcp/vserver"
)

// hookToolset is a tools/list step (chain.go).
const hookToolset = "toolset"

// MetaKeyToolList is _meta["io.futureagi/tool_list"] on a tools/list answer
// whose tool set the toolset step changed.
const MetaKeyToolList = "io.futureagi/tool_list"

// Virtual-server modes (mcp.virtual_servers.<slug>.mode): direct lists the
// tools themselves, search lists the three meta-tools in their place.
const (
	modeDirect = "direct"
	modeSearch = "search"
)

// searchMode reports whether the call's virtual server is in search mode.
func (v *callView) searchMode() bool { return v.vs.Mode == modeSearch }

// toolListMeta is _meta["io.futureagi/tool_list"]: how many tools the
// caller could see and how many were sent, and what each list would cost in
// tokens, estimated as characters / 4 of the definitions as the client
// gets them.
type toolListMeta struct {
	Mode       string `json:"mode"`
	Step       string `json:"step,omitempty"`
	ToolsFull  int    `json:"tools_full"`
	ToolsSent  int    `json:"tools_sent"`
	TokensFull int64  `json:"tokens_full"`
	TokensSent int64  `json:"tokens_sent"`
	Estimated  bool   `json:"estimated"`
}

// toolset applies a virtual server's tool set (M30).
//
// First the step: step_profiles.<step>.include keeps only the listed tools
// it matches, by the names tools.include matches, for a request sent at
// that agent step. It runs after the access, pin and description steps, so
// it only ever takes tools away. A request with no step, a step with no
// profile or a profile with no include keeps the list as it is. A profile
// narrows what is offered, never what is permitted: the step comes from the
// caller, so a call is judged by the access rules whatever the step.
//
// Then the mode: a virtual server in search mode lists the three
// meta-tools (metaTools) in place of the tools, which search_tools and
// describe_tool then serve from, and call_tool runs.
//
// A list either part changed carries _meta["io.futureagi/tool_list"]
// (toolListMeta); any other list is sent exactly as before, and the legacy
// view, which has no virtual server, is left alone.
// Keep observeHook as its first statement.
func toolset(_ *Server, cc *CallContext) *Denial {
	observeHook(cc, hookToolset)
	v := callViewOf(cc)
	if v == nil {
		return nil
	}
	full := cc.ToolList
	sent, narrowed := v.stepNarrowed(cc, full)
	mode := modeDirect
	if v.searchMode() {
		sent, mode = metaTools(), modeSearch
	} else if !narrowed {
		return nil
	}
	meta := toolListMeta{
		Mode: mode, ToolsFull: len(full), ToolsSent: len(sent),
		TokensFull: listTokens(full), TokensSent: listTokens(sent), Estimated: true,
	}
	if narrowed {
		meta.Step = string(cc.Agent.Step)
	}
	cc.ToolList = sent
	cc.SetResponseMeta(MetaKeyToolList, meta)
	return nil
}

// stepInclude is the include list of the request's agent step, nil when the
// request has no step, the virtual server no profile for it, or the profile
// no include.
func (v *callView) stepInclude(cc *CallContext) []string {
	if cc.Agent.Step == "" {
		return nil
	}
	if p := v.vs.StepProfiles[string(cc.Agent.Step)]; p != nil {
		return p.Include
	}
	return nil
}

// stepNarrowed returns list less every tool the step's profile leaves out,
// and whether a profile applied. An item that is not one of the view's
// tools is left out under a profile: a profile names tools, never more.
func (v *callView) stepNarrowed(cc *CallContext, list []Tool) ([]Tool, bool) {
	include := v.stepInclude(cc)
	if len(include) == 0 {
		return list, false
	}
	out := make([]Tool, 0, len(list))
	for _, t := range list {
		if e, ok := v.tools.Lookup(t.Name); ok && vserver.Any(include, e.Names()...) {
			out = append(out, t)
		}
	}
	return out, true
}

// listTokens estimates the tokens of list's definitions as the client gets
// them (definition, as writeToolsList sends each): characters / 4, rounded
// up.
func listTokens(list []Tool) int64 {
	var chars int64
	for _, t := range list {
		chars += int64(utf8.RuneCount(definition(t.Raw, t, t.Name, t.GatewayMeta)))
	}
	return (chars + 3) / 4
}

// The meta-tools' input schemas.
var (
	searchToolsSchema = json.RawMessage(`{"type":"object","properties":{` +
		`"query":{"type":"string","description":"Words to look for in the tools' names and descriptions."},` +
		`"limit":{"type":"integer","minimum":1,"maximum":20,"description":"How many tools to return, best match first. Default 10."}},` +
		`"required":["query"]}`)
	describeToolSchema = json.RawMessage(`{"type":"object","properties":{` +
		`"name":{"type":"string","description":"The tool's name, as search_tools returns it."}},` +
		`"required":["name"]}`)
	callToolSchema = json.RawMessage(`{"type":"object","properties":{` +
		`"name":{"type":"string","description":"The tool's name, as search_tools returns it."},` +
		`"arguments":{"type":"object","description":"The tool's arguments, as its input schema from describe_tool asks."}},` +
		`"required":["name"]}`)
)

// metaTools are the three tools a search-mode virtual server lists, in byte
// order of name, built afresh for each list (a later list step may annotate
// them).
func metaTools() []Tool {
	readOnly := func() *ToolAnnotations {
		yes := true
		return &ToolAnnotations{ReadOnlyHint: &yes}
	}
	return []Tool{
		{Name: vserver.MetaCallTool, Title: "Call a tool", InputSchema: callToolSchema,
			Description: "Run one of this server's tools with its arguments. The tool is checked, run and logged exactly as if it were called by its own name."},
		{Name: vserver.MetaDescribeTool, Title: "Describe a tool", InputSchema: describeToolSchema, Annotations: readOnly(),
			Description: "Return the full definition of one of this server's tools, with its input schema."},
		{Name: vserver.MetaSearchTools, Title: "Search tools", InputSchema: searchToolsSchema, Annotations: readOnly(),
			Description: "Find this server's tools by words in their names and descriptions. Returns each match's name and description, best match first. Use describe_tool for a tool's input schema and call_tool to run it."},
	}
}
