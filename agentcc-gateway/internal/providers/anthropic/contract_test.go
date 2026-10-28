package anthropic

import (
	"context"
	"encoding/json"
	"reflect"
	"testing"

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

// translatedBlockTypes are the content_block_start types whose payload the
// stream parser reads. Other types are either reported as unknown shapes or
// skipped on purpose (server tool blocks), so their fields are not audited.
var translatedBlockTypes = map[string]bool{"text": true, "tool_use": true}

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

func TestContract(t *testing.T) {
	for _, c := range contracttest.Discover(t, adapterName) {
		t.Run(c.ID(), func(t *testing.T) {
			t.Parallel()
			if c.IsStream {
				contractStream(t, c)
			} else {
				contractJSON(t, c)
			}
		})
	}
}

func contractJSON(t *testing.T, c contracttest.Case) {
	upstream := contracttest.ReplayServer(t, c)
	p := newTestProvider(t, upstream.URL)
	t.Cleanup(func() { _ = p.Close() })
	ctx, shapes := common.WithShapeRecorder(context.Background())

	resp, err := p.ChatCompletion(ctx, c.Request)
	if err != nil {
		t.Fatalf("ChatCompletion: %v", err)
	}

	contracttest.AssertGolden(t, c.GoldenPath(), resp)
	assertUpstreamRequest(t, c, upstream)

	paths := contracttest.UnknownFields(c.Body, reflect.TypeFor[anthropicResponse]())
	paths = append(paths, citationUnknownFields(t, c.Body)...)
	contracttest.AssertKnownPaths(t, c, paths)
	contracttest.AssertNoUnknownShapes(t, c, shapes.Shapes())
	contracttest.AssertSane(t, contracttest.FromResponse(resp), responseHasOutput(t, c.Body))
}

func contractStream(t *testing.T, c contracttest.Case) {
	events := contracttest.ParseSSE(c.Body)
	contracttest.AssertKnownEventTypes(t, events, knownStreamEvents...)

	upstream := contracttest.ReplayServer(t, c)
	p := newTestProvider(t, upstream.URL)
	t.Cleanup(func() { _ = p.Close() })
	ctx, shapes := common.WithShapeRecorder(context.Background())

	chunks, errs := p.StreamChatCompletion(ctx, c.Request)
	res := contracttest.DrainStream(t, chunks, errs)
	if res.Err != nil {
		t.Fatalf("StreamChatCompletion: %v", res.Err)
	}

	contracttest.AssertGolden(t, c.GoldenPath(), res)
	assertUpstreamRequest(t, c, upstream)
	contracttest.AssertKnownPaths(t, c, streamUnknownFields(t, events))
	contracttest.AssertNoUnknownShapes(t, c, shapes.Shapes())
	contracttest.AssertSane(t, contracttest.FromStream(res.Chunks), streamHasOutput(t, events))
}

// assertUpstreamRequest pins the outbound request and checks the fixture is
// filed under the model the gateway actually asked for.
func assertUpstreamRequest(t *testing.T, c contracttest.Case, upstream *contracttest.Replay) {
	t.Helper()
	req := upstream.Request(t)
	contracttest.AssertGolden(t, c.UpstreamGoldenPath(), req)
	body, _ := req.Body.(map[string]any)
	if body["model"] != c.Model {
		t.Errorf("upstream request model = %v, want the fixture's model directory %q", body["model"], c.Model)
	}
}

// citationUnknownFields audits citations, which anthropicContentBlock keeps
// as raw JSON and citationsForBlock decodes into anthropicCitation later.
func citationUnknownFields(t *testing.T, body []byte) []string {
	t.Helper()
	var resp anthropicResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		t.Fatalf("decoding fixture: %v", err)
	}
	var paths []string
	for _, b := range resp.Content {
		if len(b.Citations) > 0 {
			paths = append(paths, contracttest.UnknownFieldsAt(b.Citations,
				reflect.TypeFor[[]anthropicCitation](), "$.content[].citations")...)
		}
	}
	return paths
}

// streamUnknownFields audits every event against the structs the stream
// parser decodes it with: the envelope as streamEvent, then each raw payload
// the parser goes on to read as its own type. Paths are prefixed with the
// event type, e.g. "message_delta:$.delta.stop_details".
func streamUnknownFields(t *testing.T, events []contracttest.Event) []string {
	t.Helper()
	var paths []string
	for _, ev := range events {
		var e streamEvent
		if err := json.Unmarshal(ev.Data, &e); err != nil {
			t.Errorf("event %q: data is not JSON: %v", ev.Name, err)
			continue
		}
		if e.Type != ev.Name {
			t.Errorf("event %q carries data.type %q; the API sends them equal", ev.Name, e.Type)
		}
		root := ev.Name + ":$"
		paths = append(paths, contracttest.UnknownFieldsAt(ev.Data, reflect.TypeFor[streamEvent](), root)...)

		switch e.Type {
		case "message_start":
			paths = append(paths, contracttest.UnknownFieldsAt(e.Message, reflect.TypeFor[messageStartData](), root+".message")...)
		case "content_block_start":
			var b contentBlockStartData
			if json.Unmarshal(e.ContentBlock, &b) == nil && translatedBlockTypes[b.Type] {
				paths = append(paths, contracttest.UnknownFieldsAt(e.ContentBlock, reflect.TypeFor[contentBlockStartData](), root+".content_block")...)
			}
		case "content_block_delta":
			var d struct {
				Type string `json:"type"`
			}
			_ = json.Unmarshal(e.Delta, &d)
			switch d.Type {
			case "text_delta":
				paths = append(paths, contracttest.UnknownFieldsAt(e.Delta, reflect.TypeFor[textDelta](), root+".delta")...)
			case "input_json_delta":
				paths = append(paths, contracttest.UnknownFieldsAt(e.Delta, reflect.TypeFor[inputJSONDelta](), root+".delta")...)
			}
		case "message_delta":
			paths = append(paths, contracttest.UnknownFieldsAt(e.Delta, reflect.TypeFor[messageDeltaData](), root+".delta")...)
		}
	}

	kept := paths[:0]
	for _, p := range paths {
		if _, benign := streamProtocolFields[p]; !benign {
			kept = append(kept, p)
		}
	}
	return kept
}

// responseHasOutput reports whether the upstream response carried text or a
// client tool call, which the canonical response must then carry too.
func responseHasOutput(t *testing.T, body []byte) bool {
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
func streamHasOutput(t *testing.T, events []contracttest.Event) bool {
	t.Helper()
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
