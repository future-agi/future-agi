package bedrock

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func isInferenceProfile(modelID string) bool {
	return strings.HasPrefix(modelID, "arn:") ||
		strings.HasPrefix(modelID, "us.") ||
		strings.HasPrefix(modelID, "eu.") ||
		strings.HasPrefix(modelID, "ap.") ||
		strings.HasPrefix(modelID, "global.")
}

type converseRequest struct {
	Messages        []converseMessage     `json:"messages"`
	System          []converseSystemMsg   `json:"system,omitempty"`
	InferenceConfig converseInference     `json:"inferenceConfig,omitempty"`
	ToolConfig      *converseToolConfig   `json:"toolConfig,omitempty"`
	OutputConfig    *converseOutputConfig `json:"outputConfig,omitempty"`
}

type converseOutputConfig struct {
	TextFormat *converseTextFormat `json:"textFormat,omitempty"`
}

type converseTextFormat struct {
	Type      string                `json:"type"`
	Structure converseTextStructure `json:"structure"`
}

type converseTextStructure struct {
	JSONSchema converseJSONSchema `json:"jsonSchema"`
}

type converseJSONSchema struct {
	Schema      string `json:"schema"`
	Name        string `json:"name,omitempty"`
	Description string `json:"description,omitempty"`
}

type converseSystemMsg struct {
	Text string `json:"text"`
}

type converseInference struct {
	MaxTokens     int      `json:"maxTokens,omitempty"`
	Temperature   *float64 `json:"temperature,omitempty"`
	TopP          *float64 `json:"topP,omitempty"`
	StopSequences []string `json:"stopSequences,omitempty"`
}

type converseMessage struct {
	Role    string                `json:"role"`
	Content []converseContentPart `json:"content"`
}

// messageHasToolResult reports whether any content part in the message is a
// tool_result. Used to scope same-role merging to tool-result sequences only,
// so we don't silently bundle independent user/user or assistant/assistant
// turns.
func messageHasToolResult(m converseMessage) bool {
	for _, p := range m.Content {
		if p.ToolResult != nil {
			return true
		}
	}
	return false
}

type converseContentPart struct {
	Text       *string             `json:"text,omitempty"`
	Image      *converseImagePart  `json:"image,omitempty"`
	ToolUse    *converseToolUse    `json:"toolUse,omitempty"`
	ToolResult *converseToolResult `json:"toolResult,omitempty"`
}

type converseImagePart struct {
	Format string              `json:"format"`
	Source converseImageSource `json:"source"`
}

type converseImageSource struct {
	Bytes []byte `json:"bytes,omitempty"`
}

type converseToolUse struct {
	ToolUseID string          `json:"toolUseId"`
	Name      string          `json:"name"`
	Input     json.RawMessage `json:"input"`
}

type converseToolResult struct {
	ToolUseID string                `json:"toolUseId"`
	Content   []converseContentPart `json:"content"`
}

type converseToolConfig struct {
	Tools []converseTool `json:"tools"`
}

type converseTool struct {
	ToolSpec converseToolSpec `json:"toolSpec"`
}

type converseToolSpec struct {
	Name        string         `json:"name"`
	Description string         `json:"description,omitempty"`
	InputSchema converseSchema `json:"inputSchema"`
}

type converseSchema struct {
	JSON json.RawMessage `json:"json"`
}

type converseResponse struct {
	Output     converseOutput `json:"output"`
	StopReason string         `json:"stopReason"`
	Usage      converseUsage  `json:"usage"`
}

type converseOutput struct {
	Message converseMessage `json:"message"`
}

type converseUsage struct {
	InputTokens  int `json:"inputTokens"`
	OutputTokens int `json:"outputTokens"`
	TotalTokens  int `json:"totalTokens"`
}

