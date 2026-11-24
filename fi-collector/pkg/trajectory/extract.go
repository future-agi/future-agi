package trajectory

import (
	"bytes"
	"encoding/json"
	"strings"
)

// Envelope captures the minimal structural metadata needed to reconstruct the
// exact original JSON shape of `spans.input` and `spans.output` from ordered
// `trajectory_items` hashes.
type Envelope struct {
	Version int           `json:"v"`
	Input   *PayloadShape `json:"in,omitempty"`
	Output  *PayloadShape `json:"out,omitempty"`
}

// PayloadShape describes how one side (input or output) was decomposed.
//
// Supported formats:
//   - "msg_array": top-level JSON array of message objects `[m0, m1, ...]`
//   - "obj_messages": top-level JSON object containing a messages array under
//     MessagesKey (e.g. "messages" or "input"), optional tools array under
//     ToolsKey ("tools"), optional system field under SystemKey ("system" or
//     "instructions"), and any remaining small wrapper fields in Wrapper.
type PayloadShape struct {
	Format      string                     `json:"f"`
	MessagesKey string                     `json:"mk,omitempty"`
	ToolsKey    string                     `json:"tk,omitempty"`
	SystemKey   string                     `json:"sk,omitempty"`
	SystemHash  string                     `json:"sh,omitempty"` // "p:<32hex>" when top-level system/instructions string or array was extracted
	Wrapper     map[string]json.RawMessage `json:"w,omitempty"`
}

// ExtractionResult holds the deduplicated representation for a single span.
type ExtractionResult struct {
	Applied       bool
	InputHashes   []string
	OutputHashes  []string
	ToolDefHashes []string
	ItemScopeID   string
	EnvelopeJSON  string
	InputPreview  string
	OutputPreview string
	Items         []Item // all referenced items (before SeenStore filtering)
}

// ExtractSpan inspects a span's input and output JSON payloads and, when at
// least one side contains a recognized message array (or chat object with
// messages/tools), decomposes it into content-addressed Items + an Envelope.
//
// Returns Applied=false for non-conversation spans (plain text, arbitrary maps,
// invalid JSON), leaving those spans untouched as payload_encoding='inline'.
func ExtractSpan(
	projectID string,
	traceSessionID string,
	traceID string,
	input string,
	output string,
	previewMaxBytes int,
) ExtractionResult {
	scopeID := strings.TrimSpace(traceSessionID)
	if scopeID == "" {
		scopeID = strings.TrimSpace(traceID)
	}
	if projectID == "" || scopeID == "" {
		return ExtractionResult{}
	}
	if previewMaxBytes <= 0 {
		previewMaxBytes = DefaultPreviewMaxBytes
	}

	var (
		items        []Item
		inItems      []Item
		outItems     []Item
		inputHashes  []string
		outputHashes []string
		toolHashes   []string
		env          = Envelope{Version: 1}
		extractedIn  bool
		extractedOut bool
	)

	if strings.TrimSpace(input) != "" {
		if shape, msgHashes, tHashes, extracted := decomposePayload(projectID, scopeID, []byte(input), true); shape != nil {
			env.Input = shape
			inputHashes = msgHashes
			toolHashes = append(toolHashes, tHashes...)
			inItems = extracted
			items = append(items, extracted...)
			extractedIn = true
		}
	}

	if strings.TrimSpace(output) != "" {
		if shape, msgHashes, tHashes, extracted := decomposePayload(projectID, scopeID, []byte(output), false); shape != nil {
			env.Output = shape
			outputHashes = msgHashes
			toolHashes = append(toolHashes, tHashes...)
			outItems = extracted
			items = append(items, extracted...)
			extractedOut = true
		}
	}

	if !extractedIn && !extractedOut {
		return ExtractionResult{}
	}

	envBytes, err := marshalNoEscape(env)
	if err != nil {
		return ExtractionResult{}
	}

	res := ExtractionResult{
		Applied:       true,
		InputHashes:   nonNilSlice(inputHashes),
		OutputHashes:  nonNilSlice(outputHashes),
		ToolDefHashes: nonNilSlice(toolHashes),
		ItemScopeID:   scopeID,
		EnvelopeJSON:  string(envBytes),
		Items:         items,
	}
	if extractedIn {
		res.InputPreview = smartMessagePreview(input, inItems, "user", previewMaxBytes)
	} else {
		// Output was extracted, input stays inline intact inside spans.input!
		res.InputPreview = input
	}
	if extractedOut {
		res.OutputPreview = smartMessagePreview(output, outItems, "assistant", previewMaxBytes)
	} else {
		res.OutputPreview = output
	}
	return res
}

