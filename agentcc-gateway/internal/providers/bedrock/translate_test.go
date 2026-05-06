package bedrock

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ===========================================================================
// translateRequest tests
// ===========================================================================

func TestTranslateRequest_BasicChat(t *testing.T) {
	temp := 0.7
	req := &models.ChatCompletionRequest{
		Model: "anthropic.claude-3-sonnet-20240229-v1:0",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hello, how are you?"`)},
		},
		Temperature: &temp,
	}

	br, modelID := translateRequest(req)

	if modelID != "anthropic.claude-3-sonnet-20240229-v1:0" {
		t.Errorf("modelID = %q, want %q", modelID, "anthropic.claude-3-sonnet-20240229-v1:0")
	}
	if br.AnthropicVersion != "bedrock-2023-05-31" {
		t.Errorf("AnthropicVersion = %q, want %q", br.AnthropicVersion, "bedrock-2023-05-31")
	}
	if len(br.Messages) != 1 {
		t.Fatalf("expected 1 message, got %d", len(br.Messages))
	}
	if br.Messages[0].Role != "user" {
		t.Errorf("message role = %q, want %q", br.Messages[0].Role, "user")
	}
	if br.Temperature == nil || *br.Temperature != 0.7 {
		t.Errorf("temperature = %v, want 0.7", br.Temperature)
	}
}

func TestTranslateRequest_AnthropicVersion(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "claude-3-haiku",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"test"`)},
		},
	}

	br, _ := translateRequest(req)

	if br.AnthropicVersion != "bedrock-2023-05-31" {
		t.Errorf("AnthropicVersion = %q, want %q", br.AnthropicVersion, "bedrock-2023-05-31")
	}
}

func TestTranslateRequest_ModelWithSlash(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "bedrock/anthropic.claude-3-sonnet-20240229-v1:0",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
	}

	_, modelID := translateRequest(req)
	if modelID != "anthropic.claude-3-sonnet-20240229-v1:0" {
		t.Errorf("modelID = %q, want %q (should strip prefix before slash)", modelID, "anthropic.claude-3-sonnet-20240229-v1:0")
	}
}

func TestTranslateRequest_ModelWithMultipleSlashes(t *testing.T) {
	// resolveModelName takes everything after the first slash
	req := &models.ChatCompletionRequest{
		Model: "provider/sub/model-id",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"test"`)},
		},
	}

	_, modelID := translateRequest(req)
	// The implementation returns everything after the first '/'
	if modelID != "sub/model-id" {
		t.Errorf("modelID = %q, want %q", modelID, "sub/model-id")
	}
}

func TestTranslateRequest_ResponseFormatJSONSchema(t *testing.T) {
	schema := json.RawMessage(`{"name":"answer","schema":{"type":"object","properties":{"result":{"type":"string"}},"required":["result"],"additionalProperties":false},"strict":true}`)
	req := &models.ChatCompletionRequest{
		Model: "anthropic.claude-3-sonnet-20240229-v1:0",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Return structured JSON"`)},
		},
		ResponseFormat: &models.ResponseFormat{
			Type:       "json_schema",
			JSONSchema: schema,
		},
	}

	br, _ := translateRequest(req)

	if br.OutputConfig == nil || br.OutputConfig.Format == nil {
		t.Fatal("OutputConfig.Format should not be nil")
	}
	if br.OutputConfig.Format.Type != "json_schema" {
		t.Fatalf("format.type = %q, want %q", br.OutputConfig.Format.Type, "json_schema")
	}

	var parsed map[string]any
	if err := json.Unmarshal(br.OutputConfig.Format.Schema, &parsed); err != nil {
		t.Fatalf("failed to unmarshal schema: %v", err)
	}
	if parsed["type"] != "object" {
		t.Fatalf("schema type = %v, want object", parsed["type"])
	}
	if _, ok := parsed["strict"]; ok {
		t.Fatal("schema should not include OpenAI wrapper fields like strict")
	}
}

func TestTranslateRequest_SystemMessageExtraction(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet",
		Messages: []models.Message{
			{Role: "system", Content: json.RawMessage(`"You are a helpful assistant."`)},
			{Role: "user", Content: json.RawMessage(`"Hello"`)},
		},
	}

	br, _ := translateRequest(req)

	if br.System != "You are a helpful assistant." {
		t.Errorf("system = %q, want %q", br.System, "You are a helpful assistant.")
	}
	if len(br.Messages) != 1 {
		t.Fatalf("expected 1 non-system message, got %d", len(br.Messages))
	}
	if br.Messages[0].Role != "user" {
		t.Errorf("remaining message role = %q, want %q", br.Messages[0].Role, "user")
	}
}

func TestTranslateRequest_SystemMessageExtractionFromTextParts(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet",
		Messages: []models.Message{
			{
				Role: "system",
				Content: json.RawMessage(`[
					{"type":"text","text":"You are a helpful assistant."},
					{"type":"text","text":" Follow the rubric exactly."}
				]`),
			},
			{Role: "user", Content: json.RawMessage(`"Hello"`)},
		},
	}

	br, _ := translateRequest(req)

	want := "You are a helpful assistant. Follow the rubric exactly."
	if br.System != want {
		t.Errorf("system = %q, want %q", br.System, want)
	}
}

func TestTranslateRequest_MultipleSystemMessages(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet",
		Messages: []models.Message{
			{Role: "system", Content: json.RawMessage(`"First system instruction."`)},
			{Role: "system", Content: json.RawMessage(`"Second system instruction."`)},
			{Role: "user", Content: json.RawMessage(`"Go"`)},
		},
	}

	br, _ := translateRequest(req)

	expected := "First system instruction.\n\nSecond system instruction."
	if br.System != expected {
		t.Errorf("system = %q, want %q", br.System, expected)
	}
	if len(br.Messages) != 1 {
		t.Fatalf("expected 1 non-system message, got %d", len(br.Messages))
	}
}

func TestTranslateRequest_NoSystemMessage(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hello"`)},
		},
	}

	br, _ := translateRequest(req)

	if br.System != "" {
		t.Errorf("system should be empty, got %q", br.System)
	}
}

func TestTranslateRequest_MaxTokensDefault4096(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
	}

	br, _ := translateRequest(req)

	if br.MaxTokens != 4096 {
		t.Errorf("max_tokens = %d, want 4096 (default)", br.MaxTokens)
	}
}

func TestTranslateRequest_MaxTokensExplicit(t *testing.T) {
	maxTok := 1024
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
		MaxTokens: &maxTok,
	}

	br, _ := translateRequest(req)

	if br.MaxTokens != 1024 {
		t.Errorf("max_tokens = %d, want 1024", br.MaxTokens)
	}
}

