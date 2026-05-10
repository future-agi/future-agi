package bedrock

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestIsInferenceProfile(t *testing.T) {
	cases := []struct {
		modelID string
		want    bool
	}{
		{"arn:aws:bedrock:us-east-1:123:inference-profile/global.anthropic.claude-sonnet-4-5-20250929-v1:0", true},
		{"arn:aws:bedrock:us-east-1:123:application-inference-profile/abc", true},
		{"global.anthropic.claude-sonnet-4-5-20250929-v1:0", true},
		{"us.anthropic.claude-3-5-sonnet-20240620-v1:0", true},
		{"eu.anthropic.claude-3-5-sonnet-20240620-v1:0", true},
		{"ap.anthropic.claude-3-5-sonnet-20240620-v1:0", true},
		{"anthropic.claude-3-5-sonnet-20241022-v2:0", false},
		{"amazon.titan-text-express-v1", false},
		{"meta.llama3-8b-instruct-v1:0", false},
	}
	for _, c := range cases {
		if got := isInferenceProfile(c.modelID); got != c.want {
			t.Errorf("isInferenceProfile(%q) = %v, want %v", c.modelID, got, c.want)
		}
	}
}

func TestBuildConverseRequest_Basic(t *testing.T) {
	maxTok := 512
	req := &models.ChatCompletionRequest{
		Model: "bedrock/arn:aws:bedrock:us-east-1:123:inference-profile/global.anthropic.claude-sonnet-4-5-20250929-v1:0",
		Messages: []models.Message{
			{Role: "system", Content: json.RawMessage(`"You are helpful"`)},
			{Role: "user", Content: json.RawMessage(`"Hello"`)},
		},
		MaxTokens: &maxTok,
	}

	cr, modelID := buildConverseRequest(req)

	if modelID != "arn:aws:bedrock:us-east-1:123:inference-profile/global.anthropic.claude-sonnet-4-5-20250929-v1:0" {
		t.Errorf("modelID = %q, want ARN without bedrock/ prefix", modelID)
	}
	if len(cr.System) != 1 || cr.System[0].Text != "You are helpful" {
		t.Errorf("system = %+v, want 1 entry with text 'You are helpful'", cr.System)
	}
	if len(cr.Messages) != 1 || cr.Messages[0].Role != "user" {
		t.Errorf("messages = %+v, want 1 user message", cr.Messages)
	}
	if cr.InferenceConfig.MaxTokens != 512 {
		t.Errorf("maxTokens = %d, want 512", cr.InferenceConfig.MaxTokens)
	}
}

func TestBuildConverseRequest_ResponseFormatJSONSchema(t *testing.T) {
	maxTok := 256
	schema := json.RawMessage(`{"name":"answer","description":"Structured answer","schema":{"type":"object","properties":{"result":{"type":"string"}},"required":["result"],"additionalProperties":false}}`)
	req := &models.ChatCompletionRequest{
		Model: "bedrock/arn:aws:bedrock:us-east-1:123:inference-profile/global.anthropic.claude-sonnet-4-5-20250929-v1:0",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Return structured JSON"`)},
		},
		MaxCompletionTokens: &maxTok,
		ResponseFormat: &models.ResponseFormat{
			Type:       "json_schema",
			JSONSchema: schema,
		},
	}

	cr, _ := buildConverseRequest(req)

	if cr.OutputConfig == nil || cr.OutputConfig.TextFormat == nil {
		t.Fatal("OutputConfig.TextFormat should not be nil")
	}
	if cr.OutputConfig.TextFormat.Type != "json_schema" {
		t.Fatalf("textFormat.type = %q, want %q", cr.OutputConfig.TextFormat.Type, "json_schema")
	}
	if cr.OutputConfig.TextFormat.Structure.JSONSchema.Name != "answer" {
		t.Fatalf("jsonSchema.name = %q, want %q", cr.OutputConfig.TextFormat.Structure.JSONSchema.Name, "answer")
	}
	if cr.OutputConfig.TextFormat.Structure.JSONSchema.Description != "Structured answer" {
		t.Fatalf("jsonSchema.description = %q, want %q", cr.OutputConfig.TextFormat.Structure.JSONSchema.Description, "Structured answer")
	}

	var parsed map[string]any
	if err := json.Unmarshal([]byte(cr.OutputConfig.TextFormat.Structure.JSONSchema.Schema), &parsed); err != nil {
		t.Fatalf("failed to unmarshal schema string: %v", err)
	}
	if parsed["type"] != "object" {
		t.Fatalf("schema type = %v, want object", parsed["type"])
	}
}

