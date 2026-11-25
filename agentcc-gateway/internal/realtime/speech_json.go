package realtime

import (
	"bytes"
	"encoding/json"
	"io"
	"strings"
)

// validateSpeechJSON rejects ambiguous wire objects before struct decoding. Go's
// decoder otherwise accepts case-insensitive aliases and last-wins duplicates,
// whereas speech peers consume the original, case-sensitive protocol keys.
func validateSpeechJSON(data []byte) error {
	if len(data) > maxGuardrailBytes {
		return speechRefused()
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	if err := speechJSONValue(decoder, 0); err != nil {
		return err
	}
	if _, err := decoder.Token(); err != io.EOF {
		return speechRefused()
	}
	return nil
}

func speechJSONValue(decoder *json.Decoder, depth int) error {
	if depth > 64 {
		return speechRefused()
	}
	token, err := decoder.Token()
	if err != nil {
		return speechRefused()
	}
	delim, container := token.(json.Delim)
	if !container {
		if depth == 0 {
			return speechRefused()
		}
		return nil
	}
	switch delim {
	case '{':
		seen := make(map[string]bool)
		for decoder.More() {
			token, err := decoder.Token()
			key, ok := token.(string)
			if err != nil || !ok || seen[key] {
				return speechRefused()
			}
			seen[key] = true
			for _, canonical := range []string{"type", "text", "transcript", "model_id", "context_id", "continue", "cancel", "is_final", "speech_final", "channel", "channel_index", "alternatives"} {
				if key != canonical && strings.EqualFold(key, canonical) {
					return speechRefused()
				}
			}
			if err := speechJSONValue(decoder, depth+1); err != nil {
				return err
			}
		}
	case '[':
		if depth == 0 {
			return speechRefused()
		}
		for decoder.More() {
			if err := speechJSONValue(decoder, depth+1); err != nil {
				return err
			}
		}
	default:
		return speechRefused()
	}
	if _, err := decoder.Token(); err != nil {
		return speechRefused()
	}
	return nil
}
