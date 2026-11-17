package gemini

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The chat parameters this adapter does not send to the provider (release
// notes, Known limits): the translated request carries none of them, so a
// value the caller sets has no effect here.
func TestR7iChatParametersThisAdapterDoesNotSend(t *testing.T) {
	n, seed, topLogprobs := 3, 7, 2
	pen := 0.5
	lp := true
	req := &models.ChatCompletionRequest{
		Model:            "gemini-2.5-flash",
		Messages:         []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}},
		N:                &n,
		Seed:             &seed,
		PresencePenalty:  &pen,
		FrequencyPenalty: &pen,
		LogitBias:        map[string]int{"50256": -100},
		Logprobs:         &lp,
		TopLogprobs:      &topLogprobs,
		User:             "user-r7i-marker",
		ServiceTier:      "flex",
	}
	out := r7iTranslated(t, req)
	for _, field := range []string{"candidateCount", "\"n\"", "seed", "presence", "frequency", "logit", "logprob", "user-r7i-marker", "service_tier", "serviceTier", "flex"} {
		if strings.Contains(strings.ToLower(out), strings.ToLower(field)) {
			t.Errorf("translated request %s carries %s", out, field)
		}
	}
}

func r7iTranslated(t *testing.T, req *models.ChatCompletionRequest) string {
	t.Helper()
	gr, _ := translateRequest(req)
	b, _ := json.Marshal(gr)
	return string(b)
}
