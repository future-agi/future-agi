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

// withEffort is toolTurnRequest with reasoning_effort set to the raw JSON value.
func withEffort(model, effort string) *models.ChatCompletionRequest {
	req := toolTurnRequest(model)
	req.Extra = map[string]json.RawMessage{"reasoning_effort": json.RawMessage(effort)}
	return req
}

// NeedsResponses is the rule without the host check; ChatNeedsResponses adds
// the official-API check on top.
func TestNeedsResponsesIgnoresTheHost(t *testing.T) {
	if !NeedsResponses(toolTurnRequest("gpt-5.6-terra")) {
		t.Error("NeedsResponses(gpt-5.6-terra with tools) = false, want true")
	}
	if NeedsResponses(withEffort("gpt-5.6-terra", `"none"`)) {
		t.Error(`NeedsResponses(reasoning_effort "none") = true, want false`)
	}
	if NeedsResponses(nil) {
		t.Error("NeedsResponses(nil) = true, want false")
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
		{"reasoning_effort none keeps tools on chat completions", true, withEffort("gpt-5.6-terra", `"none"`), false},
		{"reasoning_effort low still reasons", true, withEffort("gpt-5.6-terra", `"low"`), true},
		{"reasoning_effort high still reasons", true, withEffort("gpt-5.6-terra", `"high"`), true},
		{"reasoning_effort null is not none", true, withEffort("gpt-5.6-terra", `null`), true},
		{"reasoning_effort that is not a string is not none", true, withEffort("gpt-5.6-terra", `0`), true},
		{"gpt-5.6-luna", true, toolTurnRequest("gpt-5.6-luna"), true},
		{"gpt-5.6-sol", true, toolTurnRequest("gpt-5.6-sol"), true},
		{"gpt-5.6-cyber", true, toolTurnRequest("gpt-5.6-cyber"), true},
		{"reasoning_effort none on another upstream", false, withEffort("gpt-5.6-terra", `"none"`), false},
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
	body, err := json.Marshal(mustResponsesRequest(t, toolTurnRequest("gpt-5.6-terra")))
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

// A caller who asked chat completions for a JSON schema gets the same schema on
// the Responses API, where the fields sit one level up under text.format.
func TestToResponsesRequestCarriesResponseFormat(t *testing.T) {
	tests := []struct {
		name   string
		format *models.ResponseFormat
		want   string // text.format as JSON; empty means no text field
	}{
		{"none", nil, ""},
		{"json_object", &models.ResponseFormat{Type: "json_object"}, `{"type":"json_object"}`},
		{"text", &models.ResponseFormat{Type: "text"}, `{"type":"text"}`},
		{
			"json_schema strict",
			&models.ResponseFormat{Type: "json_schema", JSONSchema: json.RawMessage(`{"name":"weather","description":"A forecast","schema":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"],"additionalProperties":false},"strict":true}`)},
			`{"type":"json_schema","name":"weather","description":"A forecast","schema":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"],"additionalProperties":false},"strict":true}`,
		},
		{
			"json_schema without strict stays non-strict, as on chat completions",
			&models.ResponseFormat{Type: "json_schema", JSONSchema: json.RawMessage(`{"name":"weather","schema":{"type":"object"}}`)},
			`{"type":"json_schema","name":"weather","schema":{"type":"object"},"strict":false}`,
		},
		{"an unknown type is not sent", &models.ResponseFormat{Type: "grammar"}, ""},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			req := toolTurnRequest("gpt-5.6-terra")
			req.ResponseFormat = tt.format
			body, err := json.Marshal(mustResponsesRequest(t, req))
			if err != nil {
				t.Fatal(err)
			}
			var got struct {
				ResponseFormat json.RawMessage `json:"response_format"`
				Text           *struct {
					Format json.RawMessage `json:"format"`
				} `json:"text"`
			}
			if err := json.Unmarshal(body, &got); err != nil {
				t.Fatal(err)
			}
			if got.ResponseFormat != nil {
				t.Errorf("response_format is not a Responses field but was sent: %s", got.ResponseFormat)
			}
			if tt.want == "" {
				if got.Text != nil {
					t.Errorf("text = %s, want none", got.Text.Format)
				}
				return
			}
			if got.Text == nil {
				t.Fatalf("text missing from %s", body)
			}
			if string(got.Text.Format) != tt.want {
				t.Errorf("text.format = %s, want %s", got.Text.Format, tt.want)
			}
		})
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

// A Responses answer can arrive as HTTP 200 with status "failed". The caller
// gets the failure, as a stream's response.failed already does, not an empty
// chat completion that health checks, failover and Request Logs count as a
// success.
func TestChatCompletionViaResponsesReportsAFailedResponse(t *testing.T) {
	ts := fakeOpenAI(t, func(w http.ResponseWriter, r *http.Request) {
		io.WriteString(w, `{"id":"resp_1","object":"response","created_at":1761580000,"status":"failed","model":"gpt-5.6-terra","output":[],"error":{"code":"server_error","message":"The model failed to generate a response."},"usage":null}`)
	})
	defer ts.Close()
	p := newTestProvider(t, ts.URL)

	resp, err := p.ChatCompletionViaResponses(context.Background(), toolTurnRequest("gpt-5.6-terra"))
	var apiErr *models.APIError
	if !asAPIError(err, &apiErr) || apiErr.Status != http.StatusBadGateway || !strings.Contains(apiErr.Message, "failed to generate") {
		t.Fatalf("resp = %+v, err = %v; want a 502 carrying the upstream's message", resp, err)
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

// The provider's own ChatCompletion and StreamChatCompletion take the Responses
// route for a request chat completions would refuse, so every caller gets it:
// the chat handlers, scheduled completions, batches and the traffic mirror call
// these and nothing else.
func TestChatCompletionTakesTheResponsesRouteItself(t *testing.T) {
	for _, official := range []bool{true, false} {
		t.Run(fmt.Sprintf("official=%v", official), func(t *testing.T) {
			hits := map[string]int{}
			ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				hits[r.URL.Path]++
				var req struct {
					Stream bool `json:"stream"`
				}
				_ = json.NewDecoder(r.Body).Decode(&req)
				switch {
				case r.URL.Path == "/v1/responses" && req.Stream:
					sse(w, toolStreamEvents...)
				case r.URL.Path == "/v1/responses":
					_, _ = io.WriteString(w, completedResponse)
				case req.Stream:
					sse(w, `{"id":"chatcmpl-1","object":"chat.completion.chunk","model":"gpt-5.6-terra","choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":"stop"}]}`, `[DONE]`)
				default:
					_, _ = io.WriteString(w, `{"id":"chatcmpl-1","object":"chat.completion","model":"gpt-5.6-terra","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}`)
				}
			}))
			defer ts.Close()
			p := newTestProvider(t, ts.URL)
			p.openAIAPI = official

			if _, err := p.ChatCompletion(context.Background(), toolTurnRequest("gpt-5.6-terra")); err != nil {
				t.Fatalf("ChatCompletion: %v", err)
			}
			if _, err := collect(p.StreamChatCompletion(context.Background(), toolTurnRequest("gpt-5.6-terra"))); err != nil {
				t.Fatalf("StreamChatCompletion: %v", err)
			}
			wantResponses, wantChat := 2, 0
			if !official {
				wantResponses, wantChat = 0, 2
			}
			if hits["/v1/responses"] != wantResponses || hits["/v1/chat/completions"] != wantChat {
				t.Errorf("hits = %v, want %d /v1/responses and %d /v1/chat/completions", hits, wantResponses, wantChat)
			}
		})
	}
}

// mustResponsesRequest translates req for a non-streaming call, failing the
// test on a translation error.
func mustResponsesRequest(t *testing.T, req *models.ChatCompletionRequest) *responsesRequest {
	t.Helper()
	out, err := toResponsesRequest(req, false)
	if err != nil {
		t.Fatalf("toResponsesRequest: %v", err)
	}
	return out
}

// A chat file part goes over as a Responses input_file, by data or by id.
func TestToResponsesRequestCarriesFileParts(t *testing.T) {
	req := toolTurnRequest("gpt-5.6-terra")
	req.Messages = []models.Message{{Role: "user", Content: json.RawMessage(`[
		{"type":"text","text":"Summarize these."},
		{"type":"file","file":{"file_data":"data:application/pdf;base64,JVBERi0=","filename":"synthetic.pdf"}},
		{"type":"file","file":{"file_id":"file-synthetic"}}]`)}}
	got := mustResponsesRequest(t, req)
	msg, ok := got.Input[0].(responsesMessage)
	if !ok || len(msg.Content) != 3 {
		t.Fatalf("input = %+v, want one message with three parts", got.Input)
	}
	want := []responsesContent{
		{Type: "input_text", Text: "Summarize these."},
		{Type: "input_file", FileData: "data:application/pdf;base64,JVBERi0=", Filename: "synthetic.pdf"},
		{Type: "input_file", FileID: "file-synthetic"},
	}
	for i := range want {
		if msg.Content[i] != want[i] {
			t.Errorf("part %d = %+v, want %+v", i, msg.Content[i], want[i])
		}
	}
}

// An assistant turn keeps its text and its refusal.
func TestToResponsesRequestCarriesAssistantRefusals(t *testing.T) {
	req := toolTurnRequest("gpt-5.6-terra")
	req.Messages = []models.Message{
		{Role: "user", Content: json.RawMessage(`"Help me."`)},
		{Role: "assistant", Content: json.RawMessage(`[{"type":"text","text":"Partly:"},{"type":"refusal","refusal":"I can't help with that part."}]`)},
	}
	got := mustResponsesRequest(t, req)
	msg, ok := got.Input[1].(responsesMessage)
	want := []responsesContent{{Type: "output_text", Text: "Partly:"}, {Type: "refusal", Refusal: "I can't help with that part."}}
	if !ok || len(msg.Content) != 2 || msg.Content[0] != want[0] || msg.Content[1] != want[1] {
		t.Errorf("assistant input = %+v, want %+v", got.Input[1], want)
	}
}

// Anything the Responses API cannot take in chat shape is refused with a 400
// that says what and how to keep the request on chat completions: never sent
// in chat shape, never dropped.
func TestToResponsesRequestRefusesWhatItCannotCarry(t *testing.T) {
	custom := models.Tool{}
	if err := json.Unmarshal([]byte(`{"type":"custom","custom":{"name":"run_sql","description":"Run SQL"}}`), &custom); err != nil {
		t.Fatal(err)
	}
	for _, tt := range []struct {
		name, want string
		edit       func(*models.ChatCompletionRequest)
	}{
		{"audio input", `"input_audio" content part`, func(r *models.ChatCompletionRequest) {
			r.Messages[1].Content = json.RawMessage(`[{"type":"input_audio","input_audio":{"data":"UklGRg==","format":"wav"}}]`)
		}},
		{"unknown part", `"video_url" content part`, func(r *models.ChatCompletionRequest) {
			r.Messages[1].Content = json.RawMessage(`[{"type":"video_url","video_url":{"url":"https://example.invalid/v.mp4"}}]`)
		}},
		{"file without data or id", `"file" content part`, func(r *models.ChatCompletionRequest) {
			r.Messages[1].Content = json.RawMessage(`[{"type":"file","file":{"filename":"empty.pdf"}}]`)
		}},
		{"custom tool beside a function tool", `"custom" tool`, func(r *models.ChatCompletionRequest) {
			r.Tools = append(r.Tools, custom)
		}},
		{"custom tool_choice", `"custom" tool_choice`, func(r *models.ChatCompletionRequest) {
			r.ToolChoice = json.RawMessage(`{"type":"custom","custom":{"name":"run_sql"}}`)
		}},
		{"allowed_tools with a custom tool", `"custom" tool in allowed_tools`, func(r *models.ChatCompletionRequest) {
			r.ToolChoice = json.RawMessage(`{"type":"allowed_tools","allowed_tools":{"mode":"auto","tools":[{"type":"function","function":{"name":"get_weather"}},{"type":"custom","custom":{"name":"run_sql"}}]}}`)
		}},
		{"more than one choice", `n=2`, func(r *models.ChatCompletionRequest) {
			two := 2
			r.N = &two
		}},
		{"unknown tool_choice mode", `tool_choice "sometimes"`, func(r *models.ChatCompletionRequest) {
			r.ToolChoice = json.RawMessage(`"sometimes"`)
		}},
		{"custom tool call in history", `"custom" tool call`, func(r *models.ChatCompletionRequest) {
			r.Messages[2].ToolCalls[0].Type = "custom"
		}},
		{"image in a tool result", `"image_url" content part in a tool message`, func(r *models.ChatCompletionRequest) {
			r.Messages[3].Content = json.RawMessage(`[{"type":"image_url","image_url":{"url":"data:image/png;base64,AAAA"}}]`)
		}},
		{"audio in an assistant turn", `"audio" content part in an assistant message`, func(r *models.ChatCompletionRequest) {
			r.Messages[2].Content = json.RawMessage(`[{"type":"audio","audio":{"id":"audio_1"}}]`)
		}},
	} {
		t.Run(tt.name, func(t *testing.T) {
			req := toolTurnRequest("gpt-5.6-terra")
			tt.edit(req)
			for _, stream := range []bool{false, true} {
				out, err := toResponsesRequest(req, stream)
				if err == nil {
					t.Fatalf("stream=%v: translated to %+v, want a 400", stream, out)
				}
				if err.Status != http.StatusBadRequest || err.Code != "unsupported_over_responses" ||
					!strings.Contains(err.Message, tt.want) || !strings.Contains(err.Message, `reasoning_effort to "none"`) {
					t.Errorf("stream=%v: error = %d %s %q, want a 400 naming %s", stream, err.Status, err.Code, err.Message, tt.want)
				}
			}
		})
	}
}

// Only function tools need the Responses route. A request whose tools are all
// of another kind stays on chat completions, as on any other model.
func TestNeedsResponsesOnlyForFunctionTools(t *testing.T) {
	custom := models.Tool{}
	if err := json.Unmarshal([]byte(`{"type":"custom","custom":{"name":"run_sql"}}`), &custom); err != nil {
		t.Fatal(err)
	}
	onlyCustom := toolTurnRequest("gpt-5.6-terra")
	onlyCustom.Tools = []models.Tool{custom}
	if NeedsResponses(onlyCustom) {
		t.Error("NeedsResponses(only a custom tool) = true, want false")
	}
	both := toolTurnRequest("gpt-5.6-terra")
	both.Tools = append(both.Tools, custom)
	if !NeedsResponses(both) {
		t.Error("NeedsResponses(function and custom tools) = false, want true")
	}
}

// The Responses API requires a json_schema format name; a chat response_format
// without one (as the GenAI translator builds it) gets a default.
func TestToResponsesRequestNamesAnUnnamedJSONSchema(t *testing.T) {
	for _, tt := range []struct{ schema, want string }{
		{`{"schema":{"type":"object"}}`, defaultJSONSchemaName},
		{`{"name":"","schema":{"type":"object"}}`, defaultJSONSchemaName},
		{`{"name":"weather","schema":{"type":"object"}}`, "weather"},
	} {
		req := toolTurnRequest("gpt-5.6-terra")
		req.ResponseFormat = &models.ResponseFormat{Type: "json_schema", JSONSchema: json.RawMessage(tt.schema)}
		got := mustResponsesRequest(t, req)
		var format struct {
			Name string `json:"name"`
		}
		if got.Text == nil || json.Unmarshal(got.Text.Format, &format) != nil || format.Name != tt.want {
			t.Errorf("%s: text = %+v, want format name %q", tt.schema, got.Text, tt.want)
		}
	}
}

// Chat fields with a Responses equivalent are carried.
func TestToResponsesRequestCarriesCacheKeyMetadataAndVerbosity(t *testing.T) {
	req := toolTurnRequest("gpt-5.6-terra")
	req.Extra = map[string]json.RawMessage{
		"prompt_cache_key":  json.RawMessage(`"synthetic-cache-key"`),
		"safety_identifier": json.RawMessage(`"synthetic-user-hash"`),
		"metadata":          json.RawMessage(`{"run":"synthetic"}`),
		"verbosity":         json.RawMessage(`"low"`),
	}
	body, err := json.Marshal(mustResponsesRequest(t, req))
	if err != nil {
		t.Fatal(err)
	}
	var got map[string]any
	if err := json.Unmarshal(body, &got); err != nil {
		t.Fatal(err)
	}
	text, _ := got["text"].(map[string]any)
	meta, _ := got["metadata"].(map[string]any)
	if got["prompt_cache_key"] != "synthetic-cache-key" || got["safety_identifier"] != "synthetic-user-hash" ||
		meta["run"] != "synthetic" || text["verbosity"] != "low" {
		t.Errorf("request = %s", body)
	}
	if _, ok := text["format"]; ok {
		t.Errorf("text.format = %v, want it left out without a response_format", text["format"])
	}
}

// A call item the chat answer cannot carry fails the call; it is never
// dropped from the answer.
func TestViaResponsesFailsOnACallItemItCannotCarry(t *testing.T) {
	const customCall = `{"id":"ctc_1","type":"custom_tool_call","status":"completed","call_id":"call_7","name":"run_sql","input":"SELECT 1"}`
	ts := fakeOpenAI(t, func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			Stream bool `json:"stream"`
		}
		_ = json.NewDecoder(r.Body).Decode(&req)
		final := `{"id":"resp_1","object":"response","created_at":1761580000,"status":"completed","model":"gpt-5.6-terra","output":[` + customCall + `],"usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2}}`
		if !req.Stream {
			_, _ = io.WriteString(w, final)
			return
		}
		sse(w,
			`{"type":"response.created","response":{"id":"resp_1","status":"in_progress","model":"gpt-5.6-terra","output":[]}}`,
			`{"type":"response.output_item.added","output_index":0,"item":`+customCall+`}`,
			`{"type":"response.completed","response":`+final+`}`)
	})
	defer ts.Close()
	p := newTestProvider(t, ts.URL)

	if resp, err := p.ChatCompletionViaResponses(context.Background(), toolTurnRequest("gpt-5.6-terra")); err == nil || !strings.Contains(err.Error(), "custom_tool_call") {
		t.Errorf("non-stream: resp %+v, err %v; want an error naming custom_tool_call", resp, err)
	}
	if _, err := collect(p.StreamChatCompletionViaResponses(context.Background(), toolTurnRequest("gpt-5.6-terra"))); err == nil || !strings.Contains(err.Error(), "custom_tool_call") {
		t.Errorf("stream: err %v; want an error naming custom_tool_call", err)
	}
}

