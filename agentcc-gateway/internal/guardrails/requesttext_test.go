package guardrails

import (
	"context"
	"encoding/json"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// A tool call's arguments are read in one pass over their bytes, up to
// MaxArgumentsDepth levels of nesting, once for every check on a request,
// and not after a check's context ends.

func nested(depth int, leaf string) string {
	return strings.Repeat(`{"a":`, depth) + leaf + strings.Repeat("}", depth)
}

func nestedList(depth int, leaf string) string {
	return strings.Repeat("[", depth) + leaf + strings.Repeat("]", depth)
}

func TestArguments_ReadUpToTheDepthLimit(t *testing.T) {
	ctx := context.Background()
	for _, tc := range []struct {
		name, args string
		want       string // the innermost text, as read
	}{
		{"objects", nested(MaxArgumentsDepth, `"123-45-6789"`), "a: 123-45-6789"},
		{"lists", nestedList(MaxArgumentsDepth, `"123-45-6789"`), "123-45-6789"},
		{"a member deep in a list", nestedList(MaxArgumentsDepth-1, `{"ssn":"123-45-6789"}`), "ssn: 123-45-6789"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			texts := ArgumentTexts(ctx, tc.args)
			if len(texts) == 0 || texts[len(texts)-1].Text() != tc.want {
				t.Fatalf("read %v, want the innermost text %q", texts, tc.want)
			}
			if r := UnreadableArguments(ctx, chatWithArguments(tc.args)); r != "" {
				t.Errorf("unreadable (%s), want read", r)
			}
		})
	}
}

