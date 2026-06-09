package cohere

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// --- Cohere v2 native types ---

type cohereRequest struct {
	Model          string            `json:"model"`
	Messages       []cohereMessage   `json:"messages"`
	Temperature    *float64          `json:"temperature,omitempty"`
	MaxTokens      *int              `json:"max_tokens,omitempty"`
	P              *float64          `json:"p,omitempty"`
	StopSequences  []string          `json:"stop_sequences,omitempty"`
	Stream         bool              `json:"stream,omitempty"`
	Tools          []cohereTool      `json:"tools,omitempty"`
	ResponseFormat *cohereRespFormat `json:"response_format,omitempty"`
}

type cohereMessage struct {
	Role       string           `json:"role"`
	Content    string           `json:"content,omitempty"`
	ToolCallID string           `json:"tool_call_id,omitempty"`
	ToolCalls  []cohereToolCall `json:"tool_calls,omitempty"`
}

type cohereTool struct {
	Type     string             `json:"type"`
	Function cohereToolFunction `json:"function"`
}

type cohereToolFunction struct {
	Name        string          `json:"name"`
	Description string          `json:"description,omitempty"`
	Parameters  json.RawMessage `json:"parameters,omitempty"`
}

type cohereRespFormat struct {
	Type string `json:"type"`
}

type cohereResponse struct {
	ID           string            `json:"id"`
	Message      cohereRespMessage `json:"message"`
	FinishReason string            `json:"finish_reason"`
	Usage        *cohereUsage      `json:"usage,omitempty"`
}

type cohereRespMessage struct {
	Role      string              `json:"role"`
	Content   []cohereContentPart `json:"content,omitempty"`
	ToolCalls []cohereToolCall    `json:"tool_calls,omitempty"`
}

type cohereContentPart struct {
	Type string `json:"type"`
	Text string `json:"text"`
}

type cohereToolCall struct {
	ID       string             `json:"id"`
	Type     string             `json:"type"`
	Function cohereToolCallFunc `json:"function"`
}

type cohereToolCallFunc struct {
	Name      string `json:"name"`
	Arguments string `json:"arguments"`
}

type cohereUsage struct {
	BilledUnits *cohereBilledUnits `json:"billed_units,omitempty"`
	Tokens      *cohereTokens      `json:"tokens,omitempty"`
}

type cohereBilledUnits struct {
	InputTokens  int `json:"input_tokens"`
	OutputTokens int `json:"output_tokens"`
}

type cohereTokens struct {
	InputTokens  int `json:"input_tokens"`
	OutputTokens int `json:"output_tokens"`
}

// --- Translation functions ---

func translateRequest(req *models.ChatCompletionRequest) *cohereRequest {
	cr := &cohereRequest{
		Model:       resolveModelName(req.Model),
		Temperature: req.Temperature,
		P:           req.TopP,
		Stream:      req.Stream,
	}

	if req.MaxTokens != nil {
		cr.MaxTokens = req.MaxTokens
	} else if req.MaxCompletionTokens != nil {
		cr.MaxTokens = req.MaxCompletionTokens
	}

	// Parse stop sequences.
	if len(req.Stop) > 0 {
		var stops []string
		if err := json.Unmarshal(req.Stop, &stops); err != nil {
			var single string
			if err := json.Unmarshal(req.Stop, &single); err == nil {
				stops = []string{single}
			}
		}
		cr.StopSequences = stops
	}

	// Convert messages.
	for _, msg := range req.Messages {
		cm := cohereMessage{
			Role:    mapRoleToCohere(msg.Role),
			Content: extractTextContent(msg.Content),
		}
		// Cohere v2 tool role messages require tool_call_id.
		if msg.Role == "tool" && msg.ToolCallID != "" {
			cm.ToolCallID = msg.ToolCallID
		}
		// Handle assistant messages with tool calls: serialize tool_calls into
		// the Cohere message so the conversation context is preserved.
		if msg.Role == "assistant" && len(msg.ToolCalls) > 0 {
			cr.Messages = append(cr.Messages, translateAssistantToolCallMessage(msg))
			continue
		}
		cr.Messages = append(cr.Messages, cm)
	}

	// Translate tools.
	if len(req.Tools) > 0 {
		for _, t := range req.Tools {
			if t.Type != "function" {
				continue
			}
			cr.Tools = append(cr.Tools, cohereTool{
				Type: "function",
				Function: cohereToolFunction{
					Name:        t.Function.Name,
					Description: t.Function.Description,
					Parameters:  t.Function.Parameters,
				},
			})
		}
	}

	// Response format.
	// Cohere v2 only supports "text" and "json_object". If the client sends
	// "json_schema", translate to "json_object" as best effort.
	if req.ResponseFormat != nil {
		rfType := req.ResponseFormat.Type
		if rfType == "json_schema" {
			slog.Warn("cohere: json_schema not supported, falling back to json_object")
			rfType = "json_object"
		}
		cr.ResponseFormat = &cohereRespFormat{Type: rfType}
	}

	return cr
}

