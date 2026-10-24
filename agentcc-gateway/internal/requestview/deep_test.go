package requestview

import (
	"context"
	"encoding/json"
	"runtime"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// A body is read up to 64 levels deep: an object or a list inside 64 others
// is not read. Where a reader walks a value of its own (an item, part or
// block of a type it does not know, a Gemini function response, a block in
// a block), a value nested deeper is not read, and the view says why, so a
// check that would block or rewrite the request refuses it; what the body
// holds above it is read, for a check that only logs.

// chatOf is v's chat request and the reason it gives.
func chatOf(v *View) (*models.ChatCompletionRequest, string) {
	return v.ChatRequest(context.Background())
}

// nest is inner inside levels objects, each {"a": ...}.
func nest(levels int, inner string) string {
	return strings.Repeat(`{"a":`, levels) + inner + strings.Repeat("}", levels)
}

// deepSlot is a reader with a place in its body for a value it walks on its
// own: body(v) puts v there, after a message whose text is "hello", and
// start is how many objects and lists hold that place.
type deepSlot struct {
	name  string
	view  func(body []byte) *View
	body  func(v string) string
	start int
}

var deepSlots = []deepSlot{
	{"responses, an item of a type the reader does not know", Responses, func(v string) string {
		return `{"model":"m","input":[{"role":"user","content":"hello"},{"type":"zz_unknown","x":` + v + `}]}`
	}, 3},
	{"responses, a content part of a type the reader does not know", Responses, func(v string) string {
		return `{"model":"m","input":[{"role":"user","content":"hello"},{"type":"message","role":"user","content":[{"type":"zz_unknown","x":` + v + `}]}]}`
	}, 5},
	{"threads, a content part of a type the reader does not know", Thread, func(v string) string {
		return `{"messages":[{"role":"user","content":"hello"},{"role":"user","content":[{"type":"zz_unknown","x":` + v + `}]}]}`
	}, 5},
	{"messages, a block of a type the reader does not know", Anthropic, func(v string) string {
		return `{"model":"m","max_tokens":1,"messages":[{"role":"user","content":"hello"},{"role":"user","content":[{"type":"zz_unknown","x":` + v + `}]}]}`
	}, 5},
	{"generateContent, a function response", func(b []byte) *View { return Gemini(b, "g", false) }, func(v string) string {
		return `{"contents":[{"role":"user","parts":[{"text":"hello"}]},{"role":"user","parts":[{"functionResponse":{"name":"f","response":` + v + `}}]}]}`
	}, 6},
	{"countTokens, a function response in generateContentRequest", func(b []byte) *View { return GeminiCountTokens(b, "g").View }, func(v string) string {
		return `{"generateContentRequest":{"contents":[{"role":"user","parts":[{"text":"hello"}]},{"role":"user","parts":[{"functionResponse":{"name":"f","response":` + v + `}}]}]}}`
	}, 7},
}

// readTexts is every text of req's messages, joined.
func readTexts(req *models.ChatCompletionRequest) string {
	var b strings.Builder
	for _, m := range req.Messages {
		s, _ := contentText(m.Content)
		b.WriteString(s + "\n")
	}
	return b.String()
}

const deepSecret = "SECRET-123-45-6789"

// At the limit, the innermost object is inside 63 others and its text is
// read; one level more and it is not, and the view says why while the rest
// of the body is read.
func TestView_ReadsUpTo64LevelsDeep(t *testing.T) {
	inner := `{"text":"` + deepSecret + `"}`
	for _, s := range deepSlots {
		t.Run(s.name, func(t *testing.T) {
			req, reason := chatOf(s.view([]byte(s.body(nest(63-s.start, inner)))))
			if req == nil || reason != "" {
				t.Fatalf("at the limit: unread (%q)", reason)
			}
			if !strings.Contains(readTexts(req), deepSecret) {
				t.Errorf("at the limit: the innermost text is not read: %q", readTexts(req))
			}

			req, reason = chatOf(s.view([]byte(s.body(nest(64-s.start, inner)))))
			if !strings.Contains(reason, "nested more than 64 levels deep") {
				t.Errorf("one level past the limit: reason %q, want one that says the body is nested more than 64 levels deep", reason)
			}
			if req == nil {
				t.Fatalf("one level past the limit: nothing read (%q), want what is above it", reason)
			}
			if texts := readTexts(req); !strings.Contains(texts, "hello") || strings.Contains(texts, deepSecret) {
				t.Errorf("one level past the limit: read %q, want hello and not the text past the limit", texts)
			}
		})
	}
}

// Blocks in blocks (a tool result's content, a search result's, a
// document's) are read to the same limit.
func TestAnthropic_BlocksInBlocksAreReadUpTo64LevelsDeep(t *testing.T) {
	// Tool result j of the chain is inside 4+2j objects and lists; the text
	// block inside n of them is inside 4+2n.
	chain := func(n int) []byte {
		return []byte(`{"model":"m","max_tokens":1,"messages":[{"role":"user","content":"hello"},{"role":"user","content":[` +
			strings.Repeat(`{"type":"tool_result","tool_use_id":"t","content":[`, n) +
			`{"type":"text","text":"` + deepSecret + `"}` + strings.Repeat(`]}`, n) + `]}]}`)
	}
	req, reason := chatOf(Anthropic(chain(29)))
	if req == nil || reason != "" || !strings.Contains(readTexts(req), deepSecret) {
		t.Fatalf("at the limit (a text block inside 62): reason %q, read %v", reason, req != nil)
	}
	req, reason = chatOf(Anthropic(chain(30)))
	if !strings.Contains(reason, "nested more than 64 levels deep") {
		t.Errorf("past the limit (a text block inside 64): reason %q, want one that says the body is nested more than 64 levels deep", reason)
	}
	if req == nil || strings.Contains(readTexts(req), deepSecret) || !strings.Contains(readTexts(req), "hello") {
		t.Errorf("past the limit: want hello read and the text past the limit not read")
	}
}

// Values 9000 levels deep, one or several in a body:
// Each is read above the limit, and the view says why it stops there.
func TestView_ValuesNested9000DeepAreReadToTheLimit(t *testing.T) {
	deep := nest(9000, `"`+deepSecret+`"`)
	for _, s := range deepSlots {
		t.Run(s.name, func(t *testing.T) {
			req, reason := chatOf(s.view([]byte(s.body(deep))))
			if !strings.Contains(reason, "nested more than 64 levels deep") {
				t.Errorf("reason %q, want one that says the body is nested more than 64 levels deep", reason)
			}
			if req == nil || !strings.Contains(readTexts(req), "hello") {
				t.Errorf("the text above the limit is not read")
			}
		})
	}
	items := make([]string, 4)
	for i := range items {
		items[i] = `{"type":"zz_unknown","x":` + deep + `}`
	}
	body := `{"model":"m","input":[{"role":"user","content":"hello"},` + strings.Join(items, ",") + `]}`
	req, reason := chatOf(Responses([]byte(body)))
	if !strings.Contains(reason, "nested more than 64 levels deep") || req == nil {
		t.Errorf("four items 9000 deep: reason %q, read %v", reason, req != nil)
	}
}

// allocated is how many bytes f allocates.
func allocated(f func()) uint64 {
	var before, after runtime.MemStats
	runtime.GC()
	runtime.ReadMemStats(&before)
	f()
	runtime.ReadMemStats(&after)
	return after.TotalAlloc - before.TotalAlloc
}

// bigText is a text of n bytes ending in the probe's secret.
func bigText(n int) string {
	return strings.Repeat("lorem ipsum ", n/12) + deepSecret
}

// A read decodes each object and list of the body once: what it allocates
// grows with the body, not with the body times its depth. Each body here
// holds a 2 MB text 50 levels deep (within the limit), where a reader walks
// it on its own.
func TestView_ReadDecodesEachValueOnce(t *testing.T) {
	text, _ := json.Marshal(bigText(2 << 20))
	bodies := map[string]func() *View{
		"responses, an item of a type the reader does not know": func() *View {
			return Responses([]byte(`{"model":"m","input":[{"type":"zz_unknown","x":` + nest(50, `{"text":`+string(text)+`}`) + `}]}`))
		},
		"generateContent, a function response": func() *View {
			return Gemini([]byte(`{"contents":[{"role":"user","parts":[{"functionResponse":{"name":"f","response":`+nest(50, `{"text":`+string(text)+`}`)+`}}]}]}`), "g", false)
		},
		"messages, blocks in blocks": func() *View {
			return Anthropic([]byte(`{"model":"m","max_tokens":1,"messages":[{"role":"user","content":[` +
				strings.Repeat(`{"type":"tool_result","tool_use_id":"t","content":[`, 25) +
				`{"type":"text","text":` + string(text) + `}` + strings.Repeat(`]}`, 25) + `]}]}`))
		},
	}
	for name, view := range bodies {
		t.Run(name, func(t *testing.T) {
			v := view()
			var req *models.ChatCompletionRequest
			var reason string
			n := allocated(func() { req, reason = chatOf(v) })
			if req == nil || reason != "" || !strings.Contains(readTexts(req), deepSecret) {
				t.Fatalf("not read: %q", reason)
			}
			if limit := uint64(16 * len(v.Body())); n > limit {
				t.Errorf("the read allocated %d bytes for a body of %d, want at most %d (16 times the body)", n, len(v.Body()), limit)
			}
		})
	}
}

// Writing a rewrite into the body writes each value once too, however deep
// the text it rewrites.
func TestApplyChecks_WritesEachValueOnce(t *testing.T) {
	text := bigText(2 << 20)
	quoted, _ := json.Marshal(text)
	v := Responses([]byte(`{"model":"m","input":[{"type":"zz_unknown","x":` + nest(50, `{"text":`+string(quoted)+`}`) + `}]}`))
	req, reason := chatOf(v)
	if req == nil || reason != "" || len(req.Messages) != 1 {
		t.Fatalf("not read: %q", reason)
	}
	masked := strings.Replace(text, deepSecret, "SECRET-***", 1)
	req.Messages[0].Content, _ = json.Marshal(masked)
	var err error
	n := allocated(func() { err = v.ApplyChecks() })
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(v.Body()), deepSecret) || !strings.Contains(string(v.Body()), "SECRET-***") {
		t.Fatalf("the rewrite is not in the body")
	}
	if limit := uint64(16 * len(v.Body())); n > limit {
		t.Errorf("the rewrite allocated %d bytes for a body of %d, want at most %d (16 times the body)", n, len(v.Body()), limit)
	}
}

