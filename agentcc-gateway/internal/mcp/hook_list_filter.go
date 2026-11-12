package mcp

import (
	"encoding/json"

	"github.com/futureagi/agentcc-gateway/internal/mcp/vserver"
)

// The first list step of tools/list, resources/list and prompts/list
// (chain.go).
const (
	hookAssembleList    = "assembleList"
	hookFilterResources = "filterResources"
	hookFilterPrompts   = "filterPrompts"
)

// assembleList builds the tools/list answer: through a virtual server,
// its composed tools (members, include and exclude, overrides, names) less
// every tool a rule refuses the caller, each built from the upstream's raw
// definition with only the gateway-owned fields edited; on the legacy view,
// the registry's tools filtered by the key's lists and the request's
// narrowing. Either way sorted by exposed name, byte order.
// Keep observeHook as its first statement.
func assembleList(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookAssembleList)
	list := []Tool{}
	if v := callViewOf(cc); v != nil {
		for _, e := range v.tools.Entries {
			if v.refusal(cc, e) == "" {
				list = append(list, exposedTool(v.byRef[e.Ref()], e))
			}
		}
		cc.ToolList = list
		return nil
	}
	for _, rt := range s.registry.ListTools() {
		if checkToolKeyAccess(cc.Context(), rt.Name) == "" && requestRefusal(cc, []string{rt.Server + "/" + rt.OrigName, rt.Name}) == "" {
			list = append(list, rt.Tool)
		}
	}
	cc.ToolList = list
	return nil
}

// exposedTool is an entry as clients see it: the upstream definition with
// its name, and an overridden title or description, replaced. The raw
// definition gets the same edits, so a writer that passes Raw through
// sends every other upstream field as it came.
func exposedTool(rt *RegisteredTool, e vserver.Entry) Tool {
	t := rt.Upstream
	t.Name = e.Exposed
	t.Description = e.Description
	fields := map[string]string{"name": e.Exposed}
	if e.Description != e.OriginalDescription {
		fields["description"] = e.Description
	}
	if e.Title != rt.Title {
		fields["title"] = e.Title
	}
	t.Raw = editRaw(t.Raw, fields)
	return t
}

// editRaw returns a raw JSON object with the given string fields set, the
// gateway-owned ones; nil when raw is empty or not an object, so a writer
// marshals the struct instead.
func editRaw(raw json.RawMessage, fields map[string]string) json.RawMessage {
	if len(raw) == 0 {
		return nil
	}
	var obj map[string]json.RawMessage
	if json.Unmarshal(raw, &obj) != nil || obj == nil {
		return nil
	}
	for k, v := range fields {
		b, err := json.Marshal(v)
		if err != nil {
			return nil
		}
		obj[k] = b
	}
	out, err := json.Marshal(obj)
	if err != nil {
		return nil
	}
	return out
}

// filterResources builds the resources/list answer: the virtual server's
// members' resources under mcp://<member>/<uri>, less every one an access
// rule refuses the caller, or the registry's.
// Keep observeHook as its first statement.
func filterResources(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookFilterResources)
	if v := callViewOf(cc); v != nil {
		cc.ResourceList = v.resourceList(cc)
		return nil
	}
	cc.ResourceList = s.registry.ListResources()
	return nil
}

// filterPrompts builds the prompts/list answer: the virtual server's
// members' prompts, named like its tools, less every one an access rule
// refuses the caller, or the registry's.
// Keep observeHook as its first statement.
func filterPrompts(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookFilterPrompts)
	if v := callViewOf(cc); v != nil {
		list := []Prompt{}
		for _, e := range v.prompts.Entries {
			if v.itemRefusal(cc, e.Member, e.Name, e.Exposed) == "" {
				list = append(list, v.prompt(e))
			}
		}
		cc.PromptList = list
		return nil
	}
	cc.PromptList = s.registry.ListPrompts()
	return nil
}