func TestTranslateRequest_MaxCompletionTokens(t *testing.T) {
	maxCompTok := 2048
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
		MaxCompletionTokens: &maxCompTok,
	}

	br, _ := translateRequest(req)

	if br.MaxTokens != 2048 {
		t.Errorf("max_tokens = %d, want 2048 (from MaxCompletionTokens)", br.MaxTokens)
	}
}

func TestTranslateRequest_MaxTokensTakesPrecedenceOverMaxCompletionTokens(t *testing.T) {
	maxTok := 500
	maxCompTok := 1000
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
		MaxTokens:           &maxTok,
		MaxCompletionTokens: &maxCompTok,
	}

	br, _ := translateRequest(req)

	if br.MaxTokens != 500 {
		t.Errorf("max_tokens = %d, want 500 (MaxTokens takes precedence)", br.MaxTokens)
	}
}

func TestTranslateRequest_StopSequences_Array(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
		Stop: json.RawMessage(`["stop1","stop2"]`),
	}

	br, _ := translateRequest(req)

	if len(br.StopSequences) != 2 {
		t.Fatalf("expected 2 stop sequences, got %d", len(br.StopSequences))
	}
	if br.StopSequences[0] != "stop1" || br.StopSequences[1] != "stop2" {
		t.Errorf("stop sequences = %v, want [stop1 stop2]", br.StopSequences)
	}
}

func TestTranslateRequest_StopSequences_SingleString(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
		Stop: json.RawMessage(`"END"`),
	}

	br, _ := translateRequest(req)

	if len(br.StopSequences) != 1 {
		t.Fatalf("expected 1 stop sequence, got %d", len(br.StopSequences))
	}
	if br.StopSequences[0] != "END" {
		t.Errorf("stop sequence = %q, want %q", br.StopSequences[0], "END")
	}
}

func TestTranslateRequest_StopSequences_Nil(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
	}

	br, _ := translateRequest(req)

	if br.StopSequences != nil {
		t.Errorf("stop sequences should be nil when Stop is not set, got %v", br.StopSequences)
	}
}

func TestTranslateRequest_TopP(t *testing.T) {
	topP := 0.9
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
		TopP: &topP,
	}

	br, _ := translateRequest(req)

	if br.TopP == nil || *br.TopP != 0.9 {
		t.Errorf("top_p = %v, want 0.9", br.TopP)
	}
}

func TestTranslateRequest_Tools(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"What is the weather?"`)},
		},
		Tools: []models.Tool{
			{
				Type: "function",
				Function: models.ToolFunction{
					Name:        "get_weather",
					Description: "Get weather info",
					Parameters:  json.RawMessage(`{"type":"object","properties":{"city":{"type":"string"}}}`),
				},
			},
		},
	}

	br, _ := translateRequest(req)

	if len(br.Tools) != 1 {
		t.Fatalf("expected 1 tool, got %d", len(br.Tools))
	}
	if br.Tools[0].Name != "get_weather" {
		t.Errorf("tool name = %q, want %q", br.Tools[0].Name, "get_weather")
	}
	if br.Tools[0].Description != "Get weather info" {
		t.Errorf("tool description = %q, want %q", br.Tools[0].Description, "Get weather info")
	}
	if string(br.Tools[0].InputSchema) != `{"type":"object","properties":{"city":{"type":"string"}}}` {
		t.Errorf("tool input_schema = %s", string(br.Tools[0].InputSchema))
	}
}

func TestTranslateRequest_Tools_NonFunctionTypeSkipped(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
		Tools: []models.Tool{
			{
				Type: "other_type",
				Function: models.ToolFunction{
					Name:        "should_skip",
					Description: "Skipped tool",
				},
			},
			{
				Type: "function",
				Function: models.ToolFunction{
					Name:        "should_keep",
					Description: "Kept tool",
					Parameters:  json.RawMessage(`{}`),
				},
			},
		},
	}

	br, _ := translateRequest(req)

	if len(br.Tools) != 1 {
		t.Fatalf("expected 1 tool (non-function skipped), got %d", len(br.Tools))
	}
	if br.Tools[0].Name != "should_keep" {
		t.Errorf("tool name = %q, want %q", br.Tools[0].Name, "should_keep")
	}
}

func TestTranslateRequest_ToolChoice_Auto(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
		ToolChoice: json.RawMessage(`"auto"`),
	}

	br, _ := translateRequest(req)

	if br.ToolChoice == nil {
		t.Fatal("tool_choice should not be nil for auto")
	}
	if br.ToolChoice.Type != "auto" {
		t.Errorf("tool_choice type = %q, want %q", br.ToolChoice.Type, "auto")
	}
}

func TestTranslateRequest_ToolChoice_Required(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
		ToolChoice: json.RawMessage(`"required"`),
	}

	br, _ := translateRequest(req)

	if br.ToolChoice == nil {
		t.Fatal("tool_choice should not be nil for required")
	}
	if br.ToolChoice.Type != "any" {
		t.Errorf("tool_choice type = %q, want %q (required maps to any)", br.ToolChoice.Type, "any")
	}
}

func TestTranslateRequest_ToolChoice_SpecificFunction(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
		ToolChoice: json.RawMessage(`{"type":"function","function":{"name":"get_weather"}}`),
	}

	br, _ := translateRequest(req)

	if br.ToolChoice == nil {
		t.Fatal("tool_choice should not be nil for specific function")
	}
	if br.ToolChoice.Type != "tool" {
		t.Errorf("tool_choice type = %q, want %q", br.ToolChoice.Type, "tool")
	}
	if br.ToolChoice.Name != "get_weather" {
		t.Errorf("tool_choice name = %q, want %q", br.ToolChoice.Name, "get_weather")
	}
}

func TestTranslateRequest_ToolChoice_None(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
		ToolChoice: json.RawMessage(`"none"`),
	}

	br, _ := translateRequest(req)

	// "none" is not mapped and returns nil
	if br.ToolChoice != nil {
		t.Errorf("tool_choice should be nil for 'none', got %+v", br.ToolChoice)
	}
}

func TestTranslateRequest_ToolChoice_NotSet(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
	}

	br, _ := translateRequest(req)

	if br.ToolChoice != nil {
		t.Errorf("tool_choice should be nil when not set, got %+v", br.ToolChoice)
	}
}

func TestTranslateRequest_StreamFieldNotInBody(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
		Stream: true,
	}

	br, _ := translateRequest(req)

	// The Stream field is json:"-" so it should not appear in the marshaled body
	body, err := json.Marshal(br)
	if err != nil {
		t.Fatalf("marshal error: %v", err)
	}

	var parsed map[string]interface{}
	if err := json.Unmarshal(body, &parsed); err != nil {
		t.Fatalf("unmarshal error: %v", err)
	}
	if _, ok := parsed["stream"]; ok {
		t.Error("stream field should not appear in marshaled bedrock request body")
	}
}

func TestTranslateRequest_ModelFieldNotInBody(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "claude-3-sonnet",
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"Hi"`)},
		},
	}

	br, _ := translateRequest(req)

	body, err := json.Marshal(br)
	if err != nil {
		t.Fatalf("marshal error: %v", err)
	}

	var parsed map[string]interface{}
	if err := json.Unmarshal(body, &parsed); err != nil {
		t.Fatalf("unmarshal error: %v", err)
	}
	if _, ok := parsed["model"]; ok {
		t.Error("model field should not appear in marshaled bedrock request body (it goes in the URL)")
	}
}

