package server

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/translation"
)

// Translation bypasses models.WriteError, so notify the same narrow release
// boundary before serializing a gateway error. Raw upstream bodies never call it.
func inspectionErrorFromCanonical(w http.ResponseWriter, translator translation.InboundTranslator, err *models.APIError) (int, []byte, string) {
	if guarded, ok := w.(interface {
		GuardrailError(*models.APIError) *models.APIError
	}); ok {
		err = guarded.GuardrailError(err)
	}
	return translator.ErrorFromCanonical(err)
}

// processTextInspection is called only by the named, supported handlers. A
// direct/internal invocation gets its own explicit declaration; an HTTP call
// retains the dispatch declaration and principal. Neither caller extras nor
// EndpointType can create a receipt, and authentication alone binds identity.
func (h *Handlers) processTextInspection(ctx context.Context, rc *models.RequestContext, operation string, call pipeline.ProviderFunc) error {
	contract := models.InspectionContract{Operation: operation, Input: models.InspectionSupported, Output: models.InspectionSupported}
	return h.processInspection(ctx, rc, contract, call)
}

func (h *Handlers) processInputInspection(ctx context.Context, rc *models.RequestContext, operation string, call pipeline.ProviderFunc) error {
	return h.processInspection(ctx, rc, models.InspectionContract{Operation: operation, Input: models.InspectionSupported}, call)
}

func (h *Handlers) processInspection(ctx context.Context, rc *models.RequestContext, contract models.InspectionContract, call pipeline.ProviderFunc) error {
	if rc.Inspection == nil {
		rc.Inspection = models.InspectionFromContext(ctx)
	}
	if rc.Inspection == nil {
		rc.Inspection = models.NewInspectionState(contract)
	}
	request := rc.Request
	if rc.GuardrailRequest != nil {
		request = rc.GuardrailRequest
	}
	if rc.SelectedGuardrailRequest != nil {
		request = rc.SelectedGuardrailRequest
	}
	if rc.GuardrailInputUnsupported == "" && completeTextRequest(request) {
		rc.Inspection.Complete(models.InspectionInput, request)
	}
	return h.engine.Process(ctx, rc, func(ctx context.Context, rc *models.RequestContext) error {
		if err := call(ctx, rc); err != nil {
			return err
		}
		response := rc.Response
		if rc.GuardrailResponse != nil {
			response = rc.GuardrailResponse
		}
		if rc.GuardrailOutputUnsupported == "" && completeTextResponse(response) {
			rc.Inspection.Complete(models.InspectionOutput, response)
		}
		return nil
	})
}

// reAdmitChangedInspection runs at chat retry/fallback attempt boundaries.
// Changing an evaluator-visible field invalidates the original input decision;
// validate and check the actual attempted request before any provider submission.
func (h *Handlers) reAdmitChangedInspection(ctx context.Context, rc *models.RequestContext) *models.APIError {
	request := rc.Request
	if rc.GuardrailRequest != nil {
		request = rc.GuardrailRequest
	}
	if rc.SelectedGuardrailRequest != nil {
		request = rc.SelectedGuardrailRequest
	}
	if rc.Inspection.IsComplete(models.InspectionInput, request) {
		return nil
	}
	if rc.GuardrailInputUnsupported == "" && completeTextRequest(request) {
		rc.Inspection.Complete(models.InspectionInput, request)
	}
	return h.engine.AdmitSelectedRequest(ctx, rc)
}

func completeTextRequest(req *models.ChatCompletionRequest) bool {
	if req == nil || len(req.Messages) == 0 || len(req.Tools) > 0 || presentJSON(req.ToolChoice) || req.ResponseFormat != nil || req.Audio != nil {
		return false
	}
	for _, modality := range req.Modalities {
		if modality != "text" {
			return false
		}
	}
	// These are explicit evaluator context, not hidden provider content. Responses
	// projections retain their original parameter names (R2); unknown passthrough
	// fields, previous response references and native tools are never complete.
	for key, raw := range req.Extra {
		switch key {
		case "metadata":
		case "max_output_tokens":
			var n int
			if json.Unmarshal(raw, &n) != nil {
				return false
			}
		case "store":
			var enabled bool
			if json.Unmarshal(raw, &enabled) != nil {
				return false
			}
		default:
			return false
		}
	}
	if presentJSON(req.Stop) {
		if _, ok := textInputMessages(req.Stop); !ok {
			return false
		}
	}
	for _, message := range req.Messages {
		if !completeTextMessage(message) {
			return false
		}
	}
	return true
}

