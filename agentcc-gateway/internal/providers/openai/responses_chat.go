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
// The provider's own ChatCompletion and StreamChatCompletion send such a request
// to /v1/responses instead and translate the answer back, so the model keeps
// its tools and its reasoning. Deciding there, rather than in each caller,
// covers every path that sends a chat request upstream: /v1/chat/completions
// (with failover, model fallbacks and async jobs), /v1/messages and the GenAI
// routes that translate into a chat request, scheduled completions, batches and
// the traffic mirror.

// responsesToolModels are the model families that take function tools on chat
// completions only with reasoning off.
var responsesToolModels = []string{"gpt-5.6"}

// ChatNeedsResponses reports whether req must go to the Responses API: the
// official API is the only upstream that serves both endpoints, and req is one
// that NeedsResponses refuses on chat completions.
func (p *Provider) ChatNeedsResponses(req *models.ChatCompletionRequest) bool {
	return p.openAIAPI && NeedsResponses(req)
}

// NeedsResponses reports whether chat completions refuse req, whatever the
// host: it carries function tools, for a model that takes them on chat
// completions only without reasoning, and the caller has not turned reasoning
// off. A caller who sends reasoning_effort "none" gets tools on chat completions
// as asked, and a request whose tools are all of another type (custom tools,
// say) is left on chat completions for the upstream to answer.
func NeedsResponses(req *models.ChatCompletionRequest) bool {
	if req == nil || !hasFunctionTool(req.Tools) || reasoningOff(req) {
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

// hasFunctionTool reports whether tools has a plain function tool, the kind
// the Responses route carries.
func hasFunctionTool(tools []models.Tool) bool {
	for _, t := range tools {
		if isFunctionTool(t) {
			return true
		}
	}
	return false
}

// isFunctionTool reports whether t is a plain function tool. Any other entry
// keeps the caller's bytes in Raw (see models.Tool).
func isFunctionTool(t models.Tool) bool {
	return len(t.Raw) == 0 && (t.Type == "" || t.Type == "function")
}

// reasoningOff reports whether the caller set reasoning_effort to "none".
func reasoningOff(req *models.ChatCompletionRequest) bool {
	raw, ok := req.Extra["reasoning_effort"]
	if !ok {
		return false
	}
	var effort string
	return json.Unmarshal(raw, &effort) == nil && effort == "none"
}

// ChatCompletionViaResponses serves a chat request on the Responses API.
func (p *Provider) ChatCompletionViaResponses(ctx context.Context, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	rr, apiErr := toResponsesRequest(req, false)
	if apiErr != nil {
		return nil, apiErr
	}
	body, err := json.Marshal(rr)
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
	// A failed response can come back as a 200; fail it as the stream's
	// response.failed does, rather than answer an empty completion.
	if resp.Status == "failed" {
		msg := "response failed"
		if resp.Error != nil && resp.Error.Message != "" {
			msg = resp.Error.Message
		}
		return nil, models.ErrUpstreamProvider(http.StatusBadGateway, msg)
	}
	return resp.toChat()
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

		rr, apiErr := toResponsesRequest(req, true)
		if apiErr != nil {
			errs <- apiErr
			return
		}
		body, err := json.Marshal(rr)
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
	Text              *responsesText      `json:"text,omitempty"`
	User              string              `json:"user,omitempty"`
	ServiceTier       string              `json:"service_tier,omitempty"`
	// Carried from chat fields of the same name.
	PromptCacheKey   json.RawMessage `json:"prompt_cache_key,omitempty"`
	SafetyIdentifier json.RawMessage `json:"safety_identifier,omitempty"`
	Metadata         json.RawMessage `json:"metadata,omitempty"`
	// Store is the caller's store, false when not set: chat completions store
	// nothing by default, and the Responses API stores unless told not to.
	Store  bool `json:"store"`
	Stream bool `json:"stream,omitempty"`
}

type responsesReasoning struct {
	Effort string `json:"effort"`
}

// responsesText carries the output format, chat's response_format, and chat's
// verbosity.
type responsesText struct {
	Format    json.RawMessage `json:"format,omitempty"`
	Verbosity json.RawMessage `json:"verbosity,omitempty"`
}

// responsesJSONSchemaFormat is a json_schema text format. The Responses API
// takes chat's json_schema fields one level up.
type responsesJSONSchemaFormat struct {
	Type        string          `json:"type"`
	Name        string          `json:"name"`
	Description string          `json:"description,omitempty"`
	Schema      json.RawMessage `json:"schema,omitempty"`
	// Strict is always sent, as on function tools: chat completions default it
	// to false, and the Responses API makes json_schema strict by default.
	Strict bool `json:"strict"`
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
	FileData string `json:"file_data,omitempty"`
	FileID   string `json:"file_id,omitempty"`
	Filename string `json:"filename,omitempty"`
	Refusal  string `json:"refusal,omitempty"`
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
// The Responses API has no stop, seed, presence/frequency penalties or
// logit_bias, and returns logprobs in another shape: a caller's own chat
// request that sets one is refused with a 400 naming it, and a translated
// request (/v1/messages, GenAI) is sent without it and the drop logged, as n
// is. Modalities, audio and other unknown fields are not sent. A tool, tool
// choice, tool call or content part the Responses API cannot be sent in chat
// shape is refused with a 400 rather than forwarded or dropped.
func toResponsesRequest(req *models.ChatCompletionRequest, stream bool) (_ *responsesRequest, apiErr *models.APIError) {
	// Word the remedy for the route the caller used: only a caller's own chat
	// request can set reasoning_effort.
	defer func() {
		if apiErr != nil && apiErr.Code == unsupportedCode {
			apiErr.Message += overResponsesRemedy(req)
		}
	}()
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
	if raw, ok := req.Extra["store"]; ok {
		var store bool
		if json.Unmarshal(raw, &store) == nil {
			out.Store = store
		}
	}
	// A response has one output; more choices cannot be asked for. A caller's
	// own chat request asked for them and is told so. A translated request
	// (GenAI candidateCount) gets one, as before this route existed.
	if req.N != nil && *req.N > 1 {
		if req.TranslatedFrom == "" {
			return nil, unsupportedOverResponses(fmt.Sprintf("n=%d (more than one choice)", *req.N))
		}
		slog.Info("responses route answers one choice; extra candidates dropped",
			"requested", *req.N, "translated_from", req.TranslatedFrom, "model", req.Model)
	}

	if params := chatOnlyParams(req); len(params) > 0 {
		if req.TranslatedFrom == "" {
			return nil, unsupportedOverResponses(strings.Join(params, ", "))
		}
		slog.Info("responses route drops chat-only parameters",
			"parameters", params, "translated_from", req.TranslatedFrom, "model", req.Model)
	}

	out.PromptCacheKey = req.Extra["prompt_cache_key"]
	out.SafetyIdentifier = req.Extra["safety_identifier"]
	out.Metadata = req.Extra["metadata"]

	for _, m := range req.Messages {
		items, err := responsesInput(m)
		if err != nil {
			return nil, err
		}
		out.Input = append(out.Input, items...)
	}
	for _, t := range req.Tools {
		if !isFunctionTool(t) {
			return nil, unsupportedOverResponses(fmt.Sprintf("a %q tool", toolType(t)))
		}
		out.Tools = append(out.Tools, responsesTool(t))
	}
	choice, err := responsesToolChoice(req.ToolChoice)
	if err != nil {
		return nil, err
	}
	out.ToolChoice = choice
	format := responsesTextFormat(req.ResponseFormat)
	verbosity := req.Extra["verbosity"]
	if format != nil || verbosity != nil {
		out.Text = &responsesText{Format: format, Verbosity: verbosity}
	}
	return out, nil
}

// chatOnlyParams names the chat parameters set on req that the Responses API
// cannot carry. A value that changes nothing (a zero penalty, logprobs false,
// an empty stop or logit_bias) is not counted.
func chatOnlyParams(req *models.ChatCompletionRequest) []string {
	var params []string
	if stopSet(req.Stop) {
		params = append(params, "stop")
	}
	if req.Seed != nil {
		params = append(params, "seed")
	}
	if req.Logprobs != nil && *req.Logprobs {
		params = append(params, "logprobs")
	}
	if req.TopLogprobs != nil && *req.TopLogprobs > 0 {
		params = append(params, "top_logprobs")
	}
	if req.FrequencyPenalty != nil && *req.FrequencyPenalty != 0 {
		params = append(params, "frequency_penalty")
	}
	if req.PresencePenalty != nil && *req.PresencePenalty != 0 {
		params = append(params, "presence_penalty")
	}
	if len(req.LogitBias) > 0 {
		params = append(params, "logit_bias")
	}
	return params
}

// stopSet reports whether a chat stop value names at least one sequence.
func stopSet(raw json.RawMessage) bool {
	var one string
	if json.Unmarshal(raw, &one) == nil {
		return one != ""
	}
	var many []string
	if json.Unmarshal(raw, &many) == nil {
		for _, s := range many {
			if s != "" {
				return true
			}
		}
		return false
	}
	return len(raw) > 0 && string(raw) != "null"
}

// unsupportedOverResponses is the 400 for a request that must go over the
// Responses API but carries something only chat completions can take. With
// reasoning_effort "none" the request stays on chat completions as sent.
func unsupportedOverResponses(what string) *models.APIError {
	return models.ErrBadRequest(unsupportedCode, fmt.Sprintf(
		"This request has function tools for a model that takes them only over the Responses API, which cannot carry %s.", what))
}

const unsupportedCode = "unsupported_over_responses"

// overResponsesRemedy is the advice for a request the Responses route cannot
// carry, for the route the caller used.
func overResponsesRemedy(req *models.ChatCompletionRequest) string {
	if req.TranslatedFrom != "" {
		return " Remove it from the request, or send it without function tools."
	}
	return ` Remove it, or set reasoning_effort to "none" to keep the request on chat completions.`
}

// toolType names a tool's type for an error message.
func toolType(t models.Tool) string {
	if t.Type != "" {
		return t.Type
	}
	var named map[string]json.RawMessage
	if json.Unmarshal(t.Raw, &named) == nil {
		for key := range named {
			return key
		}
	}
	return "non-function"
}

// defaultJSONSchemaName names a json_schema format that came without a name.
const defaultJSONSchemaName = "response"

// responsesTextFormat translates a chat response_format into a Responses text
// format. A type the Responses API does not take is not sent.
func responsesTextFormat(rf *models.ResponseFormat) json.RawMessage {
	if rf == nil {
		return nil
	}
	switch rf.Type {
	case "text", "json_object":
		b, _ := json.Marshal(struct {
			Type string `json:"type"`
		}{rf.Type})
		return b
	case "json_schema":
		var js struct {
			Name        string          `json:"name"`
			Description string          `json:"description"`
			Schema      json.RawMessage `json:"schema"`
			Strict      *bool           `json:"strict"`
		}
		if len(rf.JSONSchema) > 0 {
			_ = json.Unmarshal(rf.JSONSchema, &js)
		}
		if string(js.Schema) == "null" {
			js.Schema = nil
		}
		// The Responses API requires a name. Chat clients that build the
		// schema themselves (the GenAI translator, for one) may leave it out.
		if js.Name == "" {
			js.Name = defaultJSONSchemaName
		}
		b, _ := json.Marshal(responsesJSONSchemaFormat{
			Type:        "json_schema",
			Name:        js.Name,
			Description: js.Description,
			Schema:      js.Schema,
			Strict:      js.Strict != nil && *js.Strict,
		})
		return b
	}
	return nil
}

// responsesInput translates one chat message into Responses input items.
func responsesInput(m models.Message) ([]any, *models.APIError) {
	switch m.Role {
	case "tool":
		text, err := toolOutputText(m.Content)
		if err != nil {
			return nil, err
		}
		return []any{responsesFunctionCallOutput{
			Type:   "function_call_output",
			CallID: m.ToolCallID,
			Output: text,
		}}, nil
	case "assistant":
		content, err := assistantContent(m.Content)
		if err != nil {
			return nil, err
		}
		var items []any
		if len(content) > 0 {
			items = append(items, responsesMessage{Type: "message", Role: "assistant", Content: content})
		}
		for _, tc := range m.ToolCalls {
			if tc.Type != "" && tc.Type != "function" {
				return nil, unsupportedOverResponses(fmt.Sprintf("a %q tool call", tc.Type))
			}
			items = append(items, responsesFunctionCall{
				Type:      "function_call",
				CallID:    tc.ID,
				Name:      tc.Function.Name,
				Arguments: tc.Function.Arguments,
			})
		}
		return items, nil
	default:
		content, err := inputContent(m.Content)
		if err != nil {
			return nil, err
		}
		return []any{responsesMessage{Type: "message", Role: m.Role, Content: content}}, nil
	}
}

// chatContentPart is one part of a chat message's array content.
type chatContentPart struct {
	Type     string `json:"type"`
	Text     string `json:"text"`
	Refusal  string `json:"refusal"`
	ImageURL *struct {
		URL    string `json:"url"`
		Detail string `json:"detail"`
	} `json:"image_url"`
	File *struct {
		FileData string `json:"file_data"`
		FileID   string `json:"file_id"`
		Filename string `json:"filename"`
	} `json:"file"`
}

// contentParts reads chat content: a string is one text part. ok is false for
// content that is neither a string nor an array of parts.
func contentParts(raw json.RawMessage) (parts []chatContentPart, ok bool) {
	if len(raw) == 0 || string(raw) == "null" {
		return nil, true
	}
	var text string
	if json.Unmarshal(raw, &text) == nil {
		return []chatContentPart{{Type: "text", Text: text}}, true
	}
	if json.Unmarshal(raw, &parts) != nil {
		return nil, false
	}
	return parts, true
}

// badContent is the 400 for content that is neither a string nor an array of
// well-formed parts, naming the first part that does not decode, as chat
// completions refuses it. Its raw JSON is never sent on as text.
func badContent(raw json.RawMessage) *models.APIError {
	var items []json.RawMessage
	if json.Unmarshal(raw, &items) == nil {
		for i, item := range items {
			var part chatContentPart
			if json.Unmarshal(item, &part) != nil {
				var typed struct {
					Type string `json:"type"`
				}
				_ = json.Unmarshal(item, &typed)
				return models.ErrBadRequest("invalid_content", fmt.Sprintf("content part %d (type %q) has the wrong shape", i, typed.Type))
			}
		}
	}
	return models.ErrBadRequest("invalid_content", "message content must be a string or an array of content parts")
}

// inputContent translates the content of a user, system or developer message
// into Responses input content: text, images and files. Any other part (audio,
// say) is refused rather than dropped.
func inputContent(raw json.RawMessage) ([]responsesContent, *models.APIError) {
	parts, ok := contentParts(raw)
	if !ok {
		return nil, badContent(raw)
	}
	out := make([]responsesContent, 0, len(parts))
	for _, part := range parts {
		switch {
		case part.Type == "text":
			if part.Text != "" {
				out = append(out, responsesContent{Type: "input_text", Text: part.Text})
			}
		case part.Type == "image_url" && part.ImageURL != nil:
			detail := part.ImageURL.Detail
			if detail == "" {
				detail = "auto"
			}
			out = append(out, responsesContent{Type: "input_image", ImageURL: part.ImageURL.URL, Detail: detail})
		case part.Type == "file" && part.File != nil && (part.File.FileData != "" || part.File.FileID != ""):
			out = append(out, responsesContent{
				Type:     "input_file",
				FileData: part.File.FileData,
				FileID:   part.File.FileID,
				Filename: part.File.Filename,
			})
		default:
			return nil, unsupportedOverResponses(fmt.Sprintf("a %q content part", part.Type))
		}
	}
	return out, nil
}

// assistantContent translates an assistant message's content into Responses
// output content: text and refusals.
func assistantContent(raw json.RawMessage) ([]responsesContent, *models.APIError) {
	parts, ok := contentParts(raw)
	if !ok {
		return nil, badContent(raw)
	}
	var out []responsesContent
	for _, part := range parts {
		switch part.Type {
		case "text":
			if part.Text != "" {
				out = append(out, responsesContent{Type: "output_text", Text: part.Text})
			}
		case "refusal":
			if part.Refusal != "" {
				out = append(out, responsesContent{Type: "refusal", Refusal: part.Refusal})
			}
		default:
			return nil, unsupportedOverResponses(fmt.Sprintf("a %q content part in an assistant message", part.Type))
		}
	}
	return out, nil
}

// toolOutputText joins the text of a tool message, the only content a
// function call output carries.
func toolOutputText(raw json.RawMessage) (string, *models.APIError) {
	parts, ok := contentParts(raw)
	if !ok {
		return "", badContent(raw)
	}
	var sb strings.Builder
	for _, part := range parts {
		if part.Type != "text" {
			return "", unsupportedOverResponses(fmt.Sprintf("a %q content part in a tool message", part.Type))
		}
		sb.WriteString(part.Text)
	}
	return sb.String(), nil
}

// responsesTool translates a chat function tool; toResponsesRequest refuses any
// other kind.
func responsesTool(t models.Tool) json.RawMessage {
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
// strings; a named function moves its name up a level. Any other object (a
// custom tool, allowed_tools) is refused rather than sent in chat shape.
func responsesToolChoice(raw json.RawMessage) (json.RawMessage, *models.APIError) {
	if len(raw) == 0 || string(raw) == "null" {
		return nil, nil
	}
	var mode string
	if json.Unmarshal(raw, &mode) == nil {
		switch mode {
		case "none", "auto", "required":
			return raw, nil
		}
		return nil, unsupportedOverResponses(fmt.Sprintf("tool_choice %q", mode))
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
		return b, nil
	}
	if named.Type == "allowed_tools" {
		return responsesAllowedTools(raw)
	}
	what := "this tool_choice"
	if named.Type != "" {
		what = fmt.Sprintf("a %q tool_choice", named.Type)
	}
	return nil, unsupportedOverResponses(what)
}

// responsesAllowedTools translates an allowed_tools tool_choice that lists
// function tools: the mode and the tools move up a level, and each function's
// name with them. Any other entry is refused.
func responsesAllowedTools(raw json.RawMessage) (json.RawMessage, *models.APIError) {
	var chat struct {
		AllowedTools struct {
			Mode  string `json:"mode"`
			Tools []struct {
				Type     string `json:"type"`
				Function struct {
					Name string `json:"name"`
				} `json:"function"`
			} `json:"tools"`
		} `json:"allowed_tools"`
	}
	if json.Unmarshal(raw, &chat) != nil || (chat.AllowedTools.Mode != "auto" && chat.AllowedTools.Mode != "required") {
		return nil, unsupportedOverResponses("this allowed_tools tool_choice")
	}
	type tool struct {
		Type string `json:"type"`
		Name string `json:"name"`
	}
	out := struct {
		Type  string `json:"type"`
		Mode  string `json:"mode"`
		Tools []tool `json:"tools"`
	}{Type: "allowed_tools", Mode: chat.AllowedTools.Mode, Tools: []tool{}}
	for _, t := range chat.AllowedTools.Tools {
		if t.Type != "function" || t.Function.Name == "" {
			return nil, unsupportedOverResponses(fmt.Sprintf("a %q tool in allowed_tools", t.Type))
		}
		out.Tools = append(out.Tools, tool{Type: "function", Name: t.Function.Name})
	}
	b, _ := json.Marshal(out)
	return b, nil
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

// toChat translates a Responses API response into a chat completion. An output
// item that is a call of another kind than a function cannot be answered in
// chat shape; it fails the call rather than being dropped.
func (r *responsesResponse) toChat() (*models.ChatCompletionResponse, error) {
	msg := models.Message{Role: "assistant"}
	var text strings.Builder
	for _, item := range r.Output {
		if err := checkOutputItem(item.Type); err != nil {
			return nil, err
		}
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
	}, nil
}

// checkOutputItem fails an output item the chat answer cannot carry: a call of
// another kind than a function (a custom tool, web search, ...). Reasoning and
// messages are carried or have nothing to carry.
func checkOutputItem(itemType string) *models.APIError {
	if itemType != "function_call" && strings.HasSuffix(itemType, "_call") {
		return models.ErrUpstreamProvider(http.StatusBadGateway, fmt.Sprintf(
			"the Responses API answered with a %q item, which a chat completion cannot carry", itemType))
	}
	return nil
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
		// Sent at once so the caller sees the stream open, but it is only the
		// acknowledgement: reasoning comes before the first output.
		ack := c.chunk(models.Delta{Role: "assistant"}, nil)
		ack.Acknowledgement = true
		return []models.StreamChunk{ack}, nil

	case "response.output_text.delta", "response.refusal.delta":
		delta := ev.Delta
		return []models.StreamChunk{c.chunk(models.Delta{Content: &delta}, nil)}, nil

	case "response.output_item.added":
		if ev.Item == nil {
			return nil, nil
		}
		if err := checkOutputItem(ev.Item.Type); err != nil {
			c.done = true
			return nil, err
		}
		if ev.Item.Type != "function_call" {
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
			Function: &models.FunctionCallDelta{Name: ev.Item.Name, Arguments: ev.Item.Arguments},
		}}}, nil)}, nil

	case "response.function_call_arguments.delta":
		index, ok := c.toolIndex[ev.ItemID]
		if !ok {
			return nil, nil
		}
		return []models.StreamChunk{c.chunk(models.Delta{ToolCalls: []models.ToolCallDelta{{
			Index:    index,
			Function: &models.FunctionCallDelta{Arguments: ev.Delta},
		}}}, nil)}, nil

	case "response.completed", "response.incomplete":
		c.done = true
		resp := ev.Response
		if resp == nil {
			resp = &responsesResponse{}
		}
		// The final response lists every output item, including any the
		// stream never announced with output_item.added.
		var out []models.StreamChunk
		for _, item := range resp.Output {
			if err := checkOutputItem(item.Type); err != nil {
				return nil, err
			}
			// A function call the stream never announced is carried here,
			// as the non-streaming answer carries it.
			if _, seen := c.toolIndex[item.ID]; item.Type == "function_call" && !seen {
				if c.toolIndex == nil {
					c.toolIndex = map[string]int{}
				}
				index := len(c.toolIndex)
				c.toolIndex[item.ID] = index
				out = append(out, c.chunk(models.Delta{ToolCalls: []models.ToolCallDelta{{
					Index:    index,
					ID:       item.CallID,
					Type:     "function",
					Function: &models.FunctionCallDelta{Name: item.Name, Arguments: item.Arguments},
				}}}, nil))
			}
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
		return append(out, final, usage), nil

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