// ===========================================================================
// translateMessage tests
// ===========================================================================

func TestTranslateMessage_StandardText(t *testing.T) {
	msg := models.Message{
		Role:    "user",
		Content: json.RawMessage(`"Hello there"`),
	}

	bm := translateMessage(msg)

	if bm.Role != "user" {
		t.Errorf("role = %q, want %q", bm.Role, "user")
	}

	var blocks []bedrockContentBlock
	if err := json.Unmarshal(bm.Content, &blocks); err != nil {
		t.Fatalf("failed to unmarshal content blocks: %v", err)
	}
	if len(blocks) != 1 {
		t.Fatalf("expected 1 block, got %d", len(blocks))
	}
	if blocks[0].Type != "text" {
		t.Errorf("block type = %q, want %q", blocks[0].Type, "text")
	}
	if blocks[0].Text != "Hello there" {
		t.Errorf("block text = %q, want %q", blocks[0].Text, "Hello there")
	}
}

func TestTranslateMessage_ToolResult(t *testing.T) {
	msg := models.Message{
		Role:       "tool",
		Content:    json.RawMessage(`"The weather is sunny"`),
		ToolCallID: "call_123",
	}

	bm := translateMessage(msg)

	if bm.Role != "tool" {
		t.Errorf("role = %q, want %q", bm.Role, "tool")
	}

	var blocks []bedrockContentBlock
	if err := json.Unmarshal(bm.Content, &blocks); err != nil {
		t.Fatalf("failed to unmarshal content blocks: %v", err)
	}
	if len(blocks) != 1 {
		t.Fatalf("expected 1 block, got %d", len(blocks))
	}
	if blocks[0].Type != "tool_result" {
		t.Errorf("block type = %q, want %q", blocks[0].Type, "tool_result")
	}
	if blocks[0].ToolUseID != "call_123" {
		t.Errorf("tool_use_id = %q, want %q", blocks[0].ToolUseID, "call_123")
	}
}

func TestTranslateMessage_AssistantWithToolCalls(t *testing.T) {
	msg := models.Message{
		Role:    "assistant",
		Content: json.RawMessage(`"Let me check the weather"`),
		ToolCalls: []models.ToolCall{
			{
				ID:   "call_456",
				Type: "function",
				Function: models.FunctionCall{
					Name:      "get_weather",
					Arguments: `{"city":"London"}`,
				},
			},
		},
	}

	bm := translateMessage(msg)

	if bm.Role != "assistant" {
		t.Errorf("role = %q, want %q", bm.Role, "assistant")
	}

	var blocks []bedrockContentBlock
	if err := json.Unmarshal(bm.Content, &blocks); err != nil {
		t.Fatalf("failed to unmarshal content blocks: %v", err)
	}
	if len(blocks) != 2 {
		t.Fatalf("expected 2 blocks (text + tool_use), got %d", len(blocks))
	}
	if blocks[0].Type != "text" {
		t.Errorf("first block type = %q, want %q", blocks[0].Type, "text")
	}
	if blocks[0].Text != "Let me check the weather" {
		t.Errorf("first block text = %q, want %q", blocks[0].Text, "Let me check the weather")
	}
	if blocks[1].Type != "tool_use" {
		t.Errorf("second block type = %q, want %q", blocks[1].Type, "tool_use")
	}
	if blocks[1].ID != "call_456" {
		t.Errorf("tool_use id = %q, want %q", blocks[1].ID, "call_456")
	}
	if blocks[1].Name != "get_weather" {
		t.Errorf("tool_use name = %q, want %q", blocks[1].Name, "get_weather")
	}

	var input map[string]string
	if err := json.Unmarshal(blocks[1].Input, &input); err != nil {
		t.Fatalf("failed to unmarshal tool input: %v", err)
	}
	if input["city"] != "London" {
		t.Errorf("tool input city = %q, want %q", input["city"], "London")
	}
}

func TestTranslateMessage_AssistantToolCallsWithoutText(t *testing.T) {
	msg := models.Message{
		Role: "assistant",
		ToolCalls: []models.ToolCall{
			{
				ID:   "call_789",
				Type: "function",
				Function: models.FunctionCall{
					Name:      "search",
					Arguments: `{"query":"test"}`,
				},
			},
		},
	}

	bm := translateMessage(msg)

	var blocks []bedrockContentBlock
	if err := json.Unmarshal(bm.Content, &blocks); err != nil {
		t.Fatalf("failed to unmarshal content blocks: %v", err)
	}

	// Only tool_use block, no text block since content is empty
	if len(blocks) != 1 {
		t.Fatalf("expected 1 block (tool_use only, no text), got %d", len(blocks))
	}
	if blocks[0].Type != "tool_use" {
		t.Errorf("block type = %q, want %q", blocks[0].Type, "tool_use")
	}
}

func TestTranslateMessage_MultiPartContent(t *testing.T) {
	// When content is an array of parts
	msg := models.Message{
		Role:    "user",
		Content: json.RawMessage(`[{"type":"text","text":"Hello from array"}]`),
	}

	bm := translateMessage(msg)

	// extractTextContent should get the text from the array
	var blocks []bedrockContentBlock
	if err := json.Unmarshal(bm.Content, &blocks); err != nil {
		t.Fatalf("failed to unmarshal content: %v", err)
	}
	if len(blocks) != 1 {
		t.Fatalf("expected 1 block, got %d", len(blocks))
	}
	if blocks[0].Text != "Hello from array" {
		t.Errorf("text = %q, want %q", blocks[0].Text, "Hello from array")
	}
}

// ===========================================================================
// translateVisionContent tests
// ===========================================================================