// A call item that appears only in the final response, never announced by an
// output_item.added event, still fails the stream rather than being dropped.
func TestStreamViaResponsesFailsOnACallItemOnlyInTheFinalResponse(t *testing.T) {
	final := `{"id":"resp_1","object":"response","created_at":1761580000,"status":"completed","model":"gpt-5.6-terra","output":[{"id":"ws_1","type":"web_search_call","status":"completed"}],"usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2}}`
	ts := fakeOpenAI(t, func(w http.ResponseWriter, r *http.Request) {
		sse(w,
			`{"type":"response.created","response":{"id":"resp_1","status":"in_progress","model":"gpt-5.6-terra","output":[]}}`,
			`{"type":"response.completed","response":`+final+`}`)
	})
	defer ts.Close()
	p := newTestProvider(t, ts.URL)
	if _, err := collect(p.StreamChatCompletionViaResponses(context.Background(), toolTurnRequest("gpt-5.6-terra"))); err == nil || !strings.Contains(err.Error(), "web_search_call") {
		t.Errorf("stream: err %v; want an error naming web_search_call", err)
	}
}

// An allowed_tools choice over function tools moves to the Responses shape.
func TestToResponsesRequestTranslatesAllowedTools(t *testing.T) {
	req := toolTurnRequest("gpt-5.6-terra")
	req.ToolChoice = json.RawMessage(`{"type":"allowed_tools","allowed_tools":{"mode":"required","tools":[{"type":"function","function":{"name":"get_weather"}}]}}`)
	got := mustResponsesRequest(t, req)
	if want := `{"type":"allowed_tools","mode":"required","tools":[{"type":"function","name":"get_weather"}]}`; string(got.ToolChoice) != want {
		t.Errorf("tool_choice = %s, want %s", got.ToolChoice, want)
	}
}