func translateResponse(resp *cohereResponse, model string) *models.ChatCompletionResponse {
	msg := models.Message{
		Role: "assistant",
	}

	// Extract text content.
	var textParts []string
	for _, part := range resp.Message.Content {
		if part.Type == "text" {
			textParts = append(textParts, part.Text)
		}
	}
	if len(textParts) > 0 {
		combined := textParts[0]
		for i := 1; i < len(textParts); i++ {
			combined += textParts[i]
		}
		msg.Content = json.RawMessage(mustMarshal(combined))
	}

	// Map tool calls.
	for _, tc := range resp.Message.ToolCalls {
		msg.ToolCalls = append(msg.ToolCalls, models.ToolCall{
			ID:   tc.ID,
			Type: "function",
			Function: models.FunctionCall{
				Name:      tc.Function.Name,
				Arguments: tc.Function.Arguments,
			},
		})
	}

	result := &models.ChatCompletionResponse{
		ID:      resp.ID,
		Object:  "chat.completion",
		Created: time.Now().Unix(),
		Model:   model,
		Choices: []models.Choice{
			{
				Index:        0,
				Message:      msg,
				FinishReason: mapCohereFinishReason(resp.FinishReason),
			},
		},
	}

	if resp.Usage != nil {
		usage := &models.Usage{}
		if resp.Usage.Tokens != nil {
			usage.PromptTokens = resp.Usage.Tokens.InputTokens
			usage.CompletionTokens = resp.Usage.Tokens.OutputTokens
			usage.TotalTokens = usage.PromptTokens + usage.CompletionTokens
		} else if resp.Usage.BilledUnits != nil {
			usage.PromptTokens = resp.Usage.BilledUnits.InputTokens
			usage.CompletionTokens = resp.Usage.BilledUnits.OutputTokens
			usage.TotalTokens = usage.PromptTokens + usage.CompletionTokens
		}
		result.Usage = usage
	}

	return result
}

func translateAssistantToolCallMessage(msg models.Message) cohereMessage {
	cm := cohereMessage{
		Role:    "assistant",
		Content: extractTextContent(msg.Content),
	}
	for _, tc := range msg.ToolCalls {
		cm.ToolCalls = append(cm.ToolCalls, cohereToolCall{
			ID:   tc.ID,
			Type: "function",
			Function: cohereToolCallFunc{
				Name:      tc.Function.Name,
				Arguments: tc.Function.Arguments,
			},
		})
	}
	return cm
}

func mapRoleToCohere(role string) string {
	switch role {
	case "system":
		return "system"
	case "user":
		return "user"
	case "assistant":
		return "assistant"
	case "tool":
		return "tool"
	default:
		return role
	}
}

func mapCohereFinishReason(reason string) string {
	switch reason {
	case "COMPLETE":
		return "stop"
	case "MAX_TOKENS":
		return "length"
	case "STOP_SEQUENCE":
		return "stop"
	case "TOOL_CALL":
		return "tool_calls"
	case "ERROR":
		return "stop"
	default:
		return "stop"
	}
}

func parseCohereError(status int, body []byte) *models.APIError {
	var errResp struct {
		Message string `json:"message"`
	}
	if err := json.Unmarshal(body, &errResp); err == nil && errResp.Message != "" {
		return &models.APIError{
			Status:  mapCohereStatus(status),
			Type:    mapCohereErrorType(status),
			Code:    fmt.Sprintf("provider_%d", status),
			Message: errResp.Message,
		}
	}

	msg := string(body)
	if len(msg) > 500 {
		msg = msg[:500] + "..."
	}
	return models.ErrUpstreamProvider(status, fmt.Sprintf("cohere error (HTTP %d): %s", status, msg))
}

func mapCohereStatus(status int) int {
	switch {
	case status == 429:
		return http.StatusTooManyRequests
	case status >= 500:
		return http.StatusBadGateway
	case status >= 400:
		return status
	default:
		return http.StatusBadGateway
	}
}

func mapCohereErrorType(status int) string {
	switch {
	case status == 401:
		return models.ErrTypeAuthentication
	case status == 403:
		return models.ErrTypePermission
	case status == 404:
		return models.ErrTypeNotFound
	case status == 429:
		return models.ErrTypeRateLimit
	case status >= 400 && status < 500:
		return models.ErrTypeInvalidRequest
	default:
		return models.ErrTypeUpstream
	}
}

// --- Cohere v2 Embed types ---

type cohereEmbedRequest struct {
	Model          string   `json:"model"`
	Texts          []string `json:"texts"`
	InputType      string   `json:"input_type"`
	EmbeddingTypes []string `json:"embedding_types"`
}