func buildConverseRequest(req *models.ChatCompletionRequest) (*converseRequest, string) {
	modelID := resolveModelName(req.Model)
	cr := &converseRequest{}

	cfg := converseInference{Temperature: req.Temperature, TopP: req.TopP}
	switch {
	case req.MaxTokens != nil:
		cfg.MaxTokens = *req.MaxTokens
	case req.MaxCompletionTokens != nil:
		cfg.MaxTokens = *req.MaxCompletionTokens
	default:
		cfg.MaxTokens = 4096
	}
	if len(req.Stop) > 0 {
		var stops []string
		if err := json.Unmarshal(req.Stop, &stops); err == nil {
			cfg.StopSequences = stops
		} else {
			var single string
			if json.Unmarshal(req.Stop, &single) == nil {
				cfg.StopSequences = []string{single}
			}
		}
	}
	cr.InferenceConfig = cfg

	for _, msg := range req.Messages {
		if msg.Role == "system" {
			if text := extractTextContent(msg.Content); text != "" {
				cr.System = append(cr.System, converseSystemMsg{Text: text})
			}
			continue
		}
		cm := buildConverseMessage(msg)

		// Bedrock Converse API requires alternating user/assistant roles.
		// We merge consecutive same-role messages ONLY when BOTH sides are
		// tool results — that's the only case the API forces us to collapse
		// (multiple OpenAI "tool" messages all become role:"user" with
		// ToolResult blocks). Don't bundle independent user/user or
		// assistant/assistant turns, and don't bundle a real user follow-up
		// into a prior tool result: those silently rewrite caller intent.
		// If those occur the request goes through as-is and Bedrock returns
		// a clear alternation error.
		shouldMerge := len(cr.Messages) > 0 &&
			cm.Role == cr.Messages[len(cr.Messages)-1].Role &&
			msg.Role == "tool" &&
			messageHasToolResult(cr.Messages[len(cr.Messages)-1])
		if shouldMerge {
			cr.Messages[len(cr.Messages)-1].Content = append(
				cr.Messages[len(cr.Messages)-1].Content, cm.Content...,
			)
		} else {
			cr.Messages = append(cr.Messages, cm)
		}
	}

	if len(req.Tools) > 0 {
		tc := &converseToolConfig{}
		for _, t := range req.Tools {
			if t.Type != "function" {
				continue
			}
			tc.Tools = append(tc.Tools, converseTool{
				ToolSpec: converseToolSpec{
					Name:        t.Function.Name,
					Description: t.Function.Description,
					InputSchema: converseSchema{JSON: t.Function.Parameters},
				},
			})
		}
		if len(tc.Tools) > 0 {
			cr.ToolConfig = tc
		}
	}

	if format := buildConverseOutputFormat(req.ResponseFormat); format != nil {
		cr.OutputConfig = &converseOutputConfig{TextFormat: format}
	}

	return cr, modelID
}

func buildConverseOutputFormat(rf *models.ResponseFormat) *converseTextFormat {
	if rf == nil || rf.Type != "json_schema" || len(rf.JSONSchema) == 0 {
		return nil
	}
	var wrapper struct {
		Name        string          `json:"name"`
		Description string          `json:"description"`
		Schema      json.RawMessage `json:"schema"`
	}
	schema := rf.JSONSchema
	name := ""
	description := ""
	if json.Unmarshal(rf.JSONSchema, &wrapper) == nil {
		if len(wrapper.Schema) > 0 {
			schema = wrapper.Schema
		}
		name = wrapper.Name
		description = wrapper.Description
	}
	// Bedrock's Converse outputConfig requires `additionalProperties: false` on
	// every `object` schema. Inject it transparently so callers can ship
	// OpenAI-shape json_schema without provider-specific tweaks.
	schema = ensureObjectAdditionalPropertiesFalse(schema)
	return &converseTextFormat{
		Type: "json_schema",
		Structure: converseTextStructure{
			JSONSchema: converseJSONSchema{
				Schema:      string(schema),
				Name:        name,
				Description: description,
			},
		},
	}
}

func buildConverseMessage(msg models.Message) converseMessage {
	if msg.Role == "tool" {
		toolResult := &converseToolResult{
			ToolUseID: msg.ToolCallID,
			Content:   buildConverseContentParts(msg.Content),
		}
		if len(toolResult.Content) == 0 {
			empty := ""
			toolResult.Content = []converseContentPart{{Text: &empty}}
		}
		return converseMessage{
			Role:    "user",
			Content: []converseContentPart{{ToolResult: toolResult}},
		}
	}

	cm := converseMessage{Role: msg.Role, Content: buildConverseContentParts(msg.Content)}

	if msg.Role == "assistant" && len(msg.ToolCalls) > 0 {
		for _, tc := range msg.ToolCalls {
			cm.Content = append(cm.Content, converseContentPart{ToolUse: &converseToolUse{
				ToolUseID: tc.ID,
				Name:      tc.Function.Name,
				Input:     json.RawMessage(tc.Function.Arguments),
			}})
		}
	}

	if len(cm.Content) == 0 {
		empty := ""
		cm.Content = []converseContentPart{{Text: &empty}}
	}
	return cm
}