// The depth limit applies where a reader walks a value: a body nested 100
// deep elsewhere is read in full, and ChatRequest gives no reason.
func TestView_DepthLimitIsWhereAReaderWalks(t *testing.T) {
	deep := nest(100, `"x"`)
	for name, v := range map[string]*View{
		"responses":       Responses([]byte(`{"model":"m","metadata":` + deep + `,"input":"hello","tools":[{"type":"function","name":"lookup","parameters":` + deep + `}]}`)),
		"messages":        Anthropic([]byte(`{"model":"m","max_tokens":1,"metadata":` + deep + `,"messages":[{"role":"user","content":"hello"}],"tools":[{"name":"lookup","input_schema":` + deep + `}]}`)),
		"generateContent": Gemini([]byte(`{"contents":[{"role":"user","parts":[{"text":"hello"}]}],"labels":`+deep+`,"tools":[{"functionDeclarations":[{"name":"lookup","parameters":`+deep+`}]}]}`), "g", false),
		"threads":         Thread([]byte(`{"metadata":` + deep + `,"messages":[{"role":"user","content":"hello"}]}`)),
	} {
		t.Run(name, func(t *testing.T) {
			req, reason := chatOf(v)
			if req == nil || reason != "" || !strings.Contains(readTexts(req), "hello") {
				t.Fatalf("read %v, reason %q; want the body read in full", req != nil, reason)
			}
			if name != "threads" && (len(req.Tools) != 1 || req.Tools[0].Function.Name != "lookup") {
				t.Errorf("tools %+v, want lookup", req.Tools)
			}
		})
	}
}