// smartMessagePreview chooses a concise, human-readable preview (capped at
// maxBytes) for list tables (Trace List, Span List, Session List first/last
// message & message search) so multi-turn agent prompts with large leading
// system prompts or tool schemas surface the actual latest user/assistant turn
// instead of truncated JSON boilerplate. Detail/eval paths ignore the preview
// and hydrate the full lossless payload from trajectory_items.
func smartMessagePreview(raw string, items []Item, preferredRole string, maxBytes int) string {
	if len(raw) <= maxBytes {
		return raw
	}
	// Walk backwards so the newest turn wins; prefer preferredRole ("user" for
	// input, "assistant" for output), else fall back to the newest non-system,
	// non-tool_def message.
	var fallback string
	for i := len(items) - 1; i >= 0; i-- {
		it := items[i]
		if it.ItemType == "tool_def" || it.Role == "system" || it.Role == "developer" {
			continue
		}
		if it.Role == preferredRole {
			return TruncateUTF8(it.Content, maxBytes)
		}
		if fallback == "" {
			fallback = it.Content
		}
	}
	if fallback != "" {
		return TruncateUTF8(fallback, maxBytes)
	}
	return TruncateUTF8(raw, maxBytes)
}

// decomposePayload tries to extract messages (and optional tools / top-level
// system prompt) from a JSON array or chat request/response object.
func decomposePayload(
	projectID string,
	scopeID string,
	raw []byte,
	allowToolsAndSystem bool,
) (shape *PayloadShape, msgHashes []string, toolHashes []string, items []Item) {
	trimmed := bytes.TrimSpace(raw)
	if len(trimmed) < 2 {
		return nil, nil, nil, nil
	}

	// Case 1: top-level JSON array of message objects, e.g. [{"role":"user",...}]
	if trimmed[0] == '[' {
		var arr []json.RawMessage
		if err := json.Unmarshal(trimmed, &arr); err != nil || len(arr) == 0 {
			return nil, nil, nil, nil
		}
		if !looksLikeMessageArray(arr) {
			return nil, nil, nil, nil
		}
		hashes, extracted := extractMessages(projectID, scopeID, arr)
		return &PayloadShape{Format: "msg_array"}, hashes, nil, extracted
	}

	// Case 2: top-level JSON object containing messages/input/choices + optional tools/system
	if trimmed[0] == '{' {
		var obj map[string]json.RawMessage
		if err := json.Unmarshal(trimmed, &obj); err != nil || len(obj) == 0 {
			return nil, nil, nil, nil
		}

		// Find primary message-list key in precedence order.
		var msgKey string
		var msgArr []json.RawMessage
		for _, candidate := range []string{"messages", "input", "choices"} {
			rawField, ok := obj[candidate]
			if !ok {
				continue
			}
			var arr []json.RawMessage
			if err := json.Unmarshal(rawField, &arr); err == nil && len(arr) > 0 {
				if candidate == "choices" && looksLikeChoicesArray(arr) {
					msgKey = candidate
					msgArr = arr
					break
				}
				if candidate != "choices" && looksLikeMessageArray(arr) {
					msgKey = candidate
					msgArr = arr
					break
				}
			}
		}

		// Also check optional tools array and top-level system/instructions (Anthropic / Responses API).
		var toolsKey string
		var toolsArr []json.RawMessage
		var sysKey string
		var sysRaw json.RawMessage
		if allowToolsAndSystem {
			if rawTools, ok := obj["tools"]; ok {
				var arr []json.RawMessage
				if err := json.Unmarshal(rawTools, &arr); err == nil && len(arr) > 0 {
					toolsKey = "tools"
					toolsArr = arr
				}
			}
			for _, sk := range []string{"system", "instructions"} {
				if rawSys, ok := obj[sk]; ok && len(bytes.TrimSpace(rawSys)) > 2 {
					sysKey = sk
					sysRaw = bytes.TrimSpace(rawSys)
					break
				}
			}
		}

		if msgKey == "" && toolsKey == "" {
			return nil, nil, nil, nil
		}

		shape = &PayloadShape{
			Format:      "obj_messages",
			MessagesKey: msgKey,
			ToolsKey:    toolsKey,
			SystemKey:   sysKey,
		}

		if msgKey != "" {
			hashes, extracted := extractMessages(projectID, scopeID, msgArr)
			msgHashes = hashes
			items = append(items, extracted...)
		}

		if toolsKey != "" {
			for _, rawTool := range toolsArr {
				tBytes := bytes.TrimSpace(rawTool)
				h := HashCanonical(tBytes)
				toolHashes = append(toolHashes, ProjectHashPrefix+h)
				items = append(items, Item{
					ProjectID: projectID,
					ScopeID:   ProjectScopeID,
					ItemHash:  h,
					ItemType:  "tool_def",
					Role:      "",
					Content:   string(tBytes),
				})
			}
		}

		if sysKey != "" {
			h := HashCanonical(sysRaw)
			shape.SystemHash = ProjectHashPrefix + h
			items = append(items, Item{
				ProjectID: projectID,
				ScopeID:   ProjectScopeID,
				ItemHash:  h,
				ItemType:  "message",
				Role:      "system",
				Content:   string(sysRaw),
			})
		}

		// Preserve all remaining wrapper keys (model, temperature, max_tokens, etc.).
		wrapper := make(map[string]json.RawMessage, len(obj))
		for k, v := range obj {
			if k == msgKey || k == toolsKey || k == sysKey {
				continue
			}
			wrapper[k] = v
		}
		if len(wrapper) > 0 {
			shape.Wrapper = wrapper
		}
		return shape, msgHashes, toolHashes, items
	}

	return nil, nil, nil, nil
}

