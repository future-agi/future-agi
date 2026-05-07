package gemini

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

func ptrFloat64(v float64) *float64 { return &v }
func ptrInt(v int) *int             { return &v }

func mustJSON(v interface{}) json.RawMessage {
	b, err := json.Marshal(v)
	if err != nil {
		panic(err)
	}
	return b
}

// ---------------------------------------------------------------------------
// translateRequest tests
// ---------------------------------------------------------------------------

func TestTranslateRequest_BasicChat(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "gemini-1.5-pro",
		Messages: []models.Message{
			{Role: "user", Content: mustJSON("Hello")},
		},
	}

	gr, model := translateRequest(req)

	if model != "gemini-1.5-pro" {
		t.Errorf("model = %q, want %q", model, "gemini-1.5-pro")
	}
	if len(gr.Contents) != 1 {
		t.Fatalf("contents length = %d, want 1", len(gr.Contents))
	}
	if gr.Contents[0].Role != "user" {
		t.Errorf("role = %q, want %q", gr.Contents[0].Role, "user")
	}
	if len(gr.Contents[0].Parts) != 1 {
		t.Fatalf("parts length = %d, want 1", len(gr.Contents[0].Parts))
	}
	if gr.Contents[0].Parts[0].Text != "Hello" {
		t.Errorf("text = %q, want %q", gr.Contents[0].Parts[0].Text, "Hello")
	}
	if gr.SystemInstruction != nil {
		t.Error("SystemInstruction should be nil for a non-system message")
	}
}

func TestTranslateRequest_SystemInstruction(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "gemini-1.5-pro",
		Messages: []models.Message{
			{Role: "system", Content: mustJSON("You are a helpful assistant.")},
			{Role: "user", Content: mustJSON("Hello")},
		},
	}

	gr, _ := translateRequest(req)

	if gr.SystemInstruction == nil {
		t.Fatal("SystemInstruction should not be nil")
	}
	if len(gr.SystemInstruction.Parts) != 1 {
		t.Fatalf("system parts = %d, want 1", len(gr.SystemInstruction.Parts))
	}
	if gr.SystemInstruction.Parts[0].Text != "You are a helpful assistant." {
		t.Errorf("system text = %q, want %q", gr.SystemInstruction.Parts[0].Text, "You are a helpful assistant.")
	}
	// System messages should be excluded from contents.
	if len(gr.Contents) != 1 {
		t.Fatalf("contents length = %d, want 1 (system excluded)", len(gr.Contents))
	}
	if gr.Contents[0].Role != "user" {
		t.Errorf("contents[0].Role = %q, want %q", gr.Contents[0].Role, "user")
	}
}

func TestTranslateRequest_MultipleSystemMessages(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "gemini-1.5-pro",
		Messages: []models.Message{
			{Role: "system", Content: mustJSON("First instruction.")},
			{Role: "system", Content: mustJSON("Second instruction.")},
			{Role: "user", Content: mustJSON("Hello")},
		},
	}

	gr, _ := translateRequest(req)

	if gr.SystemInstruction == nil {
		t.Fatal("SystemInstruction should not be nil")
	}
	if len(gr.SystemInstruction.Parts) != 2 {
		t.Fatalf("system parts = %d, want 2", len(gr.SystemInstruction.Parts))
	}
	if gr.SystemInstruction.Parts[0].Text != "First instruction." {
		t.Errorf("system text[0] = %q, want %q", gr.SystemInstruction.Parts[0].Text, "First instruction.")
	}
	if gr.SystemInstruction.Parts[1].Text != "Second instruction." {
		t.Errorf("system text[1] = %q, want %q", gr.SystemInstruction.Parts[1].Text, "Second instruction.")
	}
	if len(gr.Contents) != 1 {
		t.Errorf("contents length = %d, want 1", len(gr.Contents))
	}
}

func TestTranslateRequest_RoleMapping_AssistantToModel(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model: "gemini-1.5-pro",
		Messages: []models.Message{
			{Role: "user", Content: mustJSON("Hi")},
			{Role: "assistant", Content: mustJSON("Hello! How can I help?")},
			{Role: "user", Content: mustJSON("Tell me a joke")},
		},
	}

	gr, _ := translateRequest(req)

	if len(gr.Contents) != 3 {
		t.Fatalf("contents length = %d, want 3", len(gr.Contents))
	}
	if gr.Contents[0].Role != "user" {
		t.Errorf("contents[0].Role = %q, want %q", gr.Contents[0].Role, "user")
	}
	if gr.Contents[1].Role != "model" {
		t.Errorf("contents[1].Role = %q, want %q (assistant->model)", gr.Contents[1].Role, "model")
	}
	if gr.Contents[2].Role != "user" {
		t.Errorf("contents[2].Role = %q, want %q", gr.Contents[2].Role, "user")
	}
}

func TestTranslateRequest_GenerationConfig(t *testing.T) {
	temp := 0.7
	topP := 0.9
	maxTokens := 1024

	req := &models.ChatCompletionRequest{
		Model:       "gemini-1.5-pro",
		Messages:    []models.Message{{Role: "user", Content: mustJSON("Hello")}},
		Temperature: &temp,
		TopP:        &topP,
		MaxTokens:   &maxTokens,
		Stop:        mustJSON([]string{"STOP", "END"}),
	}

	gr, _ := translateRequest(req)

	if gr.GenerationConfig == nil {
		t.Fatal("GenerationConfig should not be nil")
	}
	gc := gr.GenerationConfig
	if gc.Temperature == nil || *gc.Temperature != 0.7 {
		t.Errorf("Temperature = %v, want 0.7", gc.Temperature)
	}
	if gc.TopP == nil || *gc.TopP != 0.9 {
		t.Errorf("TopP = %v, want 0.9", gc.TopP)
	}
	if gc.MaxOutputTokens == nil || *gc.MaxOutputTokens != 1024 {
		t.Errorf("MaxOutputTokens = %v, want 1024", gc.MaxOutputTokens)
	}
	if len(gc.StopSequences) != 2 {
		t.Fatalf("StopSequences length = %d, want 2", len(gc.StopSequences))
	}
	if gc.StopSequences[0] != "STOP" || gc.StopSequences[1] != "END" {
		t.Errorf("StopSequences = %v, want [STOP END]", gc.StopSequences)
	}
}

func TestTranslateRequest_MaxCompletionTokensFallback(t *testing.T) {
	maxCompTokens := 2048
	req := &models.ChatCompletionRequest{
		Model:               "gemini-1.5-pro",
		Messages:            []models.Message{{Role: "user", Content: mustJSON("Hi")}},
		MaxCompletionTokens: &maxCompTokens,
	}

	gr, _ := translateRequest(req)

	if gr.GenerationConfig.MaxOutputTokens == nil || *gr.GenerationConfig.MaxOutputTokens != 2048 {
		t.Errorf("MaxOutputTokens = %v, want 2048 (from MaxCompletionTokens)",
			gr.GenerationConfig.MaxOutputTokens)
	}
}

func TestTranslateRequest_MaxTokensTakesPrecedence(t *testing.T) {
	maxTokens := 512
	maxCompTokens := 2048
	req := &models.ChatCompletionRequest{
		Model:               "gemini-1.5-pro",
		Messages:            []models.Message{{Role: "user", Content: mustJSON("Hi")}},
		MaxTokens:           &maxTokens,
		MaxCompletionTokens: &maxCompTokens,
	}

	gr, _ := translateRequest(req)

	if gr.GenerationConfig.MaxOutputTokens == nil || *gr.GenerationConfig.MaxOutputTokens != 512 {
		t.Errorf("MaxOutputTokens = %v, want 512 (MaxTokens takes precedence)",
			gr.GenerationConfig.MaxOutputTokens)
	}
}

func TestTranslateRequest_StopSingleString(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model:    "gemini-1.5-pro",
		Messages: []models.Message{{Role: "user", Content: mustJSON("Hi")}},
		Stop:     mustJSON("HALT"),
	}

	gr, _ := translateRequest(req)

	if len(gr.GenerationConfig.StopSequences) != 1 {
		t.Fatalf("StopSequences length = %d, want 1", len(gr.GenerationConfig.StopSequences))
	}
	if gr.GenerationConfig.StopSequences[0] != "HALT" {
		t.Errorf("StopSequences[0] = %q, want %q", gr.GenerationConfig.StopSequences[0], "HALT")
	}
}

