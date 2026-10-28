package openai

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/common"
)

// Chat requests carried over OpenAI's Responses API.
//
// Some OpenAI models take function tools on chat completions only with
// reasoning off: gpt-5.6 answers 400 "Function tools with reasoning_effort are
// not supported ... use /v1/responses or set reasoning_effort to 'none'".
// Gateway routes that translate their own dialect into a chat request, such as
// /v1/messages, send such a request to /v1/responses instead and translate the
// answer back, so the model keeps its tools and its reasoning.

// responsesToolModels are the model families that take function tools on chat
// completions only with reasoning off.
var responsesToolModels = []string{"gpt-5.6"}

// ChatNeedsResponses reports whether req must go to the Responses API: it
// carries tools, for a model that takes them on chat completions only without
// reasoning. Only the official API serves both endpoints.
func (p *Provider) ChatNeedsResponses(req *models.ChatCompletionRequest) bool {
	if !p.openAIAPI || len(req.Tools) == 0 {
		return false
	}
	model := resolveModelName(req.Model)
	for _, family := range responsesToolModels {
		if model == family || strings.HasPrefix(model, family+"-") {
			return true
		}
	}
	return false
}

// ChatCompletionViaResponses serves a chat request on the Responses API.
func (p *Provider) ChatCompletionViaResponses(ctx context.Context, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	body, err := json.Marshal(toResponsesRequest(req, false))
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("marshaling responses request: %v", err))
	}
	respBody, _, err := p.CreateResponse(ctx, body)
	if err != nil {
		return nil, err
	}
	var resp responsesResponse
	if err := json.Unmarshal(respBody, &resp); err != nil {
		return nil, models.ErrUpstreamProvider(http.StatusBadGateway, fmt.Sprintf("parsing responses response: %v", err))
	}
	return resp.toChat(), nil
}

// StreamChatCompletionViaResponses streams a chat request on the Responses
// API, as chat completion chunks. The channels close as StreamChatCompletion's
// do: errs first, then chunks.
func (p *Provider) StreamChatCompletionViaResponses(ctx context.Context, req *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	chunks := make(chan models.StreamChunk, 64)
	errs := make(chan error, 1)

	go func() {
		defer close(chunks)
		defer close(errs)

		body, err := json.Marshal(toResponsesRequest(req, true))
		if err != nil {
			errs <- models.ErrInternal(fmt.Sprintf("marshaling responses request: %v", err))
			return
		}
		stream, _, err := p.StreamResponse(ctx, body)
		if err != nil {
			errs <- err
			return
		}
		defer stream.Close()

		conv := responsesStreamConverter{model: req.Model, created: time.Now().Unix()}
		send := func(chunk models.StreamChunk) bool {
			select {
			case chunks <- chunk:
				return true
			case <-ctx.Done():
				return false
			}
		}

		// response.completed carries the whole response, so an event can be as
		// large as the output.
		scanner := bufio.NewScanner(stream)
		scanner.Buffer(make([]byte, 0, 64*1024), 32<<20)
		for scanner.Scan() {
			data, ok := strings.CutPrefix(scanner.Text(), "data: ")
			if !ok {
				continue
			}
			var ev responsesStreamEvent
			if err := json.Unmarshal([]byte(data), &ev); err != nil {
				slog.Warn("failed to parse responses stream event", "error", err, "provider", p.id)
				continue
			}
			out, apiErr := conv.convert(&ev)
			if apiErr != nil {
				errs <- apiErr
				return
			}
			for _, chunk := range out {
				if !send(chunk) {
					return
				}
			}
			if conv.done {
				return
			}
		}
		if ctx.Err() != nil {
			return
		}
		if err := scanner.Err(); err != nil {
			errs <- common.ReadError(err, 0, "responses stream read")
			return
		}
		// Without its terminal event the answer may be cut short.
		errs <- models.ErrUpstreamProvider(http.StatusBadGateway, "responses stream ended before the response completed")
	}()

	return chunks, errs
}

// ── request ────────────────────────────────────────────────────────────────

type responsesRequest struct {
	Model             string              `json:"model"`
	Input             []any               `json:"input"`
	Tools             []json.RawMessage   `json:"tools,omitempty"`
	ToolChoice        json.RawMessage     `json:"tool_choice,omitempty"`
	ParallelToolCalls *bool               `json:"parallel_tool_calls,omitempty"`
	MaxOutputTokens   *int                `json:"max_output_tokens,omitempty"`
	Temperature       *float64            `json:"temperature,omitempty"`
	TopP              *float64            `json:"top_p,omitempty"`
	Reasoning         *responsesReasoning `json:"reasoning,omitempty"`
	User              string              `json:"user,omitempty"`
	ServiceTier       string              `json:"service_tier,omitempty"`
	// Store is sent as false: chat completions store nothing by default, and
	// the Responses API stores unless told not to.
	Store  bool `json:"store"`
	Stream bool `json:"stream,omitempty"`
}