func TestTranslateConverseResponse_Basic(t *testing.T) {
	text := "Hello there"
	modelID := "arn:aws:bedrock:us-east-1:123:inference-profile/global.anthropic.claude-sonnet-4-5-20250929-v1:0"
	resp := &converseResponse{
		Output: converseOutput{
			Message: converseMessage{
				Role:    "assistant",
				Content: []converseContentPart{{Text: &text}},
			},
		},
		StopReason: "end_turn",
		Usage:      converseUsage{InputTokens: 10, OutputTokens: 5, TotalTokens: 15},
	}

	out := translateConverseResponse(resp, modelID)

	if out.ID == "" {
		t.Fatal("ID should be populated for converse responses")
	}
	if out.Model != modelID {
		t.Errorf("Model = %q, want %q", out.Model, modelID)
	}
	if out.Created == 0 {
		t.Fatal("Created should be non-zero")
	}

	if out.Usage.PromptTokens != 10 {
		t.Errorf("PromptTokens = %d, want 10", out.Usage.PromptTokens)
	}
	if out.Usage.CompletionTokens != 5 {
		t.Errorf("CompletionTokens = %d, want 5", out.Usage.CompletionTokens)
	}
	if out.Usage.TotalTokens != 15 {
		t.Errorf("TotalTokens = %d, want 15", out.Usage.TotalTokens)
	}
	if out.Choices[0].FinishReason != "stop" {
		t.Errorf("FinishReason = %q, want 'stop'", out.Choices[0].FinishReason)
	}
	var content string
	json.Unmarshal(out.Choices[0].Message.Content, &content)
	if content != text {
		t.Errorf("content = %q, want %q", content, text)
	}
}

func TestNewConverseStreamState_SetsMetadata(t *testing.T) {
	modelID := "arn:aws:bedrock:us-east-1:123:inference-profile/global.anthropic.claude-sonnet-4-5-20250929-v1:0"
	state := newConverseStreamState(modelID)

	if state.messageID == "" {
		t.Fatal("messageID should be populated")
	}
	if state.model != modelID {
		t.Errorf("model = %q, want %q", state.model, modelID)
	}
	if state.created == 0 {
		t.Fatal("created should be non-zero")
	}
}

func TestParseConverseEvent_MessageStart_PreservesMetadata(t *testing.T) {
	state := &converseStreamState{messageID: "msg_1", model: "model-1", created: 1700000000}
	msg := &eventStreamMessage{
		Headers: map[string]string{":message-type": "event", ":event-type": "messageStart"},
	}

	chunk, done, err := state.parseConverseEvent(msg)

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if done {
		t.Error("done should be false")
	}
	if chunk == nil {
		t.Fatal("chunk should not be nil")
	}
	if chunk.ID != state.messageID {
		t.Errorf("chunk ID = %q, want %q", chunk.ID, state.messageID)
	}
	if chunk.Model != state.model {
		t.Errorf("chunk Model = %q, want %q", chunk.Model, state.model)
	}
	if chunk.Created != state.created {
		t.Errorf("chunk Created = %d, want %d", chunk.Created, state.created)
	}
}

