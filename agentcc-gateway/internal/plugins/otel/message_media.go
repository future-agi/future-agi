package otel

import (
	"bytes"
	"encoding/json"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// suppressMessageMedia is used only under active masking. Text patterns cannot
// screen encoded audio or reusable media references. Work on export-owned
// copies, preserving transcript/expiry fields for the normal text/metadata pass.
func suppressMessageMedia(msg models.Message) models.Message {
	if msg.Audio != nil {
		audio := *msg.Audio
		if audio.Data != "" {
			audio.Data = "[REDACTED]"
		}
		if audio.ID != "" {
			audio.ID = "[REDACTED]"
		}
		msg.Audio = &audio
	}
	// This is opaque provider JSON, not necessarily a typed image part.
	// Dropping the private copy covers URLs and inline data in every shape.
	msg.Images = nil
	context := imageContent
	if msg.Role == "tool" || msg.Role == "function" {
		context = imageToolEnvelope
	}
	msg.Content = suppressInputAudioJSON(msg.Content, context)
	return msg
}

func suppressInputAudioJSON(raw json.RawMessage, context imageContext) json.RawMessage {
	if len(raw) == 0 || !json.Valid(raw) {
		// Preserve invalid-body omission; the unknown-message fallback is
		// separately suppressed under masking.
		return raw
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var value any
	if decoder.Decode(&value) != nil {
		return json.RawMessage(`"[REDACTED]"`)
	}
	value, changed := suppressInputAudioValue(value, context)
	if !changed {
		return raw
	}
	encoded, err := json.Marshal(value)
	if err != nil {
		return json.RawMessage(`"[REDACTED]"`)
	}
	return encoded
}

// Reuse the image traversal's structured/tool-envelope boundary: a quoted JSON
// example in ordinary text is not media, but serialized tool results are.
// Decoding first also recognizes escaped field names.
func suppressInputAudioValue(value any, context imageContext) (any, bool) {
	if context == imageOpaque {
		return value, false
	}
	changed := false
	switch v := value.(type) {
	case map[string]any:
		for key, child := range v {
			if key == "input_audio" {
				if audio, ok := child.(map[string]any); ok {
					for _, field := range []string{"data", "id"} {
						if _, present := audio[field]; present {
							audio[field] = "[REDACTED]"
							changed = true
						}
					}
				} else {
					v[key] = "[REDACTED]"
					changed = true
					continue
				}
			}
			if masked, didChange := suppressInputAudioValue(child, imageChildContext(v, key, context)); didChange {
				v[key] = masked
				changed = true
			}
		}
	case []any:
		for i, child := range v {
			if masked, didChange := suppressInputAudioValue(child, context); didChange {
				v[i] = masked
				changed = true
			}
		}
	case string:
		if context == imageToolEnvelope {
			masked := string(suppressInputAudioJSON(json.RawMessage(v), imageToolEnvelope))
			return masked, masked != v
		}
	}
	return value, changed
}
