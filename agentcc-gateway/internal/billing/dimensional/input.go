package dimensional

import (
	"bytes"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// InputImageOccurrences validates the normalized request's content parts. It
// counts occurrences (including repeated history/tool images), not unique URLs.
// This helper never fetches remote assets. Other media, raw native extensions,
// opaque references and remote URLs need a qualified bounded adapter instead.
// Calling it does not certify token/search/output bounds or post-transform
// equivalence: the dispatch adapter must enforce those separately.
func InputImageOccurrences(req *models.ChatCompletionRequest) (int64, error) {
	if req == nil || len(req.Extra) != 0 {
		return 0, fmt.Errorf("missing or opaque translated request")
	}
	var count int64
	totalBytes := 0
	for _, msg := range req.Messages {
		totalBytes += len(msg.Content)
		if totalBytes > 32*1024*1024 || len(msg.ThinkingBlocks) != 0 || len(msg.Annotations) != 0 {
			return 0, fmt.Errorf("unbounded or opaque message")
		}
		if len(msg.Content) == 0 || string(msg.Content) == "null" {
			continue
		}
		var text string
		if json.Unmarshal(msg.Content, &text) == nil {
			continue
		}
		var parts []struct {
			Type  string  `json:"type"`
			Text  *string `json:"text"`
			Image *struct {
				URL    string `json:"url"`
				Detail string `json:"detail,omitempty"`
			} `json:"image_url"`
		}
		decoder := json.NewDecoder(bytes.NewReader(msg.Content))
		decoder.DisallowUnknownFields()
		if err := decoder.Decode(&parts); err != nil {
			return 0, fmt.Errorf("unsupported content shape")
		}
		for _, part := range parts {
			switch part.Type {
			case "text":
			case "image_url":
				count++
				if count > 64 || part.Image == nil {
					return 0, fmt.Errorf("unbounded image occurrence")
				}
				prefix, data, ok := strings.Cut(part.Image.URL, ",")
				if !ok || !strings.HasPrefix(prefix, "data:") || !strings.HasSuffix(prefix, ";base64") {
					return 0, fmt.Errorf("remote or opaque image has no metering bound")
				}
				mime := strings.TrimSuffix(strings.TrimPrefix(prefix, "data:"), ";base64")
				if _, _, err := models.MeterImageHeader(mime, data); err != nil {
					return 0, err
				}
			default:
				return 0, fmt.Errorf("unsupported media content type %q", part.Type)
			}
		}
	}
	return count, nil
}