// Deeper than MaxArgumentsDepth nothing is read, whatever follows: the
// arguments are input the gateway cannot read, which the guardrail plugin
// refuses under a check that blocks or rewrites, arguments nested past
// Go's JSON limit (10000 levels) included.
func TestArguments_DeeperThanTheLimitAreNotRead(t *testing.T) {
	ctx := context.Background()
	for _, tc := range []struct{ name, args string }{
		{"objects", nested(MaxArgumentsDepth+1, `"123-45-6789"`)},
		{"lists", nestedList(MaxArgumentsDepth+1, `"123-45-6789"`)},
		{"objects past Go's JSON limit", nested(10001, `"123-45-6789"`)},
		{"not JSON after the limit", strings.Repeat("[", MaxArgumentsDepth+1) + "123-45-6789 is my SSN"},
		{"a wide object, deep in one member", `{"a":"x","b":` + nested(MaxArgumentsDepth, `"123-45-6789"`) + `}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if texts := ArgumentTexts(ctx, tc.args); texts != nil {
				t.Errorf("read %d texts, want none", len(texts))
			}
			if s := ArgumentsText(ctx, tc.args); s != "" {
				t.Errorf("read %.80q, want nothing", s)
			}
			called := false
			got, changed := RewriteArguments(ctx, tc.args, func(t ArgumentText) ArgumentText {
				called = true
				t.Value = "***"
				return t
			})
			if called || changed || got != tc.args {
				t.Errorf("rewrote arguments it does not read (called %v, changed %v)", called, changed)
			}
			r := UnreadableArguments(ctx, chatWithArguments(tc.args))
			if !strings.Contains(r, "cannot read this request") || !strings.Contains(r, "64 levels") {
				t.Errorf("reason %q, want it to say the request cannot be read and why", r)
			}
		})
	}
}

// What is not one JSON value is one free text, read whole.
func TestArguments_NotJSONIsOneText(t *testing.T) {
	for _, args := range []string{"123-45-6789", "1 2", `{"a":1}x`, `{"a":`, "email alice@example.com"} {
		texts := ArgumentTexts(context.Background(), args)
		if len(texts) != 1 || texts[0].Key != "" || texts[0].Value != args {
			t.Errorf("%q read as %v, want one text, itself", args, texts)
		}
	}
}

// A rewrite keeps every byte it does not rewrite, white space and escapes
// included.
func TestRewriteArguments_KeepsWhatItDoesNotRewrite(t *testing.T) {
	const args = "{ \"to\" : \"alice@example.com\" ,\n \"n\\u0061me\": \"Al\\u0069ce\", \"list\": [ 1, \"bob@example.com\" ], \"ok\": true }"
	got, changed := RewriteArguments(context.Background(), args, func(t ArgumentText) ArgumentText {
		if strings.Contains(t.Value, "@") {
			t.Value = "***"
		}
		return t
	})
	const want = "{ \"to\" : \"***\" ,\n \"n\\u0061me\": \"Al\\u0069ce\", \"list\": [ 1, \"***\" ], \"ok\": true }"
	if !changed || got != want {
		t.Errorf("rewrote to %q, want %q", got, want)
	}
	if !json.Valid([]byte(got)) {
		t.Errorf("not JSON: %s", got)
	}
}

// The texts are read in order, each member with its key, a member whose
// value is an object, a list, true, false or null by its key alone.
func TestArgumentTexts_ReadInOrder(t *testing.T) {
	texts := ArgumentTexts(context.Background(), `{"a":{"b":[1,"x",true,null,{"c":"y"}],"d":false},"e":-1.5e3}`)
	var got []string
	for _, t := range texts {
		got = append(got, t.Text())
	}
	if want := "a|b|1|x|c: y|d|e: -1.5e3"; strings.Join(got, "|") != want {
		t.Errorf("read %q, want %q", strings.Join(got, "|"), want)
	}
}

// A check whose context has ended reads nothing more: what it was reading
// is not read, and not kept for the other checks.
func TestArguments_NotReadOnceTheContextEnds(t *testing.T) {
	ctx, cancel := context.WithCancel(withArgumentsCache(context.Background()))
	cancel()
	args := `{"ssn":"123-45-6789"}`
	if texts := ArgumentTexts(ctx, args); texts != nil {
		t.Errorf("read %v after the context ended", texts)
	}
	if got, changed := RewriteArguments(ctx, args, func(t ArgumentText) ArgumentText { t.Value = "***"; return t }); changed || got != args {
		t.Errorf("rewrote to %s after the context ended", got)
	}
	if p := cacheOf(ctx).read[args]; p != nil {
		t.Errorf("kept %+v, read after the context ended", p)
	}
}

// The checks on one request read each tool call's arguments once: the
// guardrail plugin reads them before the checks run (UnreadableArguments),
// and every check, a config-file rule or an organization's check, sync or
// async, reads what it read.
func TestPlugin_EveryCheckSharesWhatWasReadInTheArguments(t *testing.T) {
	args := `{"ssn":"123-45-6789","note":"file it"}`
	seen := make(chan cacheSeen, 8)
	names := []string{"reader-1", "reader-2", "reader-3"}
	registry := map[string]Guardrail{}
	var rules []config.GuardrailRuleConfig
	for i, name := range names {
		registry[name] = &cacheReader{name: name, args: args, seen: seen}
		mode := "sync"
		if i == 2 {
			mode = "async"
		}
		rules = append(rules, config.GuardrailRuleConfig{Name: name, Stage: "pre", Mode: mode, Action: "log"})
	}
	plugin := NewPlugin(NewEngine(config.GuardrailsConfig{Rules: rules}, registry), registry, nil, nil, nil)
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Request = chatWithArguments(args)
	if r := plugin.ProcessRequest(context.Background(), rc); r.Error != nil {
		t.Fatal(r.Error)
	}
	var first *parsedArguments
	for range names {
		s := <-seen
		if s.cache == nil || s.read == nil {
			t.Fatalf("%s ran with no arguments read before it (cache %v)", s.name, s.cache)
		}
		if first == nil {
			first = s.read
		}
		if s.read != first {
			t.Errorf("%s read the arguments again", s.name)
		}
	}
}

type cacheSeen struct {
	name  string
	cache *argumentsCache
	read  *parsedArguments
}

// cacheReader records what was read in args when it starts, then reads
// them.
type cacheReader struct {
	name string
	args string
	seen chan cacheSeen
}

func (g *cacheReader) Name() string { return g.name }
func (g *cacheReader) Stage() Stage { return StagePre }
func (g *cacheReader) Check(ctx context.Context, input *CheckInput) *CheckResult {
	s := cacheSeen{name: g.name, cache: cacheOf(ctx)}
	if s.cache != nil {
		s.cache.mu.Lock()
		s.read = s.cache.read[g.args]
		s.cache.mu.Unlock()
	}
	for i := range input.Request.Messages {
		MessageText(ctx, &input.Request.Messages[i])
	}
	if p := argumentsFor(ctx, g.args); s.read != nil && p != s.read {
		s.read = nil
	}
	g.seen <- s
	return &CheckResult{Pass: true}
}

// Checks that outlive their timeout and the checks after them read one
// cache at once (run with -race).
func TestArguments_CacheIsSafeForTheChecksGoroutines(t *testing.T) {
	ctx := withArgumentsCache(context.Background())
	var wg sync.WaitGroup
	for i := 0; i < 16; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			args := nested(i%4+1, `"123-45-6789"`)
			for k := 0; k < 50; k++ {
				_ = ArgumentsText(ctx, args)
				_, _ = RewriteArguments(ctx, args, func(t ArgumentText) ArgumentText { t.Value = "***"; return t })
				_ = UnreadableArguments(ctx, chatWithArguments(args))
			}
		}(i)
	}
	wg.Wait()
}

func chatWithArguments(args string) *models.ChatCompletionRequest {
	user, _ := json.Marshal("hi")
	return &models.ChatCompletionRequest{Model: "gpt-4o", Messages: []models.Message{
		{Role: "user", Content: user},
		{Role: "assistant", ToolCalls: []models.ToolCall{{ID: "c", Type: "function",
			Function: models.FunctionCall{Name: "f", Arguments: args}}}},
	}}
}

// BenchmarkArgumentTexts reads arguments 64 levels deep with 16 KB of text
// at every level (1 MB): one pass over them.
func BenchmarkArgumentTexts(b *testing.B) {
	pad := strings.Repeat("x", 16<<10)
	args := strings.Repeat(`{"p":"`+pad+`","a":`, MaxArgumentsDepth) + `"123-45-6789"` + strings.Repeat("}", MaxArgumentsDepth)
	b.SetBytes(int64(len(args)))
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		if len(ArgumentTexts(context.Background(), args)) == 0 {
			b.Fatal("not read")
		}
	}
}