func buildConverseContentParts(content json.RawMessage) []converseContentPart {
	var blocks []converseContentPart

	var parts []struct {
		Type     string          `json:"type"`
		Text     string          `json:"text"`
		ImageURL json.RawMessage `json:"image_url"`
	}
	if err := json.Unmarshal(content, &parts); err == nil {
		for _, p := range parts {
			switch p.Type {
			case "text":
				t := p.Text
				blocks = append(blocks, converseContentPart{Text: &t})
			case "image_url":
				var iu struct {
					URL string `json:"url"`
				}
				if json.Unmarshal(p.ImageURL, &iu) == nil {
					if img := dataURLToConverseImage(iu.URL); img != nil {
						blocks = append(blocks, converseContentPart{Image: img})
					}
				}
			}
		}
		if len(blocks) > 0 {
			return blocks
		}
	}

	if text := extractTextContent(content); text != "" {
		t := text
		return []converseContentPart{{Text: &t}}
	}

	return nil
}

func dataURLToConverseImage(dataURL string) *converseImagePart {
	if !strings.HasPrefix(dataURL, "data:") {
		return nil
	}
	comma := strings.Index(dataURL, ",")
	if comma < 0 {
		return nil
	}
	header := dataURL[5:comma]
	data := dataURL[comma+1:]

	format := "jpeg"
	for _, f := range []string{"png", "gif", "webp", "jpeg", "jpg"} {
		if strings.Contains(header, f) {
			if f == "jpg" {
				f = "jpeg"
			}
			format = f
			break
		}
	}

	var raw []byte
	if strings.Contains(header, "base64") {
		var err error
		raw, err = base64.StdEncoding.DecodeString(data)
		if err != nil {
			return nil
		}
	} else {
		raw = []byte(data)
	}

	return &converseImagePart{Format: format, Source: converseImageSource{Bytes: raw}}
}

func newConverseStreamState(model string) *converseStreamState {
	return &converseStreamState{
		messageID:              fmt.Sprintf("bedrock-%d", time.Now().UnixNano()),
		model:                  model,
		created:                time.Now().Unix(),
		blockIndexToToolCallIx: make(map[int]int),
	}
}

func translateConverseResponse(resp *converseResponse, model string) *models.ChatCompletionResponse {
	created := time.Now().Unix()
	msg := models.Message{Role: "assistant"}

	var textParts []string
	var toolCalls []models.ToolCall

	for _, part := range resp.Output.Message.Content {
		if part.Text != nil {
			textParts = append(textParts, *part.Text)
		}
		if part.ToolUse != nil {
			args, _ := json.Marshal(part.ToolUse.Input)
			toolCalls = append(toolCalls, models.ToolCall{
				ID:   part.ToolUse.ToolUseID,
				Type: "function",
				Function: models.FunctionCall{
					Name:      part.ToolUse.Name,
					Arguments: string(args),
				},
			})
		}
	}

	if len(toolCalls) > 0 {
		msg.ToolCalls = toolCalls
	}
	if len(textParts) > 0 {
		msg.Content = json.RawMessage(mustMarshal(strings.Join(textParts, "")))
	}

	return &models.ChatCompletionResponse{
		ID:      fmt.Sprintf("bedrock-%d", time.Now().UnixNano()),
		Object:  "chat.completion",
		Created: created,
		Model:   model,
		Choices: []models.Choice{{
			Index:        0,
			Message:      msg,
			FinishReason: mapStopReason(resp.StopReason),
		}},
		Usage: &models.Usage{
			PromptTokens:     resp.Usage.InputTokens,
			CompletionTokens: resp.Usage.OutputTokens,
			TotalTokens:      resp.Usage.InputTokens + resp.Usage.OutputTokens,
		},
	}
}

func (p *Provider) chatCompletionConverse(ctx context.Context, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	cr, modelID := buildConverseRequest(req)

	body, err := json.Marshal(cr)
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("bedrock converse: marshaling request: %v", err))
	}

	reqURL := fmt.Sprintf("%s/model/%s/converse", p.baseURL, url.PathEscape(modelID))

	httpReq, err := http.NewRequestWithContext(ctx, "POST", reqURL, bytes.NewReader(body))
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("bedrock converse: creating request: %v", err))
	}
	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("Accept", "application/json")

	if err := signRequest(httpReq, p.credentials, p.region, bedrockService); err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("bedrock converse: signing: %v", err))
	}

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return nil, models.ErrGatewayTimeout("bedrock converse: request timed out")
		}
		return nil, models.ErrUpstreamProvider(0, fmt.Sprintf("bedrock converse: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("bedrock converse: reading response: %v", err))
	}

	if resp.StatusCode != http.StatusOK {
		return nil, parseBedrockError(resp.StatusCode, respBody)
	}

	var cr2 converseResponse
	if err := json.Unmarshal(respBody, &cr2); err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("bedrock converse: parsing response: %v", err))
	}

	return translateConverseResponse(&cr2, modelID), nil
}

