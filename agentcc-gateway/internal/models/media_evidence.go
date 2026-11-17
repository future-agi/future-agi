package models

import "encoding/json"

// MediaEvidence records native observations before lossy chat translation. It
// is NOT a price contract. In particular an audio part is not proof of a billed
// song/clip, and an image part is not proof of an image-token tariff. No asset
// bytes, prompts or URLs survive in the evidence.
type MediaEvidence struct {
	ModelVersion       string
	IdentityConflict   bool
	OutputImages       *int // only a complete, validated non-stream response
	ObservedImageParts int
	ObservedAudioParts int
	InvalidImage       bool
	Stream             bool
}

// Media-only evidence must not invent measured zero tokens on the wire either.
// Other usage encoders keep their existing shape.
func mediaUsageJSON(u Usage, data []byte) []byte {
	if u.Media == nil || !u.usageDecoded {
		return data
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(data, &fields) != nil {
		return data
	}
	if !u.HasPromptTokens() {
		delete(fields, "prompt_tokens")
	}
	if !u.HasCompletionTokens() {
		delete(fields, "completion_tokens")
	}
	if !u.HasPromptTokens() && !u.HasCompletionTokens() && u.TotalTokens == 0 {
		delete(fields, "total_tokens")
	}
	encoded, err := json.Marshal(fields)
	if err != nil {
		return data
	}
	return encoded
}

// MergeMediaEvidence retains earlier media/identity observations without
// interpreting repeated stream payloads as additional billable generations.
// Stream counts remain unknown: this adapter has no stable per-image IDs.
func MergeMediaEvidence(a, b *MediaEvidence) *MediaEvidence {
	if a == nil {
		return b
	}
	if b == nil {
		return a
	}
	m := *b
	if m.ModelVersion == "" {
		m.ModelVersion = a.ModelVersion
	}
	m.IdentityConflict = a.IdentityConflict || b.IdentityConflict || (a.ModelVersion != "" && b.ModelVersion != "" && a.ModelVersion != b.ModelVersion)
	m.InvalidImage = a.InvalidImage || b.InvalidImage
	m.Stream = a.Stream || b.Stream
	// These are observations, not additive counters. Use the maximum as a
	// lower bound rather than counting retransmitted cumulative payloads twice.
	m.ObservedImageParts = max(a.ObservedImageParts, b.ObservedImageParts)
	m.ObservedAudioParts = max(a.ObservedAudioParts, b.ObservedAudioParts)
	if m.Stream || m.InvalidImage || m.IdentityConflict {
		m.OutputImages = nil
	}
	return &m
}