func TestTranslateRequest_Tools(t *testing.T) {
	params := json.RawMessage(`{"type":"object","properties":{"location":{"type":"string"}}}`)
	req := &models.ChatCompletionRequest{
		Model:    "gemini-1.5-pro",
		Messages: []models.Message{{Role: "user", Content: mustJSON("What is the weather?")}},
		Tools: []models.Tool{
			{
				Type: "function",
				Function: models.ToolFunction{
					Name:        "get_weather",
					Description: "Get current weather",
					Parameters:  params,
				},
			},
		},
	}

	gr, _ := translateRequest(req)

	if len(gr.Tools) != 1 {
		t.Fatalf("Tools length = %d, want 1", len(gr.Tools))
	}
	if len(gr.Tools[0].FunctionDeclarations) != 1 {
		t.Fatalf("FunctionDeclarations length = %d, want 1", len(gr.Tools[0].FunctionDeclarations))
	}
	decl := gr.Tools[0].FunctionDeclarations[0]
	if decl.Name != "get_weather" {
		t.Errorf("Name = %q, want %q", decl.Name, "get_weather")
	}
	if decl.Description != "Get current weather" {
		t.Errorf("Description = %q, want %q", decl.Description, "Get current weather")
	}
	if string(decl.Parameters) != string(params) {
		t.Errorf("Parameters = %s, want %s", decl.Parameters, params)
	}
}

func TestTranslateRequest_ToolsSkipNonFunction(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model:    "gemini-1.5-pro",
		Messages: []models.Message{{Role: "user", Content: mustJSON("Hi")}},
		Tools: []models.Tool{
			{
				Type: "retrieval",
				Function: models.ToolFunction{
					Name: "some_retrieval",
				},
			},
			{
				Type: "function",
				Function: models.ToolFunction{
					Name:        "real_func",
					Description: "A real function",
				},
			},
		},
	}

	gr, _ := translateRequest(req)

	if len(gr.Tools) != 1 {
		t.Fatalf("Tools length = %d, want 1", len(gr.Tools))
	}
	if len(gr.Tools[0].FunctionDeclarations) != 1 {
		t.Fatalf("FunctionDeclarations length = %d, want 1", len(gr.Tools[0].FunctionDeclarations))
	}
	if gr.Tools[0].FunctionDeclarations[0].Name != "real_func" {
		t.Errorf("Name = %q, want %q", gr.Tools[0].FunctionDeclarations[0].Name, "real_func")
	}
}

func TestTranslateRequest_ResponseFormatJSON(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model:          "gemini-1.5-pro",
		Messages:       []models.Message{{Role: "user", Content: mustJSON("Give me JSON")}},
		ResponseFormat: &models.ResponseFormat{Type: "json_object"},
	}

	gr, _ := translateRequest(req)

	if gr.GenerationConfig.ResponseMimeType != "application/json" {
		t.Errorf("ResponseMimeType = %q, want %q", gr.GenerationConfig.ResponseMimeType, "application/json")
	}
}

func TestTranslateRequest_JSONSchema(t *testing.T) {
	// json_schema with a schema wrapper object -> should extract inner schema and set ResponseMimeType.
	schema := json.RawMessage(`{"name":"person","schema":{"type":"object","properties":{"name":{"type":"string"},"age":{"type":"integer"}},"required":["name","age"]}}`)
	req := &models.ChatCompletionRequest{
		Model:    "gemini-1.5-pro",
		Messages: []models.Message{{Role: "user", Content: mustJSON("Give me structured data")}},
		ResponseFormat: &models.ResponseFormat{
			Type:       "json_schema",
			JSONSchema: schema,
		},
	}

	gr, _ := translateRequest(req)

	if gr.GenerationConfig.ResponseMimeType != "application/json" {
		t.Errorf("ResponseMimeType = %q, want %q", gr.GenerationConfig.ResponseMimeType, "application/json")
	}
	if gr.GenerationConfig.ResponseSchema == nil {
		t.Fatal("ResponseSchema should not be nil")
	}

	// The inner schema should be extracted from the wrapper.
	var parsedSchema map[string]interface{}
	if err := json.Unmarshal(gr.GenerationConfig.ResponseSchema, &parsedSchema); err != nil {
		t.Fatalf("failed to unmarshal ResponseSchema: %v", err)
	}
	if parsedSchema["type"] != "object" {
		t.Errorf("ResponseSchema type = %v, want %q", parsedSchema["type"], "object")
	}
	props, ok := parsedSchema["properties"].(map[string]interface{})
	if !ok {
		t.Fatal("ResponseSchema should have properties")
	}
	if _, hasName := props["name"]; !hasName {
		t.Error("ResponseSchema properties should include 'name'")
	}
	if _, hasAge := props["age"]; !hasAge {
		t.Error("ResponseSchema properties should include 'age'")
	}
}

func TestTranslateRequest_JSONSchema_WithoutSchemaWrapper(t *testing.T) {
	// json_schema where the JSONSchema itself IS the schema (no nested "schema" key).
	// Should fall through and use the entire JSONSchema as ResponseSchema.
	rawSchema := json.RawMessage(`{"type":"object","properties":{"color":{"type":"string"}}}`)
	req := &models.ChatCompletionRequest{
		Model:    "gemini-1.5-pro",
		Messages: []models.Message{{Role: "user", Content: mustJSON("Give me data")}},
		ResponseFormat: &models.ResponseFormat{
			Type:       "json_schema",
			JSONSchema: rawSchema,
		},
	}

	gr, _ := translateRequest(req)

	if gr.GenerationConfig.ResponseMimeType != "application/json" {
		t.Errorf("ResponseMimeType = %q, want %q", gr.GenerationConfig.ResponseMimeType, "application/json")
	}
	if gr.GenerationConfig.ResponseSchema == nil {
		t.Fatal("ResponseSchema should not be nil")
	}

	var parsedSchema map[string]interface{}
	if err := json.Unmarshal(gr.GenerationConfig.ResponseSchema, &parsedSchema); err != nil {
		t.Fatalf("failed to unmarshal ResponseSchema: %v", err)
	}
	if parsedSchema["type"] != "object" {
		t.Errorf("ResponseSchema type = %v, want %q", parsedSchema["type"], "object")
	}
}

func TestTranslateRequest_JSONSchema_EmptySchema(t *testing.T) {
	// json_schema with empty JSONSchema field -> ResponseMimeType set but no ResponseSchema.
	req := &models.ChatCompletionRequest{
		Model:    "gemini-1.5-pro",
		Messages: []models.Message{{Role: "user", Content: mustJSON("Give me JSON")}},
		ResponseFormat: &models.ResponseFormat{
			Type: "json_schema",
		},
	}

	gr, _ := translateRequest(req)

	if gr.GenerationConfig.ResponseMimeType != "application/json" {
		t.Errorf("ResponseMimeType = %q, want %q", gr.GenerationConfig.ResponseMimeType, "application/json")
	}
	if gr.GenerationConfig.ResponseSchema != nil {
		t.Errorf("ResponseSchema should be nil when JSONSchema is empty, got %s", string(gr.GenerationConfig.ResponseSchema))
	}
}

func TestTranslateRequest_JSONObject_PreservedBehavior(t *testing.T) {
	// Existing json_object behavior should be preserved (no ResponseSchema).
	req := &models.ChatCompletionRequest{
		Model:          "gemini-1.5-pro",
		Messages:       []models.Message{{Role: "user", Content: mustJSON("Give me JSON")}},
		ResponseFormat: &models.ResponseFormat{Type: "json_object"},
	}

	gr, _ := translateRequest(req)

	if gr.GenerationConfig.ResponseMimeType != "application/json" {
		t.Errorf("ResponseMimeType = %q, want %q", gr.GenerationConfig.ResponseMimeType, "application/json")
	}
	if gr.GenerationConfig.ResponseSchema != nil {
		t.Errorf("ResponseSchema should be nil for json_object, got %s", string(gr.GenerationConfig.ResponseSchema))
	}
}

func TestTranslateRequest_ResponseFormatText(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model:          "gemini-1.5-pro",
		Messages:       []models.Message{{Role: "user", Content: mustJSON("Hello")}},
		ResponseFormat: &models.ResponseFormat{Type: "text"},
	}

	gr, _ := translateRequest(req)

	if gr.GenerationConfig.ResponseMimeType != "" {
		t.Errorf("ResponseMimeType = %q, want empty for type=text", gr.GenerationConfig.ResponseMimeType)
	}
}

func TestTranslateRequest_ModelNameWithPrefix(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model:    "google/gemini-1.5-pro",
		Messages: []models.Message{{Role: "user", Content: mustJSON("Hello")}},
	}

	_, model := translateRequest(req)

	if model != "gemini-1.5-pro" {
		t.Errorf("model = %q, want %q (prefix stripped)", model, "gemini-1.5-pro")
	}
}

func TestTranslateRequest_EmptyMessages(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model:    "gemini-1.5-pro",
		Messages: []models.Message{},
	}

	gr, model := translateRequest(req)

	if model != "gemini-1.5-pro" {
		t.Errorf("model = %q, want %q", model, "gemini-1.5-pro")
	}
	if len(gr.Contents) != 0 {
		t.Errorf("contents length = %d, want 0", len(gr.Contents))
	}
	if gr.SystemInstruction != nil {
		t.Error("SystemInstruction should be nil for empty messages")
	}
}

