package azure

import (
	"bytes"
	"context"
	"encoding/json"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/common"
	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

// Contract tests replay each fixture under testdata/contracts/<deployment>/<case>
// through the real provider (ChatCompletion / StreamChatCompletion against a
// replay server) and check what comes out. See
// internal/providers/contracttest/README.md.
//
//	go test ./internal/providers/azure/ -run Contract           # check
//	go test ./internal/providers/azure/ -run Contract -update   # rewrite goldens
//
// Azure routes by deployment, not by the body's model, so a fixture directory
// is the deployment name, which the test checks against the URL path. Name a
// deployment after the model snapshot it serves.
//
// This adapter is a copy of the OpenAI one with Azure's URL and auth: it
// decodes upstream bodies straight into the canonical types. The OpenAI
// package's contract test explains the choices-only audit used here too.

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
	contracttest.AssertKnownPaths(t, c, responseUnknownFields(t, c.Body))
	contracttest.AssertNoUnknownShapes(t, c, shapes.Shapes())
	contracttest.AssertSane(t, contracttest.FromResponse(resp), responseHasOutput(t, c.Body))
}

func contractStream(t *testing.T, c contracttest.Case) {
	choices := streamChoices(t, contracttest.ParseSSE(c.Body))

	upstream := contracttest.ReplayServer(t, c)
	p := newTestProvider(t, upstream.URL)
	t.Cleanup(func() { _ = p.Close() })
	ctx, shapes := common.WithShapeRecorder(context.Background())

	out, errs := p.StreamChatCompletion(ctx, c.Request)
	res := contracttest.DrainStream(t, out, errs)
	if res.Err != nil {
		t.Fatalf("StreamChatCompletion: %v", res.Err)
	}

	contracttest.AssertGolden(t, c.GoldenPath(), res)
	assertUpstreamRequest(t, c, upstream)
	var paths []string
	for _, ch := range choices {
		paths = append(paths, contracttest.UnknownFieldsAt(ch, reflect.TypeFor[[]models.StreamChoice](), "chunk:$.choices")...)
	}
	contracttest.AssertKnownPaths(t, c, paths)
	contracttest.AssertNoUnknownShapes(t, c, shapes.Shapes())
	contracttest.AssertSane(t, contracttest.FromStream(res.Chunks), streamHasOutput(choices))
}

// assertUpstreamRequest pins the outbound request and checks the fixture is
// filed under the deployment the gateway actually called.
func assertUpstreamRequest(t *testing.T, c contracttest.Case, upstream *contracttest.Replay) {
	t.Helper()
	req := upstream.Request(t)
	contracttest.AssertGolden(t, c.UpstreamGoldenPath(), req)
	if want := "/openai/deployments/" + c.Model + "/chat/completions"; req.Path != want {
		t.Errorf("upstream request path = %q, want %q (the fixture's deployment directory)", req.Path, want)
	}
}

// responseUnknownFields audits a whole response's choices against
// []models.Choice, the nested type with no Extra to carry unknown keys.
func responseUnknownFields(t *testing.T, body []byte) []string {
	t.Helper()
	var r struct {
		Choices json.RawMessage `json:"choices"`
	}
	if err := json.Unmarshal(body, &r); err != nil {
		t.Fatalf("decoding fixture: %v", err)
	}
	return contracttest.UnknownFieldsAt(r.Choices, reflect.TypeFor[[]models.Choice](), "$.choices")
}

// streamChoices checks the framing of a data-only stream (no event names,
// JSON objects, [DONE] last when present) and returns each chunk's raw
// choices.
func streamChoices(t *testing.T, events []contracttest.Event) []json.RawMessage {
	t.Helper()
	var out []json.RawMessage
	for i, ev := range events {
		if ev.Name != "" {
			t.Errorf("event %d is named %q; Chat Completions streams are data-only", i, ev.Name)
		}
		if string(ev.Data) == "[DONE]" {
			if i != len(events)-1 {
				t.Errorf("[DONE] is event %d of %d; it ends the stream", i, len(events))
			}
			continue
		}
		var c struct {
			Choices json.RawMessage `json:"choices"`
		}
		if !bytes.HasPrefix(bytes.TrimSpace(ev.Data), []byte("{")) || json.Unmarshal(ev.Data, &c) != nil {
			t.Errorf("event %d: data is not a JSON object: %.80s", i, ev.Data)
			continue
		}
		if len(c.Choices) > 0 {
			out = append(out, c.Choices)
		}
	}
	if len(out) == 0 {
		t.Fatalf("stream fixture has no chunks with choices")
	}
	return out
}

// responseHasOutput reports whether the upstream response carried text or a
// tool call, which the canonical response must then carry too.
func responseHasOutput(t *testing.T, body []byte) bool {
	t.Helper()
	var r struct {
		Choices []struct {
			Message struct {
				Content   *string           `json:"content"`
				ToolCalls []json.RawMessage `json:"tool_calls"`
			} `json:"message"`
		} `json:"choices"`
	}
	if err := json.Unmarshal(body, &r); err != nil {
		t.Fatalf("decoding fixture: %v", err)
	}
	for _, ch := range r.Choices {
		if (ch.Message.Content != nil && *ch.Message.Content != "") || len(ch.Message.ToolCalls) > 0 {
			return true
		}
	}
	return false
}

// streamHasOutput is responseHasOutput for a stream.
func streamHasOutput(choices []json.RawMessage) bool {
	for _, raw := range choices {
		var cs []struct {
			Delta struct {
				Content   *string           `json:"content"`
				ToolCalls []json.RawMessage `json:"tool_calls"`
			} `json:"delta"`
		}
		if json.Unmarshal(raw, &cs) != nil {
			continue
		}
		for _, c := range cs {
			if (c.Delta.Content != nil && *c.Delta.Content != "") || len(c.Delta.ToolCalls) > 0 {
				return true
			}
		}
	}
	return false
}