func completeTextMessage(message models.Message) bool {
	if len(message.ToolCalls) > 0 || message.ToolCallID != "" || presentJSON(message.ThinkingBlocks) || message.ReasoningContent != "" || presentJSON(message.Annotations) {
		return false
	}
	_, ok := strictTextContent(message.Content, "text", true)
	return ok
}

func completeTextResponse(resp *models.ChatCompletionResponse) bool {
	if resp == nil || len(resp.Choices) == 0 || len(resp.Extra) > 0 {
		return false
	}
	for _, choice := range resp.Choices {
		if !completeTextMessage(choice.Message) || (choice.Logprobs != nil && presentJSON(*choice.Logprobs)) {
			return false
		}
	}
	if resp.Usage != nil {
		raw, _ := json.Marshal(resp.Usage)
		if !numericJSON(raw) {
			return false
		}
	}
	return true
}

// Unknown nested message fields disappear during canonical decoding. Validate
// the actual incoming shape too, so a successful decode cannot grant evidence.
func completeChatWire(body []byte) bool {
	var request map[string]json.RawMessage
	if json.Unmarshal(body, &request) != nil {
		return false
	}
	var messages []json.RawMessage
	if json.Unmarshal(request["messages"], &messages) != nil {
		return false
	}
	for _, raw := range messages {
		if _, ok := inspectionObject(raw, "role content name tool_calls tool_call_id thinking_blocks reasoning_content annotations"); !ok {
			return false
		}
	}
	return true
}

func presentJSON(raw json.RawMessage) bool {
	s := strings.TrimSpace(string(raw))
	return s != "" && s != "null" && s != "[]" && s != "{}"
}

// inspectionObject deliberately accepts only named fields. This is capability
// evidence, not API validation: unknown shapes retain their usual wire behavior
// for unselected and observation-only callers, but cannot authorize Block.
func inspectionObject(raw []byte, fields string) (map[string]json.RawMessage, bool) {
	var obj map[string]json.RawMessage
	if json.Unmarshal(raw, &obj) != nil || obj == nil {
		return nil, false
	}
	allowed := " " + fields + " "
	for key := range obj {
		if !strings.Contains(allowed, " "+key+" ") {
			return nil, false
		}
	}
	return obj, true
}

// Scalar envelope fields must really be scalar, not a future content object
// smuggled under a familiar field name. Optional nulls carry no text.
func inspectionScalarFields(obj map[string]json.RawMessage, stringsOnly, numbersOnly, boolsOnly string) bool {
	for _, group := range []struct {
		names string
		kind  byte
	}{{stringsOnly, 's'}, {numbersOnly, 'n'}, {boolsOnly, 'b'}} {
		for _, key := range strings.Fields(group.names) {
			raw, exists := obj[key]
			if !exists || strings.TrimSpace(string(raw)) == "null" {
				continue
			}
			switch group.kind {
			case 's':
				if _, ok := jsonString(raw); !ok {
					return false
				}
			case 'n':
				var value float64
				if json.Unmarshal(raw, &value) != nil {
					return false
				}
			case 'b':
				var value bool
				if json.Unmarshal(raw, &value) != nil {
					return false
				}
			}
		}
	}
	return true
}

func jsonString(raw json.RawMessage) (string, bool) {
	var s string
	if !strings.HasPrefix(strings.TrimSpace(string(raw)), `"`) || json.Unmarshal(raw, &s) != nil {
		return "", false
	}
	return s, true
}

// strictTextContent never turns document/tool/refusal/thinking blocks into a
// partial text success. Empty strings/arrays are valid text; null is unknown.
func strictTextContent(raw json.RawMessage, kind string, allowString bool) (string, bool) {
	if allowString {
		if s, ok := jsonString(raw); ok {
			return s, true
		}
	}
	var parts []json.RawMessage
	if json.Unmarshal(raw, &parts) != nil || parts == nil {
		return "", false
	}
	texts := make([]string, 0, len(parts))
	for _, part := range parts {
		fields := "type text"
		if kind == "anthropic" {
			fields += " cache_control"
		}
		if kind == "output_text" {
			fields += " annotations logprobs"
		}
		obj, ok := inspectionObject(part, fields)
		if !ok {
			return "", false
		}
		if kind == "google" {
			if len(obj) != 1 {
				return "", false
			}
		} else {
			typ, _ := jsonString(obj["type"])
			want := kind
			if want == "anthropic" {
				want = "text"
			}
			if typ != want {
				return "", false
			}
		}
		if presentJSON(obj["annotations"]) || presentJSON(obj["logprobs"]) {
			return "", false
		}
		if presentJSON(obj["cache_control"]) {
			cache, ok := inspectionObject(obj["cache_control"], "type ttl")
			if !ok || !inspectionScalarFields(cache, "type ttl", "", "") {
				return "", false
			}
		}
		text, ok := jsonString(obj["text"])
		if !ok {
			return "", false
		}
		texts = append(texts, text)
	}
	return strings.Join(texts, "\n"), true
}

