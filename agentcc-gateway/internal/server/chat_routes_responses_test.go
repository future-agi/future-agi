package server

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/scheduled"
)

// Scheduled completions and batches call the provider outside the
// /v1/chat/completions handler. The provider itself chooses the Responses
// route, so they carry gpt-5.6 tools too.

func TestScheduledCompletionCarriesGPT56ToolsOverResponses(t *testing.T) {
	srv, upstream := newResponsesBridgeServerWith(t, bridgeServerOptions{configure: func(cfg *config.Config) {
		cfg.Routing.Scheduled.Enabled = true
		cfg.Routing.Scheduled.RetryAttempts = 1
	}})
	// The job's webhook reports its outcome. Waiting on it rather than polling
	// GET /v1/scheduled/{id} keeps this test clear of that handler encoding a
	// job the scheduler is still writing (an existing race, outside this
	// change).
	type outcome struct {
		Status string          `json:"status"`
		Result json.RawMessage `json:"result"`
		Error  string          `json:"error"`
	}
	done := make(chan outcome, 1)
	hook := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var got outcome
		json.NewDecoder(r.Body).Decode(&got)
		select {
		case done <- got:
		default:
		}
	}))
	t.Cleanup(hook.Close)

	// Created through the store rather than POST /v1/scheduled: that handler
	// encodes the job after handing it to the scheduler, which the race
	// detector reports (an existing race, outside this change).
	job := &scheduled.ScheduledJob{
		ID:          "sched-synthetic-gpt56",
		Status:      scheduled.StatusPending,
		ScheduledAt: time.Now(),
		CreatedAt:   time.Now(),
		UpdatedAt:   time.Now(),
		Request:     json.RawMessage(chatToolBody("gpt-5.6-terra", false, "")),
		Model:       "gpt-5.6-terra",
		MaxAttempts: 1,
		WebhookURL:  hook.URL,
	}
	if err := srv.handlers.scheduledStore.Create(job); err != nil {
		t.Fatalf("creating the scheduled job: %v", err)
	}

	var got outcome
	select {
	case got = <-done:
	case <-time.After(15 * time.Second):
		t.Fatal("scheduled job reported no outcome in 15s")
	}
	if got.Status != "completed" {
		t.Fatalf("scheduled job status = %q, error %q; want completed", got.Status, got.Error)
	}
	checkToolCompletion(t, got.Result)
	checkHits(t, upstream, 0, 1)
}

func TestBatchCarriesGPT56ToolsOverResponses(t *testing.T) {
	srv, upstream := newResponsesBridgeServer(t)

	var req models.ChatCompletionRequest
	if err := json.Unmarshal([]byte(chatToolBody("gpt-5.6-sol", false, "")), &req); err != nil {
		t.Fatal(err)
	}
	b := srv.batchProcessor.Submit([]*models.ChatCompletionRequest{&req}, 1)
	deadline := time.Now().Add(10 * time.Second)
	for b.GetStatus() == "processing" && time.Now().Before(deadline) {
		time.Sleep(20 * time.Millisecond)
	}
	if status := b.GetStatus(); status != "completed" {
		t.Fatalf("batch status = %q, want completed", status)
	}
	if len(b.Results) != 1 || b.Results[0] == nil {
		t.Fatalf("batch results = %+v, want one", b.Results)
	}
	result := b.Results[0]
	if result.Status != "success" {
		t.Fatalf("batch result = %s %q, want success", result.Status, result.Error)
	}
	raw, err := json.Marshal(result.Response)
	if err != nil {
		t.Fatal(err)
	}
	checkToolCompletion(t, raw)
	checkHits(t, upstream, 0, 1)
}

// The GenAI translator builds a json_schema response format with no name. The
// Responses API requires one, so the bridge names it.
func TestGenAIResponseSchemaCarriedOverResponses(t *testing.T) {
	srv, upstream := newResponsesBridgeServer(t)

	body := `{"contents":[{"role":"user","parts":[{"text":"Weather in Paris?"}]}],
		"tools":[{"functionDeclarations":[{"name":"get_weather","description":"Weather for a city","parameters":{"type":"object","properties":{"city":{"type":"string"}}}}]}],
		"generationConfig":{"responseMimeType":"application/json","responseSchema":{"type":"object","properties":{"summary":{"type":"string"}}}}}`
	req := httptest.NewRequest(http.MethodPost, "/v1beta/models/gpt-5.6-terra:generateContent", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200. Body: %s", w.Code, w.Body.String())
	}
	checkHits(t, upstream, 0, 1)
	var sent struct {
		Text struct {
			Format struct {
				Type   string          `json:"type"`
				Name   string          `json:"name"`
				Schema json.RawMessage `json:"schema"`
			} `json:"format"`
		} `json:"text"`
	}
	if err := json.Unmarshal(upstream.body("/v1/responses"), &sent); err != nil {
		t.Fatal(err)
	}
	if f := sent.Text.Format; f.Type != "json_schema" || f.Name != "response" || len(f.Schema) == 0 {
		t.Errorf("text.format = %+v, want a json_schema named response with the schema", f)
	}
}