func TestParseConverseEvent_MetadataUsage_PreservesMetadata(t *testing.T) {
	state := &converseStreamState{messageID: "msg_1", model: "model-1", created: 1700000000}
	msg := &eventStreamMessage{
		Headers: map[string]string{":message-type": "event", ":event-type": "metadata"},
		Payload: []byte(`{"usage":{"inputTokens":10,"outputTokens":5}}`),
	}

	chunk, done, err := state.parseConverseEvent(msg)

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !done {
		t.Error("done should be true")
	}
	if chunk == nil {
		t.Fatal("chunk should not be nil")
	}
	if chunk.ID != state.messageID {
		t.Errorf("chunk ID = %q, want %q", chunk.ID, state.messageID)
	}
	if chunk.Model != state.model {
		t.Errorf("chunk Model = %q, want %q", chunk.Model, state.model)
	}
	if chunk.Created != state.created {
		t.Errorf("chunk Created = %d, want %d", chunk.Created, state.created)
	}
	if chunk.Usage == nil {
		t.Fatal("usage should not be nil")
	}
	if chunk.Usage.PromptTokens != 10 || chunk.Usage.CompletionTokens != 5 || chunk.Usage.TotalTokens != 15 {
		t.Errorf("usage = %+v, want prompt=10 completion=5 total=15", chunk.Usage)
	}
}

func TestBuildConverseMessage_PreservesMixedTextAndImageParts(t *testing.T) {
	msg := models.Message{
		Role: "user",
		Content: json.RawMessage(`[
			{"type":"text","text":"look at this"},
			{"type":"image_url","image_url":{"url":"data:image/png;base64,aGVsbG8="}}
		]`),
	}

	cm := buildConverseMessage(msg)

	if cm.Role != "user" {
		t.Errorf("role = %q, want %q", cm.Role, "user")
	}
	if len(cm.Content) != 2 {
		t.Fatalf("content length = %d, want 2", len(cm.Content))
	}
	if cm.Content[0].Text == nil || *cm.Content[0].Text != "look at this" {
		t.Fatalf("first part text = %v, want %q", cm.Content[0].Text, "look at this")
	}
	if cm.Content[1].Image == nil {
		t.Fatal("second part image should not be nil")
	}
}

func TestBuildConverseMessage_AssistantWithToolCalls(t *testing.T) {
	msg := models.Message{
		Role:    "assistant",
		Content: json.RawMessage(`"Let me check that"`),
		ToolCalls: []models.ToolCall{{
			ID:   "call_123",
			Type: "function",
			Function: models.FunctionCall{
				Name:      "get_weather",
				Arguments: `{"city":"Paris"}`,
			},
		}},
	}

	cm := buildConverseMessage(msg)

	if cm.Role != "assistant" {
		t.Errorf("role = %q, want %q", cm.Role, "assistant")
	}
	if len(cm.Content) != 2 {
		t.Fatalf("content length = %d, want 2", len(cm.Content))
	}
	if cm.Content[0].Text == nil || *cm.Content[0].Text != "Let me check that" {
		t.Fatalf("first part text = %v, want %q", cm.Content[0].Text, "Let me check that")
	}
	if cm.Content[1].ToolUse == nil {
		t.Fatal("second part toolUse should not be nil")
	}
	if cm.Content[1].ToolUse.ToolUseID != "call_123" {
		t.Errorf("toolUseId = %q, want %q", cm.Content[1].ToolUse.ToolUseID, "call_123")
	}
	if cm.Content[1].ToolUse.Name != "get_weather" {
		t.Errorf("tool name = %q, want %q", cm.Content[1].ToolUse.Name, "get_weather")
	}
}

func TestBuildConverseMessage_ToolRoleBecomesToolResult(t *testing.T) {
	msg := models.Message{
		Role:       "tool",
		ToolCallID: "call_456",
		Content:    json.RawMessage(`"Weather is sunny"`),
	}

	cm := buildConverseMessage(msg)

	if cm.Role != "user" {
		t.Errorf("role = %q, want %q", cm.Role, "user")
	}
	if len(cm.Content) != 1 {
		t.Fatalf("content length = %d, want 1", len(cm.Content))
	}
	if cm.Content[0].ToolResult == nil {
		t.Fatal("toolResult should not be nil")
	}
	if cm.Content[0].ToolResult.ToolUseID != "call_456" {
		t.Errorf("toolUseId = %q, want %q", cm.Content[0].ToolResult.ToolUseID, "call_456")
	}
	if len(cm.Content[0].ToolResult.Content) != 1 {
		t.Fatalf("toolResult content length = %d, want 1", len(cm.Content[0].ToolResult.Content))
	}
	if cm.Content[0].ToolResult.Content[0].Text == nil || *cm.Content[0].ToolResult.Content[0].Text != "Weather is sunny" {
		t.Fatalf("toolResult text = %v, want %q", cm.Content[0].ToolResult.Content[0].Text, "Weather is sunny")
	}
}

