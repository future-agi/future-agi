package gemini

import (
	"encoding/json"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// collectMedia inspects all candidates before translation selects the first.
// Header parsing is bounded and never decompresses an image bitmap. Unknown
// formats/invalid bytes remain unknown, never a measured zero generation.
func collectMedia(resp *geminiResponse, stream bool) *models.MediaEvidence {
	m := &models.MediaEvidence{ModelVersion: resp.ModelVersion, Stream: stream}
	count := 0
	complete := !stream && len(resp.Candidates) > 0
	for _, candidate := range resp.Candidates {
		complete = complete && candidate.FinishReason != ""
		for _, part := range candidate.Content.Parts {
			if part.InlineData == nil {
				continue
			}
			data := part.InlineData
			if strings.HasPrefix(data.MimeType, "audio/") {
				m.ObservedAudioParts++
				continue
			}
			if !strings.HasPrefix(data.MimeType, "image/") {
				continue
			}
			m.ObservedImageParts++
			if m.ObservedImageParts > 64 || len(data.Data) > 8*1024*1024 {
				m.InvalidImage = true
				continue
			}
			_, _, err := models.MeterImageHeader(data.MimeType, data.Data)
			if err != nil {
				m.InvalidImage = true
				continue
			}
			count++
		}
	}
	if complete && !m.InvalidImage {
		m.OutputImages = &count
	}
	return m
}

func attachMedia(u **models.Usage, resp *geminiResponse, stream bool) {
	m := collectMedia(resp, stream)
	if m.ModelVersion == "" && m.ObservedImageParts == 0 && m.ObservedAudioParts == 0 {
		return
	}
	if *u == nil {
		// Decode {} so absent token counts cannot masquerade as measured zero.
		*u = &models.Usage{}
		_ = json.Unmarshal([]byte(`{}`), *u)
	}
	(*u).Media = m
}
