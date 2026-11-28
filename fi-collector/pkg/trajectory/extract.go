package trajectory

import (
	"bytes"
	"encoding/json"
	"io"
	"sort"
	"strings"
	"unicode/utf8"
)

// ExtractionResult holds the deduplicated representation for a single span.
type ExtractionResult struct {
	Applied       bool
	InputApplied  bool // the input side is extracted (spans.input holds a preview)
	OutputApplied bool // the output side is extracted (spans.output holds a preview)
	InputHashes   []string
	OutputHashes  []string
	ToolDefHashes []string
	ItemScopeID   string
	EnvelopeJSON  string
	InputPreview  string
	OutputPreview string
	Items         []Item // all referenced items (before SeenStore filtering)
}

// extractFaultHook lets tests corrupt a side template between extraction and
// the reconstruct self-check. Always nil outside tests. It is a plain package
// variable read on every extraction, so no test in this package that calls
// ExtractSpan (directly or through the converter) may use t.Parallel.
var extractFaultHook func(side string, t *SideTemplate)

// ExtractSpan inspects a span's input and output JSON payloads and, when at
// least one side contains a recognized message array (or chat object with
// messages/tools), decomposes it into content-addressed Items + an Envelope.
//
// Each side is decided on its own. A side is extracted only when it is valid
// UTF-8 JSON without duplicate top-level keys and ReconstructSide, run on the
// marshalled template exactly as a reader receives it, gives back the exact
// original string. Otherwise the side stays inline in spans.input/output.
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

	env := Envelope{Version: EnvelopeVersion}
	in := extractSide(projectID, scopeID, input, true)
	if in != nil && !in.selfCheck("in", scopeID) {
		in = nil
	}
	out := extractSide(projectID, scopeID, output, false)
	if out != nil && !out.selfCheck("out", scopeID) {
		out = nil
	}
	if in == nil && out == nil {
		return ExtractionResult{}
	}

	var items []Item
	inputHashes, outputHashes, toolHashes := []string{}, []string{}, []string{}
	if in != nil {
		env.Input = in.template
		inputHashes = in.msgRefs
		toolHashes = in.toolRefs
		items = append(items, in.items...)
	}
	if out != nil {
		env.Output = out.template
		outputHashes = out.msgRefs
		items = append(items, out.items...)
	}

	envBytes, err := marshalNoEscape(env)
	if err != nil {
		return ExtractionResult{}
	}

	res := ExtractionResult{
		Applied:       true,
		InputApplied:  in != nil,
		OutputApplied: out != nil,
		InputHashes:   inputHashes,
		OutputHashes:  outputHashes,
		ToolDefHashes: toolHashes,
		ItemScopeID:   scopeID,
		EnvelopeJSON:  string(envBytes),
		Items:         items,
		InputPreview:  input,
		OutputPreview: output,
	}
	if in != nil {
		res.InputPreview = smartMessagePreview(input, in.items, "user", previewMaxBytes)
	}
	if out != nil {
		res.OutputPreview = smartMessagePreview(output, out.items, "assistant", previewMaxBytes)
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

// extractedSide is one side's decomposition before the self-check.
type extractedSide struct {
	original string
	template *SideTemplate
	msgRefs  []string
	toolRefs []string
	items    []Item // messages, then tools, then the system item
}

// selfCheck marshals the template, decodes it as a reader would and requires
// ReconstructSide to return the exact original string.
func (s *extractedSide) selfCheck(side, scopeID string) bool {
	if extractFaultHook != nil {
		extractFaultHook(side, s.template)
	}
	encoded, err := marshalNoEscape(s.template)
	if err != nil {
		return false
	}
	var decoded SideTemplate
	if err := json.Unmarshal(encoded, &decoded); err != nil {
		return false
	}
	got, err := ReconstructSide(&decoded, s.msgRefs, s.toolRefs, ItemsResolver(scopeID, s.items))
	return err == nil && got == s.original
}

// valueSpan is the exact byte range [start,end) of one JSON value inside the
// side's original string.
type valueSpan struct {
	start, end int
	raw        json.RawMessage
}

// member is one top-level object member: its decoded key and value range.
type member struct {
	key   string
	value valueSpan
}

// slot is one extracted value inside the original string.
type slot struct {
	kind       byte
	start, end int
}

// extractSide decomposes one side, or returns nil when the side stays inline.
// Selection rules: a top-level message array; or a top-level object with a
// messages/input/choices array (first match wins) and, on the input side only,
// a tools array and a system/instructions value.
func extractSide(projectID, scopeID, payload string, allowToolsAndSystem bool) *extractedSide {
	trimmed := strings.TrimSpace(payload)
	if len(trimmed) < 2 || !utf8.ValidString(payload) {
		return nil
	}
	s := &extractedSide{original: payload}
	var slots []slot

	switch trimmed[0] {
	case '[':
		elems, ok := scanArray(payload, 0)
		if !ok || len(elems) == 0 || !looksLikeMessageArray(raws(elems)) {
			return nil
		}
		slots = s.addMessages(projectID, scopeID, elems, slots)

	case '{':
		members, ok := scanObject(payload)
		if !ok || len(members) == 0 {
			return nil
		}
		byKey := make(map[string]valueSpan, len(members))
		for _, m := range members {
			byKey[m.key] = m.value
		}

		var msgElems []valueSpan
		for _, candidate := range []string{"messages", "input", "choices"} {
			v, ok := byKey[candidate]
			if !ok {
				continue
			}
			elems, ok := scanArray(payload[v.start:v.end], v.start)
			if !ok || len(elems) == 0 {
				continue
			}
			arr := raws(elems)
			if candidate == "choices" && looksLikeChoicesArray(arr) || candidate != "choices" && looksLikeMessageArray(arr) {
				msgElems = elems
				break
			}
		}

		var toolElems []valueSpan
		var sysValue *valueSpan
		if allowToolsAndSystem {
			if v, ok := byKey["tools"]; ok {
				if elems, ok := scanArray(payload[v.start:v.end], v.start); ok && len(elems) > 0 {
					toolElems = elems
				}
			}
			for _, sk := range []string{"system", "instructions"} {
				if v, ok := byKey[sk]; ok && len(v.raw) > 2 {
					sysValue = &v
					break
				}
			}
		}

		if msgElems == nil && toolElems == nil {
			return nil
		}
		slots = s.addMessages(projectID, scopeID, msgElems, slots)
		for _, e := range toolElems {
			content := payload[e.start:e.end]
			h := HashItem(content)
			s.toolRefs = append(s.toolRefs, ProjectHashPrefix+h)
			s.items = append(s.items, Item{
				ProjectID: projectID,
				ScopeID:   ProjectScopeID,
				ItemHash:  h,
				ItemType:  "tool_def",
				Content:   content,
			})
			slots = append(slots, slot{kind: SlotTool, start: e.start, end: e.end})
		}
		if sysValue != nil {
			content := payload[sysValue.start:sysValue.end]
			h := HashItem(content)
			s.items = append(s.items, Item{
				ProjectID: projectID,
				ScopeID:   ProjectScopeID,
				ItemHash:  h,
				ItemType:  "message",
				Role:      "system",
				Content:   content,
			})
			slots = append(slots, slot{kind: SlotSystem, start: sysValue.start, end: sysValue.end})
			s.template = &SideTemplate{SystemHash: ProjectHashPrefix + h}
		}

	default:
		return nil
	}

	if s.template == nil {
		s.template = &SideTemplate{}
	}
	// Slots come from distinct top-level values or distinct elements of one
	// array, so they never overlap; byte order is the template order.
	sort.Slice(slots, func(i, j int) bool { return slots[i].start < slots[j].start })
	refs := make([]byte, 0, len(slots))
	gaps := make([]string, 0, len(slots)+1)
	pos := 0
	for _, sl := range slots {
		if sl.start < pos {
			return nil
		}
		gaps = append(gaps, payload[pos:sl.start])
		refs = append(refs, sl.kind)
		pos = sl.end
	}
	gaps = append(gaps, payload[pos:])
	s.template.Gaps = gaps
	s.template.Refs = string(refs)
	s.template.Length = utf8.RuneCountInString(payload)
	s.template.Checksum = PayloadChecksum(payload)
	if s.msgRefs == nil {
		s.msgRefs = []string{}
	}
	if s.toolRefs == nil {
		s.toolRefs = []string{}
	}
	return s
}

// addMessages records one item and one SlotMessage per element. System and
// developer messages are project-scoped.
func (s *extractedSide) addMessages(projectID, scopeID string, elems []valueSpan, slots []slot) []slot {
	for _, e := range elems {
		content := s.original[e.start:e.end]
		itemType, role := classifyMessage(e.raw)
		h := HashItem(content)
		targetScope, ref := scopeID, h
		if role == "system" || role == "developer" {
			targetScope, ref = ProjectScopeID, ProjectHashPrefix+h
		}
		s.msgRefs = append(s.msgRefs, ref)
		s.items = append(s.items, Item{
			ProjectID: projectID,
			ScopeID:   targetScope,
			ItemHash:  h,
			ItemType:  itemType,
			Role:      role,
			Content:   content,
		})
		slots = append(slots, slot{kind: SlotMessage, start: e.start, end: e.end})
	}
	return slots
}

// scanArray reads s as exactly one JSON array (surrounding whitespace allowed)
// and returns each element's byte range, offset by base.
func scanArray(s string, base int) ([]valueSpan, bool) {
	dec := json.NewDecoder(strings.NewReader(s))
	if tok, err := dec.Token(); err != nil || tok != json.Delim('[') {
		return nil, false
	}
	var out []valueSpan
	for dec.More() {
		v, ok := decodeValue(dec, s)
		if !ok {
			return nil, false
		}
		v.start += base
		v.end += base
		out = append(out, v)
	}
	if tok, err := dec.Token(); err != nil || tok != json.Delim(']') {
		return nil, false
	}
	return out, atEOF(dec)
}

// scanObject reads s as exactly one JSON object (surrounding whitespace
// allowed) and returns its members in order. Duplicate keys refuse the
// object: a reader would see only one of the values.
func scanObject(s string) ([]member, bool) {
	dec := json.NewDecoder(strings.NewReader(s))
	if tok, err := dec.Token(); err != nil || tok != json.Delim('{') {
		return nil, false
	}
	var out []member
	seen := make(map[string]struct{})
	for dec.More() {
		tok, err := dec.Token()
		if err != nil {
			return nil, false
		}
		key, ok := tok.(string)
		if !ok {
			return nil, false
		}
		if _, dup := seen[key]; dup {
			return nil, false
		}
		seen[key] = struct{}{}
		v, ok := decodeValue(dec, s)
		if !ok {
			return nil, false
		}
		out = append(out, member{key: key, value: v})
	}
	if tok, err := dec.Token(); err != nil || tok != json.Delim('}') {
		return nil, false
	}
	return out, atEOF(dec)
}

// decodeValue decodes the next value and locates its exact bytes in s.
func decodeValue(dec *json.Decoder, s string) (valueSpan, bool) {
	var raw json.RawMessage
	if err := dec.Decode(&raw); err != nil {
		return valueSpan{}, false
	}
	end := int(dec.InputOffset())
	start := end - len(raw)
	if start < 0 || end > len(s) || s[start:end] != string(raw) {
		return valueSpan{}, false
	}
	return valueSpan{start: start, end: end, raw: raw}, true
}

func atEOF(dec *json.Decoder) bool {
	_, err := dec.Token()
	return err == io.EOF
}

func raws(elems []valueSpan) []json.RawMessage {
	out := make([]json.RawMessage, len(elems))
	for i, e := range elems {
		out[i] = e.raw
	}
	return out
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
