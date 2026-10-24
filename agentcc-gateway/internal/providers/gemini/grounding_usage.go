package gemini

import (
	"bytes"
	"encoding/json"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// groundingMarker is in every body that carries grounding metadata, so a
// body without it is not decoded a second time.
var groundingMarker = []byte(`"groundingMetadata"`)

// GroundingUsage counts a native generateContent body's grounding into
// server-tool usage, as groundingUsage does for the chat translation.
// nil when the body is not JSON or was not
// grounded.
func GroundingUsage(body []byte) *models.ServerToolUsage {
	if !bytes.Contains(body, groundingMarker) {
		return nil
	}
	var r struct {
		Candidates []geminiCandidate `json:"candidates"`
	}
	if json.Unmarshal(body, &r) != nil {
		return nil
	}
	return groundingUsage(r.Candidates)
}