func TestTranslateRequest_NilGenerationFields(t *testing.T) {
	req := &models.ChatCompletionRequest{
		Model:    "gemini-1.5-pro",
		Messages: []models.Message{{Role: "user", Content: mustJSON("Hi")}},
	}

	gr, _ := translateRequest(req)

	if gr.GenerationConfig == nil {
		t.Fatal("GenerationConfig should not be nil even with no config fields")
	}
	if gr.GenerationConfig.Temperature != nil {
		t.Errorf("Temperature = %v, want nil", gr.GenerationConfig.Temperature)
	}
	if gr.GenerationConfig.TopP != nil {
		t.Errorf("TopP = %v, want nil", gr.GenerationConfig.TopP)
	}
	if gr.GenerationConfig.MaxOutputTokens != nil {
		t.Errorf("MaxOutputTokens = %v, want nil", gr.GenerationConfig.MaxOutputTokens)
	}
}

// ---------------------------------------------------------------------------
// translateMessage tests
// ---------------------------------------------------------------------------

func TestTranslateMessage_UserMessage(t *testing.T) {
	msg := models.Message{
		Role:    "user",
		Content: mustJSON("Hello there"),
	}

	gc := translateMessage(msg)

	if gc.Role != "user" {
		t.Errorf("Role = %q, want %q", gc.Role, "user")
	}
	if len(gc.Parts) != 1 {
		t.Fatalf("Parts length = %d, want 1", len(gc.Parts))
	}
	if gc.Parts[0].Text != "Hello there" {
		t.Errorf("Text = %q, want %q", gc.Parts[0].Text, "Hello there")
	}
}

func TestTranslateMessage_AssistantToModel(t *testing.T) {
	msg := models.Message{
		Role:    "assistant",
		Content: mustJSON("I can help with that."),
	}

	gc := translateMessage(msg)

	if gc.Role != "model" {
		t.Errorf("Role = %q, want %q (assistant->model)", gc.Role, "model")
	}
	if len(gc.Parts) != 1 {
		t.Fatalf("Parts length = %d, want 1", len(gc.Parts))
	}
	if gc.Parts[0].Text != "I can help with that." {
		t.Errorf("Text = %q, want %q", gc.Parts[0].Text, "I can help with that.")
	}
}

func TestTranslateMessage_ToolResult(t *testing.T) {
	msg := models.Message{
		Role:    "tool",
		Name:    "get_weather",
		Content: mustJSON("72°F and sunny"),
	}

	gc := translateMessage(msg)

	if gc.Role != "user" {
		t.Errorf("Role = %q, want %q (tool -> user for function responses)", gc.Role, "user")
	}
	if len(gc.Parts) != 1 {
		t.Fatalf("Parts length = %d, want 1", len(gc.Parts))
	}
	if gc.Parts[0].FunctionResponse == nil {
		t.Fatal("FunctionResponse should not be nil")
	}
	if gc.Parts[0].FunctionResponse.Name != "get_weather" {
		t.Errorf("FunctionResponse.Name = %q, want %q", gc.Parts[0].FunctionResponse.Name, "get_weather")
	}
	// Verify the response wraps the result.
	var resp map[string]interface{}
	if err := json.Unmarshal(gc.Parts[0].FunctionResponse.Response, &resp); err != nil {
		t.Fatalf("failed to unmarshal FunctionResponse.Response: %v", err)
	}
	if result, ok := resp["result"]; !ok {
		t.Error("FunctionResponse.Response missing 'result' key")
	} else if result != "72°F and sunny" {
		t.Errorf("result = %v, want %q", result, "72°F and sunny")
	}
}

func TestTranslateMessage_AssistantWithToolCalls(t *testing.T) {
	msg := models.Message{
		Role:    "assistant",
		Content: mustJSON("Let me check the weather."),
		ToolCalls: []models.ToolCall{
			{
				ID:   "call_123",
				Type: "function",
				Function: models.FunctionCall{
					Name:      "get_weather",
					Arguments: `{"location":"NYC"}`,
				},
			},
		},
	}

	gc := translateMessage(msg)

	if gc.Role != "model" {
		t.Errorf("Role = %q, want %q", gc.Role, "model")
	}
	// Should have text part first, then function call part.
	if len(gc.Parts) != 2 {
		t.Fatalf("Parts length = %d, want 2 (text + function call)", len(gc.Parts))
	}
	// First part should be text.
	if gc.Parts[0].Text != "Let me check the weather." {
		t.Errorf("Parts[0].Text = %q, want %q", gc.Parts[0].Text, "Let me check the weather.")
	}
	// Second part should be function call.
	if gc.Parts[1].FunctionCall == nil {
		t.Fatal("Parts[1].FunctionCall should not be nil")
	}
	if gc.Parts[1].FunctionCall.Name != "get_weather" {
		t.Errorf("FunctionCall.Name = %q, want %q", gc.Parts[1].FunctionCall.Name, "get_weather")
	}
	var args map[string]string
	if err := json.Unmarshal(gc.Parts[1].FunctionCall.Args, &args); err != nil {
		t.Fatalf("failed to unmarshal FunctionCall.Args: %v", err)
	}
	if args["location"] != "NYC" {
		t.Errorf("args[location] = %q, want %q", args["location"], "NYC")
	}
}

func TestTranslateMessage_AssistantWithMultipleToolCalls(t *testing.T) {
	msg := models.Message{
		Role: "assistant",
		ToolCalls: []models.ToolCall{
			{
				ID:   "call_1",
				Type: "function",
				Function: models.FunctionCall{
					Name:      "search",
					Arguments: `{"query":"golang"}`,
				},
			},
			{
				ID:   "call_2",
				Type: "function",
				Function: models.FunctionCall{
					Name:      "translate",
					Arguments: `{"text":"hello","lang":"es"}`,
				},
			},
		},
	}

	gc := translateMessage(msg)

	if gc.Role != "model" {
		t.Errorf("Role = %q, want %q", gc.Role, "model")
	}
	// No text content, just two function calls.
	if len(gc.Parts) != 2 {
		t.Fatalf("Parts length = %d, want 2", len(gc.Parts))
	}
	if gc.Parts[0].FunctionCall == nil || gc.Parts[0].FunctionCall.Name != "search" {
		t.Errorf("Parts[0] should be search function call")
	}
	if gc.Parts[1].FunctionCall == nil || gc.Parts[1].FunctionCall.Name != "translate" {
		t.Errorf("Parts[1] should be translate function call")
	}
}

func TestTranslateMessage_EmptyContent(t *testing.T) {
	msg := models.Message{
		Role:    "user",
		Content: nil,
	}

	gc := translateMessage(msg)

	if gc.Role != "user" {
		t.Errorf("Role = %q, want %q", gc.Role, "user")
	}
	if len(gc.Parts) != 1 {
		t.Fatalf("Parts length = %d, want 1", len(gc.Parts))
	}
	if gc.Parts[0].Text != "" {
		t.Errorf("Text = %q, want empty string", gc.Parts[0].Text)
	}
}

// ---------------------------------------------------------------------------
// translateVisionContent tests
// ---------------------------------------------------------------------------

func TestTranslateVisionContent_Gemini_WithImages(t *testing.T) {
	content := json.RawMessage(`[
		{"type":"text","text":"What is in this image?"},
		{"type":"image_url","image_url":{"url":"data:image/png;base64,iVBORw0KGgo="}}
	]`)

	parts := translateVisionContent(content)
	if parts == nil {
		t.Fatal("expected non-nil parts for content with images")
	}
	if len(parts) != 2 {
		t.Fatalf("parts length = %d, want 2", len(parts))
	}

	// First part: text.
	if parts[0].Text != "What is in this image?" {
		t.Errorf("parts[0].Text = %q, want %q", parts[0].Text, "What is in this image?")
	}
	if parts[0].InlineData != nil {
		t.Error("parts[0].InlineData should be nil for text part")
	}

	// Second part: inline data.
	if parts[1].InlineData == nil {
		t.Fatal("parts[1].InlineData should not be nil")
	}
	if parts[1].InlineData.MimeType != "image/png" {
		t.Errorf("InlineData.MimeType = %q, want %q", parts[1].InlineData.MimeType, "image/png")
	}
	if parts[1].InlineData.Data != "iVBORw0KGgo=" {
		t.Errorf("InlineData.Data = %q, want %q", parts[1].InlineData.Data, "iVBORw0KGgo=")
	}
}

