package cohere

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
//	go test ./internal/providers/cohere/ -run Contract           # check
//	go test ./internal/providers/cohere/ -run Contract -update   # rewrite goldens

// knownStreamEvents are the v2 Chat stream event types the API reference
// documents (StreamedChatResponseV2). A fixture with any other type fails
// before the adapter is consulted.
var knownStreamEvents = []string{
	"message-start", "content-start", "content-delta", "content-end",
	"tool-plan-delta", "tool-call-start", "tool-call-delta", "tool-call-end",
	"citation-start", "citation-end", "message-end", "debug",
}

// streamDone is the stream terminator the API reference declares for v2 Chat
// (x-fern-streaming terminator), sent as a bare data line after message-end.
const streamDone = "[DONE]"

// streamProtocolFields are stream payload fields the parser does not read
// because, by the protocol, they carry nothing at the point they arrive. They
// hold for every model, so they live here rather than in each meta.json.
var streamProtocolFields = map[string]string{
	"message-start:$.delta.message": `always {role: "assistant"} with empty content, tool_plan, tool_calls and citations; content arrives in later events`,
	"content-start:$.delta.message": "the block's type with empty text or thinking; the content arrives in content-delta",
}

// streamSanityGaps are the sanity failures every stream has today (gap K1 in
// meta.json): message-end carries finish_reason and usage under "delta", and
// the parser reads them from the top level of the event, so a stream never
// reports usage. AssertSaneWithGaps fails once that is fixed, so this list is
// removed with the fix.
var streamSanityGaps = []string{"usage is missing"}

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
	contracttest.AssertNoUnknownFields(t, c, c.Body, reflect.TypeFor[cohereResponse]())
	contracttest.AssertNoUnknownShapes(t, c, shapes.Shapes())
	contracttest.AssertSane(t, contracttest.FromResponse(resp), responseHasOutput(t, c.Body))
}

func contractStream(t *testing.T, c contracttest.Case) {
	events := streamEvents(contracttest.ParseSSE(c.Body))
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
	contracttest.AssertSaneWithGaps(t, c, contracttest.FromStream(res.Chunks), streamHasOutput(t, events), streamSanityGaps...)
}

// streamEvents drops the [DONE] terminator, which is not an event.
func streamEvents(events []contracttest.Event) []contracttest.Event {
	kept := events[:0]
	for _, ev := range events {
		if ev.Name == "" && string(ev.Data) == streamDone {
			continue
		}
		kept = append(kept, ev)
	}
	return kept
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

// streamUnknownFields audits every event against the structs the stream
// parser decodes it with: the envelope as streamEvent, then the delta of each
// translated event as its own type. The parser does not decode the delta of
// message-start, content-start or message-end, so those are audited against
// an empty struct: every field they carry must be a protocol field or a known
// gap. Events the parser does not translate (tool-plan-delta, citation-start)
// are left to the unknown-shape report. Paths are prefixed with the event
// type, e.g. "message-end:$.delta.usage".
func streamUnknownFields(t *testing.T, events []contracttest.Event) []string {
	t.Helper()
	var paths []string
	for _, ev := range events {
		var e streamEvent
		if err := json.Unmarshal(ev.Data, &e); err != nil {
			t.Errorf("event %q: data is not JSON: %v", ev.Name, err)
			continue
		}
		if ev.Name != "" && e.Type != ev.Name {
			t.Errorf("event %q carries data.type %q; the API sends them equal", ev.Name, e.Type)
		}
		root := e.Type + ":$"
		paths = append(paths, contracttest.UnknownFieldsAt(ev.Data, reflect.TypeFor[streamEvent](), root)...)

		var delta reflect.Type
		switch e.Type {
		case "message-start", "content-start", "message-end":
			delta = reflect.TypeFor[struct{}]()
		case "content-delta":
			delta = reflect.TypeFor[contentDelta]()
		case "tool-call-start":
			delta = reflect.TypeFor[toolCallStartDelta]()
		case "tool-call-delta":
			delta = reflect.TypeFor[toolCallDeltaPayload]()
		}
		if delta != nil && len(e.Delta) > 0 {
			paths = append(paths, contracttest.UnknownFieldsAt(e.Delta, delta, root+".delta")...)
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
// tool call, which the canonical response must then carry too.
func responseHasOutput(t *testing.T, body []byte) bool {
	t.Helper()
	var resp cohereResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		t.Fatalf("decoding fixture: %v", err)
	}
	if len(resp.Message.ToolCalls) > 0 {
		return true
	}
	for _, part := range resp.Message.Content {
		if part.Type == "text" && part.Text != "" {
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
		case "tool-call-start":
			return true
		case "content-delta":
			var cd contentDelta
			if json.Unmarshal(e.Delta, &cd) == nil && cd.Message.Content.Text != "" {
				return true
			}
		}
	}
	return false
}