func (p *Provider) streamChatCompletionConverse(ctx context.Context, req *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	chunks := make(chan models.StreamChunk, 64)
	errs := make(chan error, 1)

	go func() {
		defer close(chunks)
		defer close(errs)

		cr, modelID := buildConverseRequest(req)

		body, err := json.Marshal(cr)
		if err != nil {
			errs <- models.ErrInternal(fmt.Sprintf("bedrock converse stream: %v", err))
			return
		}

		reqURL := fmt.Sprintf("%s/model/%s/converse-stream", p.baseURL, url.PathEscape(modelID))

		httpReq, err := http.NewRequestWithContext(ctx, "POST", reqURL, bytes.NewReader(body))
		if err != nil {
			errs <- models.ErrInternal(fmt.Sprintf("bedrock converse stream: %v", err))
			return
		}
		httpReq.Header.Set("Content-Type", "application/json")
		httpReq.Header.Set("Accept", "application/vnd.amazon.eventstream")

		if err := signRequest(httpReq, p.credentials, p.region, bedrockService); err != nil {
			errs <- models.ErrInternal(fmt.Sprintf("bedrock converse stream: signing: %v", err))
			return
		}

		resp, err := p.httpClient.Do(httpReq)
		if err != nil {
			if ctx.Err() != nil {
				errs <- models.ErrGatewayTimeout("bedrock converse stream: timed out")
				return
			}
			errs <- models.ErrUpstreamProvider(0, fmt.Sprintf("bedrock converse stream: %v", err))
			return
		}
		defer resp.Body.Close()

		if resp.StatusCode != http.StatusOK {
			respBody, _ := io.ReadAll(io.LimitReader(resp.Body, 1024*1024))
			errs <- parseBedrockError(resp.StatusCode, respBody)
			return
		}

		state := newConverseStreamState(modelID)
		for {
			select {
			case <-ctx.Done():
				return
			default:
			}

			msg, err := readEventStreamMessage(resp.Body)
			if err != nil {
				if ctx.Err() == nil {
					errs <- models.ErrUpstreamProvider(0, fmt.Sprintf("bedrock converse stream: %v", err))
				}
				return
			}
			if msg == nil {
				return
			}

			chunk, done, err := state.parseConverseEvent(msg)
			if err != nil {
				if msg.Headers[":message-type"] == "exception" {
					errs <- models.ErrUpstreamProvider(0, err.Error())
					return
				}
				continue
			}

			if chunk != nil {
				select {
				case chunks <- *chunk:
				case <-ctx.Done():
					return
				}
			}
			if done {
				return
			}
		}
	}()

	return chunks, errs
}

type converseStreamState struct {
	messageID              string
	model                  string
	inputTokens            int
	created                int64
	toolCallIndex          int
	blockIndexToToolCallIx map[int]int
}

