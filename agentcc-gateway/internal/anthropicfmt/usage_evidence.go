package anthropicfmt

import (
	"encoding/json"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ExtractUsageEvidence retains the presence of counters on the BYOK native
// passthrough path, which does not run the canonical provider adapter.
func ExtractUsageEvidence(body []byte) *models.Usage {
	var response struct {
		Usage *struct {
			Input  *int `json:"input_tokens"`
			Output *int `json:"output_tokens"`
		} `json:"usage"`
	}
	if json.Unmarshal(body, &response) != nil || response.Usage == nil {
		return nil
	}
	return models.NewUsage(response.Usage.Input, response.Usage.Output)
}

// UsageEvidence keeps a partial report partial, rather than converting the
// missing dimension to an explicitly measured zero.
func (s *StreamUsageScanner) UsageEvidence() *models.Usage {
	var prompt, completion *int
	if in, found := firstUsageField(s.head, "input_tokens"); found {
		prompt = &in
	}
	if out, found := lastUsageField(s.tail, "output_tokens"); found {
		completion = &out
	}
	if prompt == nil && completion == nil {
		return nil
	}
	return models.NewUsage(prompt, completion)
}