func extractMessages(projectID, scopeID string, arr []json.RawMessage) ([]string, []Item) {
	hashes := make([]string, 0, len(arr))
	items := make([]Item, 0, len(arr))
	for _, rawMsg := range arr {
		mBytes := bytes.TrimSpace(rawMsg)
		itemType, role := classifyMessage(mBytes)
		h := HashCanonical(mBytes)

		targetScope := scopeID
		ref := h
		if role == "system" || role == "developer" {
			targetScope = ProjectScopeID
			ref = ProjectHashPrefix + h
		}
		hashes = append(hashes, ref)
		items = append(items, Item{
			ProjectID: projectID,
			ScopeID:   targetScope,
			ItemHash:  h,
			ItemType:  itemType,
			Role:      role,
			Content:   string(mBytes),
		})
	}
	return hashes, items
}

// classifyMessage extracts item_type and role from a message JSON object.
func classifyMessage(raw []byte) (itemType string, role string) {
	var probe struct {
		Role      string          `json:"role"`
		Type      string          `json:"type"`
		ToolCalls json.RawMessage `json:"tool_calls"`
		Message   *struct {
			Role string `json:"role"`
		} `json:"message"`
	}
	if err := json.Unmarshal(raw, &probe); err != nil {
		return "raw", ""
	}
	role = strings.ToLower(strings.TrimSpace(probe.Role))
	if role == "" && probe.Message != nil {
		role = strings.ToLower(strings.TrimSpace(probe.Message.Role))
	}
	typ := strings.ToLower(strings.TrimSpace(probe.Type))
	switch typ {
	case "tool_call", "function_call":
		return "tool_call", role
	case "tool_result", "function_call_output":
		return "tool_result", role
	case "reasoning":
		return "reasoning", role
	}
	if role == "tool" {
		return "tool_result", role
	}
	if len(bytes.TrimSpace(probe.ToolCalls)) > 2 {
		return "tool_call", role
	}
	if role != "" {
		return "message", role
	}
	return "raw", ""
}

func looksLikeMessageArray(arr []json.RawMessage) bool {
	matched := 0
	checked := min(len(arr), 5)
	for i := 0; i < checked; i++ {
		var obj map[string]json.RawMessage
		if err := json.Unmarshal(arr[i], &obj); err != nil {
			return false
		}
		if _, ok := obj["role"]; ok {
			matched++
			continue
		}
		if _, ok := obj["type"]; ok {
			matched++
			continue
		}
		if _, ok := obj["content"]; ok {
			matched++
			continue
		}
	}
	return matched > 0 && matched == checked
}

func looksLikeChoicesArray(arr []json.RawMessage) bool {
	var obj map[string]json.RawMessage
	if err := json.Unmarshal(arr[0], &obj); err != nil {
		return false
	}
	_, hasMsg := obj["message"]
	_, hasText := obj["text"]
	return hasMsg || hasText
}

func nonNilSlice(s []string) []string {
	if s == nil {
		return []string{}
	}
	return s
}