type cohereEmbedResponse struct {
	ID         string           `json:"id"`
	Embeddings cohereEmbeddings `json:"embeddings"`
	Texts      []string         `json:"texts"`
	Meta       *cohereEmbedMeta `json:"meta,omitempty"`
}

type cohereEmbeddings struct {
	Float [][]float64 `json:"float"`
}

type cohereEmbedMeta struct {
	BilledUnits *cohereBilledUnits `json:"billed_units,omitempty"`
}

func translateEmbeddingRequest(req *models.EmbeddingRequest, headers map[string]string) *cohereEmbedRequest {
	model := resolveModelName(req.Model)

	// Parse input: can be a string or []string.
	var texts []string
	if len(req.Input) > 0 {
		var single string
		if err := json.Unmarshal(req.Input, &single); err == nil {
			texts = []string{single}
		} else {
			json.Unmarshal(req.Input, &texts)
		}
	}

	// Determine input_type from provider headers, or default to "search_document".
	// Cohere supports: "search_document", "search_query", "classification", "clustering".
	// Callers should set the "x-cohere-input-type" header to control this.
	inputType := "search_document"
	if v, ok := headers["x-cohere-input-type"]; ok && v != "" {
		inputType = v
	}

	return &cohereEmbedRequest{
		Model:          model,
		Texts:          texts,
		InputType:      inputType,
		EmbeddingTypes: []string{"float"},
	}
}

func translateEmbeddingResponse(resp *cohereEmbedResponse, model string) *models.EmbeddingResponse {
	result := &models.EmbeddingResponse{
		Object: "list",
		Model:  model,
	}

	for i, emb := range resp.Embeddings.Float {
		embJSON, _ := json.Marshal(emb)
		result.Data = append(result.Data, models.EmbeddingData{
			Object:    "embedding",
			Index:     i,
			Embedding: embJSON,
		})
	}

	if resp.Meta != nil && resp.Meta.BilledUnits != nil {
		result.Usage = &models.EmbeddingUsage{
			PromptTokens: resp.Meta.BilledUnits.InputTokens,
			TotalTokens:  resp.Meta.BilledUnits.InputTokens,
		}
	}

	return result
}

// --- Cohere v2 Rerank types ---

type cohereRerankRequest struct {
	Model           string   `json:"model"`
	Query           string   `json:"query"`
	Documents       []string `json:"documents"`
	TopN            *int     `json:"top_n,omitempty"`
	ReturnDocuments bool     `json:"return_documents,omitempty"`
}

type cohereRerankResponse struct {
	ID      string               `json:"id"`
	Results []cohereRerankResult `json:"results"`
	Meta    *cohereEmbedMeta     `json:"meta,omitempty"`
}

type cohereRerankResult struct {
	Index          int                   `json:"index"`
	RelevanceScore float64               `json:"relevance_score"`
	Document       *cohereRerankDocument `json:"document,omitempty"`
}

type cohereRerankDocument struct {
	Text string `json:"text"`
}

func translateRerankRequest(req *models.RerankRequest) *cohereRerankRequest {
	return &cohereRerankRequest{
		Model:           resolveModelName(req.Model),
		Query:           req.Query,
		Documents:       req.Documents,
		TopN:            req.TopN,
		ReturnDocuments: req.ReturnDocuments,
	}
}

func translateRerankResponse(resp *cohereRerankResponse) *models.RerankResponse {
	result := &models.RerankResponse{
		ID: resp.ID,
	}

	for _, r := range resp.Results {
		rr := models.RerankResult{
			Index:          r.Index,
			RelevanceScore: r.RelevanceScore,
		}
		if r.Document != nil {
			rr.Document = &models.RerankDocument{Text: r.Document.Text}
		}
		result.Results = append(result.Results, rr)
	}

	if resp.Meta != nil && resp.Meta.BilledUnits != nil {
		result.Meta = &models.RerankMeta{
			BilledUnits: map[string]int{
				"search_units": resp.Meta.BilledUnits.InputTokens,
			},
		}
	}

	return result
}

// --- Helpers ---

func extractTextContent(content json.RawMessage) string {
	if len(content) == 0 {
		return ""
	}
	var s string
	if err := json.Unmarshal(content, &s); err == nil {
		return s
	}
	var parts []struct {
		Type string `json:"type"`
		Text string `json:"text"`
	}
	if err := json.Unmarshal(content, &parts); err == nil {
		var texts []string
		for _, p := range parts {
			if p.Type == "text" {
				texts = append(texts, p.Text)
			}
		}
		return strings.Join(texts, "")
	}
	return ""
}

func mustMarshal(v interface{}) string {
	b, _ := json.Marshal(v)
	return string(b)
}

func resolveModelName(model string) string {
	for i := 0; i < len(model); i++ {
		if model[i] == '/' {
			return model[i+1:]
		}
	}
	return model
}
