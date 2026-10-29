package openai

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// A tool-using request as the /v1/messages translator builds it: a system
// prompt, a user turn with an image, an assistant tool call, its result, and a
// follow-up.
func toolTurnRequest(model string) *models.ChatCompletionRequest {
	maxTokens := 1024
	return &models.ChatCompletionRequest{
		Model: model,
		Messages: []models.Message{
			{Role: "system", Content: json.RawMessage(`"You are helpful."`)},
			{Role: "user", Content: json.RawMessage(`[{"type":"text","text":"Weather here?"},{"type":"image_url","image_url":{"url":"data:image/png;base64,AAAA"}}]`)},
			{Role: "assistant", ToolCalls: []models.ToolCall{{
				ID: "call_1", Type: "function",
				Function: models.FunctionCall{Name: "get_weather", Arguments: `{"city":"Paris"}`},
			}}},
			{Role: "tool", ToolCallID: "call_1", Content: json.RawMessage(`"sunny"`)},
			{Role: "user", Content: json.RawMessage(`"And tomorrow?"`)},
		},
		Tools: []models.Tool{{Type: "function", Function: models.ToolFunction{
			Name:        "get_weather",
			Description: "Weather for a city",
			Parameters:  json.RawMessage(`{"type":"object","properties":{"city":{"type":"string"}}}`),
		}}},
		ToolChoice: json.RawMessage(`{"type":"function","function":{"name":"get_weather"}}`),
		MaxTokens:  &maxTokens,
		Stop:       json.RawMessage(`["\n\nHuman:"]`),
	}
}

func TestChatNeedsResponses(t *testing.T) {
	withTools := toolTurnRequest("gpt-5.6-terra")
	noTools := toolTurnRequest("gpt-5.6-terra")
	noTools.Tools = nil

	tests := []struct {
		name     string
		official bool
		req      *models.ChatCompletionRequest
		want     bool
	}{
		{"gpt-5.6 with tools on the official API", true, withTools, true},
		{"the family name alone", true, toolTurnRequest("gpt-5.6"), true},
		{"gpt-5.6 without tools stays on chat completions", true, noTools, false},
		{"gpt-5.5 takes tools on chat completions", true, toolTurnRequest("gpt-5.5"), false},
		{"a longer version number is another family", true, toolTurnRequest("gpt-5.60"), false},
		{"another OpenAI-format upstream may not serve /v1/responses", false, withTools, false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			p := newTestProvider(t, "http://upstream.invalid")
			p.openAIAPI = tt.official
			if got := p.ChatNeedsResponses(tt.req); got != tt.want {
				t.Errorf("ChatNeedsResponses = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestToResponsesRequest(t *testing.T) {
	body, err := json.Marshal(toResponsesRequest(toolTurnRequest("gpt-5.6-terra"), false))
	if err != nil {
		t.Fatal(err)
	}
	var got map[string]any
	if err := json.Unmarshal(body, &got); err != nil {
		t.Fatal(err)
	}

	for _, key := range []string{"messages", "stop", "max_tokens"} {
		if _, ok := got[key]; ok {
			t.Errorf("%q is not a Responses field but was sent", key)
		}
	}
	if got["store"] != false {
		t.Errorf("store = %v, want false", got["store"])
	}
	if got["max_output_tokens"] != float64(1024) {
		t.Errorf("max_output_tokens = %v, want 1024", got["max_output_tokens"])
	}

	input := got["input"].([]any)
	var kinds []string
	for _, raw := range input {
		item := raw.(map[string]any)
		kind := item["type"].(string)
		if role, ok := item["role"]; ok {
			kind += ":" + role.(string)
		}
		kinds = append(kinds, kind)
	}
	want := "message:system message:user function_call function_call_output message:user"
	if strings.Join(kinds, " ") != want {
		t.Errorf("input = %v, want %s", kinds, want)
	}

	user := input[1].(map[string]any)["content"].([]any)
	if user[0].(map[string]any)["type"] != "input_text" || user[1].(map[string]any)["type"] != "input_image" {
		t.Errorf("user content = %v, want input_text then input_image", user)
	}
	if user[1].(map[string]any)["image_url"] != "data:image/png;base64,AAAA" {
		t.Errorf("image_url = %v", user[1].(map[string]any)["image_url"])
	}
	call := input[2].(map[string]any)
	if call["call_id"] != "call_1" || call["name"] != "get_weather" || call["arguments"] != `{"city":"Paris"}` {
		t.Errorf("function_call = %v", call)
	}
	result := input[3].(map[string]any)
	if result["call_id"] != "call_1" || result["output"] != "sunny" {
		t.Errorf("function_call_output = %v", result)
	}

	tool := got["tools"].([]any)[0].(map[string]any)
	if tool["name"] != "get_weather" || tool["strict"] != false || tool["parameters"] == nil {
		t.Errorf("tool = %v, want a flat, non-strict function tool", tool)
	}
	choice := got["tool_choice"].(map[string]any)
	if choice["type"] != "function" || choice["name"] != "get_weather" {
		t.Errorf("tool_choice = %v, want the function named at the top level", choice)
	}
}

// fakeOpenAI answers like the OpenAI API does for gpt-5.6: chat completions
// refuse function tools, and /v1/responses serves them.
func fakeOpenAI(t *testing.T, responses http.HandlerFunc) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/v1/chat/completions":
			w.WriteHeader(http.StatusBadRequest)
			io.WriteString(w, `{"error":{"message":"Function tools with reasoning_effort are not supported for gpt-5.6-terra in /v1/chat/completions. To use function tools, use /v1/responses or set reasoning_effort to 'none'.","type":"invalid_request_error","param":"tools","code":null}}`)
		case "/v1/responses":
			responses(w, r)
		default:
			http.NotFound(w, r)
		}
	}))
}