// A chat file part reaches the Responses API as an input_file.
func TestChatCompletionsCarriesFilePartsOverResponses(t *testing.T) {
	srv, upstream := newResponsesBridgeServer(t)
	body := `{"model":"gpt-5.6-terra",
		"tools":[{"type":"function","function":{"name":"get_weather","parameters":{"type":"object","properties":{"city":{"type":"string"}}}}}],
		"messages":[{"role":"user","content":[{"type":"text","text":"Which city is in this file?"},
			{"type":"file","file":{"file_data":"data:application/pdf;base64,JVBERi0=","filename":"synthetic.pdf"}}]}]}`
	w := postBridgeChat(t, srv, body, nil)
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d: %s", w.Code, w.Body.String())
	}
	checkHits(t, upstream, 0, 1)
	var sent struct {
		Input []struct {
			Content []map[string]string `json:"content"`
		} `json:"input"`
	}
	if err := json.Unmarshal(upstream.body("/v1/responses"), &sent); err != nil || len(sent.Input) != 1 || len(sent.Input[0].Content) != 2 {
		t.Fatalf("sent %s: %v", upstream.body("/v1/responses"), err)
	}
	file := sent.Input[0].Content[1]
	if file["type"] != "input_file" || file["file_data"] != "data:application/pdf;base64,JVBERi0=" || file["filename"] != "synthetic.pdf" {
		t.Errorf("file part = %v, want an input_file with the data and filename", file)
	}
}

// What the bridge cannot carry is a clear 400 from the gateway, sent before any
// upstream call, never a chat-shaped request upstream or a silently dropped
// part.
func TestChatCompletionsRefusesWhatResponsesCannotCarry(t *testing.T) {
	for _, tt := range []struct{ name, body, want string }{
		{"custom tool beside a function tool", `{"model":"gpt-5.6-terra",
			"tools":[{"type":"function","function":{"name":"get_weather","parameters":{"type":"object"}}},{"type":"custom","custom":{"name":"run_sql"}}],
			"messages":[{"role":"user","content":"Hi"}]}`, `"custom" tool`},
		{"audio input", `{"model":"gpt-5.6-terra",
			"tools":[{"type":"function","function":{"name":"get_weather","parameters":{"type":"object"}}}],
			"messages":[{"role":"user","content":[{"type":"input_audio","input_audio":{"data":"UklGRg==","format":"wav"}}]}]}`, `"input_audio" content part`},
	} {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/stream=%v", tt.name, stream), func(t *testing.T) {
				srv, upstream := newResponsesBridgeServer(t)
				body := tt.body
				if stream {
					body = `{"stream":true,` + body[1:]
				}
				w := postBridgeChat(t, srv, body, nil)
				if w.Code != http.StatusBadRequest {
					t.Fatalf("status = %d, want 400: %s", w.Code, w.Body.String())
				}
				var resp struct {
					Error struct {
						Code    string `json:"code"`
						Message string `json:"message"`
					} `json:"error"`
				}
				if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil || resp.Error.Code != "unsupported_over_responses" ||
					!bytes.Contains([]byte(resp.Error.Message), []byte(tt.want)) {
					t.Errorf("error = %s, want unsupported_over_responses naming %s", w.Body.String(), tt.want)
				}
				checkHits(t, upstream, 0, 0)
			})
		}
	}
}

// A gpt-5.6 request whose only tools are custom tools needs no Responses route
// and goes to chat completions as on any other model.
func TestChatCompletionsKeepsCustomOnlyToolsOnChatCompletions(t *testing.T) {
	srv, upstream := newResponsesBridgeServer(t)
	body := `{"model":"gpt-5.6-terra","tools":[{"type":"custom","custom":{"name":"run_sql"}}],"messages":[{"role":"user","content":"Hi"}]}`
	postBridgeChat(t, srv, body, nil)
	checkHits(t, upstream, 1, 0)
}
