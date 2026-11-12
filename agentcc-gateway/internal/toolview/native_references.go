package toolview

import "github.com/futureagi/agentcc-gateway/internal/models"

// Reference checks follow protocol locations, never arbitrary strings or opaque
// extension objects. Unknown selectors/items and ID-only history stay closed:
// removing a declaration cannot silently invalidate an unresolvable reference.
func nativeReferences(protocol models.NativeProtocol, root *nativeNode, functions map[string]bool) bool {
	if nativeReferenceFields(root) {
		return true
	}
	switch protocol {
	case models.NativeMessages, models.NativeResponses:
		if root.field("toolChoice") != nil || root.field("toolConfig") != nil || root.field("tool_config") != nil {
			return true
		}
		if choice := root.field("tool_choice"); choice != nil && nativeChoice(protocol, choice, functions) {
			return true
		}
		if protocol == models.NativeMessages {
			if nativeContent(root.field("system"), protocol, functions) {
				return true
			}
			return nativeSequence(root.field("messages"), false, func(message *nativeNode) bool {
				return nativeReferenceFields(message) || nativeContent(message.field("content"), protocol, functions)
			})
		}
		if root.field("previous_response_id") != nil || root.field("conversation") != nil {
			return true // Stored history cannot be resolved from this body.
		}
		input := root.field("input")
		if input != nil && input.kind == '"' {
			return false
		}
		return nativeSequence(input, false, func(item *nativeNode) bool {
			if nativeReferenceFields(item) {
				return true
			}
			if typ := item.field("type"); typ != nil && typ.kind != '"' {
				return true
			}
			switch item.field("type").str() {
			case "", "message":
				if item.field("role").str() == "" {
					return true
				}
				return nativeContent(item.field("content"), protocol, functions)
			case "function_call":
				return nativeUnresolvedFunction(item.field("name"), functions)
			case "reasoning":
				return !nativeReasoningItem(item)
			default:
				return true
			}
		})
	case models.NativeGenAI:
		if root.field("tool_choice") != nil || root.field("toolChoice") != nil {
			return true
		}
		cfg, ambiguous := nativeAlias(root, "toolConfig", "tool_config")
		if ambiguous || (cfg != nil && nativeGeminiChoice(cfg, functions)) {
			return true
		}
		checkContent := func(content *nativeNode) bool {
			if nativeReferenceFields(content) || content.field("type") != nil {
				return true
			}
			return nativeSequence(content.field("parts"), true, func(part *nativeNode) bool {
				if part.field("type") != nil {
					return true // A foreign/unknown item envelope is not a Gemini part.
				}
				// A part may carry text, data and opaque payloads. Only the two
				// function reference fields have name semantics here.
				for _, key := range []string{"tool_calls", "tool_call_id", "tool_use_id", "tool_choice", "toolChoice", "toolConfig", "tool_config"} {
					if part.field(key) != nil {
						return true
					}
				}
				for _, pair := range [][2]string{{"functionCall", "function_call"}, {"functionResponse", "function_response"}} {
					reference, ambiguous := nativeAlias(part, pair[0], pair[1])
					if ambiguous || (reference != nil && (reference.kind != '{' || nativeUnresolvedFunction(reference.field("name"), functions))) {
						return true
					}
				}
				return false
			})
		}
		system, ambiguous := nativeAlias(root, "systemInstruction", "system_instruction")
		if ambiguous || (system != nil && system.kind != '"' && (system.kind != '{' || checkContent(system))) {
			return true
		}
		return nativeSequence(root.field("contents"), true, checkContent)
	default:
		return true
	}
}

// Only retained Function declarations resolve a named function reference.
// Nonempty undeclared names and builtin spellings without a Function stay closed.
func nativeUnresolvedFunction(n *nativeNode, functions map[string]bool) bool {
	return n == nil || n.kind != '"' || n.text == "" || !functions[n.text]
}