const completedResponse = `{"id":"resp_1","object":"response","created_at":1761580000,"status":"completed","model":"gpt-5.6-terra",
"output":[
 {"id":"rs_1","type":"reasoning","summary":[]},
 {"id":"msg_1","type":"message","status":"completed","role":"assistant","content":[{"type":"output_text","text":"Let me check.","annotations":[]}]},
 {"id":"fc_1","type":"function_call","status":"completed","call_id":"call_2","name":"get_weather","arguments":"{\"city\":\"Paris\"}"}
],
"usage":{"input_tokens":120,"input_tokens_details":{"cached_tokens":64},"output_tokens":40,"output_tokens_details":{"reasoning_tokens":16},"total_tokens":160}}`

func TestChatCompletionViaResponses(t *testing.T) {
	var sent map[string]any
	ts := fakeOpenAI(t, func(w http.ResponseWriter, r *http.Request) {
		json.NewDecoder(r.Body).Decode(&sent)
		io.WriteString(w, completedResponse)
	})
	defer ts.Close()
	p := newTestProvider(t, ts.URL)

	resp, err := p.ChatCompletionViaResponses(context.Background(), toolTurnRequest("gpt-5.6-terra"))
	if err != nil {
		t.Fatalf("ChatCompletionViaResponses: %v", err)
	}
	if sent["stream"] != nil {
		t.Errorf("stream = %v, want it left out", sent["stream"])
	}

	choice := resp.Choices[0]
	if choice.FinishReason != "tool_calls" {
		t.Errorf("finish_reason = %q, want tool_calls", choice.FinishReason)
	}
	if string(choice.Message.Content) != `"Let me check."` {
		t.Errorf("content = %s", choice.Message.Content)
	}
	if len(choice.Message.ToolCalls) != 1 {
		t.Fatalf("tool_calls = %v, want one", choice.Message.ToolCalls)
	}
	tc := choice.Message.ToolCalls[0]
	if tc.ID != "call_2" || tc.Function.Name != "get_weather" || tc.Function.Arguments != `{"city":"Paris"}` {
		t.Errorf("tool call = %+v", tc)
	}
	u := resp.Usage
	if u.PromptTokens != 120 || u.CompletionTokens != 40 || u.TotalTokens != 160 {
		t.Errorf("usage = %+v", u)
	}
	if u.PromptTokensDetails == nil || string(*u.PromptTokensDetails) != `{"cached_tokens":64}` {
		t.Errorf("prompt_tokens_details = %v, want the cached tokens", u.PromptTokensDetails)
	}
}

func TestChatCompletionViaResponsesKeepsTheUpstreamError(t *testing.T) {
	ts := fakeOpenAI(t, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusBadRequest)
		io.WriteString(w, `{"error":{"message":"Invalid schema for function 'get_weather'","type":"invalid_request_error"}}`)
	})
	defer ts.Close()
	p := newTestProvider(t, ts.URL)

	_, err := p.ChatCompletionViaResponses(context.Background(), toolTurnRequest("gpt-5.6-terra"))
	var apiErr *models.APIError
	if !asAPIError(err, &apiErr) || apiErr.Status != http.StatusBadRequest || !strings.Contains(apiErr.Message, "Invalid schema") {
		t.Fatalf("err = %v, want the upstream's 400", err)
	}
}