// The caller's store and a single choice are carried; store defaults to false.
func TestToResponsesRequestCarriesStoreAndOneChoice(t *testing.T) {
	req := toolTurnRequest("gpt-5.6-terra")
	if got := mustResponsesRequest(t, req); got.Store {
		t.Error("store = true without the caller asking, want false")
	}
	one := 1
	req.N = &one
	req.Extra = map[string]json.RawMessage{"store": json.RawMessage(`true`)}
	if got := mustResponsesRequest(t, req); !got.Store {
		t.Error("store = false, want the caller's true")
	}
}

// A refusal's remedy fits the route: a translated request (GenAI, Messages)
// cannot set reasoning_effort, so it is not told to.
func TestOverResponsesRemedyFitsTheRoute(t *testing.T) {
	custom := models.Tool{}
	if err := json.Unmarshal([]byte(`{"type":"custom","custom":{"name":"run_sql"}}`), &custom); err != nil {
		t.Fatal(err)
	}
	req := toolTurnRequest("gpt-5.6-terra")
	req.Tools = append(req.Tools, custom)
	_, err := toResponsesRequest(req, false)
	if err == nil || !strings.Contains(err.Message, `reasoning_effort to "none"`) {
		t.Errorf("direct: %v", err)
	}
	req.TranslatedFrom = "google"
	_, err = toResponsesRequest(req, false)
	if err == nil || strings.Contains(err.Message, "reasoning_effort") || !strings.Contains(err.Message, "without function tools") {
		t.Errorf("translated: %v", err)
	}
	// A translated n > 1 is carried as one choice, not refused.
	two := 2
	ok := toolTurnRequest("gpt-5.6-terra")
	ok.N, ok.TranslatedFrom = &two, "google"
	if _, err := toResponsesRequest(ok, false); err != nil {
		t.Errorf("translated n=2: %v", err)
	}
}

