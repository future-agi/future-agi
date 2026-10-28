package openai

import (
	"context"
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/common"
)

// These cover the chat-over-Responses translator's unknown-shape reports the
// contract fixtures (testdata/contracts/gpt-5.6-sol, endpoint
// chat_via_responses) do not reach. Reporting must not change what is
// translated.

func TestResponsesToChatReportsSkippedShapes(t *testing.T) {
	body := `{"id":"resp_1","status":"incomplete","incomplete_details":{"reason":"max_messages"},"output":[
		{"type":"reasoning","id":"rs_1","summary":[]},
		{"type":"web_search_call","id":"ws_1","status":"completed"},
		{"type":"message","id":"msg_1","content":[{"type":"output_text","text":"Hi"},{"type":"reasoning_text","text":"x"}]}]}`
	var r responsesResponse
	if err := json.Unmarshal([]byte(body), &r); err != nil {
		t.Fatal(err)
	}
	ctx, rec := common.WithShapeRecorder(context.Background())
	got := r.toChatShape(common.NewShapeReporter(ctx, adapterName, "test"))
	if want := r.toChat(); !reflect.DeepEqual(got, want) {
		t.Errorf("reporting changed the translation:\n got %+v\nwant %+v", got, want)
	}
	want := []common.UnknownShape{
		{Provider: adapterName, Kind: common.ShapeUnknownBlock, Value: "reasoning"},
		{Provider: adapterName, Kind: common.ShapeUnknownBlock, Value: "web_search_call"},
		{Provider: adapterName, Kind: common.ShapeUnknownBlock, Value: "message.reasoning_text"},
		{Provider: adapterName, Kind: common.ShapeUnmappedFinishReason, Value: "incomplete:max_messages"},
	}
	if shapes := rec.Shapes(); !reflect.DeepEqual(shapes, want) {
		t.Errorf("shapes = %+v\nwant     %+v", shapes, want)
	}
}

func TestResponsesFinishReasonReportsUnknownStatus(t *testing.T) {
	ctx, rec := common.WithShapeRecorder(context.Background())
	shape := common.NewShapeReporter(ctx, adapterName, "test")
	for _, status := range []string{"completed", "incomplete", ""} {
		(&responsesResponse{Status: status}).finishReasonShape(shape, false)
	}
	if got := (&responsesResponse{Status: "cancelled"}).finishReasonShape(shape, false); got != "stop" {
		t.Errorf("finish reason = %q, want the unchanged default stop", got)
	}
	want := []common.UnknownShape{{Provider: adapterName, Kind: common.ShapeUnmappedFinishReason, Value: "status:cancelled"}}
	if shapes := rec.Shapes(); !reflect.DeepEqual(shapes, want) {
		t.Errorf("shapes = %+v\nwant     %+v", shapes, want)
	}
}

func TestResponsesStreamReportsSkippedShapes(t *testing.T) {
	body := "event: response.created\n" +
		`data: {"type":"response.created","response":{"id":"resp_1","model":"gpt-5.6-sol"}}` + "\n\n" +
		"event: response.in_progress\n" +
		`data: {"type":"response.in_progress","response":{"id":"resp_1"}}` + "\n\n" +
		"event: response.output_item.added\n" +
		`data: {"type":"response.output_item.added","item":{"type":"web_search_call","id":"ws_1"}}` + "\n\n" +
		"event: response.web_search_call.searching\n" +
		`data: {"type":"response.web_search_call.searching","item_id":"ws_1"}` + "\n\n" +
		"event: response.output_text.delta\n" +
		"data: {not json\n\n" +
		"event: response.output_text.delta\n" +
		`data: {"type":"response.output_text.delta","delta":"Hi"}` + "\n\n" +
		"event: response.output_text.done\n" +
		`data: {"type":"response.output_text.done","text":"Hi"}` + "\n\n" +
		"event: response.incomplete\n" +
		`data: {"type":"response.incomplete","response":{"id":"resp_1","status":"incomplete","incomplete_details":{"reason":"steered"}}}` + "\n\n"
	p := newTestProvider(t, serveShapes(t, "text/event-stream", body))
	t.Cleanup(func() { _ = p.Close() })
	ctx, rec := common.WithShapeRecorder(context.Background())

	chunks, errs := p.StreamChatCompletionViaResponses(ctx, &models.ChatCompletionRequest{Model: "gpt-5.6-sol"})
	var content strings.Builder
	var finish string
	for ch := range chunks {
		for _, c := range ch.Choices {
			if c.Delta.Content != nil {
				content.WriteString(*c.Delta.Content)
			}
			if c.FinishReason != nil {
				finish = *c.FinishReason
			}
		}
	}
	if err := <-errs; err != nil {
		t.Fatalf("stream error: %v", err)
	}
	if content.String() != "Hi" || finish != "stop" {
		t.Errorf("content %q, finish %q: reporting must not change what is emitted", content.String(), finish)
	}
	want := []common.UnknownShape{
		{Provider: adapterName, Kind: common.ShapeUnknownBlock, Value: "web_search_call"},
		{Provider: adapterName, Kind: common.ShapeUnknownEvent, Value: "response.web_search_call.searching"},
		{Provider: adapterName, Kind: common.ShapeMalformed, Value: "responses stream event"},
		{Provider: adapterName, Kind: common.ShapeUnmappedFinishReason, Value: "incomplete:steered"},
	}
	if shapes := rec.Shapes(); !reflect.DeepEqual(shapes, want) {
		t.Errorf("shapes = %+v\nwant     %+v", shapes, want)
	}
}