func TestTranslateVisionContent_Gemini_MultipleImages(t *testing.T) {
	content := json.RawMessage(`[
		{"type":"text","text":"Compare these images"},
		{"type":"image_url","image_url":{"url":"data:image/png;base64,abc="}},
		{"type":"image_url","image_url":{"url":"data:image/jpeg;base64,xyz="}}
	]`)

	parts := translateVisionContent(content)
	if parts == nil {
		t.Fatal("expected non-nil parts")
	}
	if len(parts) != 3 {
		t.Fatalf("parts length = %d, want 3", len(parts))
	}
	if parts[0].Text != "Compare these images" {
		t.Errorf("parts[0].Text = %q, want %q", parts[0].Text, "Compare these images")
	}
	if parts[1].InlineData == nil || parts[1].InlineData.MimeType != "image/png" {
		t.Errorf("parts[1] should be image/png inline data")
	}
	if parts[2].InlineData == nil || parts[2].InlineData.MimeType != "image/jpeg" {
		t.Errorf("parts[2] should be image/jpeg inline data")
	}
}

func TestTranslateVisionContent_Gemini_HTTPUrl(t *testing.T) {
	// Gemini supports fileUri for remote images, so HTTP URLs are forwarded
	// as a fileData part with the inferred MIME type.
	content := json.RawMessage(`[
		{"type":"text","text":"What is this?"},
		{"type":"image_url","image_url":{"url":"https://example.com/image.png"}}
	]`)

	parts := translateVisionContent(content)
	if parts == nil {
		t.Fatal("expected non-nil parts (has image_url)")
	}
	if len(parts) != 2 {
		t.Fatalf("parts length = %d, want 2 (text + fileData)", len(parts))
	}
	if parts[0].Text != "What is this?" {
		t.Errorf("parts[0].Text = %q, want %q", parts[0].Text, "What is this?")
	}
	if parts[1].FileData == nil {
		t.Fatal("expected parts[1].FileData to be set for HTTP URL")
	}
	if parts[1].FileData.FileURI != "https://example.com/image.png" {
		t.Errorf("FileURI = %q, want the source URL", parts[1].FileData.FileURI)
	}
	if parts[1].FileData.MimeType == "" {
		t.Errorf("MimeType should be inferred from the .png extension")
	}
}

// The “file“ content block used to only accept “file_id“ (a URL).
// Callers (OpenAI-format chat completion with inline PDFs) send a
// “file_data“ data URI instead, which must be translated into a Gemini
// inlineData part rather than silently dropped.
func TestTranslateVisionContent_Gemini_FileDataInlinePdf(t *testing.T) {
	content := json.RawMessage(`[
		{"type":"text","text":"Summarise this doc"},
		{"type":"file","file":{"file_data":"data:application/pdf;base64,JVBERi0x","filename":"doc.pdf"}}
	]`)

	parts := translateVisionContent(content)
	if parts == nil {
		t.Fatal("expected non-nil parts (has file block)")
	}
	if len(parts) != 2 {
		t.Fatalf("parts length = %d, want 2 (text + inlineData)", len(parts))
	}
	if parts[1].InlineData == nil {
		t.Fatal("expected parts[1].InlineData to be set for inline file_data")
	}
	if parts[1].InlineData.MimeType != "application/pdf" {
		t.Errorf("InlineData.MimeType = %q, want application/pdf", parts[1].InlineData.MimeType)
	}
	if parts[1].InlineData.Data != "JVBERi0x" {
		t.Errorf("InlineData.Data = %q, want the base64 payload", parts[1].InlineData.Data)
	}
}

// Remote files via “file_id“ should still be forwarded as a fileData
// part (with default MIME type fallback when “format“ is absent).
func TestTranslateVisionContent_Gemini_FileIDWithDefaultMime(t *testing.T) {
	content := json.RawMessage(`[
		{"type":"file","file":{"file_id":"https://example.com/doc.pdf"}}
	]`)
	parts := translateVisionContent(content)
	if len(parts) != 1 || parts[0].FileData == nil {
		t.Fatalf("expected 1 fileData part, got %+v", parts)
	}
	if parts[0].FileData.FileURI != "https://example.com/doc.pdf" {
		t.Errorf("FileURI = %q, want the source URL", parts[0].FileData.FileURI)
	}
	// Missing ``format`` → permissive fallback so Gemini still sees the file.
	if parts[0].FileData.MimeType != "application/octet-stream" {
		t.Errorf("MimeType = %q, want application/octet-stream fallback", parts[0].FileData.MimeType)
	}
}

func TestTranslateVisionContent_Gemini_NoImages(t *testing.T) {
	content := json.RawMessage(`[{"type":"text","text":"Just text"}]`)

	parts := translateVisionContent(content)
	if parts != nil {
		t.Errorf("expected nil for text-only content array, got %d parts", len(parts))
	}
}

func TestTranslateVisionContent_Gemini_EmptyContent(t *testing.T) {
	parts := translateVisionContent(nil)
	if parts != nil {
		t.Errorf("expected nil for nil content, got %d parts", len(parts))
	}
}

func TestTranslateVisionContent_Gemini_InvalidJSON(t *testing.T) {
	parts := translateVisionContent(json.RawMessage(`{not valid`))
	if parts != nil {
		t.Errorf("expected nil for invalid JSON, got %d parts", len(parts))
	}
}

// ---------------------------------------------------------------------------
// parseImageURLToPart tests
// ---------------------------------------------------------------------------

func TestParseImageURLToPart_DataURI(t *testing.T) {
	part := parseImageURLToPart("data:image/png;base64,iVBORw0KGgo=")
	if part == nil {
		t.Fatal("expected non-nil part")
	}
	if part.InlineData == nil {
		t.Fatal("expected InlineData to be set")
	}
	if part.InlineData.MimeType != "image/png" {
		t.Errorf("MimeType = %q, want %q", part.InlineData.MimeType, "image/png")
	}
	if part.InlineData.Data != "iVBORw0KGgo=" {
		t.Errorf("Data = %q, want %q", part.InlineData.Data, "iVBORw0KGgo=")
	}
}

func TestParseImageURLToPart_DataURI_JPEG(t *testing.T) {
	part := parseImageURLToPart("data:image/jpeg;base64,/9j/4AAQ")
	if part == nil {
		t.Fatal("expected non-nil part")
	}
	if part.InlineData == nil {
		t.Fatal("expected InlineData to be set")
	}
	if part.InlineData.MimeType != "image/jpeg" {
		t.Errorf("MimeType = %q, want %q", part.InlineData.MimeType, "image/jpeg")
	}
	if part.InlineData.Data != "/9j/4AAQ" {
		t.Errorf("Data = %q, want %q", part.InlineData.Data, "/9j/4AAQ")
	}
}

func TestParseImageURLToPart_HTTPUrl_ReturnsFileData(t *testing.T) {
	part := parseImageURLToPart("https://example.com/image.png")
	if part == nil {
		t.Fatal("expected non-nil part for HTTP URL")
	}
	if part.FileData == nil {
		t.Fatal("expected FileData to be set for HTTP URL")
	}
	if part.FileData.FileURI != "https://example.com/image.png" {
		t.Errorf("FileURI = %q, want %q", part.FileData.FileURI, "https://example.com/image.png")
	}
	if part.FileData.MimeType != "image/png" {
		t.Errorf("MimeType = %q, want %q", part.FileData.MimeType, "image/png")
	}
}

func TestParseImageURLToPart_HTTPUrl_JPEG(t *testing.T) {
	part := parseImageURLToPart("https://example.com/photo.jpg")
	if part == nil {
		t.Fatal("expected non-nil part")
	}
	if part.FileData == nil {
		t.Fatal("expected FileData to be set")
	}
	if part.FileData.MimeType != "image/jpeg" {
		t.Errorf("MimeType = %q, want %q", part.FileData.MimeType, "image/jpeg")
	}
}

func TestParseImageURLToPart_HTTPUrl_WithQueryParams(t *testing.T) {
	part := parseImageURLToPart("https://example.com/photo.webp?width=100&height=100")
	if part == nil {
		t.Fatal("expected non-nil part")
	}
	if part.FileData == nil {
		t.Fatal("expected FileData to be set")
	}
	if part.FileData.MimeType != "image/webp" {
		t.Errorf("MimeType = %q, want %q", part.FileData.MimeType, "image/webp")
	}
}

func TestParseImageURLToPart_HTTPUrl_NoExtension(t *testing.T) {
	part := parseImageURLToPart("https://example.com/image")
	if part == nil {
		t.Fatal("expected non-nil part")
	}
	if part.FileData == nil {
		t.Fatal("expected FileData to be set")
	}
	// Default MIME type for unknown extension.
	if part.FileData.MimeType != "image/jpeg" {
		t.Errorf("MimeType = %q, want %q (default)", part.FileData.MimeType, "image/jpeg")
	}
}

func TestParseImageURLToPart_InvalidDataURI(t *testing.T) {
	// No semicolon -> parseDataURI returns empty data -> nil.
	part := parseImageURLToPart("data:image/png")
	if part != nil {
		t.Errorf("expected nil for invalid data URI, got %+v", part)
	}
}