func TestTranslateVisionContent_Bedrock_WithImages(t *testing.T) {
	content := json.RawMessage(`[
		{"type":"text","text":"What is in this image?"},
		{"type":"image_url","image_url":{"url":"data:image/png;base64,iVBORw0KGgo="}}
	]`)

	blocks := translateVisionContent(content)
	if blocks == nil {
		t.Fatal("expected non-nil blocks for content with images")
	}
	if len(blocks) != 2 {
		t.Fatalf("blocks length = %d, want 2", len(blocks))
	}

	// First block: text.
	if blocks[0].Type != "text" {
		t.Errorf("blocks[0].Type = %q, want %q", blocks[0].Type, "text")
	}
	if blocks[0].Text != "What is in this image?" {
		t.Errorf("blocks[0].Text = %q, want %q", blocks[0].Text, "What is in this image?")
	}

	// Second block: image with base64 source.
	if blocks[1].Type != "image" {
		t.Errorf("blocks[1].Type = %q, want %q", blocks[1].Type, "image")
	}
	if blocks[1].Source == nil {
		t.Fatal("blocks[1].Source should not be nil")
	}
	if blocks[1].Source.Type != "base64" {
		t.Errorf("Source.Type = %q, want %q", blocks[1].Source.Type, "base64")
	}
	if blocks[1].Source.MediaType != "image/png" {
		t.Errorf("Source.MediaType = %q, want %q", blocks[1].Source.MediaType, "image/png")
	}
	if blocks[1].Source.Data != "iVBORw0KGgo=" {
		t.Errorf("Source.Data = %q, want %q", blocks[1].Source.Data, "iVBORw0KGgo=")
	}
}

func TestTranslateVisionContent_Bedrock_NoImages(t *testing.T) {
	content := json.RawMessage(`[{"type":"text","text":"Just text, no images"}]`)

	blocks := translateVisionContent(content)
	if blocks != nil {
		t.Errorf("expected nil for text-only content array, got %d blocks", len(blocks))
	}
}

func TestTranslateVisionContent_Bedrock_EmptyContent(t *testing.T) {
	blocks := translateVisionContent(nil)
	if blocks != nil {
		t.Errorf("expected nil for nil content, got %d blocks", len(blocks))
	}
}

func TestTranslateVisionContent_Bedrock_InvalidJSON(t *testing.T) {
	blocks := translateVisionContent(json.RawMessage(`{not valid`))
	if blocks != nil {
		t.Errorf("expected nil for invalid JSON, got %d blocks", len(blocks))
	}
}

func TestTranslateVisionContent_Bedrock_MultipleImages(t *testing.T) {
	content := json.RawMessage(`[
		{"type":"text","text":"Compare these"},
		{"type":"image_url","image_url":{"url":"data:image/png;base64,abc="}},
		{"type":"image_url","image_url":{"url":"https://example.com/img.jpg"}}
	]`)

	blocks := translateVisionContent(content)
	if blocks == nil {
		t.Fatal("expected non-nil blocks")
	}
	if len(blocks) != 3 {
		t.Fatalf("blocks length = %d, want 3", len(blocks))
	}
	if blocks[0].Type != "text" || blocks[0].Text != "Compare these" {
		t.Errorf("blocks[0] should be text 'Compare these'")
	}
	if blocks[1].Type != "image" || blocks[1].Source.Type != "base64" {
		t.Errorf("blocks[1] should be base64 image")
	}
	if blocks[2].Type != "image" || blocks[2].Source.Type != "url" {
		t.Errorf("blocks[2] should be URL image")
	}
}

// ===========================================================================
// convertImageToBedrock tests
// ===========================================================================

func TestConvertImageToBedrock_DataURI(t *testing.T) {
	block := convertImageToBedrock("data:image/png;base64,iVBORw0KGgo=")
	if block == nil {
		t.Fatal("expected non-nil block")
	}
	if block.Type != "image" {
		t.Errorf("Type = %q, want %q", block.Type, "image")
	}
	if block.Source == nil {
		t.Fatal("Source should not be nil")
	}
	if block.Source.Type != "base64" {
		t.Errorf("Source.Type = %q, want %q", block.Source.Type, "base64")
	}
	if block.Source.MediaType != "image/png" {
		t.Errorf("Source.MediaType = %q, want %q", block.Source.MediaType, "image/png")
	}
	if block.Source.Data != "iVBORw0KGgo=" {
		t.Errorf("Source.Data = %q, want %q", block.Source.Data, "iVBORw0KGgo=")
	}
}

func TestConvertImageToBedrock_DataURI_JPEG(t *testing.T) {
	block := convertImageToBedrock("data:image/jpeg;base64,/9j/4AAQSkZJRg==")
	if block == nil {
		t.Fatal("expected non-nil block")
	}
	if block.Source.MediaType != "image/jpeg" {
		t.Errorf("Source.MediaType = %q, want %q", block.Source.MediaType, "image/jpeg")
	}
	if block.Source.Data != "/9j/4AAQSkZJRg==" {
		t.Errorf("Source.Data = %q, want %q", block.Source.Data, "/9j/4AAQSkZJRg==")
	}
}

func TestConvertImageToBedrock_HTTPURL(t *testing.T) {
	block := convertImageToBedrock("https://example.com/photo.jpg")
	if block == nil {
		t.Fatal("expected non-nil block")
	}
	if block.Type != "image" {
		t.Errorf("Type = %q, want %q", block.Type, "image")
	}
	if block.Source == nil {
		t.Fatal("Source should not be nil")
	}
	if block.Source.Type != "url" {
		t.Errorf("Source.Type = %q, want %q", block.Source.Type, "url")
	}
	if block.Source.URL != "https://example.com/photo.jpg" {
		t.Errorf("Source.URL = %q, want %q", block.Source.URL, "https://example.com/photo.jpg")
	}
}

func TestConvertImageToBedrock_InvalidDataURI(t *testing.T) {
	// No semicolon in data URI -> parseDataURI returns empty data -> nil block.
	block := convertImageToBedrock("data:image/png")
	if block != nil {
		t.Errorf("expected nil for invalid data URI without semicolon, got %+v", block)
	}
}

// ===========================================================================
// parseDataURI tests (Bedrock)
// ===========================================================================

func TestParseDataURI_Bedrock_PNG(t *testing.T) {
	mediaType, data := parseDataURI("data:image/png;base64,iVBORw0KGgo=")
	if mediaType != "image/png" {
		t.Errorf("mediaType = %q, want %q", mediaType, "image/png")
	}
	if data != "iVBORw0KGgo=" {
		t.Errorf("data = %q, want %q", data, "iVBORw0KGgo=")
	}
}

func TestParseDataURI_Bedrock_JPEG(t *testing.T) {
	mediaType, data := parseDataURI("data:image/jpeg;base64,/9j/4AAQ")
	if mediaType != "image/jpeg" {
		t.Errorf("mediaType = %q, want %q", mediaType, "image/jpeg")
	}
	if data != "/9j/4AAQ" {
		t.Errorf("data = %q, want %q", data, "/9j/4AAQ")
	}
}

