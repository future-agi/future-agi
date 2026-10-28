package gemini

import (
	"context"
	"encoding/json"
	"fmt"
	"reflect"
	"slices"
	"sort"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/providers/common"
	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

// Contract tests replay each fixture under testdata/contracts/<model>/<case>
// through the real provider (ChatCompletion / StreamChatCompletion against a
// replay server) and check what comes out. See
// internal/providers/contracttest/README.md.
//
//	go test ./internal/providers/gemini/ -run Contract           # check
//	go test ./internal/providers/gemini/ -run Contract -update   # rewrite goldens
//
// Gemini specifics, against the Anthropic template:
//
//   - The stream is data-only SSE (alt=sse): no event names, and a chunk has
//     no type. Every chunk is a whole GenerateContentResponse, decoded with
//     the same geminiResponse as a non-streaming body, so stream and
//     non-stream paths share one root ("$") and one allowlist entry covers
//     both. AssertKnownEventTypes does not apply; instead every event must be
//     unnamed and carry a JSON object.
//   - Parts are an untagged union, so a new part kind shows up as an unknown
//     field on the part ($.candidates[].content.parts[].<kind>) and, when it
//     is the only key, as an unknown_block report named by its keys.
//   - The model goes in the URL path, not the body, so the upstream check is
//     on the path.

// protocolFields are upstream fields the adapter does not decode because, for
// every model, they carry nothing the canonical response could use. They live
// here rather than in each meta.json.
var protocolFields = map[string]string{
	"$.candidates[].index": "the candidate's position; the gateway never sets candidateCount, so there is " +
		"one candidate and it is always index 0, which becomes choice 0",
}

// sanityGaps are cases whose canonical output breaks an AssertSane invariant
// because of a known adapter gap. meta.json cannot allowlist those, so they are
// listed here by case ID. For each, AssertSane must fail exactly once per
// listed message prefix: a different failure is reported as usual, and an
// entry whose gap was fixed fails as stale.
var sanityGaps = map[string]sanityGap{
	"gemini-3.1-flash-lite/prompt_blocked": {
		// "usage is missing" is FromResponse's: it reads usage only beside a
		// choice. The canonical response does carry usage (see the golden).
		failures: []string{`finish_reason "" is not canonical`, "usage is missing"},
		reason: "known gap G4: a blocked prompt (promptFeedback.blockReason, no candidates) becomes a 200 with an " +
			"empty choices array, so an OpenAI client indexing choices[0] fails; there is no " +
			"content_filter finish_reason and the block reason is dropped",
	},
	"gemini-3.1-flash-lite/stream_prompt_blocked": {
		failures: []string{`finish_reason "" is not canonical`, "usage is missing"},
		reason: "known gap G4: a blocked prompt's only chunk has no candidates, so the stream parser emits " +
			"nothing; the client sees an empty stream with no finish_reason, no usage and no error",
	},
	"gemini-3.8-flash/url_context": {
		failures: []string{"usage.total_tokens = 9956, want prompt 31 + completion 291"},
		reason: "known gap G9: toolUsePromptTokenCount (the fetched page content, billed as input) is dropped, " +
			"so prompt_tokens under-counts and total_tokens (Gemini's totalTokenCount, which includes it) " +
			"no longer equals prompt + completion",
	},
}

type sanityGap struct {
	failures []string
	reason   string
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
	contracttest.AssertKnownPaths(t, c, withoutProtocolFields(
		contracttest.UnknownFields(c.Body, reflect.TypeFor[geminiResponse]())))
	contracttest.AssertNoUnknownShapes(t, c, shapes.Shapes())
	assertSane(t, c, contracttest.FromResponse(resp), chunkHasOutput(t, c.Body))
}

func contractStream(t *testing.T, c contracttest.Case) {
	events := contracttest.ParseSSE(c.Body)
	assertDataOnlyChunks(t, events)

	upstream := contracttest.ReplayServer(t, c)
	p := newTestProvider(t, upstream.URL)
	t.Cleanup(func() { _ = p.Close() })
	ctx, shapes := common.WithShapeRecorder(context.Background())

	chunks, errs := p.StreamChatCompletion(ctx, c.Request)
	res := contracttest.DrainStream(t, chunks, errs)
	upstreamFailed := slices.ContainsFunc(events, func(ev contracttest.Event) bool { return isErrorChunk(ev.Data) })
	switch {
	case res.Err != nil && !upstreamFailed:
		t.Fatalf("StreamChatCompletion: %v", res.Err)
	case res.Err == nil && upstreamFailed:
		t.Errorf("the upstream stream carries an error chunk, but the stream ended without an error")
	}

	contracttest.AssertGolden(t, c.GoldenPath(), res)
	assertUpstreamRequest(t, c, upstream)

	var paths []string
	hasOutput := false
	for _, ev := range events {
		if isErrorChunk(ev.Data) {
			// It ends the stream as its error, which the golden pins.
			continue
		}
		paths = append(paths, contracttest.UnknownFields(ev.Data, reflect.TypeFor[geminiResponse]())...)
		hasOutput = hasOutput || chunkHasOutput(t, ev.Data)
	}
	contracttest.AssertKnownPaths(t, c, withoutProtocolFields(paths))
	contracttest.AssertNoUnknownShapes(t, c, shapes.Shapes())
	if res.Err == nil {
		// A failed stream has no finish_reason or usage: its error is the
		// client's signal.
		assertSane(t, c, contracttest.FromStream(res.Chunks), hasOutput)
	}
}

// isErrorChunk reports whether a stream chunk is the {"error": {...}} object
// a failing upstream sends in place of a response chunk.
func isErrorChunk(data []byte) bool {
	var obj struct {
		Error json.RawMessage `json:"error"`
	}
	return json.Unmarshal(data, &obj) == nil && len(obj.Error) > 0 && string(obj.Error) != "null"
}

// assertUpstreamRequest pins the outbound request and checks the fixture is
// filed under the model the gateway actually asked for. Gemini takes the
// model in the URL path.
func assertUpstreamRequest(t *testing.T, c contracttest.Case, upstream *contracttest.Replay) {
	t.Helper()
	req := upstream.Request(t)
	contracttest.AssertGolden(t, c.UpstreamGoldenPath(), req)
	wantPath, wantQuery := "/v1beta/models/"+c.Model+":generateContent", ""
	if c.IsStream {
		wantPath, wantQuery = "/v1beta/models/"+c.Model+":streamGenerateContent", "alt=sse"
	}
	if req.Path != wantPath || req.Query != wantQuery {
		t.Errorf("upstream request went to %s?%s, want %s?%s (the fixture's model directory is %q)",
			req.Path, req.Query, wantPath, wantQuery, c.Model)
	}
}

// assertDataOnlyChunks checks the stream is what alt=sse sends: unnamed events,
// each one JSON object.
func assertDataOnlyChunks(t *testing.T, events []contracttest.Event) {
	t.Helper()
	if len(events) == 0 {
		t.Fatalf("stream fixture has no events")
	}
	for i, ev := range events {
		if ev.Name != "" {
			t.Errorf("event %d is named %q; Gemini's alt=sse stream sends data-only events", i, ev.Name)
		}
		var obj map[string]json.RawMessage
		if err := json.Unmarshal(ev.Data, &obj); err != nil {
			t.Errorf("event %d: data is not a JSON object: %v", i, err)
		}
	}
}

func withoutProtocolFields(paths []string) []string {
	kept := paths[:0]
	for _, p := range paths {
		if _, benign := protocolFields[p]; !benign {
			kept = append(kept, p)
		}
	}
	return kept
}

// chunkHasOutput reports whether a generateContent body, or one stream chunk,
// carried answer text, a function call or inline media in candidate 0, which
// the canonical form must then carry too. Thought summaries are not output.
func chunkHasOutput(t *testing.T, body []byte) bool {
	t.Helper()
	var resp geminiResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		t.Fatalf("decoding fixture: %v", err)
	}
	if len(resp.Candidates) == 0 {
		return false
	}
	for _, p := range resp.Candidates[0].Content.Parts {
		if p.FunctionCall != nil || p.InlineData != nil || (p.Text != "" && !p.Thought) {
			return true
		}
	}
	return false
}