func TestParseImageURLToPart_EmptyData(t *testing.T) {
	// Data URI with empty base64 data -> nil.
	part := parseImageURLToPart("data:image/gif;base64,")
	if part != nil {
		t.Errorf("expected nil for empty data, got %+v", part)
	}
}

func TestParseImageURLToPart_UnknownScheme(t *testing.T) {
	part := parseImageURLToPart("ftp://example.com/image.png")
	if part != nil {
		t.Errorf("expected nil for ftp URL, got %+v", part)
	}
}

// ---------------------------------------------------------------------------
// parseDataURI tests (Gemini)
// ---------------------------------------------------------------------------

func TestParseDataURI_Gemini_PNG(t *testing.T) {
	mediaType, data := parseDataURI("data:image/png;base64,iVBORw0KGgo=")
	if mediaType != "image/png" {
		t.Errorf("mediaType = %q, want %q", mediaType, "image/png")
	}
	if data != "iVBORw0KGgo=" {
		t.Errorf("data = %q, want %q", data, "iVBORw0KGgo=")
	}
}

func TestParseDataURI_Gemini_JPEG(t *testing.T) {
	mediaType, data := parseDataURI("data:image/jpeg;base64,/9j/4AAQ")
	if mediaType != "image/jpeg" {
		t.Errorf("mediaType = %q, want %q", mediaType, "image/jpeg")
	}
	if data != "/9j/4AAQ" {
		t.Errorf("data = %q, want %q", data, "/9j/4AAQ")
	}
}

func TestParseDataURI_Gemini_NoSemicolon(t *testing.T) {
	mediaType, data := parseDataURI("data:image/png")
	if mediaType != "" {
		t.Errorf("mediaType = %q, want empty", mediaType)
	}
	if data != "" {
		t.Errorf("data = %q, want empty", data)
	}
}

// ---------------------------------------------------------------------------
// translateMessage with vision content tests (Gemini)
// ---------------------------------------------------------------------------

func TestTranslateMessage_Gemini_VisionContent_ImageOnly(t *testing.T) {
	// When content has only image_url parts (no text), extractTextContent returns ""
	// and the vision path is taken.
	msg := models.Message{
		Role: "user",
		Content: json.RawMessage(`[
			{"type":"image_url","image_url":{"url":"data:image/png;base64,iVBORw0KGgo="}}
		]`),
	}

	gc := translateMessage(msg)

	if gc.Role != "user" {
		t.Errorf("Role = %q, want %q", gc.Role, "user")
	}
	if len(gc.Parts) != 1 {
		t.Fatalf("Parts length = %d, want 1", len(gc.Parts))
	}
	if gc.Parts[0].InlineData == nil {
		t.Fatal("Parts[0].InlineData should not be nil")
	}
	if gc.Parts[0].InlineData.MimeType != "image/png" {
		t.Errorf("InlineData.MimeType = %q, want %q", gc.Parts[0].InlineData.MimeType, "image/png")
	}
	if gc.Parts[0].InlineData.Data != "iVBORw0KGgo=" {
		t.Errorf("InlineData.Data = %q, want %q", gc.Parts[0].InlineData.Data, "iVBORw0KGgo=")
	}
}

func TestTranslateMessage_Gemini_VisionContent_WithText(t *testing.T) {
	// When content has both text and image_url parts, the vision path handles both.
	msg := models.Message{
		Role: "user",
		Content: json.RawMessage(`[
			{"type":"text","text":"Describe this image"},
			{"type":"image_url","image_url":{"url":"data:image/png;base64,iVBORw0KGgo="}}
		]`),
	}

	gc := translateMessage(msg)

	if gc.Role != "user" {
		t.Errorf("Role = %q, want %q", gc.Role, "user")
	}
	// Vision path preserves both text and image parts.
	if len(gc.Parts) != 2 {
		t.Fatalf("Parts length = %d, want 2 (text + image)", len(gc.Parts))
	}
	if gc.Parts[0].Text != "Describe this image" {
		t.Errorf("Parts[0].Text = %q, want %q", gc.Parts[0].Text, "Describe this image")
	}
	if gc.Parts[1].InlineData == nil {
		t.Fatal("Parts[1].InlineData is nil, want non-nil")
	}
	if gc.Parts[1].InlineData.MimeType != "image/png" {
		t.Errorf("Parts[1].InlineData.MimeType = %q, want %q", gc.Parts[1].InlineData.MimeType, "image/png")
	}
}

func TestTranslateMessage_Gemini_VisionContent_TextOnlyFallback(t *testing.T) {
	// Content array with only text should use extractTextContent path, not vision.
	msg := models.Message{
		Role:    "user",
		Content: json.RawMessage(`[{"type":"text","text":"Just a text message"}]`),
	}

	gc := translateMessage(msg)

	if gc.Role != "user" {
		t.Errorf("Role = %q, want %q", gc.Role, "user")
	}
	if len(gc.Parts) != 1 {
		t.Fatalf("Parts length = %d, want 1", len(gc.Parts))
	}
	if gc.Parts[0].Text != "Just a text message" {
		t.Errorf("Parts[0].Text = %q, want %q", gc.Parts[0].Text, "Just a text message")
	}
}

// ---------------------------------------------------------------------------
// translateResponse tests
// ---------------------------------------------------------------------------

func TestTranslateResponse_TextResponse(t *testing.T) {
	resp := &geminiResponse{
		Candidates: []geminiCandidate{
			{
				Content: geminiContent{
					Parts: []geminiPart{
						{Text: "Hello! I'm Gemini."},
					},
				},
				FinishReason: "STOP",
			},
		},
		UsageMetadata: &geminiUsageMetadata{
			PromptTokenCount:     10,
			CandidatesTokenCount: 5,
			TotalTokenCount:      15,
		},
	}

	result := translateResponse(resp, "gemini-1.5-pro")

	if result.Object != "chat.completion" {
		t.Errorf("Object = %q, want %q", result.Object, "chat.completion")
	}
	if result.Model != "gemini-1.5-pro" {
		t.Errorf("Model = %q, want %q", result.Model, "gemini-1.5-pro")
	}
	if !strings.HasPrefix(result.ID, "gen-") {
		t.Errorf("ID = %q, want prefix %q", result.ID, "gen-")
	}
	if result.Created == 0 {
		t.Error("Created should be non-zero")
	}
	if len(result.Choices) != 1 {
		t.Fatalf("Choices length = %d, want 1", len(result.Choices))
	}
	choice := result.Choices[0]
	if choice.Index != 0 {
		t.Errorf("Index = %d, want 0", choice.Index)
	}
	if choice.FinishReason != "stop" {
		t.Errorf("FinishReason = %q, want %q", choice.FinishReason, "stop")
	}
	if choice.Message.Role != "assistant" {
		t.Errorf("Message.Role = %q, want %q", choice.Message.Role, "assistant")
	}

	var content string
	if err := json.Unmarshal(choice.Message.Content, &content); err != nil {
		t.Fatalf("failed to unmarshal content: %v", err)
	}
	if content != "Hello! I'm Gemini." {
		t.Errorf("content = %q, want %q", content, "Hello! I'm Gemini.")
	}

	if result.Usage == nil {
		t.Fatal("Usage should not be nil")
	}
	if result.Usage.PromptTokens != 10 {
		t.Errorf("PromptTokens = %d, want 10", result.Usage.PromptTokens)
	}
	if result.Usage.CompletionTokens != 5 {
		t.Errorf("CompletionTokens = %d, want 5", result.Usage.CompletionTokens)
	}
	if result.Usage.TotalTokens != 15 {
		t.Errorf("TotalTokens = %d, want 15", result.Usage.TotalTokens)
	}
}

func TestTranslateResponse_FunctionCallResponse(t *testing.T) {
	resp := &geminiResponse{
		Candidates: []geminiCandidate{
			{
				Content: geminiContent{
					Parts: []geminiPart{
						{
							FunctionCall: &geminiFunctionCall{
								Name: "get_weather",
								Args: json.RawMessage(`{"location":"NYC"}`),
							},
						},
					},
				},
				FinishReason: "STOP",
			},
		},
	}

	result := translateResponse(resp, "gemini-1.5-pro")

	if len(result.Choices) != 1 {
		t.Fatalf("Choices length = %d, want 1", len(result.Choices))
	}
	msg := result.Choices[0].Message
	if len(msg.ToolCalls) != 1 {
		t.Fatalf("ToolCalls length = %d, want 1", len(msg.ToolCalls))
	}
	tc := msg.ToolCalls[0]
	if tc.ID != "call_0" {
		t.Errorf("ToolCall.ID = %q, want %q", tc.ID, "call_0")
	}
	if tc.Type != "function" {
		t.Errorf("ToolCall.Type = %q, want %q", tc.Type, "function")
	}
	if tc.Function.Name != "get_weather" {
		t.Errorf("Function.Name = %q, want %q", tc.Function.Name, "get_weather")
	}
	if tc.Function.Arguments != `{"location":"NYC"}` {
		t.Errorf("Function.Arguments = %q, want %q", tc.Function.Arguments, `{"location":"NYC"}`)
	}
}