func TestParseDataURI_Bedrock_NoSemicolon(t *testing.T) {
	mediaType, data := parseDataURI("data:image/png")
	if mediaType != "" {
		t.Errorf("mediaType = %q, want empty", mediaType)
	}
	if data != "" {
		t.Errorf("data = %q, want empty", data)
	}
}

func TestParseDataURI_Bedrock_EmptyData(t *testing.T) {
	mediaType, data := parseDataURI("data:image/gif;base64,")
	if mediaType != "image/gif" {
		t.Errorf("mediaType = %q, want %q", mediaType, "image/gif")
	}
	if data != "" {
		t.Errorf("data = %q, want empty", data)
	}
}

// ===========================================================================
// translateMessage with vision content tests (Bedrock)
// ===========================================================================

func TestTranslateMessage_Bedrock_VisionContent_ImageOnly(t *testing.T) {
	// When content has only image_url parts (no text), extractTextContent returns ""
	// and the vision path is taken via translateVisionContent.
	msg := models.Message{
		Role: "user",
		Content: json.RawMessage(`[
			{"type":"image_url","image_url":{"url":"data:image/png;base64,iVBORw0KGgo="}}
		]`),
	}

	bm := translateMessage(msg)

	if bm.Role != "user" {
		t.Errorf("role = %q, want %q", bm.Role, "user")
	}

	var blocks []bedrockContentBlock
	if err := json.Unmarshal(bm.Content, &blocks); err != nil {
		t.Fatalf("failed to unmarshal content: %v", err)
	}
	if len(blocks) != 1 {
		t.Fatalf("blocks length = %d, want 1", len(blocks))
	}
	if blocks[0].Type != "image" {
		t.Errorf("blocks[0].Type = %q, want %q", blocks[0].Type, "image")
	}
	if blocks[0].Source == nil {
		t.Fatal("blocks[0].Source should not be nil")
	}
	if blocks[0].Source.Type != "base64" {
		t.Errorf("Source.Type = %q, want %q", blocks[0].Source.Type, "base64")
	}
	if blocks[0].Source.MediaType != "image/png" {
		t.Errorf("Source.MediaType = %q, want %q", blocks[0].Source.MediaType, "image/png")
	}
	if blocks[0].Source.Data != "iVBORw0KGgo=" {
		t.Errorf("Source.Data = %q, want %q", blocks[0].Source.Data, "iVBORw0KGgo=")
	}
}

func TestTranslateMessage_Bedrock_VisionContent_WithText(t *testing.T) {
	// When content has both text and image_url parts, the vision path handles both.
	msg := models.Message{
		Role: "user",
		Content: json.RawMessage(`[
			{"type":"text","text":"Describe this image"},
			{"type":"image_url","image_url":{"url":"data:image/png;base64,iVBORw0KGgo="}}
		]`),
	}

	bm := translateMessage(msg)

	if bm.Role != "user" {
		t.Errorf("role = %q, want %q", bm.Role, "user")
	}

	var blocks []bedrockContentBlock
	if err := json.Unmarshal(bm.Content, &blocks); err != nil {
		t.Fatalf("failed to unmarshal content: %v", err)
	}
	// Vision path preserves both text and image parts.
	if len(blocks) != 2 {
		t.Fatalf("blocks length = %d, want 2 (text + image)", len(blocks))
	}
	if blocks[0].Type != "text" {
		t.Errorf("blocks[0].Type = %q, want %q", blocks[0].Type, "text")
	}
	if blocks[0].Text != "Describe this image" {
		t.Errorf("blocks[0].Text = %q, want %q", blocks[0].Text, "Describe this image")
	}
	if blocks[1].Type != "image" {
		t.Errorf("blocks[1].Type = %q, want %q", blocks[1].Type, "image")
	}
	if blocks[1].Source == nil {
		t.Fatal("blocks[1].Source is nil, want non-nil")
	}
	if blocks[1].Source.Type != "base64" {
		t.Errorf("blocks[1].Source.Type = %q, want %q", blocks[1].Source.Type, "base64")
	}
}

func TestTranslateMessage_Bedrock_VisionContent_HTTPUrl_ImageOnly(t *testing.T) {
	// Image-only content with HTTP URL (no text parts).
	msg := models.Message{
		Role: "user",
		Content: json.RawMessage(`[
			{"type":"image_url","image_url":{"url":"https://example.com/img.jpg"}}
		]`),
	}

	bm := translateMessage(msg)

	var blocks []bedrockContentBlock
	if err := json.Unmarshal(bm.Content, &blocks); err != nil {
		t.Fatalf("failed to unmarshal content: %v", err)
	}
	if len(blocks) != 1 {
		t.Fatalf("blocks length = %d, want 1", len(blocks))
	}
	if blocks[0].Source.Type != "url" {
		t.Errorf("Source.Type = %q, want %q", blocks[0].Source.Type, "url")
	}
	if blocks[0].Source.URL != "https://example.com/img.jpg" {
		t.Errorf("Source.URL = %q, want %q", blocks[0].Source.URL, "https://example.com/img.jpg")
	}
}

func TestTranslateMessage_Bedrock_VisionContent_TextOnlyFallback(t *testing.T) {
	// Text-only array should fall through to extractTextContent, not translateVisionContent.
	msg := models.Message{
		Role:    "user",
		Content: json.RawMessage(`[{"type":"text","text":"Just text"}]`),
	}

	bm := translateMessage(msg)

	var blocks []bedrockContentBlock
	if err := json.Unmarshal(bm.Content, &blocks); err != nil {
		t.Fatalf("failed to unmarshal content: %v", err)
	}
	if len(blocks) != 1 {
		t.Fatalf("blocks length = %d, want 1", len(blocks))
	}
	if blocks[0].Type != "text" {
		t.Errorf("blocks[0].Type = %q, want %q", blocks[0].Type, "text")
	}
	if blocks[0].Text != "Just text" {
		t.Errorf("blocks[0].Text = %q, want %q", blocks[0].Text, "Just text")
	}
}

