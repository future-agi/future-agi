package azure

import (
	"context"
	"net/http"
	"net/http/httptest"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/common"
)

// These cover the unknown-shape reports the contract fixtures cannot reach:
// a documented upstream never sends a malformed chunk, and a non-canonical
// finish_reason fails the contract suite's sanity check.

func serveShapes(t *testing.T, contentType, body string) string {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", contentType)
		_, _ = w.Write([]byte(body))
	}))
	t.Cleanup(srv.Close)
	return srv.URL
}

func TestStreamReportsSkippedShapes(t *testing.T) {
	body := ": keep-alive\n\n" +
		"data: {\"id\":\"c1\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"Hi\"},\"finish_reason\":null}]}\n\n" +
		"data:{\"id\":\"c1\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\" lost\"}}]}\n\n" +
		"event: error\n" +
		"data: {not json\n\n" +
		"data: {\"id\":\"c1\",\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"error\"}]}\n\n" +
		"data: [DONE]\n\n"
	p := newTestProvider(t, serveShapes(t, "text/event-stream", body))
	t.Cleanup(func() { _ = p.Close() })
	ctx, rec := common.WithShapeRecorder(context.Background())

	chunks, errs := p.StreamChatCompletion(ctx, &models.ChatCompletionRequest{Model: "dep", Stream: true})
	n := 0
	for range chunks {
		n++
	}
	if err := <-errs; err != nil {
		t.Fatalf("stream error: %v", err)
	}
	if n != 2 {
		t.Errorf("got %d chunks, want 2: reporting must not change what is emitted", n)
	}

	want := []common.UnknownShape{
		{Provider: adapterName, Kind: common.ShapeMalformed, Value: "data line without a space after the colon"},
		{Provider: adapterName, Kind: common.ShapeUnknownEvent, Value: "error"},
		{Provider: adapterName, Kind: common.ShapeMalformed, Value: "chat.completion.chunk"},
		{Provider: adapterName, Kind: common.ShapeUnmappedFinishReason, Value: "error"},
	}
	if got := rec.Shapes(); !reflect.DeepEqual(got, want) {
		t.Errorf("shapes = %+v\nwant     %+v", got, want)
	}
}

func TestChatCompletionReportsUnmappedFinishReason(t *testing.T) {
	body := `{"id":"c1","object":"chat.completion","created":1,"model":"m","choices":[{"index":0,` +
		`"message":{"role":"assistant","content":"x"},"finish_reason":"end_turn"}]}`
	p := newTestProvider(t, serveShapes(t, "application/json", body))
	t.Cleanup(func() { _ = p.Close() })
	ctx, rec := common.WithShapeRecorder(context.Background())

	if _, err := p.ChatCompletion(ctx, &models.ChatCompletionRequest{Model: "dep"}); err != nil {
		t.Fatalf("ChatCompletion: %v", err)
	}
	want := []common.UnknownShape{{Provider: adapterName, Kind: common.ShapeUnmappedFinishReason, Value: "end_turn"}}
	if got := rec.Shapes(); !reflect.DeepEqual(got, want) {
		t.Errorf("shapes = %+v, want %+v", got, want)
	}
}