func TestTranslateResponse_MultipleFunctionCalls(t *testing.T) {
	resp := &geminiResponse{
		Candidates: []geminiCandidate{
			{
				Content: geminiContent{
					Parts: []geminiPart{
						{
							FunctionCall: &geminiFunctionCall{
								Name: "func_a",
								Args: json.RawMessage(`{"a":1}`),
							},
						},
						{
							FunctionCall: &geminiFunctionCall{
								Name: "func_b",
								Args: json.RawMessage(`{"b":2}`),
							},
						},
					},
				},
				FinishReason: "STOP",
			},
		},
	}

	result := translateResponse(resp, "gemini-1.5-pro")

	if len(result.Choices[0].Message.ToolCalls) != 2 {
		t.Fatalf("ToolCalls length = %d, want 2", len(result.Choices[0].Message.ToolCalls))
	}
	if result.Choices[0].Message.ToolCalls[0].ID != "call_0" {
		t.Errorf("ToolCall[0].ID = %q, want %q", result.Choices[0].Message.ToolCalls[0].ID, "call_0")
	}
	if result.Choices[0].Message.ToolCalls[1].ID != "call_1" {
		t.Errorf("ToolCall[1].ID = %q, want %q", result.Choices[0].Message.ToolCalls[1].ID, "call_1")
	}
	if result.Choices[0].Message.ToolCalls[0].Function.Name != "func_a" {
		t.Errorf("ToolCall[0].Function.Name = %q, want %q", result.Choices[0].Message.ToolCalls[0].Function.Name, "func_a")
	}
	if result.Choices[0].Message.ToolCalls[1].Function.Name != "func_b" {
		t.Errorf("ToolCall[1].Function.Name = %q, want %q", result.Choices[0].Message.ToolCalls[1].Function.Name, "func_b")
	}
}

func TestTranslateResponse_TextAndFunctionCall(t *testing.T) {
	resp := &geminiResponse{
		Candidates: []geminiCandidate{
			{
				Content: geminiContent{
					Parts: []geminiPart{
						{Text: "Let me check."},
						{
							FunctionCall: &geminiFunctionCall{
								Name: "lookup",
								Args: json.RawMessage(`{"id":"x"}`),
							},
						},
					},
				},
				FinishReason: "STOP",
			},
		},
	}

	result := translateResponse(resp, "gemini-1.5-pro")

	msg := result.Choices[0].Message
	var content string
	if err := json.Unmarshal(msg.Content, &content); err != nil {
		t.Fatalf("failed to unmarshal content: %v", err)
	}
	if content != "Let me check." {
		t.Errorf("content = %q, want %q", content, "Let me check.")
	}
	if len(msg.ToolCalls) != 1 {
		t.Fatalf("ToolCalls length = %d, want 1", len(msg.ToolCalls))
	}
	if msg.ToolCalls[0].Function.Name != "lookup" {
		t.Errorf("Function.Name = %q, want %q", msg.ToolCalls[0].Function.Name, "lookup")
	}
}

func TestTranslateResponse_FunctionCallUsesToolCallsFinishReason(t *testing.T) {
	resp := &geminiResponse{
		Candidates: []geminiCandidate{
			{
				Content: geminiContent{
					Parts: []geminiPart{{
						FunctionCall: &geminiFunctionCall{
							Name: "lookup",
							Args: json.RawMessage(`{"id":"x"}`),
						},
					}},
				},
				FinishReason: "STOP",
			},
		},
	}

	result := translateResponse(resp, "gemini-1.5-pro")

	if result.Choices[0].FinishReason != "tool_calls" {
		t.Errorf("finish reason = %q, want %q", result.Choices[0].FinishReason, "tool_calls")
	}
}

func TestTranslateResponse_FinishReasonMapping(t *testing.T) {
	tests := []struct {
		gemini string
		openai string
	}{
		{"STOP", "stop"},
		{"MAX_TOKENS", "length"},
		{"SAFETY", "content_filter"},
		{"RECITATION", "content_filter"},
		{"OTHER", "stop"},
		{"UNKNOWN_VALUE", "stop"},
		{"", "stop"},
	}

	for _, tt := range tests {
		t.Run(tt.gemini, func(t *testing.T) {
			resp := &geminiResponse{
				Candidates: []geminiCandidate{
					{
						Content:      geminiContent{Parts: []geminiPart{{Text: "test"}}},
						FinishReason: tt.gemini,
					},
				},
			}
			result := translateResponse(resp, "test-model")
			if result.Choices[0].FinishReason != tt.openai {
				t.Errorf("mapGeminiFinishReason(%q) = %q, want %q",
					tt.gemini, result.Choices[0].FinishReason, tt.openai)
			}
		})
	}
}

func TestTranslateResponse_NoCandidates(t *testing.T) {
	resp := &geminiResponse{
		Candidates: nil,
		UsageMetadata: &geminiUsageMetadata{
			PromptTokenCount: 5,
			TotalTokenCount:  5,
		},
	}

	result := translateResponse(resp, "gemini-1.5-pro")

	if len(result.Choices) != 0 {
		t.Errorf("Choices length = %d, want 0", len(result.Choices))
	}
	if result.Usage == nil {
		t.Fatal("Usage should not be nil")
	}
	if result.Usage.PromptTokens != 5 {
		t.Errorf("PromptTokens = %d, want 5", result.Usage.PromptTokens)
	}
}

func TestTranslateResponse_NoUsage(t *testing.T) {
	resp := &geminiResponse{
		Candidates: []geminiCandidate{
			{
				Content:      geminiContent{Parts: []geminiPart{{Text: "Hi"}}},
				FinishReason: "STOP",
			},
		},
		UsageMetadata: nil,
	}

	result := translateResponse(resp, "gemini-1.5-pro")

	if result.Usage != nil {
		t.Errorf("Usage = %v, want nil", result.Usage)
	}
}

func TestTranslateResponse_GeneratedIDPrefix(t *testing.T) {
	resp := &geminiResponse{
		Candidates: []geminiCandidate{
			{
				Content:      geminiContent{Parts: []geminiPart{{Text: "test"}}},
				FinishReason: "STOP",
			},
		},
	}

	result := translateResponse(resp, "gemini-1.5-pro")

	if !strings.HasPrefix(result.ID, "gen-") {
		t.Errorf("ID = %q, should start with 'gen-'", result.ID)
	}
}

// ---------------------------------------------------------------------------
// buildURL tests
// ---------------------------------------------------------------------------

func TestBuildURL(t *testing.T) {
	tests := []struct {
		name    string
		baseURL string
		model   string
		stream  bool
		want    string
	}{
		{
			name:    "non-streaming",
			baseURL: "https://generativelanguage.googleapis.com",
			model:   "gemini-1.5-pro",
			stream:  false,
			want:    "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent",
		},
		{
			name:    "streaming",
			baseURL: "https://generativelanguage.googleapis.com",
			model:   "gemini-1.5-pro",
			stream:  true,
			want:    "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:streamGenerateContent?alt=sse",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := buildURL(tt.baseURL, tt.model, tt.stream)
			if got != tt.want {
				t.Errorf("buildURL() = %q, want %q", got, tt.want)
			}
		})
	}
}

func TestBuildURL_DifferentModels(t *testing.T) {
	got := buildURL("https://example.com", "gemini-2.0-flash", false)
	want := "https://example.com/v1beta/models/gemini-2.0-flash:generateContent"
	if got != want {
		t.Errorf("buildURL() = %q, want %q", got, want)
	}
}

// ---------------------------------------------------------------------------
// isVertexAI tests
// ---------------------------------------------------------------------------

func TestIsVertexAI(t *testing.T) {
	tests := []struct {
		name    string
		baseURL string
		want    bool
	}{
		{"AI Studio URL", "https://generativelanguage.googleapis.com", false},
		{"Vertex AI URL", "https://us-central1-aiplatform.googleapis.com", true},
		{"Vertex AI URL with path", "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/my-proj/locations/us-central1", true},
		{"Custom non-Vertex URL", "https://example.com", false},
		{"Vertex AI europe region", "https://europe-west4-aiplatform.googleapis.com", true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := isVertexAI(tt.baseURL)
			if got != tt.want {
				t.Errorf("isVertexAI(%q) = %v, want %v", tt.baseURL, got, tt.want)
			}
		})
	}
}