func (s *converseStreamState) parseConverseEvent(msg *eventStreamMessage) (*models.StreamChunk, bool, error) {
	if msg.Headers[":message-type"] == "exception" {
		return nil, false, fmt.Errorf("bedrock converse exception: %s", string(msg.Payload))
	}
	if msg.Headers[":message-type"] != "event" {
		return nil, false, nil
	}

	switch msg.Headers[":event-type"] {
	case "messageStart":
		if s.blockIndexToToolCallIx == nil {
			s.blockIndexToToolCallIx = make(map[int]int)
		}
		return &models.StreamChunk{
			ID:      s.messageID,
			Object:  "chat.completion.chunk",
			Model:   s.model,
			Created: s.created,
			Choices: []models.StreamChoice{{Index: 0, Delta: models.Delta{Role: "assistant"}}},
		}, false, nil

	case "contentBlockStart":
		if s.blockIndexToToolCallIx == nil {
			s.blockIndexToToolCallIx = make(map[int]int)
		}
		var e struct {
			Start struct {
				ToolUse struct {
					ToolUseID string `json:"toolUseId"`
					Name      string `json:"name"`
				} `json:"toolUse"`
			} `json:"start"`
			ContentBlockIndex int `json:"contentBlockIndex"`
		}
		json.Unmarshal(msg.Payload, &e)
		if e.Start.ToolUse.ToolUseID == "" {
			return nil, false, nil
		}
		idx := s.toolCallIndex
		s.toolCallIndex++
		s.blockIndexToToolCallIx[e.ContentBlockIndex] = idx
		return &models.StreamChunk{
			ID:      s.messageID,
			Object:  "chat.completion.chunk",
			Model:   s.model,
			Created: s.created,
			Choices: []models.StreamChoice{{Index: 0, Delta: models.Delta{ToolCalls: []models.ToolCallDelta{{
				Index:    idx,
				ID:       e.Start.ToolUse.ToolUseID,
				Type:     "function",
				Function: &models.FunctionCall{Name: e.Start.ToolUse.Name, Arguments: ""},
			}}}}},
		}, false, nil

	case "contentBlockDelta":
		var e struct {
			Delta struct {
				Text    string `json:"text"`
				ToolUse struct {
					Input string `json:"input"`
				} `json:"toolUse"`
			} `json:"delta"`
			ContentBlockIndex int `json:"contentBlockIndex"`
		}
		json.Unmarshal(msg.Payload, &e)
		if e.Delta.Text != "" {
			t := e.Delta.Text
			return &models.StreamChunk{
				ID:      s.messageID,
				Object:  "chat.completion.chunk",
				Model:   s.model,
				Created: s.created,
				Choices: []models.StreamChoice{{Index: 0, Delta: models.Delta{Content: &t}}},
			}, false, nil
		}
		if e.Delta.ToolUse.Input != "" {
			idx, ok := s.blockIndexToToolCallIx[e.ContentBlockIndex]
			if !ok {
				idx = 0
			}
			return &models.StreamChunk{
				ID:      s.messageID,
				Object:  "chat.completion.chunk",
				Model:   s.model,
				Created: s.created,
				Choices: []models.StreamChoice{{Index: 0, Delta: models.Delta{ToolCalls: []models.ToolCallDelta{{
					Index:    idx,
					Function: &models.FunctionCall{Arguments: e.Delta.ToolUse.Input},
				}}}}},
			}, false, nil
		}
		return nil, false, nil

	case "messageStop":
		var e struct {
			StopReason string `json:"stopReason"`
		}
		json.Unmarshal(msg.Payload, &e)
		reason := mapStopReason(e.StopReason)
		return &models.StreamChunk{
			ID:      s.messageID,
			Object:  "chat.completion.chunk",
			Model:   s.model,
			Created: s.created,
			Choices: []models.StreamChoice{{Index: 0, FinishReason: &reason}},
		}, false, nil

	case "metadata":
		var e struct {
			Usage struct {
				InputTokens  int `json:"inputTokens"`
				OutputTokens int `json:"outputTokens"`
			} `json:"usage"`
		}
		json.Unmarshal(msg.Payload, &e)
		return &models.StreamChunk{
			ID:      s.messageID,
			Object:  "chat.completion.chunk",
			Model:   s.model,
			Created: s.created,
			Choices: []models.StreamChoice{{Index: 0, Delta: models.Delta{}}},
			Usage: &models.Usage{
				PromptTokens:     e.Usage.InputTokens,
				CompletionTokens: e.Usage.OutputTokens,
				TotalTokens:      e.Usage.InputTokens + e.Usage.OutputTokens,
			},
		}, true, nil
	}

	return nil, false, nil
}

// ensureObjectAdditionalPropertiesFalse walks a JSON schema tree and adds
// `additionalProperties: false` to every `object` node that lacks it. Bedrock's
// Converse API rejects object schemas missing this field. Callers ship
// OpenAI-shape json_schema (where the field is optional) and we normalize here.
func ensureObjectAdditionalPropertiesFalse(schema json.RawMessage) json.RawMessage {
	if len(schema) == 0 {
		return schema
	}
	var node interface{}
	if err := json.Unmarshal(schema, &node); err != nil {
		return schema
	}
	walked := walkSchemaAddObjectAP(node)
	out, err := json.Marshal(walked)
	if err != nil {
		return schema
	}
	return out
}

func walkSchemaAddObjectAP(n interface{}) interface{} {
	switch v := n.(type) {
	case map[string]interface{}:
		if t, ok := v["type"].(string); ok && t == "object" {
			if _, has := v["additionalProperties"]; !has {
				v["additionalProperties"] = false
			}
		}
		for k, vv := range v {
			v[k] = walkSchemaAddObjectAP(vv)
		}
		return v
	case []interface{}:
		for i, x := range v {
			v[i] = walkSchemaAddObjectAP(x)
		}
		return v
	default:
		return n
	}
}