func TestTranslateMessage_Bedrock_TextOnlyFallbackPreservesAllTextParts(t *testing.T) {
	msg := models.Message{
		Role: "user",
		Content: json.RawMessage(`[
			{"type":"text","text":"<variable_1>"},
			{"type":"text","text":"actual input"},
			{"type":"text","text":"</variable_1>"},
			{"type":"text","text":"\nEvaluate carefully."}
		]`),
	}

	bm := translateMessage(msg)

	var blocks []bedrockContentBlock
	if err := json.Unmarshal(bm.Content, &blocks); err != nil {
		t.Fatalf("failed to unmarshal content: %v", err)
	}
	if len(blocks) != 1 {
		t.Fatalf("blocks length = %d, want 1", len(blocks))
	}
	if blocks[0].Type != "text" {
		t.Errorf("blocks[0].Type = %q, want %q", blocks[0].Type, "text")
	}
	want := "<variable_1>actual input</variable_1>\nEvaluate carefully."
	if blocks[0].Text != want {
		t.Errorf("blocks[0].Text = %q, want %q", blocks[0].Text, want)
	}
}

// ===========================================================================
// translateResponse tests
// ===========================================================================

func TestTranslateResponse_TextResponse(t *testing.T) {
	resp := &bedrockResponse{
		ID:    "msg_01XFDUDYJgAACzvnptvVoYEL",
		Type:  "message",
		Model: "claude-3-sonnet-20240229",
		Role:  "assistant",
		Content: []bedrockContentBlock{
			{Type: "text", Text: "Hello! I'm doing well, thank you."},
		},
		StopReason: "end_turn",
		Usage: bedrockUsage{
			InputTokens:  12,
			OutputTokens: 8,
		},
	}

	result := translateResponse(resp)

	if result.ID != "msg_01XFDUDYJgAACzvnptvVoYEL" {
		t.Errorf("ID = %q, want %q", result.ID, "msg_01XFDUDYJgAACzvnptvVoYEL")
	}
	if result.Object != "chat.completion" {
		t.Errorf("Object = %q, want %q", result.Object, "chat.completion")
	}
	if result.Model != "claude-3-sonnet-20240229" {
		t.Errorf("Model = %q, want %q", result.Model, "claude-3-sonnet-20240229")
	}
	if len(result.Choices) != 1 {
		t.Fatalf("expected 1 choice, got %d", len(result.Choices))
	}
	choice := result.Choices[0]
	if choice.Index != 0 {
		t.Errorf("choice index = %d, want 0", choice.Index)
	}
	if choice.FinishReason != "stop" {
		t.Errorf("finish_reason = %q, want %q", choice.FinishReason, "stop")
	}
	if choice.Message.Role != "assistant" {
		t.Errorf("message role = %q, want %q", choice.Message.Role, "assistant")
	}

	var content string
	if err := json.Unmarshal(choice.Message.Content, &content); err != nil {
		t.Fatalf("failed to unmarshal content: %v", err)
	}
	if content != "Hello! I'm doing well, thank you." {
		t.Errorf("content = %q, want %q", content, "Hello! I'm doing well, thank you.")
	}
}

func TestTranslateResponse_ToolUse(t *testing.T) {
	resp := &bedrockResponse{
		ID:    "msg_tool_use",
		Type:  "message",
		Model: "claude-3-sonnet-20240229",
		Role:  "assistant",
		Content: []bedrockContentBlock{
			{Type: "text", Text: "Let me check that."},
			{
				Type:  "tool_use",
				ID:    "toolu_01A09q90qw90lq917835lqs8",
				Name:  "get_weather",
				Input: json.RawMessage(`{"city":"Paris"}`),
			},
		},
		StopReason: "tool_use",
		Usage: bedrockUsage{
			InputTokens:  25,
			OutputTokens: 40,
		},
	}

	result := translateResponse(resp)

	if result.Choices[0].FinishReason != "tool_calls" {
		t.Errorf("finish_reason = %q, want %q", result.Choices[0].FinishReason, "tool_calls")
	}

	msg := result.Choices[0].Message
	if len(msg.ToolCalls) != 1 {
		t.Fatalf("expected 1 tool call, got %d", len(msg.ToolCalls))
	}

	tc := msg.ToolCalls[0]
	if tc.ID != "toolu_01A09q90qw90lq917835lqs8" {
		t.Errorf("tool call id = %q, want %q", tc.ID, "toolu_01A09q90qw90lq917835lqs8")
	}
	if tc.Type != "function" {
		t.Errorf("tool call type = %q, want %q", tc.Type, "function")
	}
	if tc.Function.Name != "get_weather" {
		t.Errorf("tool call function name = %q, want %q", tc.Function.Name, "get_weather")
	}
	if tc.Function.Arguments != `{"city":"Paris"}` {
		t.Errorf("tool call function arguments = %q, want %q", tc.Function.Arguments, `{"city":"Paris"}`)
	}

	// Text should also be present
	var content string
	if err := json.Unmarshal(msg.Content, &content); err != nil {
		t.Fatalf("failed to unmarshal content: %v", err)
	}
	if content != "Let me check that." {
		t.Errorf("content = %q, want %q", content, "Let me check that.")
	}
}

func TestTranslateResponse_UsageMapping(t *testing.T) {
	resp := &bedrockResponse{
		ID:    "msg_usage",
		Type:  "message",
		Model: "claude-3-sonnet",
		Role:  "assistant",
		Content: []bedrockContentBlock{
			{Type: "text", Text: "OK"},
		},
		StopReason: "end_turn",
		Usage: bedrockUsage{
			InputTokens:  100,
			OutputTokens: 50,
		},
	}

	result := translateResponse(resp)

	if result.Usage == nil {
		t.Fatal("usage should not be nil")
	}
	if result.Usage.PromptTokens != 100 {
		t.Errorf("prompt_tokens = %d, want 100", result.Usage.PromptTokens)
	}
	if result.Usage.CompletionTokens != 50 {
		t.Errorf("completion_tokens = %d, want 50", result.Usage.CompletionTokens)
	}
	if result.Usage.TotalTokens != 150 {
		t.Errorf("total_tokens = %d, want 150", result.Usage.TotalTokens)
	}
}

func TestTranslateResponse_StopReasonMapping(t *testing.T) {
	tests := []struct {
		name       string
		stopReason string
		want       string
	}{
		{"end_turn", "end_turn", "stop"},
		{"max_tokens", "max_tokens", "length"},
		{"stop_sequence", "stop_sequence", "stop"},
		{"tool_use", "tool_use", "tool_calls"},
		{"unknown_reason", "unknown_reason", "stop"},
		{"empty_reason", "", "stop"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := mapStopReason(tt.stopReason)
			if got != tt.want {
				t.Errorf("mapStopReason(%q) = %q, want %q", tt.stopReason, got, tt.want)
			}
		})
	}
}