func nativeAlias(n *nativeNode, camel, snake string) (*nativeNode, bool) {
	a, b := n.field(camel), n.field(snake)
	if a != nil {
		return a, b != nil
	}
	return b, false
}

// nativeSequence accepts only the container forms supported at this location.
func nativeSequence(n *nativeNode, singleton bool, check func(*nativeNode) bool) bool {
	if n == nil || n.kind == 'n' {
		return false
	}
	items, ok := nativeList(n, singleton)
	if !ok {
		return true
	}
	for _, item := range items {
		if item.kind != '{' || check(item) {
			return true
		}
	}
	return false
}

// References in these fields are not native declarations or free-form text.
// Call this only on protocol containers, never on extension/payload objects.
func nativeReferenceFields(n *nativeNode) bool {
	for _, key := range []string{"functionCall", "function_call", "functionResponse", "function_response", "tool_calls", "tool_call_id", "tool_use_id"} {
		if n.field(key) != nil {
			return true
		}
	}
	return false
}

func nativeContent(n *nativeNode, protocol models.NativeProtocol, functions map[string]bool) bool {
	if n != nil && n.kind == '"' {
		return false
	}
	return nativeSequence(n, false, func(part *nativeNode) bool {
		if nativeReferenceFields(part) {
			return true
		}
		if protocol == models.NativeMessages && part.field("type").str() == "tool_use" {
			return nativeUnresolvedFunction(part.field("name"), functions)
		}
		return !nativeNonToolPart(part, protocol)
	})
}

func nativeOnlyFields(n *nativeNode, allowed ...string) bool {
	if n == nil || n.kind != '{' {
		return false
	}
	for _, key := range n.keys {
		found := false
		for _, want := range allowed {
			found = found || key == want
		}
		if !found {
			return false
		}
	}
	return true
}

func nativeChoice(protocol models.NativeProtocol, choice *nativeNode, functions map[string]bool) bool {
	if protocol == models.NativeResponses {
		if choice.kind == '"' {
			// A retained Function can satisfy the required tool-call obligation.
			if choice.text == "required" {
				return len(functions) == 0
			}
			return choice.text != "auto" && choice.text != "none"
		}
		return !nativeOnlyFields(choice, "type", "name") || choice.field("type").str() != "function" || nativeUnresolvedFunction(choice.field("name"), functions)
	}
	if !nativeOnlyFields(choice, "type", "name", "disable_parallel_tool_use") {
		return true
	}
	if parallel := choice.field("disable_parallel_tool_use"); parallel != nil && parallel.kind != 't' && parallel.kind != 'f' {
		return true
	}
	switch choice.field("type").str() {
	case "tool":
		return nativeUnresolvedFunction(choice.field("name"), functions)
	case "any":
		return choice.field("name") != nil || len(functions) == 0
	case "auto", "none":
		return choice.field("name") != nil
	default:
		return true
	}
}

func nativeGeminiChoice(cfg *nativeNode, functions map[string]bool) bool {
	if !nativeOnlyFields(cfg, "functionCallingConfig", "function_calling_config") {
		return true
	}
	call, ambiguous := nativeAlias(cfg, "functionCallingConfig", "function_calling_config")
	if ambiguous {
		return true
	}
	if call == nil {
		return false
	}
	if !nativeOnlyFields(call, "mode", "allowedFunctionNames", "allowed_function_names") {
		return true
	}
	mode := call.field("mode")
	if mode != nil {
		switch mode.str() {
		case "AUTO", "NONE", "ANY", "VALIDATED":
		default:
			return true
		}
	}
	names, ambiguous := nativeAlias(call, "allowedFunctionNames", "allowed_function_names")
	if ambiguous {
		return true
	}
	if names == nil {
		return mode.str() == "ANY" || mode.str() == "VALIDATED"
	}
	if names.kind != '[' || len(names.children) == 0 {
		return true
	}
	for _, name := range names.children {
		if nativeUnresolvedFunction(name, functions) {
			return true
		}
	}
	return false
}