// ---------------------------------------------------------------------------
// buildVertexURL tests
// ---------------------------------------------------------------------------

func TestBuildVertexURL_WithProjectPath(t *testing.T) {
	baseURL := "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/my-project/locations/us-central1"
	model := "gemini-1.5-pro"

	got := buildVertexURL(baseURL, model, false)
	want := "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/my-project/locations/us-central1/publishers/google/models/gemini-1.5-pro:generateContent"
	if got != want {
		t.Errorf("buildVertexURL() =\n  %q\nwant:\n  %q", got, want)
	}
}

func TestBuildVertexURL_WithProjectPath_Streaming(t *testing.T) {
	baseURL := "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/my-project/locations/us-central1"
	model := "gemini-1.5-pro"

	got := buildVertexURL(baseURL, model, true)
	want := "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/my-project/locations/us-central1/publishers/google/models/gemini-1.5-pro:streamGenerateContent?alt=sse"
	if got != want {
		t.Errorf("buildVertexURL() =\n  %q\nwant:\n  %q", got, want)
	}
}

func TestBuildVertexURL_WithoutProjectPath(t *testing.T) {
	baseURL := "https://us-central1-aiplatform.googleapis.com"
	model := "gemini-2.0-flash"

	got := buildVertexURL(baseURL, model, false)
	want := "https://us-central1-aiplatform.googleapis.com/v1beta1/models/gemini-2.0-flash:generateContent"
	if got != want {
		t.Errorf("buildVertexURL() =\n  %q\nwant:\n  %q", got, want)
	}
}

func TestBuildVertexURL_WithoutProjectPath_Streaming(t *testing.T) {
	baseURL := "https://us-central1-aiplatform.googleapis.com"
	model := "gemini-2.0-flash"

	got := buildVertexURL(baseURL, model, true)
	want := "https://us-central1-aiplatform.googleapis.com/v1beta1/models/gemini-2.0-flash:streamGenerateContent?alt=sse"
	if got != want {
		t.Errorf("buildVertexURL() =\n  %q\nwant:\n  %q", got, want)
	}
}

// ---------------------------------------------------------------------------
// buildURL with Vertex AI detection tests
// ---------------------------------------------------------------------------

func TestBuildURL_VertexAI_NonStreaming(t *testing.T) {
	// When baseURL is a Vertex AI URL with project path, buildURL should delegate to buildVertexURL.
	baseURL := "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/my-project/locations/us-central1"
	got := buildURL(baseURL, "gemini-1.5-pro", false)
	want := "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/my-project/locations/us-central1/publishers/google/models/gemini-1.5-pro:generateContent"
	if got != want {
		t.Errorf("buildURL() =\n  %q\nwant:\n  %q", got, want)
	}
}

func TestBuildURL_VertexAI_Streaming(t *testing.T) {
	baseURL := "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/my-project/locations/us-central1"
	got := buildURL(baseURL, "gemini-1.5-pro", true)
	want := "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/my-project/locations/us-central1/publishers/google/models/gemini-1.5-pro:streamGenerateContent?alt=sse"
	if got != want {
		t.Errorf("buildURL() =\n  %q\nwant:\n  %q", got, want)
	}
}

func TestBuildURL_VertexAI_WithoutProjectPath(t *testing.T) {
	baseURL := "https://europe-west4-aiplatform.googleapis.com"
	got := buildURL(baseURL, "gemini-1.5-flash", false)
	want := "https://europe-west4-aiplatform.googleapis.com/v1beta1/models/gemini-1.5-flash:generateContent"
	if got != want {
		t.Errorf("buildURL() =\n  %q\nwant:\n  %q", got, want)
	}
}

func TestBuildURL_AIStudio_UnchangedBehavior(t *testing.T) {
	// AI Studio URLs should still produce the v1beta format.
	baseURL := "https://generativelanguage.googleapis.com"
	got := buildURL(baseURL, "gemini-1.5-pro", false)
	want := "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent"
	if got != want {
		t.Errorf("buildURL() = %q, want %q", got, want)
	}

	got = buildURL(baseURL, "gemini-1.5-pro", true)
	want = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:streamGenerateContent?alt=sse"
	if got != want {
		t.Errorf("buildURL() = %q, want %q", got, want)
	}
}

// ---------------------------------------------------------------------------
// parseStreamData tests
// ---------------------------------------------------------------------------

func TestParseStreamData_TextContentChunk(t *testing.T) {
	data := `{
		"candidates": [{
			"content": {
				"role": "model",
				"parts": [{"text": "Hello world"}]
			}
		}]
	}`

	state := newStreamState("gemini-1.5-pro")
	chunk, err := state.parseStreamData(data)

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if chunk == nil {
		t.Fatal("chunk should not be nil")
	}
	if chunk.Object != "chat.completion.chunk" {
		t.Errorf("Object = %q, want %q", chunk.Object, "chat.completion.chunk")
	}
	if chunk.Model != "gemini-1.5-pro" {
		t.Errorf("Model = %q, want %q", chunk.Model, "gemini-1.5-pro")
	}
	if !strings.HasPrefix(chunk.ID, "gen-") {
		t.Errorf("ID = %q, want prefix %q", chunk.ID, "gen-")
	}
	if len(chunk.Choices) != 1 {
		t.Fatalf("Choices length = %d, want 1", len(chunk.Choices))
	}

	sc := chunk.Choices[0]
	if sc.Delta.Content == nil {
		t.Fatal("Delta.Content should not be nil")
	}
	if *sc.Delta.Content != "Hello world" {
		t.Errorf("Delta.Content = %q, want %q", *sc.Delta.Content, "Hello world")
	}
	if sc.FinishReason != nil {
		t.Errorf("FinishReason should be nil for intermediate chunk, got %q", *sc.FinishReason)
	}
}

func TestParseStreamData_FunctionCallChunk(t *testing.T) {
	data := `{
		"candidates": [{
			"content": {
				"role": "model",
				"parts": [{
					"functionCall": {
						"name": "search",
						"args": {"query": "golang"}
					}
				}]
			}
		}]
	}`

	state := newStreamState("gemini-1.5-pro")
	chunk, err := state.parseStreamData(data)

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if chunk == nil {
		t.Fatal("chunk should not be nil")
	}
	if len(chunk.Choices) != 1 {
		t.Fatalf("Choices length = %d, want 1", len(chunk.Choices))
	}

	delta := chunk.Choices[0].Delta
	if len(delta.ToolCalls) != 1 {
		t.Fatalf("ToolCalls length = %d, want 1", len(delta.ToolCalls))
	}
	if delta.ToolCalls[0].Function == nil {
		t.Fatal("Function should not be nil")
	}
	if delta.ToolCalls[0].Function.Name != "search" {
		t.Errorf("Function.Name = %q, want %q", delta.ToolCalls[0].Function.Name, "search")
	}
	if delta.ToolCalls[0].Type != "function" {
		t.Errorf("Type = %q, want %q", delta.ToolCalls[0].Type, "function")
	}
}

func TestParseStreamData_FunctionCallChunkUsesToolCallsFinishReason(t *testing.T) {
	data := `{
		"candidates": [{
			"content": {
				"role": "model",
				"parts": [{
					"functionCall": {
						"name": "search",
						"args": {"query": "golang"}
					}
				}]
			},
			"finishReason": "STOP"
		}]
	}`

	state := newStreamState("gemini-1.5-pro")
	chunk, err := state.parseStreamData(data)

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if chunk == nil {
		t.Fatal("chunk should not be nil")
	}
	if chunk.Choices[0].FinishReason == nil {
		t.Fatal("finish reason should not be nil")
	}
	if *chunk.Choices[0].FinishReason != "tool_calls" {
		t.Errorf("finish reason = %q, want %q", *chunk.Choices[0].FinishReason, "tool_calls")
	}
}

func TestParseStreamData_ChunkWithFinishReason(t *testing.T) {
	data := `{
		"candidates": [{
			"content": {
				"role": "model",
				"parts": [{"text": "Done."}]
			},
			"finishReason": "STOP"
		}]
	}`

	state := newStreamState("gemini-1.5-pro")
	chunk, err := state.parseStreamData(data)

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	sc := chunk.Choices[0]
	if sc.FinishReason == nil {
		t.Fatal("FinishReason should not be nil")
	}
	if *sc.FinishReason != "stop" {
		t.Errorf("FinishReason = %q, want %q", *sc.FinishReason, "stop")
	}
}

func TestParseStreamData_ChunkWithMaxTokensFinishReason(t *testing.T) {
	data := `{
		"candidates": [{
			"content": {
				"role": "model",
				"parts": [{"text": "truncated output"}]
			},
			"finishReason": "MAX_TOKENS"
		}]
	}`

	state := newStreamState("gemini-1.5-pro")
	chunk, err := state.parseStreamData(data)

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if *chunk.Choices[0].FinishReason != "length" {
		t.Errorf("FinishReason = %q, want %q", *chunk.Choices[0].FinishReason, "length")
	}
}

