package mcp

// The first list step of tools/list, resources/list and prompts/list
// (chain.go).
const (
	hookAssembleList    = "assembleList"
	hookFilterResources = "filterResources"
	hookFilterPrompts   = "filterPrompts"
)

// assembleList builds the tools/list answer: the registry's tools under
// their exposed names, filtered by the caller's access.
//
// Default: the registry listing filtered by the per-key allow and deny
// lists.
// Keep observeHook as its first statement.
func assembleList(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookAssembleList)
	tools := s.registry.ListMCPTools()

	// Per-key filtering: only return tools the key is allowed to use.
	cc.ToolList = filterToolsByKey(cc.Context(), tools)
	return nil
}

// filterResources builds the resources/list answer.
//
// Default: the registry listing.
// Keep observeHook as its first statement.
func filterResources(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookFilterResources)
	cc.ResourceList = s.registry.ListResources()
	return nil
}

// filterPrompts builds the prompts/list answer.
//
// Default: the registry listing.
// Keep observeHook as its first statement.
func filterPrompts(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookFilterPrompts)
	cc.PromptList = s.registry.ListPrompts()
	return nil
}
