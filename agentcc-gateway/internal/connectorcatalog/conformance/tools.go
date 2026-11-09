package conformance

import (
	"encoding/json"
	"fmt"
	"slices"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
	"github.com/futureagi/agentcc-gateway/internal/mcp/vserver"
)

// hintNames are the four MCP tool annotation hints a manifest declares.
var hintNames = []string{"readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"}

// Tool is one tool the mock serves and how the gateway must treat it.
type Tool struct {
	Name     string `json:"name"`     // the upstream's name
	Exposed  string `json:"exposed"`  // its name through the virtual server
	Declared bool   `json:"declared"` // the manifest declares it
	// Posture is the manifest's posture for it, the provider default when
	// the manifest does not declare it.
	Posture string `json:"posture"`
	// Denied: the virtual server's access rule refuses it, because its
	// posture is deny, or ask until M26 (§2.8).
	Denied  bool `json:"denied"`
	Generic bool `json:"generic,omitempty"`
	// Hints are the annotation hints the mock serves; the gateway passes
	// them through unchanged.
	Hints map[string]bool `json:"hints"`
	// Arguments are what the harness calls it with: the recorded call's,
	// else a value of each argument its input schema requires.
	Arguments json.RawMessage `json:"arguments"`
	// ResultIsError: the canned result the mock answers with is isError.
	ResultIsError bool `json:"result_is_error,omitempty"`

	def    json.RawMessage // the definition the mock lists
	result json.RawMessage // the canned result, when there is one
}

// fixture is the tool's entry in the mock's tools.json: its definition plus
// the mock-only result key.
func (t *Tool) fixture() (json.RawMessage, error) {
	var obj map[string]json.RawMessage
	if err := json.Unmarshal(t.def, &obj); err != nil {
		return nil, fmt.Errorf("tool %s: %w", t.Name, err)
	}
	if len(t.result) > 0 {
		obj["result"] = t.result
	}
	return json.Marshal(obj)
}

// gatewayRefuses is the gateway's decision before M26 (§2.8): deny is a deny
// glob, and ask cannot be enforced, so it is held as deny too.
func gatewayRefuses(posture string) bool {
	return posture == manifest.PostureDeny || posture == manifest.PostureAsk
}

// declaredTools are a provider's curated and generated tools as the mock
// serves them when there is no cassette.
func declaredTools(p *manifest.Provider) ([]*Tool, error) {
	var out []*Tool
	for _, mt := range p.AllTools() {
		def := map[string]any{"name": mt.Name, "annotations": declaredHints(mt.Annotations)}
		if mt.Title != "" {
			def["title"] = mt.Title
		}
		if mt.Description != "" {
			def["description"] = mt.Description
		}
		def["inputSchema"] = json.RawMessage(`{"type":"object"}`)
		if len(mt.InputSchema) > 0 {
			def["inputSchema"] = mt.InputSchema
		}
		t := &Tool{Name: mt.Name, Declared: true, Hints: declaredHints(mt.Annotations)}
		if len(mt.OutputSchema) > 0 {
			def["outputSchema"] = mt.OutputSchema
			result, err := structuredResult(mt.OutputSchema)
			if err != nil {
				return nil, fmt.Errorf("tool %s: %w", mt.Name, err)
			}
			t.result = result
		}
		raw, err := json.Marshal(def)
		if err != nil {
			return nil, fmt.Errorf("tool %s: %w", mt.Name, err)
		}
		t.def = raw
		t.Arguments = exampleArguments(def["inputSchema"].(json.RawMessage))
		out = append(out, t)
	}
	return out, nil
}

// structuredResult is a canned result for a tool with an output schema: the
// structured content an MCP server must send, and the same JSON as text.
func structuredResult(outputSchema json.RawMessage) (json.RawMessage, error) {
	value := exampleFor(outputSchema, 0)
	text, err := json.Marshal(value)
	if err != nil {
		return nil, err
	}
	return json.Marshal(map[string]any{
		"content":           []any{map[string]any{"type": "text", "text": string(text)}},
		"structuredContent": value,
	})
}

func declaredHints(a manifest.Annotations) map[string]bool {
	return map[string]bool{
		"readOnlyHint": a.ReadOnlyHint, "destructiveHint": a.DestructiveHint,
		"idempotentHint": a.IdempotentHint, "openWorldHint": a.OpenWorldHint,
	}
}

// servedHints reads the four hints a tool definition carries; a hint the
// definition leaves out is absent from the map.
func servedHints(def json.RawMessage) map[string]bool {
	var obj struct {
		Annotations map[string]json.RawMessage `json:"annotations"`
	}
	hints := map[string]bool{}
	if json.Unmarshal(def, &obj) != nil {
		return hints
	}
	for _, name := range hintNames {
		var v bool
		if raw, ok := obj.Annotations[name]; ok && json.Unmarshal(raw, &v) == nil {
			hints[name] = v
		}
	}
	return hints
}

// decide sets each tool's exposed name, posture and decision, and sorts the
// tools by exposed name, the order the gateway lists them in.
func decide(serverID string, p *manifest.Provider, tools []*Tool) {
	declared := map[string]manifest.Tool{}
	for _, mt := range p.AllTools() {
		declared[mt.Name] = mt
	}
	for _, t := range tools {
		t.Exposed = vserver.EncodePrefixed(serverID, t.Name)
		t.Posture = p.Tools.DefaultPosture
		if mt, ok := declared[t.Name]; ok {
			t.Declared, t.Generic = true, mt.Generic
			t.Posture = mt.EffectivePosture(p.Tools.DefaultPosture)
		}
		t.Denied = gatewayRefuses(t.Posture)
	}
	slices.SortFunc(tools, func(a, b *Tool) int { return strings.Compare(a.Exposed, b.Exposed) })
}

// drift lists how a recording departs from the manifest: a declared tool it
// does not serve, or serves with other hints than the manifest declares.
func drift(p *manifest.Provider, recorded []*Tool) []string {
	byName := map[string]*Tool{}
	for _, t := range recorded {
		byName[t.Name] = t
	}
	var out []string
	for _, mt := range p.AllTools() {
		t, ok := byName[mt.Name]
		if !ok {
			out = append(out, fmt.Sprintf("the manifest declares %s, which the recording does not serve", mt.Name))
			continue
		}
		if want := declaredHints(mt.Annotations); !sameHints(t.Hints, want) {
			out = append(out, fmt.Sprintf("%s: the recording serves hints %s, the manifest declares %s",
				mt.Name, hintsString(t.Hints), hintsString(want)))
		}
	}
	return out
}

func sameHints(a, b map[string]bool) bool {
	if len(a) != len(b) {
		return false
	}
	for k, v := range a {
		if w, ok := b[k]; !ok || w != v {
			return false
		}
	}
	return true
}

func hintsString(h map[string]bool) string {
	parts := make([]string, 0, len(hintNames))
	for _, name := range hintNames {
		if v, ok := h[name]; ok {
			parts = append(parts, fmt.Sprintf("%s=%t", name, v))
		}
	}
	return "{" + strings.Join(parts, " ") + "}"
}