type responsesReasoning struct {
	Effort string `json:"effort"`
}

type responsesMessage struct {
	Type    string             `json:"type"`
	Role    string             `json:"role"`
	Content []responsesContent `json:"content"`
}

type responsesContent struct {
	Type     string `json:"type"`
	Text     string `json:"text,omitempty"`
	ImageURL string `json:"image_url,omitempty"`
	Detail   string `json:"detail,omitempty"`
}

type responsesFunctionCall struct {
	Type      string `json:"type"`
	CallID    string `json:"call_id"`
	Name      string `json:"name"`
	Arguments string `json:"arguments"`
}

type responsesFunctionCallOutput struct {
	Type   string `json:"type"`
	CallID string `json:"call_id"`
	Output string `json:"output"`
}

type responsesFunctionTool struct {
	Type        string          `json:"type"`
	Name        string          `json:"name"`
	Description string          `json:"description,omitempty"`
	Parameters  json.RawMessage `json:"parameters"`
	// Strict is always sent: the Responses API makes function tools strict by
	// default, and chat completions do not, so most tool schemas fail it.
	Strict bool `json:"strict"`
}

// toResponsesRequest translates a chat request into a Responses API request.
// stop has no Responses equivalent and is not sent.
func toResponsesRequest(req *models.ChatCompletionRequest, stream bool) *responsesRequest {
	out := &responsesRequest{
		Model:       resolveModelName(req.Model),
		Temperature: req.Temperature,
		TopP:        req.TopP,
		User:        req.User,
		ServiceTier: req.ServiceTier,
		Stream:      stream,
	}
	out.MaxOutputTokens = req.MaxCompletionTokens
	if out.MaxOutputTokens == nil {
		out.MaxOutputTokens = req.MaxTokens
	}
	if raw, ok := req.Extra["parallel_tool_calls"]; ok {
		var parallel bool
		if json.Unmarshal(raw, &parallel) == nil {
			out.ParallelToolCalls = &parallel
		}
	}
	if raw, ok := req.Extra["reasoning_effort"]; ok {
		var effort string
		if json.Unmarshal(raw, &effort) == nil && effort != "" {
			out.Reasoning = &responsesReasoning{Effort: effort}
		}
	}

	for _, m := range req.Messages {
		out.Input = append(out.Input, responsesInput(m)...)
	}
	for _, t := range req.Tools {
		out.Tools = append(out.Tools, responsesTool(t))
	}
	out.ToolChoice = responsesToolChoice(req.ToolChoice)
	return out
}

// responsesInput translates one chat message into Responses input items.
func responsesInput(m models.Message) []any {
	switch m.Role {
	case "tool":
		return []any{responsesFunctionCallOutput{
			Type:   "function_call_output",
			CallID: m.ToolCallID,
			Output: contentText(m.Content),
		}}
	case "assistant":
		var items []any
		if text := contentText(m.Content); text != "" {
			items = append(items, responsesMessage{
				Type:    "message",
				Role:    "assistant",
				Content: []responsesContent{{Type: "output_text", Text: text}},
			})
		}
		for _, tc := range m.ToolCalls {
			items = append(items, responsesFunctionCall{
				Type:      "function_call",
				CallID:    tc.ID,
				Name:      tc.Function.Name,
				Arguments: tc.Function.Arguments,
			})
		}
		return items
	default:
		return []any{responsesMessage{
			Type:    "message",
			Role:    m.Role,
			Content: inputContent(m.Content),
		}}
	}
}

// chatContentPart is one part of a chat message's array content.
type chatContentPart struct {
	Type     string `json:"type"`
	Text     string `json:"text"`
	ImageURL *struct {
		URL    string `json:"url"`
		Detail string `json:"detail"`
	} `json:"image_url"`
}

// inputContent translates chat content, a string or an array of parts, into
// Responses input content.
func inputContent(raw json.RawMessage) []responsesContent {
	var text string
	if json.Unmarshal(raw, &text) == nil {
		return []responsesContent{{Type: "input_text", Text: text}}
	}
	var parts []chatContentPart
	if json.Unmarshal(raw, &parts) != nil {
		return []responsesContent{{Type: "input_text", Text: string(raw)}}
	}
	out := make([]responsesContent, 0, len(parts))
	for _, part := range parts {
		switch {
		case part.Type == "image_url" && part.ImageURL != nil:
			detail := part.ImageURL.Detail
			if detail == "" {
				detail = "auto"
			}
			out = append(out, responsesContent{Type: "input_image", ImageURL: part.ImageURL.URL, Detail: detail})
		case part.Text != "":
			out = append(out, responsesContent{Type: "input_text", Text: part.Text})
		}
	}
	return out
}