func asAPIError(err error, target **models.APIError) bool {
	e, ok := err.(*models.APIError)
	if ok {
		*target = e
	}
	return ok
}

// sse writes Responses stream events as the API does: an event line, a data
// line, a blank line.
func sse(w http.ResponseWriter, events ...string) {
	w.Header().Set("Content-Type", "text/event-stream")
	for _, data := range events {
		var ev struct {
			Type string `json:"type"`
		}
		json.Unmarshal([]byte(data), &ev)
		fmt.Fprintf(w, "event: %s\ndata: %s\n\n", ev.Type, data)
	}
}

var toolStreamEvents = []string{
	`{"type":"response.created","sequence_number":0,"response":{"id":"resp_1","object":"response","created_at":1761580000,"status":"in_progress","model":"gpt-5.6-terra","output":[],"usage":null}}`,
	`{"type":"response.in_progress","sequence_number":1,"response":{"id":"resp_1","status":"in_progress"}}`,
	`{"type":"response.output_item.added","sequence_number":2,"output_index":0,"item":{"id":"rs_1","type":"reasoning","summary":[]}}`,
	`{"type":"response.output_item.done","sequence_number":3,"output_index":0,"item":{"id":"rs_1","type":"reasoning","summary":[]}}`,
	`{"type":"response.output_item.added","sequence_number":4,"output_index":1,"item":{"id":"msg_1","type":"message","status":"in_progress","role":"assistant","content":[]}}`,
	`{"type":"response.content_part.added","sequence_number":5,"item_id":"msg_1","output_index":1,"content_index":0,"part":{"type":"output_text","text":"","annotations":[]}}`,
	`{"type":"response.output_text.delta","sequence_number":6,"item_id":"msg_1","output_index":1,"content_index":0,"delta":"Let me "}`,
	`{"type":"response.output_text.delta","sequence_number":7,"item_id":"msg_1","output_index":1,"content_index":0,"delta":"check."}`,
	`{"type":"response.output_text.done","sequence_number":8,"item_id":"msg_1","output_index":1,"content_index":0,"text":"Let me check."}`,
	`{"type":"response.output_item.done","sequence_number":9,"output_index":1,"item":{"id":"msg_1","type":"message","status":"completed","role":"assistant","content":[{"type":"output_text","text":"Let me check.","annotations":[]}]}}`,
	`{"type":"response.output_item.added","sequence_number":10,"output_index":2,"item":{"id":"fc_1","type":"function_call","status":"in_progress","arguments":"","call_id":"call_2","name":"get_weather"}}`,
	`{"type":"response.function_call_arguments.delta","sequence_number":11,"item_id":"fc_1","output_index":2,"delta":"{\"city\":"}`,
	`{"type":"response.function_call_arguments.delta","sequence_number":12,"item_id":"fc_1","output_index":2,"delta":"\"Paris\"}"}`,
	`{"type":"response.function_call_arguments.done","sequence_number":13,"item_id":"fc_1","output_index":2,"arguments":"{\"city\":\"Paris\"}"}`,
	`{"type":"response.output_item.done","sequence_number":14,"output_index":2,"item":{"id":"fc_1","type":"function_call","status":"completed","arguments":"{\"city\":\"Paris\"}","call_id":"call_2","name":"get_weather"}}`,
	`{"type":"response.completed","sequence_number":15,"response":` + strings.ReplaceAll(completedResponse, "\n", "") + `}`,
}

// collect reads a chat stream to its end.
func collect(chunks <-chan models.StreamChunk, errs <-chan error) ([]models.StreamChunk, error) {
	var got []models.StreamChunk
	var err error
	for chunks != nil || errs != nil {
		select {
		case c, ok := <-chunks:
			if !ok {
				chunks = nil
				continue
			}
			got = append(got, c)
		case e, ok := <-errs:
			if !ok {
				errs = nil
				continue
			}
			err = e
		}
	}
	return got, err
}