func TestParseConverseEvent_ContentBlockStartToolUse(t *testing.T) {
	state := &converseStreamState{messageID: "msg_1", model: "model-1"}
	msg := &eventStreamMessage{
		Headers: map[string]string{":message-type": "event", ":event-type": "contentBlockStart"},
		Payload: []byte(`{"start":{"toolUse":{"toolUseId":"call_1","name":"get_weather"}},"contentBlockIndex":3}`),
	}

	chunk, done, err := state.parseConverseEvent(msg)

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if done {
		t.Error("done should be false")
	}
	if chunk == nil {
		t.Fatal("chunk should not be nil")
	}
	if len(chunk.Choices[0].Delta.ToolCalls) != 1 {
		t.Fatalf("tool_calls length = %d, want 1", len(chunk.Choices[0].Delta.ToolCalls))
	}
	tc := chunk.Choices[0].Delta.ToolCalls[0]
	if tc.Index != 0 || tc.ID != "call_1" {
		t.Errorf("tool call = %+v, want index=0 id=call_1", tc)
	}
	if tc.Function == nil || tc.Function.Name != "get_weather" {
		t.Errorf("tool function = %+v, want name=get_weather", tc.Function)
	}
}

func TestParseConverseEvent_ContentBlockDeltaToolUseInput(t *testing.T) {
	state := &converseStreamState{messageID: "msg_1", model: "model-1"}

	_, _, err := state.parseConverseEvent(&eventStreamMessage{
		Headers: map[string]string{":message-type": "event", ":event-type": "contentBlockStart"},
		Payload: []byte(`{"start":{"toolUse":{"toolUseId":"call_1","name":"get_weather"}},"contentBlockIndex":3}`),
	})
	if err != nil {
		t.Fatalf("unexpected setup error: %v", err)
	}

	msg := &eventStreamMessage{
		Headers: map[string]string{":message-type": "event", ":event-type": "contentBlockDelta"},
		Payload: []byte(`{"delta":{"toolUse":{"input":"{\"city\""}},"contentBlockIndex":3}`),
	}

	chunk, done, err := state.parseConverseEvent(msg)

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if done {
		t.Error("done should be false")
	}
	if chunk == nil {
		t.Fatal("chunk should not be nil")
	}
	if len(chunk.Choices[0].Delta.ToolCalls) != 1 {
		t.Fatalf("tool_calls length = %d, want 1", len(chunk.Choices[0].Delta.ToolCalls))
	}
	tc := chunk.Choices[0].Delta.ToolCalls[0]
	if tc.Index != 0 {
		t.Errorf("tool call index = %d, want 0", tc.Index)
	}
	if tc.Function == nil || tc.Function.Arguments != `{"city"` {
		t.Errorf("tool function = %+v, want args=%q", tc.Function, `{"city"`)
	}
}

func TestParseConverseEvent_MessageStopToolUseReason(t *testing.T) {
	state := &converseStreamState{messageID: "msg_1", model: "model-1"}
	msg := &eventStreamMessage{
		Headers: map[string]string{":message-type": "event", ":event-type": "messageStop"},
		Payload: []byte(`{"stopReason":"tool_use"}`),
	}

	chunk, done, err := state.parseConverseEvent(msg)

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if done {
		t.Error("done should be false until metadata")
	}
	if chunk == nil || chunk.Choices[0].FinishReason == nil {
		t.Fatal("finish_reason chunk should be present")
	}
	if *chunk.Choices[0].FinishReason != "tool_calls" {
		t.Errorf("finish_reason = %q, want %q", *chunk.Choices[0].FinishReason, "tool_calls")
	}
}
