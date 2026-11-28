package trajectory

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"unicode/utf8"
)

// EnvelopeVersion is the only payload_envelope version the collector writes
// and the readers accept.
const EnvelopeVersion = 2

// Slot kinds in SideTemplate.Refs. Each character names where the next
// extracted value comes from.
const (
	SlotMessage byte = 'm' // next entry of the side's message hashes
	SlotTool    byte = 't' // next entry of tool_def_hashes (input side only)
	SlotSystem  byte = 's' // the side's SystemHash
)

// Envelope is the payload_envelope column. The original text of each
// extracted side is kept as literal gaps around ordered slots, so a reader
// rebuilds it by pure concatenation:
//
//	original = g[0] + c(r[0]) + g[1] + ... + c(r[n-1]) + g[n]
//
// where c(r[i]) is the stored content of the item the slot refers to.
type Envelope struct {
	Version int           `json:"v"`
	Input   *SideTemplate `json:"in,omitempty"`
	Output  *SideTemplate `json:"out,omitempty"`
}

// SideTemplate describes one extracted side (input or output).
type SideTemplate struct {
	// Gaps holds len(Refs)+1 literal strings, in byte order.
	Gaps []string `json:"g"`
	// Refs has one SlotMessage/SlotTool/SlotSystem character per slot.
	Refs string `json:"r"`
	// SystemHash is "p:<hash>" when the side has a SlotSystem slot.
	SystemHash string `json:"sh,omitempty"`
	// Length is the original side's length in Unicode code points, what
	// ClickHouse lengthUTF8 returns for the inline column.
	Length int `json:"il"`
	// Checksum is PayloadChecksum of the original side.
	Checksum string `json:"x"`
}

// ErrReconstruct is wrapped by every ReconstructSide failure.
var ErrReconstruct = errors.New("trajectory: cannot reconstruct payload")

// ParseEnvelope decodes a payload_envelope value and rejects any version
// other than EnvelopeVersion.
func ParseEnvelope(raw string) (Envelope, error) {
	var env Envelope
	if err := json.Unmarshal([]byte(raw), &env); err != nil {
		return Envelope{}, fmt.Errorf("%w: envelope: %v", ErrReconstruct, err)
	}
	if env.Version != EnvelopeVersion {
		return Envelope{}, fmt.Errorf("%w: envelope version %d", ErrReconstruct, env.Version)
	}
	return env, nil
}

// ReconstructSide rebuilds one side from its template. msgRefs are the side's
// ordered message refs (spans.input_item_hashes or output_item_hashes),
// toolRefs the ordered tool refs (spans.tool_def_hashes, input side only) and
// resolve maps a ref ("<hash>" or "p:<hash>") to the stored item content.
//
// It fails when the counts do not match the template, an item is missing, or
// the result does not match the template's length and checksum.
func ReconstructSide(t *SideTemplate, msgRefs, toolRefs []string, resolve func(ref string) (string, bool)) (string, error) {
	if t == nil {
		return "", fmt.Errorf("%w: no template", ErrReconstruct)
	}
	if len(t.Gaps) != len(t.Refs)+1 {
		return "", fmt.Errorf("%w: %d gaps for %d slots", ErrReconstruct, len(t.Gaps), len(t.Refs))
	}
	if n := strings.Count(t.Refs, string(SlotMessage)); n != len(msgRefs) {
		return "", fmt.Errorf("%w: %d message slots for %d hashes", ErrReconstruct, n, len(msgRefs))
	}
	if n := strings.Count(t.Refs, string(SlotTool)); n != len(toolRefs) {
		return "", fmt.Errorf("%w: %d tool slots for %d hashes", ErrReconstruct, n, len(toolRefs))
	}
	size := 0
	for _, g := range t.Gaps {
		size += len(g)
	}
	contents := make([]string, len(t.Refs))
	mi, ti := 0, 0
	for i := 0; i < len(t.Refs); i++ {
		var ref string
		switch t.Refs[i] {
		case SlotMessage:
			ref = msgRefs[mi]
			mi++
		case SlotTool:
			ref = toolRefs[ti]
			ti++
		case SlotSystem:
			if t.SystemHash == "" {
				return "", fmt.Errorf("%w: system slot without sh", ErrReconstruct)
			}
			ref = t.SystemHash
		default:
			return "", fmt.Errorf("%w: unknown slot %q", ErrReconstruct, t.Refs[i])
		}
		content, ok := resolve(ref)
		if !ok {
			return "", fmt.Errorf("%w: item %s missing", ErrReconstruct, ref)
		}
		contents[i] = content
		size += len(content)
	}
	var b strings.Builder
	b.Grow(size)
	for i, content := range contents {
		b.WriteString(t.Gaps[i])
		b.WriteString(content)
	}
	b.WriteString(t.Gaps[len(t.Gaps)-1])
	out := b.String()
	if utf8.RuneCountInString(out) != t.Length || PayloadChecksum(out) != t.Checksum {
		return "", fmt.Errorf("%w: length or checksum mismatch", ErrReconstruct)
	}
	return out, nil
}

// ItemsResolver resolves refs against items: a "p:" ref looks under
// ProjectScopeID, any other ref under scopeID.
func ItemsResolver(scopeID string, items []Item) func(ref string) (string, bool) {
	byKey := make(map[[2]string]string, len(items))
	for _, it := range items {
		byKey[[2]string{it.ScopeID, it.ItemHash}] = it.Content
	}
	return func(ref string) (string, bool) {
		scope, hash := scopeID, ref
		if strings.HasPrefix(ref, ProjectHashPrefix) {
			scope, hash = ProjectScopeID, ref[len(ProjectHashPrefix):]
		}
		content, ok := byKey[[2]string{scope, hash}]
		return content, ok
	}
}