// contentText joins the text of chat content, a string or an array of parts.
func contentText(raw json.RawMessage) string {
	if len(raw) == 0 || string(raw) == "null" {
		return ""
	}
	var text string
	if json.Unmarshal(raw, &text) == nil {
		return text
	}
	var parts []chatContentPart
	if json.Unmarshal(raw, &parts) != nil {
		return string(raw)
	}
	var sb strings.Builder
	for _, part := range parts {
		sb.WriteString(part.Text)
	}
	return sb.String()
}

// responsesTool translates a chat tool. A tool that is not a plain function
// keeps the caller's bytes, as on chat completions, so the upstream answers it.
func responsesTool(t models.Tool) json.RawMessage {
	if len(t.Raw) > 0 {
		return t.Raw
	}
	params := t.Function.Parameters
	if len(params) == 0 || string(params) == "null" {
		params = json.RawMessage(`{"type":"object","properties":{}}`)
	}
	tool := responsesFunctionTool{
		Type:        "function",
		Name:        t.Function.Name,
		Description: t.Function.Description,
		Parameters:  params,
		Strict:      t.Function.Strict != nil && *t.Function.Strict,
	}
	b, _ := json.Marshal(tool)
	return b
}

// responsesToolChoice translates a chat tool_choice. The modes are the same
// strings; a named function moves its name up a level.
func responsesToolChoice(raw json.RawMessage) json.RawMessage {
	if len(raw) == 0 {
		return nil
	}
	var named struct {
		Type     string `json:"type"`
		Function struct {
			Name string `json:"name"`
		} `json:"function"`
	}
	if json.Unmarshal(raw, &named) == nil && named.Type == "function" && named.Function.Name != "" {
		b, _ := json.Marshal(struct {
			Type string `json:"type"`
			Name string `json:"name"`
		}{"function", named.Function.Name})
		return b
	}
	return raw
}

// ── response ───────────────────────────────────────────────────────────────

type responsesResponse struct {
	ID                string                `json:"id"`
	Model             string                `json:"model"`
	CreatedAt         int64                 `json:"created_at"`
	Status            string                `json:"status"`
	IncompleteDetails *responsesIncomplete  `json:"incomplete_details"`
	Output            []responsesOutputItem `json:"output"`
	Usage             *responsesUsage       `json:"usage"`
	Error             *responsesError       `json:"error"`
}

type responsesIncomplete struct {
	Reason string `json:"reason"`
}

type responsesError struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

type responsesOutputItem struct {
	Type      string `json:"type"`
	ID        string `json:"id"`
	CallID    string `json:"call_id"`
	Name      string `json:"name"`
	Arguments string `json:"arguments"`
	Content   []struct {
		Type    string `json:"type"`
		Text    string `json:"text"`
		Refusal string `json:"refusal"`
	} `json:"content"`
}

type responsesUsage struct {
	InputTokens        int `json:"input_tokens"`
	OutputTokens       int `json:"output_tokens"`
	TotalTokens        int `json:"total_tokens"`
	InputTokensDetails *struct {
		CachedTokens int `json:"cached_tokens"`
	} `json:"input_tokens_details"`
	OutputTokensDetails *struct {
		ReasoningTokens int `json:"reasoning_tokens"`
	} `json:"output_tokens_details"`
}

// toChat translates a Responses API response into a chat completion.
func (r *responsesResponse) toChat() *models.ChatCompletionResponse {
	msg := models.Message{Role: "assistant"}
	var text strings.Builder
	for _, item := range r.Output {
		switch item.Type {
		case "message":
			for _, c := range item.Content {
				text.WriteString(c.Text)
				text.WriteString(c.Refusal)
			}
		case "function_call":
			msg.ToolCalls = append(msg.ToolCalls, models.ToolCall{
				ID:       item.CallID,
				Type:     "function",
				Function: models.FunctionCall{Name: item.Name, Arguments: item.Arguments},
			})
		}
	}
	if text.Len() > 0 || len(msg.ToolCalls) == 0 {
		msg.Content, _ = json.Marshal(text.String())
	}
	return &models.ChatCompletionResponse{
		ID:      r.ID,
		Object:  "chat.completion",
		Created: r.CreatedAt,
		Model:   r.Model,
		Choices: []models.Choice{{
			Message:      msg,
			FinishReason: r.finishReason(len(msg.ToolCalls) > 0),
		}},
		Usage: r.Usage.toChat(),
	}
}

// finishReason is the chat finish reason for a response that ended.
func (r *responsesResponse) finishReason(calledTools bool) string {
	if r.Status == "incomplete" && r.IncompleteDetails != nil {
		switch r.IncompleteDetails.Reason {
		case "max_output_tokens":
			return "length"
		case "content_filter":
			return "content_filter"
		}
	}
	if calledTools {
		return "tool_calls"
	}
	return "stop"
}