// assertSane is contracttest.AssertSane, except for a case in sanityGaps, which
// must fail it in exactly the listed ways. A live case may fail in fewer of
// them: its real response need not trigger the gap its fixture pinned (a prompt
// the model answers instead of blocking), just as stale allowlist entries are
// not reported in live mode.
func assertSane(t *testing.T, c contracttest.Case, got contracttest.Completion, expectOutput bool) {
	t.Helper()
	gap, known := sanityGaps[c.ID()]
	if !known {
		contracttest.AssertSane(t, got, expectOutput)
		return
	}
	probe := &sanityProbe{TB: t}
	contracttest.AssertSane(probe, got, expectOutput)
	if c.Live {
		for _, f := range probe.failures {
			if !slices.ContainsFunc(gap.failures, func(w string) bool { return strings.HasPrefix(f, w) }) {
				t.Errorf("%s", f)
			}
		}
		return
	}
	sort.Strings(probe.failures)
	want := append([]string(nil), gap.failures...)
	sort.Strings(want)
	matched := len(probe.failures) == len(want)
	for i := 0; matched && i < len(want); i++ {
		matched = strings.HasPrefix(probe.failures[i], want[i])
	}
	if !matched {
		t.Errorf("sanityGaps[%q] expects AssertSane to fail with\n  %s\nbut it failed with\n  %s\n"+
			"If the gap (%s) was fixed, remove the entry; otherwise update it.",
			c.ID(), strings.Join(want, "\n  "), strings.Join(probe.failures, "\n  "), gap.reason)
	}
}

// sanityProbe records AssertSane's failures instead of failing the test.
type sanityProbe struct {
	testing.TB
	failures []string
}

func (p *sanityProbe) Helper() {}

func (p *sanityProbe) Errorf(format string, args ...any) {
	p.failures = append(p.failures, fmt.Sprintf(format, args...))
}
