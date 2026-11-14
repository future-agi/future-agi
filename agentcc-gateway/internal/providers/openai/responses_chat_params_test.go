package openai

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Each chat-only parameter the Responses API has no field for: a caller's own
// chat request is refused naming it; a translated request is carried without
// it.
func TestChatOnlyParametersOverResponses(t *testing.T) {
	seed, top, on := 7, 3, true
	penalty := 0.5
	cases := []struct {
		name string
		set  func(*models.ChatCompletionRequest)
	}{
		{"stop", func(r *models.ChatCompletionRequest) { r.Stop = json.RawMessage(`["\n\nHuman:"]`) }},
		{"seed", func(r *models.ChatCompletionRequest) { r.Seed = &seed }},
		{"logprobs", func(r *models.ChatCompletionRequest) { r.Logprobs = &on }},
		{"top_logprobs", func(r *models.ChatCompletionRequest) { r.TopLogprobs = &top }},
		{"frequency_penalty", func(r *models.ChatCompletionRequest) { r.FrequencyPenalty = &penalty }},
		{"presence_penalty", func(r *models.ChatCompletionRequest) { r.PresencePenalty = &penalty }},
		{"logit_bias", func(r *models.ChatCompletionRequest) { r.LogitBias = map[string]int{"50256": -100} }},
	}
	for _, tc := range cases {
		t.Run(tc.name+"/direct", func(t *testing.T) {
			req := toolTurnRequest("gpt-5.6-terra")
			tc.set(req)
			_, err := toResponsesRequest(req, false)
			if err == nil || err.Code != unsupportedCode || err.Status != 400 {
				t.Fatalf("got %v, want a 400 %s", err, unsupportedCode)
			}
			if !strings.Contains(err.Message, "cannot carry "+tc.name+".") ||
				!strings.Contains(err.Message, `reasoning_effort to "none"`) {
				t.Errorf("message %q does not name %s with the remedy", err.Message, tc.name)
			}
		})
		for _, from := range []string{"anthropic", "google"} {
			t.Run(tc.name+"/translated-"+from, func(t *testing.T) {
				req := toolTurnRequest("gpt-5.6-terra")
				tc.set(req)
				req.TranslatedFrom = from
				got := mustResponsesRequest(t, req)
				body, _ := json.Marshal(got)
				for _, key := range []string{`"stop"`, `"seed"`, `"logprobs"`, `"top_logprobs"`, `"frequency_penalty"`, `"presence_penalty"`, `"logit_bias"`} {
					if strings.Contains(string(body), key) {
						t.Errorf("%s sent over Responses: %s", key, body)
					}
				}
			})
		}
	}
}

// Values that change nothing are not refused.
func TestChatOnlyParametersThatChangeNothingAreNotRefused(t *testing.T) {
	zero, off, none := 0.0, false, 0
	req := toolTurnRequest("gpt-5.6-terra")
	req.Stop = json.RawMessage(`[]`)
	req.FrequencyPenalty, req.PresencePenalty = &zero, &zero
	req.Logprobs, req.TopLogprobs = &off, &none
	req.LogitBias = map[string]int{"50256": 0}
	if _, err := toResponsesRequest(req, false); err != nil {
		t.Fatalf("got %v", err)
	}
	req.Stop = json.RawMessage(`""`)
	if _, err := toResponsesRequest(req, false); err != nil {
		t.Fatalf("empty stop string: %v", err)
	}
}

// All the parameters a request sets are named at once.
func TestChatOnlyParametersAreNamedTogether(t *testing.T) {
	seed := 1
	req := toolTurnRequest("gpt-5.6-terra")
	req.Stop = json.RawMessage(`"END"`)
	req.Seed = &seed
	_, err := toResponsesRequest(req, false)
	if err == nil || !strings.Contains(err.Message, "cannot carry stop, seed.") {
		t.Fatalf("got %v", err)
	}
}

// The stream's first chunk, sent on response.created, is marked as the
// acknowledgement; the first output delta is not.
func TestResponsesStreamMarksTheAcknowledgement(t *testing.T) {
	c := responsesStreamConverter{model: "gpt-5.6-terra"}
	created, _ := c.convert(&responsesStreamEvent{Type: "response.created", Response: &responsesResponse{ID: "resp_1"}})
	if len(created) != 1 || !created[0].Acknowledgement {
		t.Fatalf("response.created chunk = %+v, want one acknowledgement", created)
	}
	text, _ := c.convert(&responsesStreamEvent{Type: "response.output_text.delta", Delta: "Hi"})
	if len(text) != 1 || text[0].Acknowledgement {
		t.Fatalf("output delta chunk = %+v, want output", text)
	}
	if b, _ := json.Marshal(created[0]); strings.Contains(string(b), "cknowledgement") {
		t.Errorf("the mark reached the wire: %s", b)
	}
}