// A content part with the wrong JSON shape is a 400 naming it, never sent on
// as text.
func TestToResponsesRequestRefusesMalformedParts(t *testing.T) {
	req := toolTurnRequest("gpt-5.6-terra")
	req.Messages[1].Content = json.RawMessage(`[{"type":"text","text":"look"},{"type":"image_url","image_url":"data:image/png;base64,AAAA"}]`)
	out, err := toResponsesRequest(req, false)
	if err == nil || err.Status != http.StatusBadRequest || !strings.Contains(err.Message, `content part 1 (type "image_url")`) {
		t.Fatalf("got %+v, %v; want a 400 naming part 1", out, err)
	}
}

// A function call that appears only in the final response of a stream is
// carried as a tool call, as the non-streaming answer carries it.
func TestStreamViaResponsesCarriesAFunctionCallOnlyInTheFinalResponse(t *testing.T) {
	final := `{"id":"resp_1","object":"response","created_at":1761580000,"status":"completed","model":"gpt-5.6-terra","output":[{"id":"fc_9","type":"function_call","status":"completed","call_id":"call_9","name":"get_weather","arguments":"{\"city\":\"Paris\"}"}],"usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2}}`
	ts := fakeOpenAI(t, func(w http.ResponseWriter, r *http.Request) {
		sse(w,
			`{"type":"response.created","response":{"id":"resp_1","status":"in_progress","model":"gpt-5.6-terra","output":[]}}`,
			`{"type":"response.completed","response":`+final+`}`)
	})
	defer ts.Close()
	p := newTestProvider(t, ts.URL)
	chunks, err := collect(p.StreamChatCompletionViaResponses(context.Background(), toolTurnRequest("gpt-5.6-terra")))
	if err != nil {
		t.Fatal(err)
	}
	var name, args, finish string
	for _, c := range chunks {
		for _, ch := range c.Choices {
			for _, tc := range ch.Delta.ToolCalls {
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
	if name != "get_weather" || args != `{"city":"Paris"}` || finish != "tool_calls" {
		t.Errorf("tool call %q %q, finish %q", name, args, finish)
	}
}