func numericJSON(raw json.RawMessage) bool {
	var value any
	if json.Unmarshal(raw, &value) != nil {
		return false
	}
	var numeric func(any) bool
	numeric = func(v any) bool {
		switch x := v.(type) {
		case nil, float64:
			return true
		case map[string]any:
			for _, child := range x {
				if !numeric(child) {
					return false
				}
			}
			return true
		default:
			return false
		}
	}
	return numeric(value)
}

func completeNativeInput(dialect string, body []byte) bool {
	switch dialect {
	case "anthropic":
		obj, ok := inspectionObject(body, "model messages system max_tokens stream temperature top_p top_k stop_sequences metadata")
		if !ok {
			return false
		}
		if raw, exists := obj["system"]; exists {
			if _, ok := strictTextContent(raw, "anthropic", true); !ok {
				return false
			}
		}
		var messages []json.RawMessage
		if json.Unmarshal(obj["messages"], &messages) != nil || len(messages) == 0 {
			return false
		}
		for _, raw := range messages {
			message, ok := inspectionObject(raw, "role content")
			if !ok {
				return false
			}
			if _, ok := strictTextContent(message["content"], "anthropic", true); !ok {
				return false
			}
		}
		return true
	case "google":
		obj, ok := inspectionObject(body, "contents systemInstruction generationConfig safetySettings")
		if !ok {
			return false
		}
		var contents []json.RawMessage
		if json.Unmarshal(obj["contents"], &contents) != nil || len(contents) == 0 {
			return false
		}
		for _, key := range []string{"systemInstruction", "system_instruction"} {
			if raw, exists := obj[key]; exists {
				contents = append(contents, raw)
			}
		}
		for _, raw := range contents {
			content, ok := inspectionObject(raw, "role parts")
			if !ok {
				return false
			}
			if _, ok := strictTextContent(content["parts"], "google", false); !ok {
				return false
			}
		}
		for _, key := range []string{"generationConfig", "generation_config"} {
			if raw, exists := obj[key]; exists {
				if _, ok := inspectionObject(raw, "temperature topP topK candidateCount maxOutputTokens stopSequences presencePenalty frequencyPenalty seed responseMimeType top_p top_k candidate_count max_output_tokens stop_sequences presence_penalty frequency_penalty response_mime_type"); !ok {
					return false
				}
			}
		}
		for _, key := range []string{"safetySettings", "safety_settings"} {
			if raw, exists := obj[key]; exists {
				var settings []json.RawMessage
				if json.Unmarshal(raw, &settings) != nil {
					return false
				}
				for _, setting := range settings {
					if _, ok := inspectionObject(setting, "category threshold method"); !ok {
						return false
					}
				}
			}
		}
		return true
	}
	return false
}

func completeResponsesInput(req *models.ResponsesRequest) bool {
	if req == nil || req.PreviousResponseID != "" || presentJSON(req.Tools) || len(req.Extra) > 0 {
		return false
	}
	if _, ok := jsonString(req.Input); ok {
		return true
	}
	var items []json.RawMessage
	if json.Unmarshal(req.Input, &items) != nil || items == nil {
		return false
	}
	for _, raw := range items {
		item, ok := inspectionObject(raw, "type role content")
		if !ok {
			return false
		}
		if typ, exists := item["type"]; exists {
			if s, _ := jsonString(typ); s != "message" {
				return false
			}
		}
		if _, ok := strictTextContent(item["content"], "input_text", true); !ok {
			return false
		}
	}
	return true
}