func TestStreamChatCompletionViaResponses(t *testing.T) {
	var sent map[string]any
	ts := fakeOpenAI(t, func(w http.ResponseWriter, r *http.Request) {
		json.NewDecoder(r.Body).Decode(&sent)
		sse(w, toolStreamEvents...)
	})
	defer ts.Close()
	p := newTestProvider(t, ts.URL)

	chunks, err := collect(p.StreamChatCompletionViaResponses(context.Background(), toolTurnRequest("gpt-5.6-terra")))
	if err != nil {
		t.Fatalf("stream error: %v", err)
	}
	if sent["stream"] != true {
		t.Errorf("stream = %v, want true", sent["stream"])
	}

	var content, args, name, id, finish string
	var usage *models.Usage
	for _, c := range chunks {
		if c.Usage != nil {
			usage = c.Usage
		}
		for _, ch := range c.Choices {
			if ch.Delta.Content != nil {
				content += *ch.Delta.Content
			}
			for _, tc := range ch.Delta.ToolCalls {
				if tc.Index != 0 {
					t.Errorf("tool call index = %d, want 0", tc.Index)
				}
				if tc.ID != "" {
					id = tc.ID
				}
				if tc.Function != nil {
					name += tc.Function.Name
					args += tc.Function.Arguments
				}
			}
			if ch.FinishReason != nil {
				finish = *ch.FinishReason
			}
		}
	}
	if chunks[0].Choices[0].Delta.Role != "assistant" {
		t.Errorf("first chunk = %+v, want the assistant role", chunks[0])
	}
	if content != "Let me check." {
		t.Errorf("content = %q", content)
	}
	if id != "call_2" || name != "get_weather" || args != `{"city":"Paris"}` {
		t.Errorf("tool call = id %q name %q args %q", id, name, args)
	}
	if finish != "tool_calls" {
		t.Errorf("finish_reason = %q, want tool_calls", finish)
	}
	if usage == nil || usage.PromptTokens != 120 || usage.CompletionTokens != 40 {
		t.Errorf("usage = %+v, want 120 in, 40 out", usage)
	}
}

func TestStreamChatCompletionViaResponsesEndsWithTheFailure(t *testing.T) {
	head := toolStreamEvents[:8]
	tests := []struct {
		name   string
		events []string
		want   string
	}{
		{
			name:   "response.failed",
			events: append(append([]string{}, head...), `{"type":"response.failed","sequence_number":8,"response":{"id":"resp_1","status":"failed","error":{"code":"server_error","message":"The model had an error."}}}`),
			want:   "The model had an error.",
		},
		{
			name:   "error event",
			events: append(append([]string{}, head...), `{"type":"error","sequence_number":8,"code":"rate_limit_exceeded","message":"Rate limit reached.","param":null}`),
			want:   "Rate limit reached.",
		},
		{
			name:   "cut before response.completed",
			events: head,
			want:   "ended before the response completed",
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			ts := fakeOpenAI(t, func(w http.ResponseWriter, r *http.Request) { sse(w, tt.events...) })
			defer ts.Close()
			p := newTestProvider(t, ts.URL)

			chunks, err := collect(p.StreamChatCompletionViaResponses(context.Background(), toolTurnRequest("gpt-5.6-terra")))
			if err == nil || !strings.Contains(err.Error(), tt.want) {
				t.Fatalf("err = %v, want one containing %q", err, tt.want)
			}
			for _, c := range chunks {
				for _, ch := range c.Choices {
					if ch.FinishReason != nil {
						t.Errorf("a failed stream sent finish_reason %q", *ch.FinishReason)
					}
				}
			}
		})
	}
}

func TestStreamViaResponsesReportsATruncatedAnswer(t *testing.T) {
	incomplete := strings.Replace(strings.ReplaceAll(completedResponse, "\n", ""), `"status":"completed","model"`, `"status":"incomplete","incomplete_details":{"reason":"max_output_tokens"},"model"`, 1)
	events := append(append([]string{}, toolStreamEvents[:10]...),
		`{"type":"response.incomplete","sequence_number":10,"response":`+incomplete+`}`)
	ts := fakeOpenAI(t, func(w http.ResponseWriter, r *http.Request) { sse(w, events...) })
	defer ts.Close()
	p := newTestProvider(t, ts.URL)

	req := toolTurnRequest("gpt-5.6-terra")
	chunks, err := collect(p.StreamChatCompletionViaResponses(context.Background(), req))
	if err != nil {
		t.Fatalf("stream error: %v", err)
	}
	var finish string
	for _, c := range chunks {
		for _, ch := range c.Choices {
			if ch.FinishReason != nil {
				finish = *ch.FinishReason
			}
		}
	}
	if finish != "length" {
		t.Errorf("finish_reason = %q, want length", finish)
	}
}