func TestTranslateResponse_EmptyContent(t *testing.T) {
	resp := &bedrockResponse{
		ID:         "msg_empty",
		Type:       "message",
		Model:      "claude-3-sonnet",
		Role:       "assistant",
		Content:    []bedrockContentBlock{},
		StopReason: "end_turn",
		Usage:      bedrockUsage{InputTokens: 5, OutputTokens: 0},
	}

	result := translateResponse(resp)

	if len(result.Choices) != 1 {
		t.Fatalf("expected 1 choice, got %d", len(result.Choices))
	}
	if result.Choices[0].Message.Content != nil {
		t.Errorf("content should be nil for empty response, got %s", string(result.Choices[0].Message.Content))
	}
}

// ===========================================================================
// parseBedrockError tests
// ===========================================================================

func TestParseBedrockError_ValidJSON(t *testing.T) {
	body := []byte(`{"message":"Model not found","type":"not_found"}`)
	err := parseBedrockError(404, body)

	if err.Status != 404 {
		t.Errorf("status = %d, want 404", err.Status)
	}
	if err.Message != "Model not found" {
		t.Errorf("message = %q, want %q", err.Message, "Model not found")
	}
	if err.Type != "not_found" {
		t.Errorf("type = %q, want %q", err.Type, "not_found")
	}
}

func TestParseBedrockError_RateLimitStatus(t *testing.T) {
	body := []byte(`{"message":"Too many requests","type":"rate_limit"}`)
	err := parseBedrockError(429, body)

	if err.Status != 429 {
		t.Errorf("status = %d, want 429", err.Status)
	}
	if err.Type != "rate_limit_error" {
		t.Errorf("type = %q, want %q", err.Type, "rate_limit_error")
	}
}

func TestParseBedrockError_ServerError(t *testing.T) {
	body := []byte(`{"message":"Internal server error","type":"server_error"}`)
	err := parseBedrockError(500, body)

	if err.Status != 502 {
		t.Errorf("status = %d, want 502 (mapped from 500)", err.Status)
	}
	if err.Type != "upstream_error" {
		t.Errorf("type = %q, want %q", err.Type, "upstream_error")
	}
}

func TestParseBedrockError_InvalidJSON(t *testing.T) {
	body := []byte(`not valid json at all`)
	err := parseBedrockError(500, body)

	if err == nil {
		t.Fatal("expected error")
	}
	// Should include the raw body text in the message
	if err.Message == "" {
		t.Error("message should not be empty for invalid JSON")
	}
}

func TestParseBedrockError_AuthError(t *testing.T) {
	body := []byte(`{"message":"Access denied","type":"auth_error"}`)
	err := parseBedrockError(403, body)

	if err.Type != "authentication_error" {
		t.Errorf("type = %q, want %q", err.Type, "authentication_error")
	}
}

// ===========================================================================
// parseStreamPayload tests
// ===========================================================================

func TestParseStreamPayload_MessageStart(t *testing.T) {
	state := newBedrockStreamState("claude-3-sonnet")

	msg := &eventStreamMessage{
		Headers: map[string]string{":message-type": "event"},
		Payload: []byte(`{"type":"message_start","message":{"id":"msg_123","model":"claude-3-sonnet","usage":{"input_tokens":10}}}`),
	}

	chunk, done, err := state.parseStreamPayload(msg)

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if done {
		t.Error("should not be done on message_start")
	}
	if chunk == nil {
		t.Fatal("chunk should not be nil for message_start")
	}
	if chunk.ID != "msg_123" {
		t.Errorf("chunk ID = %q, want %q", chunk.ID, "msg_123")
	}
	if chunk.Object != "chat.completion.chunk" {
		t.Errorf("object = %q, want %q", chunk.Object, "chat.completion.chunk")
	}
	if len(chunk.Choices) != 1 {
		t.Fatalf("expected 1 choice, got %d", len(chunk.Choices))
	}
	if chunk.Choices[0].Delta.Role != "assistant" {
		t.Errorf("delta role = %q, want %q", chunk.Choices[0].Delta.Role, "assistant")
	}

	// Verify internal state was updated
	if state.messageID != "msg_123" {
		t.Errorf("state messageID = %q, want %q", state.messageID, "msg_123")
	}
	if state.inputTokens != 10 {
		t.Errorf("state inputTokens = %d, want 10", state.inputTokens)
	}
}

func TestParseStreamPayload_ContentBlockDeltaTextDelta(t *testing.T) {
	state := newBedrockStreamState("claude-3-sonnet")
	state.messageID = "msg_123"

	msg := &eventStreamMessage{
		Headers: map[string]string{":message-type": "event"},
		Payload: []byte(`{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}`),
	}

	chunk, done, err := state.parseStreamPayload(msg)

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if done {
		t.Error("should not be done on content_block_delta")
	}
	if chunk == nil {
		t.Fatal("chunk should not be nil for text_delta")
	}
	if chunk.Choices[0].Delta.Content == nil {
		t.Fatal("delta content should not be nil")
	}
	if *chunk.Choices[0].Delta.Content != "Hello" {
		t.Errorf("delta content = %q, want %q", *chunk.Choices[0].Delta.Content, "Hello")
	}
}

func TestParseStreamPayload_ContentBlockDeltaNonTextDelta(t *testing.T) {
	state := newBedrockStreamState("claude-3-sonnet")
	state.messageID = "msg_123"

	msg := &eventStreamMessage{
		Headers: map[string]string{":message-type": "event"},
		Payload: []byte(`{"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{"}}`),
	}

	chunk, done, err := state.parseStreamPayload(msg)

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if done {
		t.Error("should not be done")
	}
	// Non text_delta types should return nil chunk
	if chunk != nil {
		t.Error("chunk should be nil for non-text_delta")
	}
}

func TestParseStreamPayload_ContentBlockStartToolUse(t *testing.T) {
	state := newBedrockStreamState("claude-3-sonnet")
	state.messageID = "msg_123"

	msg := &eventStreamMessage{
		Headers: map[string]string{":message-type": "event"},
		Payload: []byte(`{"type":"content_block_start","index":2,"content_block":{"type":"tool_use","id":"call_1","name":"get_weather"}}`),
	}

	chunk, done, err := state.parseStreamPayload(msg)

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if done {
		t.Error("should not be done on content_block_start")
	}
	if chunk == nil {
		t.Fatal("chunk should not be nil for tool_use start")
	}
	if len(chunk.Choices[0].Delta.ToolCalls) != 1 {
		t.Fatalf("tool_calls length = %d, want 1", len(chunk.Choices[0].Delta.ToolCalls))
	}
	tc := chunk.Choices[0].Delta.ToolCalls[0]
	if tc.Index != 0 {
		t.Errorf("tool call index = %d, want 0", tc.Index)
	}
	if tc.ID != "call_1" {
		t.Errorf("tool call id = %q, want %q", tc.ID, "call_1")
	}
	if tc.Function == nil || tc.Function.Name != "get_weather" || tc.Function.Arguments != "" {
		t.Errorf("tool call function = %+v, want name=get_weather args=''", tc.Function)
	}
}

