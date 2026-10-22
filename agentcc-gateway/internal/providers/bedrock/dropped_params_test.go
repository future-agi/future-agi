package bedrock

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The chat parameters this adapter does not send to the provider (release
// notes, Known limits): the translated request carries none of them, so a
// value the caller sets has no effect here.
func TestChatParametersThisAdapterDoesNotSend(t *testing.T) {
	n, seed, topLogprobs := 3, 7, 2
	pen := 0.5
	lp := true
	req := &models.ChatCompletionRequest{
		Model:            "anthropic.claude-3-5-sonnet-20240620-v1:0",
		Messages:         []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}},
		N:                &n,
		Seed:             &seed,
		PresencePenalty:  &pen,
		FrequencyPenalty: &pen,
		LogitBias:        map[string]int{"50256": -100},
		Logprobs:         &lp,
		TopLogprobs:      &topLogprobs,
		User:             "user-dropped-marker",
		ServiceTier:      "flex",
	}
	out := translatedJSON(t, req)
	for _, field := range []string{"candidateCount", "\"n\"", "seed", "presence", "frequency", "logit", "logprob", "user-dropped-marker", "service_tier", "serviceTier", "flex"} {
		if strings.Contains(strings.ToLower(out), strings.ToLower(field)) {
			t.Errorf("translated request %s carries %s", out, field)
		}
	}
}

func translatedJSON(t *testing.T, req *models.ChatCompletionRequest) string {
	t.Helper()
	br, _ := translateRequest(req)
	cr, _ := buildConverseRequest(req)
	b1, _ := json.Marshal(br)
	b2, _ := json.Marshal(cr)
	return string(b1) + string(b2)
}
