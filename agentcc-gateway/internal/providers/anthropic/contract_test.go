package anthropic

import (
	"context"
	"encoding/json"
	"errors"
	"mime"
	"net/http"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/common"
	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

// Contract tests replay each fixture under testdata/contracts/<model>/<case>
// through the real provider (ChatCompletion / StreamChatCompletion against a
// replay server) and check what comes out. See
// internal/providers/contracttest/README.md.
//
//	go test ./internal/providers/anthropic/ -run Contract           # check
//	go test ./internal/providers/anthropic/ -run Contract -update   # rewrite goldens

// knownStreamEvents are the SSE event types the Messages API documents. A
// fixture with any other type fails before the adapter is consulted.
var knownStreamEvents = []string{
	"message_start", "content_block_start", "content_block_delta", "content_block_stop",
	"message_delta", "message_stop", "ping", "error",
}

// ContractEnums are the value sets the Messages API documents for the
// enum-like fields the adapter reads or passes on. Every value found at one
// of these paths in a fixture (or a live recording) must be listed, so a new
// stop reason, block type, delta type, tier or error type fails the suite
// even where the adapter skips it without a trace. Spec names the schema in
// Anthropic's OpenAPI spec (anthropic-sdk-python scripts/mock-spec.json.gz,
// read 2026-10-28) the values come from; the spec-diff check compares the two.
// message_start's stop_reason, stop_details and content are left out: by the
// protocol they are null or empty there (see streamProtocolFields).
var ContractEnums = []contracttest.EnumSpec{
	{Spec: "MessageStreamEvent.type + the streaming docs' ping and error", Paths: []string{"*:$.type"},
		Values: knownStreamEvents},
	{Spec: "Message.type", Paths: []string{"$.type", "message_start:$.message.type"}, Values: []string{"message"}},
	{Spec: "Message.role", Paths: []string{"$.role", "message_start:$.message.role"}, Values: []string{"assistant"}},
	{Spec: "StopReason", Paths: []string{"$.stop_reason", "message_delta:$.delta.stop_reason"},
		Values: []string{"end_turn", "max_tokens", "stop_sequence", "tool_use", "pause_turn", "refusal", "model_context_window_exceeded"}},
	{Spec: "RefusalStopDetails.type", Paths: []string{"$.stop_details.type", "message_delta:$.delta.stop_details.type"},
		Values: []string{"refusal"}},
	{Spec: "RefusalCategory", Paths: []string{"$.stop_details.category", "message_delta:$.delta.stop_details.category"},
		Values: []string{"cyber", "bio", "frontier_llm", "reasoning_extraction", "general_harms"}},
	{Spec: "ContentBlock (discriminator)", Paths: []string{"$.content[].type", "content_block_start:$.content_block.type"},
		Values: []string{"text", "thinking", "redacted_thinking", "tool_use", "server_tool_use", "web_search_tool_result",
			"web_fetch_tool_result", "code_execution_tool_result", "bash_code_execution_tool_result",
			"text_editor_code_execution_tool_result", "tool_search_tool_result", "container_upload"}},
	{Spec: "ContentBlockDeltaEvent.delta (discriminator)", Paths: []string{"content_block_delta:$.delta.type"},
		Values: []string{"text_delta", "input_json_delta", "citations_delta", "thinking_delta", "signature_delta"}},
	{Spec: "ResponseTextBlock.citations[] (discriminator)", Paths: []string{"$.content[].citations[].type", "content_block_start:$.content_block.citations[].type", "content_block_delta:$.delta.citation.type"},
		Values: []string{"char_location", "page_location", "content_block_location", "web_search_result_location", "search_result_location"}},
	{Spec: "Usage.service_tier", Paths: []string{"$.usage.service_tier", "message_start:$.message.usage.service_tier"},
		Values: []string{"standard", "priority", "batch"}},
	{Spec: "Speed", Paths: []string{"$.usage.speed", "message_start:$.message.usage.speed"}, Values: []string{"standard", "fast"}},
	{Spec: "ErrorResponse.type", Paths: []string{"error:$.type"}, Values: []string{"error"}},
	{Spec: errorTypeSpec, Paths: []string{"error:$.error.type"}, Values: errorTypes},
	{Spec: "ErrorResponse.error.details.error_code", Paths: []string{"error:$.error.details.error_code"}, Values: errorCodes},
}

// errorTypeSpec and errorTypes are the error types an error body (or a
// mid-stream error event) can carry: the spec's ErrorType plus the two the
// errors page (platform.claude.com/docs/en/api/errors, read 2026-10-28)
// documents and the spec leaves out, 409 conflict_error and 413
// request_too_large. The page warns the set grows over time.
const errorTypeSpec = "ErrorType + the errors page's conflict_error and request_too_large"

var errorTypes = []string{"invalid_request_error", "authentication_error", "billing_error", "permission_error", "not_found_error",
	"conflict_error", "request_too_large", "rate_limit_error", "timeout_error", "api_error", "overloaded_error"}

// errorCodes are the spec's error.details.error_code values (the spend-cap
// 429 carries enforced_spend_limit_reached; docs/en/api/rate-limits).
var errorCodes = []string{"available_tools_limit_exceeded", "claude_code_key_creator_not_member", "claude_code_version_too_old",
	"cmek_key_disabled", "cmek_key_network_blocked", "cmek_key_unreachable", "compaction_block_ambiguous", "compaction_block_misplaced",
	"compaction_content_mismatch", "compaction_incomplete_turn", "compaction_nothing_to_summarize", "compaction_signature_invalid",
	"compaction_too_many_tool_references", "compaction_tool_changes_mismatch", "compaction_unavailable", "enforced_spend_limit_reached",
	"organization_on_hold", "request_body_encoding_invalid", "request_body_encoding_unsupported", "request_body_too_large_after_decoding",
	"skill_read_blocked_undecryptable", "tool_name_conflict", "tool_reference_unresolved"}

// errorTypeStatus is the HTTP status the errors page gives each error type,
// for judging the canonical error of a mid-stream error event, which arrives
// in a 200 stream and so has no status of its own.
var errorTypeStatus = map[string]int{
	"invalid_request_error": 400, "authentication_error": 401, "billing_error": 402, "permission_error": 403,
	"not_found_error": 404, "conflict_error": 409, "request_too_large": 413, "rate_limit_error": 429,
	"api_error": 500, "timeout_error": 504, "overloaded_error": 529,
}

// contractStrict is how the adapter decodes Anthropic bodies, for the strict
// checks (contracttest.Strict).
var contractStrict = &contracttest.Strict{
	Docs:  contractDocs,
	Enums: ContractEnums,
	Decoded: []contracttest.Decoded{
		// citationsForBlock decodes a text block's raw citations.
		{Path: "$.content[].citations", Type: reflect.TypeFor[[]anthropicCitation]()},
		// The stream parser decodes each event's raw payloads by event, and
		// content blocks and deltas by their type; the types it skips stay
		// opaque (and are reported as unknown shapes when they carry content).
		{Path: "message_start:$.message", Type: reflect.TypeFor[messageStartData]()},
		{Path: "content_block_start:$.content_block", Tag: "type", Types: map[string]reflect.Type{
			"text":     reflect.TypeFor[contentBlockStartData](),
			"tool_use": reflect.TypeFor[contentBlockStartData](),
		}},
		{Path: "content_block_delta:$.delta", Tag: "type", Types: map[string]reflect.Type{
			"text_delta":       reflect.TypeFor[textDelta](),
			"input_json_delta": reflect.TypeFor[inputJSONDelta](),
		}},
		{Path: "message_delta:$.delta", Type: reflect.TypeFor[messageDeltaData]()},
	},
	// Tool input is whatever the model passed the tool.
	Free:   []string{"$.content[].input"},
	Ignore: streamProtocolFields,
}

// streamProtocolFields are stream payload fields the parser does not read
// because, by the protocol, they carry nothing at the point they arrive. They
// hold for every model, so they live here rather than in each meta.json.
var streamProtocolFields = map[string]string{
	"message_start:$.message.type":              `always "message"`,
	"message_start:$.message.role":              `always "assistant"`,
	"message_start:$.message.content":           "always empty; content arrives as content blocks",
	"message_start:$.message.stop_reason":       "always null at message_start; arrives on message_delta",
	"message_start:$.message.stop_sequence":     "always null at message_start; arrives on message_delta",
	"message_start:$.message.stop_details":      "always null at message_start; arrives on message_delta",
	"content_block_start:$.content_block.text":  "always empty at block start; text arrives as text_delta",
	"content_block_start:$.content_block.input": "always {} at block start; input arrives as input_json_delta",
}

// contractDocs splits a case's body into what the adapter decodes: an error
// body as anthropicErrorResponse (parseAnthropicError), a stream event by
// event (streamEvent, or anthropicErrorResponse for an error event), and a
// response as anthropicResponse.
func contractDocs(c contracttest.Case) []contracttest.Doc {
	switch {
	case c.IsError() && !isJSON(c.ContentType):
		return nil // an HTML or text page from Cloudflare or a proxy: not JSON by design
	case c.IsError():
		return []contracttest.Doc{{Root: contracttest.ErrorRoot, Data: c.Body, Type: reflect.TypeFor[anthropicErrorResponse]()}}
	case c.IsStream:
		events := contracttest.ParseSSE(c.Body)
		docs := make([]contracttest.Doc, len(events))
		for i, ev := range events {
			typ := reflect.TypeFor[streamEvent]()
			if ev.Name == "error" {
				typ = reflect.TypeFor[anthropicErrorResponse]()
			}
			docs[i] = contracttest.Doc{Root: ev.Name + ":$", Data: ev.Data, Type: typ}
		}
		return docs
	default:
		return []contracttest.Doc{{Root: "$", Data: c.Body, Type: reflect.TypeFor[anthropicResponse]()}}
	}
}

func TestContract(t *testing.T) {
	for _, c := range contracttest.Discover(t, adapterName) {
		t.Run(c.ID(), func(t *testing.T) {
			t.Parallel()
			strict := strictFor(t, c)
			out := runCase(t, c)
			assertGoldens(t, c, out)
			checkCase(t, c, out)
			strict.AssertMutationsDetected(t, c, func(t testing.TB, m contracttest.Case) {
				checkCase(t, m, runCase(t, m))
			})
		})
	}
}

// strictFor is the strict declaration of c's endpoint: chat (contractStrict)
// or one of the non-chat surfaces in contract_native_test.go.
func strictFor(t testing.TB, c contracttest.Case) *contracttest.Strict {
	t.Helper()
	switch c.Endpoint {
	case contracttest.EndpointChat:
		return contractStrict
	case contracttest.EndpointAnthropicMessages:
		return nativeStrict
	case contracttest.EndpointAnthropicCountTokens:
		return countTokensStrict
	case contracttest.EndpointListModels:
		return listModelsStrict
	}
	t.Fatalf("endpoint %q: the anthropic adapter does not implement it", c.Endpoint)
	return nil
}

// caseOutput is what the adapter did with one case.
type caseOutput struct {
	resp     *models.ChatCompletionResponse
	stream   contracttest.StreamResult
	err      error
	shapes   []common.UnknownShape
	upstream *contracttest.Replay

	// native is set for the passthrough endpoints, models for list_models.
	native *nativeResult
	models []models.ModelObject
}

// newContractProvider builds the real provider against a replay server. It
// takes a testing.TB so mutants can run it.
func newContractProvider(t testing.TB, url string) *Provider {
	t.Helper()
	p, err := New("test-provider", config.ProviderConfig{BaseURL: url, APIKey: "test-api-key", MaxConcurrent: 10, ConnPoolSize: 5})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	t.Cleanup(func() { _ = p.Close() })
	return p
}

// runCase sends c's request through the real provider against a replay of
// c's body.
func runCase(t testing.TB, c contracttest.Case) caseOutput {
	t.Helper()
	upstream := contracttest.ReplayServer(t, c)
	p := newContractProvider(t, upstream.URL)
	ctx, shapes := common.WithShapeRecorder(context.Background())

	out := caseOutput{upstream: upstream}
	switch {
	case c.Endpoint != contracttest.EndpointChat:
		runNonChat(t, c, p, ctx, &out)
	case c.IsStream:
		chunks, errs := p.StreamChatCompletion(ctx, c.Request)
		out.stream = contracttest.DrainStream(t, chunks, errs)
		out.err = out.stream.Err
	default:
		out.resp, out.err = p.ChatCompletion(ctx, c.Request)
	}
	out.shapes = shapes.Shapes()
	return out
}

// assertGoldens pins the canonical output (or error) and the outbound
// request.
func assertGoldens(t *testing.T, c contracttest.Case, out caseOutput) {
	t.Helper()
	switch {
	case c.Endpoint != contracttest.EndpointChat:
		assertNonChatGoldens(t, c, out)
		return
	case c.IsError():
		contracttest.AssertGolden(t, c.GoldenPath(), contracttest.ErrorGolden(out.err))
	case c.IsStream:
		contracttest.AssertGolden(t, c.GoldenPath(), out.stream)
	default:
		contracttest.AssertGolden(t, c.GoldenPath(), out.resp)
	}
	contracttest.AssertGolden(t, c.UpstreamGoldenPath(), out.upstream.Request(t))
}

// checkCase is every check except the goldens: what a live recording and the
// mutation self-test go through.
func checkCase(t testing.TB, c contracttest.Case, out caseOutput) {
	t.Helper()
	if c.Endpoint != contracttest.EndpointChat {
		checkNonChat(t, c, out)
		return
	}
	body, _ := out.upstream.Request(t).Body.(map[string]any)
	if body["model"] != c.Model {
		t.Errorf("upstream request model = %v, want the fixture's model directory %q", body["model"], c.Model)
	}

	var events []contracttest.Event
	var streamErr *anthropicErrorResponse
	if c.IsStream && !c.IsError() {
		events = contracttest.ParseSSE(c.Body)
		streamErr = streamErrorEvent(events)
	}

	switch {
	case c.IsError():
		if c.IsStream && len(out.stream.Chunks) > 0 {
			t.Errorf("an upstream %d answer produced %d stream chunks", c.Status, len(out.stream.Chunks))
		}
		contracttest.AssertSaneError(t, c, out.err)
	case streamErr != nil:
		assertMidStreamError(t, c, out, events, streamErr)
	case out.err != nil:
		t.Fatalf("adapter error: %v", out.err)
	}
	if events != nil {
		contracttest.AssertKnownEventTypes(t, events, knownStreamEvents...)
		checkEventEnvelopes(t, events)
	}

	contractStrict.Check(t, c)
	contracttest.AssertNoUnknownShapes(t, c, out.shapes)

	switch {
	case c.IsError(), streamErr != nil:
	case c.IsStream:
		contracttest.AssertSane(t, contracttest.FromStream(out.stream.Chunks), streamHasOutput(events))
	default:
		contracttest.AssertSane(t, contracttest.FromResponse(out.resp), responseHasOutput(t, c.Body))
	}
}

// streamErrorEvent is the payload of the stream's error event, or nil when
// it has none (or it is not an error body: the strict checks report that).
func streamErrorEvent(events []contracttest.Event) *anthropicErrorResponse {
	for _, ev := range events {
		if ev.Name != "error" {
			continue
		}
		var e anthropicErrorResponse
		if json.Unmarshal(ev.Data, &e) != nil {
			return &anthropicErrorResponse{}
		}
		return &e
	}
	return nil
}

// assertMidStreamError checks a 200 stream that carries an error event ends
// with that error: the canonical error is judged as if the upstream had
// answered with the status the errors page gives its type (overloaded_error
// is a 529), the upstream's message survives, and the output before the
// error reaches the client.
func assertMidStreamError(t testing.TB, c contracttest.Case, out caseOutput, events []contracttest.Event, e *anthropicErrorResponse) {
	t.Helper()
	status, ok := errorTypeStatus[e.Error.Type]
	if !ok {
		status = http.StatusInternalServerError // an unknown type fails the error-type enum
	}
	asIf := c
	asIf.Status, asIf.Headers = status, nil
	contracttest.AssertSaneError(t, asIf, out.err)
	var apiErr *models.APIError
	if errors.As(out.err, &apiErr) && apiErr.Message != e.Error.Message {
		t.Errorf("the stream ended with message %q; the upstream's error event says %q", apiErr.Message, e.Error.Message)
	}
	if streamHasOutput(events) {
		got := contracttest.FromStream(out.stream.Chunks)
		if got.Content == "" && len(got.ToolCalls) == 0 {
			t.Errorf("the output the upstream streamed before its error event did not reach the client")
		}
	}
}

// checkEventEnvelopes checks every event's data is a JSON object whose type
// is the SSE event name, as the API sends them.
func checkEventEnvelopes(t testing.TB, events []contracttest.Event) {
	t.Helper()
	for _, ev := range events {
		var e struct {
			Type string `json:"type"`
		}
		if err := json.Unmarshal(ev.Data, &e); err != nil {
			t.Errorf("event %q: data is not a JSON object: %v", ev.Name, err)
			continue
		}
		if e.Type != ev.Name {
			t.Errorf("event %q carries data.type %q; the API sends them equal", ev.Name, e.Type)
		}
	}
}

// responseHasOutput reports whether the upstream response carried text or a
// client tool call, which the canonical response must then carry too.
func responseHasOutput(t testing.TB, body []byte) bool {
	t.Helper()
	var resp anthropicResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		t.Fatalf("decoding fixture: %v", err)
	}
	for _, b := range resp.Content {
		if (b.Type == "text" && b.Text != "") || b.Type == "tool_use" {
			return true
		}
	}
	return false
}

// streamHasOutput is responseHasOutput for a stream.
func streamHasOutput(events []contracttest.Event) bool {
	for _, ev := range events {
		var e streamEvent
		if json.Unmarshal(ev.Data, &e) != nil {
			continue
		}
		switch e.Type {
		case "content_block_start":
			var b contentBlockStartData
			if json.Unmarshal(e.ContentBlock, &b) == nil && b.Type == "tool_use" {
				return true
			}
		case "content_block_delta":
			var td textDelta
			if json.Unmarshal(e.Delta, &td) == nil && td.Type == "text_delta" && td.Text != "" {
				return true
			}
		}
	}
	return false
}

// isJSON reports whether a response content type is JSON.
func isJSON(contentType string) bool {
	mt, _, err := mime.ParseMediaType(contentType)
	return err == nil && mt == "application/json"
}
