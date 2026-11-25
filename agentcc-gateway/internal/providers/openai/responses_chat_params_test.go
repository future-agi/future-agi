package openai

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Decision B deliberately replaces the original direct-caller 400 assertions:
// preserve dispatch, omit unsupported settings and report only their names.
func TestChatOnlyParametersOverResponses(t *testing.T) {
	seed, top, on, n := 0, 3, true, 2
	penalty := 0.5
	cases := []struct {
		name string
		set  func(*models.ChatCompletionRequest)
	}{
		{"stop", func(r *models.ChatCompletionRequest) { r.Stop = json.RawMessage(`["secret-stop\r\nvalue"]`) }},
		{"seed", func(r *models.ChatCompletionRequest) { r.Seed = &seed }},
		{"logprobs", func(r *models.ChatCompletionRequest) { r.Logprobs = &on }},
		{"top_logprobs", func(r *models.ChatCompletionRequest) { r.TopLogprobs = &top }},
		{"frequency_penalty", func(r *models.ChatCompletionRequest) { r.FrequencyPenalty = &penalty }},
		{"presence_penalty", func(r *models.ChatCompletionRequest) { r.PresencePenalty = &penalty }},
		{"logit_bias", func(r *models.ChatCompletionRequest) { r.LogitBias = map[string]int{"50256": -100} }},
		{"n", func(r *models.ChatCompletionRequest) { r.N = &n }},
	}
	p := &Provider{openAIAPI: true}
	for _, tc := range cases {
		for _, from := range []string{"", "anthropic", "google"} {
			for _, stream := range []bool{false, true} {
				t.Run(fmt.Sprintf("%s/from=%s/stream=%t", tc.name, from, stream), func(t *testing.T) {
					req := toolTurnRequest("gpt-5.6-terra")
					tc.set(req)
					req.TranslatedFrom, req.Stream = from, stream
					got, err := toResponsesRequest(req, stream)
					if err != nil {
						t.Fatalf("decision B must dispatch, not refuse: %v", err)
					}
					body, _ := json.Marshal(got)
					var sent map[string]json.RawMessage
					_ = json.Unmarshal(body, &sent)
					for _, key := range []string{"stop", "seed", "logprobs", "top_logprobs", "frequency_penalty", "presence_penalty", "logit_bias", "n", "include"} {
						if _, ok := sent[key]; ok {
							t.Errorf("%s sent over Responses: %s", key, body)
						}
					}
					if drops := p.ResponsesDrops(req); len(drops) != 1 || drops[0] != tc.name {
						t.Errorf("drops = %v, want only %s", drops, tc.name)
					}
				})
			}
		}
	}
}

func TestChatOnlyParametersThatChangeNothingAreNotRefused(t *testing.T) {
	zero, off, none, one := 0.0, false, 0, 1
	req := toolTurnRequest("gpt-5.6-terra")
	req.FrequencyPenalty, req.PresencePenalty = &zero, &zero
	req.Logprobs, req.TopLogprobs = &off, &none
	req.LogitBias = map[string]int{"50256": 0}
	req.N = &one
	p := &Provider{openAIAPI: true}
	for _, stop := range []string{`[]`, `""`, `[""]`, `null`} {
		req.Stop = json.RawMessage(stop)
		if _, err := toResponsesRequest(req, false); err != nil {
			t.Fatalf("stop=%s: %v", stop, err)
		}
		if drops := p.ResponsesDrops(req); len(drops) != 0 {
			t.Errorf("stop=%s: no-op settings reported as %v", stop, drops)
		}
	}
}

// Decision B changes the old combined-refusal assertion into combined notes.
func TestChatOnlyParametersAreNamedTogether(t *testing.T) {
	seed := 1
	req := toolTurnRequest("gpt-5.6-terra")
	req.Stop = json.RawMessage(`"END"`)
	req.Seed = &seed
	mustResponsesRequest(t, req)
	p := &Provider{openAIAPI: true}
	if got := strings.Join(p.ResponsesDrops(req), ","); got != "stop,seed" {
		t.Fatalf("drops = %q", got)
	}
}

func TestResponsesDropsRequireAValidResponsesAttempt(t *testing.T) {
	p := &Provider{openAIAPI: true}
	for _, tc := range []struct {
		name string
		edit func(*models.ChatCompletionRequest)
	}{
		{"reasoning off", func(r *models.ChatCompletionRequest) { r.Extra["reasoning_effort"] = json.RawMessage(`"none"`) }},
		{"tools removed", func(r *models.ChatCompletionRequest) { r.Tools = nil }},
		{"other model", func(r *models.ChatCompletionRequest) { r.Model = "gpt-5.5" }},
		{"invalid tool choice", func(r *models.ChatCompletionRequest) { r.ToolChoice = json.RawMessage(`"sometimes"`) }},
		{"malformed supported field", func(r *models.ChatCompletionRequest) { r.Extra["metadata"] = json.RawMessage(`{`) }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			req := toolTurnRequest("gpt-5.6-terra")
			req.Stop = json.RawMessage(`"secret"`)
			req.Extra = make(map[string]json.RawMessage)
			tc.edit(req)
			if drops := p.ResponsesDrops(req); len(drops) != 0 {
				t.Fatalf("no dispatch should report no drops, got %v", drops)
			}
		})
	}
	req := toolTurnRequest("gpt-5.6-terra")
	req.Stop = json.RawMessage(`"secret"`)
	if drops := (&Provider{}).ResponsesDrops(req); len(drops) != 0 {
		t.Fatalf("unofficial upstream reports %v", drops)
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