func TestParseStreamData_ChunkWithUsage(t *testing.T) {
	data := `{
		"candidates": [{
			"content": {
				"role": "model",
				"parts": [{"text": "final"}]
			},
			"finishReason": "STOP"
		}],
		"usageMetadata": {
			"promptTokenCount": 10,
			"candidatesTokenCount": 20,
			"totalTokenCount": 30
		}
	}`

	state := newStreamState("gemini-1.5-pro")
	chunk, err := state.parseStreamData(data)

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if chunk.Usage == nil {
		t.Fatal("Usage should not be nil")
	}
	if chunk.Usage.PromptTokens != 10 {
		t.Errorf("PromptTokens = %d, want 10", chunk.Usage.PromptTokens)
	}
	if chunk.Usage.CompletionTokens != 20 {
		t.Errorf("CompletionTokens = %d, want 20", chunk.Usage.CompletionTokens)
	}
	if chunk.Usage.TotalTokens != 30 {
		t.Errorf("TotalTokens = %d, want 30", chunk.Usage.TotalTokens)
	}
}

func TestParseStreamData_NoFinishReason(t *testing.T) {
	data := `{"candidates":[{"content":{"parts":[{"text":"partial"}]}}]}`

	state := newStreamState("gemini-1.5-pro")
	chunk, err := state.parseStreamData(data)

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if chunk.Choices[0].FinishReason != nil {
		t.Errorf("FinishReason should be nil for intermediate chunk, got %q",
			*chunk.Choices[0].FinishReason)
	}
}

func TestParseStreamData_NoCandidates(t *testing.T) {
	data := `{"usageMetadata":{"promptTokenCount":10,"candidatesTokenCount":0,"totalTokenCount":10}}`

	state := newStreamState("gemini-1.5-pro")
	chunk, err := state.parseStreamData(data)

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if chunk == nil {
		t.Fatal("chunk should not be nil")
	}
	if len(chunk.Choices) != 0 {
		t.Errorf("Choices length = %d, want 0", len(chunk.Choices))
	}
}

func TestParseStreamData_InvalidJSON(t *testing.T) {
	state := newStreamState("gemini-1.5-pro")
	_, err := state.parseStreamData("not json")

	if err == nil {
		t.Error("expected error for invalid JSON, got nil")
	}
}

func TestParseStreamData_ConsistentChunkID(t *testing.T) {
	state := newStreamState("gemini-1.5-pro")

	data1 := `{"candidates":[{"content":{"parts":[{"text":"Hello"}]}}]}`
	data2 := `{"candidates":[{"content":{"parts":[{"text":" world"}]}}]}`

	chunk1, err := state.parseStreamData(data1)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	chunk2, err := state.parseStreamData(data2)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if chunk1.ID != chunk2.ID {
		t.Errorf("chunk IDs differ: %q vs %q (should be same for one stream)", chunk1.ID, chunk2.ID)
	}
}

// ---------------------------------------------------------------------------
// mapRoleToGemini tests
// ---------------------------------------------------------------------------

func TestMapRoleToGemini(t *testing.T) {
	tests := []struct {
		input string
		want  string
	}{
		{"assistant", "model"},
		{"user", "user"},
		{"tool", "tool"},
		{"system", "system"},
	}
	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			got := mapRoleToGemini(tt.input)
			if got != tt.want {
				t.Errorf("mapRoleToGemini(%q) = %q, want %q", tt.input, got, tt.want)
			}
		})
	}
}

// ---------------------------------------------------------------------------
// resolveModelName tests
// ---------------------------------------------------------------------------

func TestResolveModelName(t *testing.T) {
	tests := []struct {
		input string
		want  string
	}{
		{"gemini-1.5-pro", "gemini-1.5-pro"},
		{"google/gemini-1.5-pro", "gemini-1.5-pro"},
		{"provider/sub/model", "sub/model"},
		{"no-slash", "no-slash"},
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

// ---------------------------------------------------------------------------
// extractTextContent tests
// ---------------------------------------------------------------------------

func TestExtractTextContent(t *testing.T) {
	tests := []struct {
		name  string
		input json.RawMessage
		want  string
	}{
		{"plain string", mustJSON("hello"), "hello"},
		{"empty", nil, ""},
		{"array with text part", mustJSON([]map[string]string{{"type": "text", "text": "hello"}}), "hello"},
		{"array with multiple text parts", mustJSON([]map[string]string{{"type": "text", "text": "hello"}, {"type": "text", "text": " world"}}), "hello world"},
		{"array without text", mustJSON([]map[string]string{{"type": "image_url"}}), ""},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := extractTextContent(tt.input)
			if got != tt.want {
				t.Errorf("extractTextContent(%s) = %q, want %q", tt.input, got, tt.want)
			}
		})
	}
}

// ---------------------------------------------------------------------------
// parseGeminiError tests
// ---------------------------------------------------------------------------

func TestParseGeminiError_StructuredError(t *testing.T) {
	body := []byte(`{"error":{"code":400,"message":"Invalid argument","status":"INVALID_ARGUMENT"}}`)
	apiErr := parseGeminiError(400, body)

	if apiErr.Status != 400 {
		t.Errorf("Status = %d, want 400", apiErr.Status)
	}
	if apiErr.Message != "Invalid argument" {
		t.Errorf("Message = %q, want %q", apiErr.Message, "Invalid argument")
	}
	if apiErr.Type != models.ErrTypeInvalidRequest {
		t.Errorf("Type = %q, want %q", apiErr.Type, models.ErrTypeInvalidRequest)
	}
	if apiErr.Code != "provider_INVALID_ARGUMENT" {
		t.Errorf("Code = %q, want %q", apiErr.Code, "provider_INVALID_ARGUMENT")
	}
}

func TestParseGeminiError_RateLimitError(t *testing.T) {
	body := []byte(`{"error":{"code":429,"message":"Quota exceeded","status":"RESOURCE_EXHAUSTED"}}`)
	apiErr := parseGeminiError(429, body)

	if apiErr.Status != 429 {
		t.Errorf("Status = %d, want 429", apiErr.Status)
	}
	if apiErr.Type != models.ErrTypeRateLimit {
		t.Errorf("Type = %q, want %q", apiErr.Type, models.ErrTypeRateLimit)
	}
}

func TestParseGeminiError_UnstructuredBody(t *testing.T) {
	body := []byte("some raw error text")
	apiErr := parseGeminiError(500, body)

	if apiErr.Status != 502 {
		t.Errorf("Status = %d, want 502 (ErrUpstreamProvider)", apiErr.Status)
	}
	if !strings.Contains(apiErr.Message, "some raw error text") {
		t.Errorf("Message = %q, should contain the raw text", apiErr.Message)
	}
}

func TestParseGeminiError_LongBody(t *testing.T) {
	// Bodies longer than 500 chars get truncated.
	body := make([]byte, 600)
	for i := range body {
		body[i] = 'x'
	}
	apiErr := parseGeminiError(500, body)

	if !strings.HasSuffix(apiErr.Message, "...") {
		t.Errorf("long body should be truncated with '...' suffix")
	}
}

// ---------------------------------------------------------------------------
// mapGeminiStatus tests
// ---------------------------------------------------------------------------

func TestMapGeminiStatus(t *testing.T) {
	tests := []struct {
		input string
		want  int
	}{
		{"INVALID_ARGUMENT", 400},
		{"UNAUTHENTICATED", 401},
		{"PERMISSION_DENIED", 403},
		{"NOT_FOUND", 404},
		{"RESOURCE_EXHAUSTED", 429},
		{"UNKNOWN", 502},
	}
	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			got := mapGeminiStatus(tt.input)
			if got != tt.want {
				t.Errorf("mapGeminiStatus(%q) = %d, want %d", tt.input, got, tt.want)
			}
		})
	}
}

// ---------------------------------------------------------------------------
// mapGeminiErrorType tests
// ---------------------------------------------------------------------------

func TestMapGeminiErrorType(t *testing.T) {
	tests := []struct {
		input string
		want  string
	}{
		{"INVALID_ARGUMENT", models.ErrTypeInvalidRequest},
		{"UNAUTHENTICATED", models.ErrTypeAuthentication},
		{"PERMISSION_DENIED", models.ErrTypePermission},
		{"NOT_FOUND", models.ErrTypeNotFound},
		{"RESOURCE_EXHAUSTED", models.ErrTypeRateLimit},
		{"INTERNAL", models.ErrTypeUpstream},
	}
	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			got := mapGeminiErrorType(tt.input)
			if got != tt.want {
				t.Errorf("mapGeminiErrorType(%q) = %q, want %q", tt.input, got, tt.want)
			}
		})
	}
}