func TestParseStreamPayload_ContentBlockDeltaInputJSONDelta(t *testing.T) {
	state := newBedrockStreamState("claude-3-sonnet")
	state.messageID = "msg_123"

	_, _, err := state.parseStreamPayload(&eventStreamMessage{
		Headers: map[string]string{":message-type": "event"},
		Payload: []byte(`{"type":"content_block_start","index":2,"content_block":{"type":"tool_use","id":"call_1","name":"get_weather"}}`),
	})
	if err != nil {
		t.Fatalf("unexpected setup error: %v", err)
	}

	msg := &eventStreamMessage{
		Headers: map[string]string{":message-type": "event"},
		Payload: []byte(`{"type":"content_block_delta","index":2,"delta":{"type":"input_json_delta","partial_json":"{\"city\""}}`),
	}

	chunk, done, err := state.parseStreamPayload(msg)

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if done {
		t.Error("should not be done")
	}
	if chunk == nil {
		t.Fatal("chunk should not be nil for input_json_delta")
	}
	if len(chunk.Choices[0].Delta.ToolCalls) != 1 {
		t.Fatalf("tool_calls length = %d, want 1", len(chunk.Choices[0].Delta.ToolCalls))
	}
	tc := chunk.Choices[0].Delta.ToolCalls[0]
	if tc.Index != 0 {
		t.Errorf("tool call index = %d, want 0", tc.Index)
	}
	if tc.Function == nil || tc.Function.Arguments != `{"city"` {
		t.Errorf("tool call args = %+v, want %q", tc.Function, `{"city"`)
	}
}

func TestParseStreamPayload_MessageDelta(t *testing.T) {
	state := newBedrockStreamState("claude-3-sonnet")
	state.messageID = "msg_123"
	state.inputTokens = 10

	msg := &eventStreamMessage{
		Headers: map[string]string{":message-type": "event"},
		Payload: []byte(`{"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":25}}`),
	}

	chunk, done, err := state.parseStreamPayload(msg)

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if done {
		t.Error("should not be done on message_delta (only message_stop)")
	}
	if chunk == nil {
		t.Fatal("chunk should not be nil for message_delta")
	}
	if chunk.Choices[0].FinishReason == nil {
		t.Fatal("finish_reason should not be nil")
	}
	if *chunk.Choices[0].FinishReason != "stop" {
		t.Errorf("finish_reason = %q, want %q", *chunk.Choices[0].FinishReason, "stop")
	}
	if chunk.Usage == nil {
		t.Fatal("usage should not be nil on message_delta with usage")
	}
	if chunk.Usage.PromptTokens != 10 {
		t.Errorf("prompt_tokens = %d, want 10", chunk.Usage.PromptTokens)
	}
	if chunk.Usage.CompletionTokens != 25 {
		t.Errorf("completion_tokens = %d, want 25", chunk.Usage.CompletionTokens)
	}
	if chunk.Usage.TotalTokens != 35 {
		t.Errorf("total_tokens = %d, want 35", chunk.Usage.TotalTokens)
	}
}

func TestParseStreamPayload_MessageStop(t *testing.T) {
	state := newBedrockStreamState("claude-3-sonnet")
	state.messageID = "msg_123"

	msg := &eventStreamMessage{
		Headers: map[string]string{":message-type": "event"},
		Payload: []byte(`{"type":"message_stop"}`),
	}

	chunk, done, err := state.parseStreamPayload(msg)

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !done {
		t.Error("should be done on message_stop")
	}
	if chunk != nil {
		t.Error("chunk should be nil on message_stop")
	}
}

func TestParseStreamPayload_Exception(t *testing.T) {
	state := newBedrockStreamState("claude-3-sonnet")

	msg := &eventStreamMessage{
		Headers: map[string]string{":message-type": "exception"},
		Payload: []byte(`throttling exception: rate limit exceeded`),
	}

	_, _, err := state.parseStreamPayload(msg)

	if err == nil {
		t.Fatal("expected error for exception message type")
	}
	if err.Error() != "bedrock exception: throttling exception: rate limit exceeded" {
		t.Errorf("error = %q", err.Error())
	}
}

func TestParseStreamPayload_UnknownEventType(t *testing.T) {
	state := newBedrockStreamState("claude-3-sonnet")

	msg := &eventStreamMessage{
		Headers: map[string]string{":message-type": "event"},
		Payload: []byte(`{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}`),
	}

	chunk, done, err := state.parseStreamPayload(msg)

	if err != nil {
		t.Fatalf("unexpected error for unknown event: %v", err)
	}
	if done {
		t.Error("should not be done for unknown event")
	}
	if chunk != nil {
		t.Error("chunk should be nil for unhandled event type")
	}
}

func TestParseStreamPayload_NonEventMessageType(t *testing.T) {
	state := newBedrockStreamState("claude-3-sonnet")

	msg := &eventStreamMessage{
		Headers: map[string]string{":message-type": "unknown_type"},
		Payload: []byte(`some data`),
	}

	chunk, done, err := state.parseStreamPayload(msg)

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if done {
		t.Error("should not be done")
	}
	if chunk != nil {
		t.Error("chunk should be nil for non-event message type")
	}
}

// ===========================================================================
// resolveModelName tests
// ===========================================================================

func TestResolveModelName(t *testing.T) {
	tests := []struct {
		input string
		want  string
	}{
		{"claude-3-sonnet", "claude-3-sonnet"},
		{"bedrock/claude-3-sonnet", "claude-3-sonnet"},
		{"provider/sub/model", "sub/model"},
		{"", ""},
		{"just-a-model", "just-a-model"},
	}

	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			got := resolveModelName(tt.input)
			if got != tt.want {
				t.Errorf("resolveModelName(%q) = %q, want %q", tt.input, got, tt.want)
			}
		})
	}
}

// ===========================================================================
// extractTextContent tests
// ===========================================================================

func TestExtractTextContent(t *testing.T) {
	tests := []struct {
		name    string
		content json.RawMessage
		want    string
	}{
		{
			name:    "simple string",
			content: json.RawMessage(`"hello"`),
			want:    "hello",
		},
		{
			name:    "array with text part",
			content: json.RawMessage(`[{"type":"text","text":"from array"}]`),
			want:    "from array",
		},
		{
			name:    "empty",
			content: nil,
			want:    "",
		},
		{
			name:    "empty string",
			content: json.RawMessage(`""`),
			want:    "",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := extractTextContent(tt.content)
			if got != tt.want {
				t.Errorf("extractTextContent = %q, want %q", got, tt.want)
			}
		})
	}
}
