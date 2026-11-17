package anthropic

import "encoding/json"

// MarshalJSON preserves required empty fields for the selected block variant.
// Removing omitempty on the union's string fields would instead add text to
// tool_use blocks (and thinking/signature to text blocks). SDK stream helpers
// append deltas to these initial values, so missing text/thinking is not valid.
func (b ContentBlock) MarshalJSON() ([]byte, error) {
	type alias ContentBlock
	wire := struct {
		alias
		Text      *string `json:"text,omitempty"`
		Thinking  *string `json:"thinking,omitempty"`
		Signature *string `json:"signature,omitempty"`
	}{alias: alias(b)}
	switch b.Type {
	case "text":
		wire.Text = &b.Text
	case "thinking":
		wire.Thinking = &b.Thinking
		wire.Signature = &b.Signature
	}
	return json.Marshal(wire)
}