// Native output keeps the original wire bytes. Only a whole validated shape
// can complete its projection; usage-only or partial text never does so.
func nativeOutputProjection(dialect string, body []byte) (*models.ChatCompletionResponse, bool) {
	var text string
	switch dialect {
	case "anthropic":
		obj, ok := inspectionObject(body, "id type role model content stop_reason stop_sequence usage")
		if !ok || !inspectionScalarFields(obj, "id type role model stop_reason stop_sequence", "", "") {
			return nil, false
		}
		if raw, exists := obj["usage"]; exists && !numericJSON(raw) {
			return nil, false
		}
		text, ok = strictTextContent(obj["content"], "anthropic", false)
		if !ok {
			return nil, false
		}
	case "google":
		obj, ok := inspectionObject(body, "candidates usageMetadata modelVersion responseId")
		if !ok || !inspectionScalarFields(obj, "modelVersion responseId", "", "") {
			return nil, false
		}
		if raw, exists := obj["usageMetadata"]; exists && !numericJSON(raw) {
			return nil, false
		}
		var candidates []json.RawMessage
		if json.Unmarshal(obj["candidates"], &candidates) != nil || len(candidates) == 0 {
			return nil, false
		}
		var texts []string
		for _, raw := range candidates {
			candidate, ok := inspectionObject(raw, "content finishReason index safetyRatings")
			if !ok || !inspectionScalarFields(candidate, "finishReason", "index", "") {
				return nil, false
			}
			// These nested provider annotations are not projected; do not certify
			// a response with additional uninspected fields.
			if presentJSON(candidate["safetyRatings"]) {
				return nil, false
			}
			content, ok := inspectionObject(candidate["content"], "role parts")
			if !ok || !inspectionScalarFields(content, "role", "", "") {
				return nil, false
			}
			part, ok := strictTextContent(content["parts"], "google", false)
			if !ok {
				return nil, false
			}
			texts = append(texts, part)
		}
		text = strings.Join(texts, "\n")
	case "responses":
		obj, ok := inspectionObject(body, "id object created_at model status output usage error incomplete_details instructions max_output_tokens metadata parallel_tool_calls previous_response_id reasoning store temperature text tool_choice tools top_p truncation user background service_tier")
		if !ok || !inspectionScalarFields(obj, "id object model status truncation user service_tier tool_choice", "created_at max_output_tokens temperature top_p", "parallel_tool_calls store background") {
			return nil, false
		}
		// Echoed instructions, references, tool/schema settings and reasoning are
		// content too. Until projected in full, conservatively refuse their release.
		for _, key := range []string{"error", "incomplete_details", "instructions", "previous_response_id", "tools", "metadata"} {
			if raw := obj[key]; presentJSON(raw) && string(raw) != `""` {
				return nil, false
			}
		}
		if raw, exists := obj["usage"]; exists && !numericJSON(raw) {
			return nil, false
		}
		// A normal text Responses reply echoes these default configuration
		// objects. Accept exactly the non-content defaults, not JSON schemas or
		// surfaced reasoning tucked under familiar configuration names.
		if raw := obj["text"]; presentJSON(raw) {
			textConfig, ok := inspectionObject(raw, "format verbosity")
			if !ok {
				return nil, false
			}
			format, ok := inspectionObject(textConfig["format"], "type")
			if !ok {
				return nil, false
			}
			if typ, _ := jsonString(format["type"]); typ != "text" {
				return nil, false
			}
			if raw, exists := textConfig["verbosity"]; exists {
				verbosity, _ := jsonString(raw)
				if verbosity != "low" && verbosity != "medium" && verbosity != "high" {
					return nil, false
				}
			}
		}
		if raw := obj["reasoning"]; presentJSON(raw) {
			reasoning, ok := inspectionObject(raw, "effort summary")
			if !ok {
				return nil, false
			}
			for _, value := range reasoning {
				if strings.TrimSpace(string(value)) != "null" {
					return nil, false
				}
			}
		}
		var output []json.RawMessage
		if json.Unmarshal(obj["output"], &output) != nil || output == nil {
			return nil, false
		}
		var texts []string
		for _, raw := range output {
			item, ok := inspectionObject(raw, "type id role status content")
			if !ok || !inspectionScalarFields(item, "type id role status", "", "") {
				return nil, false
			}
			if typ, _ := jsonString(item["type"]); typ != "message" {
				return nil, false
			}
			part, ok := strictTextContent(item["content"], "output_text", false)
			if !ok {
				return nil, false
			}
			texts = append(texts, part)
		}
		text = strings.Join(texts, "\n")
	default:
		return nil, false
	}
	return &models.ChatCompletionResponse{Choices: []models.Choice{{Message: textMessage("assistant", text)}}}, true
}

func setNativeOutputProjection(rc *models.RequestContext, dialect string, body []byte) {
	projection, complete := nativeOutputProjection(dialect, body)
	if complete {
		rc.GuardrailResponse = projection
		return
	}
	// Retain the legacy observation projection, but never certify it for selected
	// blocking inspection. Unselected/org/static behavior remains unchanged.
	rc.GuardrailOutputUnsupported = "native output contains content without a complete text projection"
}