func (u *responsesUsage) toChat() *models.Usage {
	if u == nil {
		return nil
	}
	usage := &models.Usage{
		PromptTokens:     u.InputTokens,
		CompletionTokens: u.OutputTokens,
		TotalTokens:      u.TotalTokens,
	}
	if u.InputTokensDetails != nil {
		raw := json.RawMessage(fmt.Sprintf(`{"cached_tokens":%d}`, u.InputTokensDetails.CachedTokens))
		usage.PromptTokensDetails = &raw
	}
	if u.OutputTokensDetails != nil {
		raw := json.RawMessage(fmt.Sprintf(`{"reasoning_tokens":%d}`, u.OutputTokensDetails.ReasoningTokens))
		usage.CompletionTokensDetails = &raw
	}
	return usage
}

// ── stream ─────────────────────────────────────────────────────────────────

// responsesStreamEvent holds the fields of every Responses stream event this
// translation reads; each event type fills its own.
type responsesStreamEvent struct {
	Type     string               `json:"type"`
	Delta    string               `json:"delta"`
	ItemID   string               `json:"item_id"`
	Item     *responsesOutputItem `json:"item"`
	Response *responsesResponse   `json:"response"`
	Code     string               `json:"code"`
	Message  string               `json:"message"`
}

// responsesStreamConverter translates Responses stream events into chat
// completion chunks.
type responsesStreamConverter struct {
	model   string
	created int64
	id      string
	// toolIndex numbers function calls in the order they start, as chat
	// tool_calls deltas are numbered; keyed by output item ID.
	toolIndex map[string]int
	done      bool
}

func (c *responsesStreamConverter) chunk(delta models.Delta, finish *string) models.StreamChunk {
	return models.StreamChunk{
		ID:      c.id,
		Object:  "chat.completion.chunk",
		Created: c.created,
		Model:   c.model,
		Choices: []models.StreamChoice{{Delta: delta, FinishReason: finish}},
	}
}

// convert translates one event. It returns the error a failed response ends
// with, and sets done after the terminal event.
func (c *responsesStreamConverter) convert(ev *responsesStreamEvent) ([]models.StreamChunk, *models.APIError) {
	switch ev.Type {
	case "response.created":
		if ev.Response != nil {
			c.id = ev.Response.ID
			if ev.Response.Model != "" {
				c.model = ev.Response.Model
			}
		}
		return []models.StreamChunk{c.chunk(models.Delta{Role: "assistant"}, nil)}, nil

	case "response.output_text.delta", "response.refusal.delta":
		delta := ev.Delta
		return []models.StreamChunk{c.chunk(models.Delta{Content: &delta}, nil)}, nil

	case "response.output_item.added":
		if ev.Item == nil || ev.Item.Type != "function_call" {
			return nil, nil
		}
		if c.toolIndex == nil {
			c.toolIndex = map[string]int{}
		}
		index := len(c.toolIndex)
		c.toolIndex[ev.Item.ID] = index
		return []models.StreamChunk{c.chunk(models.Delta{ToolCalls: []models.ToolCallDelta{{
			Index:    index,
			ID:       ev.Item.CallID,
			Type:     "function",
			Function: &models.FunctionCall{Name: ev.Item.Name, Arguments: ev.Item.Arguments},
		}}}, nil)}, nil

	case "response.function_call_arguments.delta":
		index, ok := c.toolIndex[ev.ItemID]
		if !ok {
			return nil, nil
		}
		return []models.StreamChunk{c.chunk(models.Delta{ToolCalls: []models.ToolCallDelta{{
			Index:    index,
			Function: &models.FunctionCall{Arguments: ev.Delta},
		}}}, nil)}, nil

	case "response.completed", "response.incomplete":
		c.done = true
		resp := ev.Response
		if resp == nil {
			resp = &responsesResponse{}
		}
		finish := resp.finishReason(len(c.toolIndex) > 0)
		final := c.chunk(models.Delta{}, &finish)
		usage := models.StreamChunk{
			ID:      c.id,
			Object:  "chat.completion.chunk",
			Created: c.created,
			Model:   c.model,
			Choices: []models.StreamChoice{},
			Usage:   resp.Usage.toChat(),
		}
		return []models.StreamChunk{final, usage}, nil

	case "response.failed":
		c.done = true
		msg := "response failed"
		if ev.Response != nil && ev.Response.Error != nil {
			msg = ev.Response.Error.Message
		}
		return nil, models.ErrUpstreamProvider(http.StatusBadGateway, msg)

	case "error":
		c.done = true
		return nil, models.ErrUpstreamProvider(http.StatusBadGateway, ev.Message)
	}
	return nil, nil
}
