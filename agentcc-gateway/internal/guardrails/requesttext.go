package guardrails

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"sync"
	"unicode"
	"unicode/utf8"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The text a check on the request reads in a message: its content, and the
// arguments of the tool calls it makes. A check that reads every message's
// text (pii-detection, secret-detection, keyword-blocklist,
// content-moderation, topic-restriction, input-validation's patterns, the
// external and FutureAGI checks) reads it with MessageText, so a value in a
// tool call's arguments is read as a value in a message is. A tool call's
// name is not read: it names a
// tool the request offers, which Tool Permissions reads.
//
// A tool call's arguments are read in one pass over their bytes
// (parseArguments), which stops when ctx ends, so a check that outlives its
// timeout stops reading them. The checks on one request share what was read
// (argumentsCache, carried by ctx), so each tool call's arguments are read
// once whatever the number of checks. Arguments nested deeper than
// MaxArgumentsDepth are not read: a check on the request that would block
// or rewrite such a request refuses it (UnreadableArguments, and the
// guardrail plugin's request_unavailable).

// MaxArgumentsDepth is how deep the checks read a tool call's arguments
// that are JSON: an object or a list inside MaxArgumentsDepth others is not
// read.
const MaxArgumentsDepth = 64

// ContentText is the text of a message's content as a check reads it: a
// string itself, or the text of its text parts joined with a space. ok is
// false when the content holds no text.
func ContentText(raw json.RawMessage) (string, bool) {
	if len(raw) == 0 {
		return "", false
	}
	var s string
	if err := json.Unmarshal(raw, &s); err == nil {
		return s, true
	}
	var parts []struct {
		Type string `json:"type"`
		Text string `json:"text"`
	}
	if err := json.Unmarshal(raw, &parts); err == nil {
		var texts []string
		for _, p := range parts {
			if p.Type == "text" && p.Text != "" {
				texts = append(texts, p.Text)
			}
		}
		if len(texts) > 0 {
			return strings.Join(texts, " "), true
		}
	}
	return "", false
}

// MessageText is the text a check on the request reads in m: its content's
// text (ContentText), then the arguments of each tool call it makes
// (ArgumentsText), each on a line of its own. ok is false when m holds no
// text.
func MessageText(ctx context.Context, m *models.Message) (string, bool) {
	text, ok := ContentText(m.Content)
	if len(m.ToolCalls) == 0 {
		return text, ok
	}
	parts := make([]string, 0, 1+len(m.ToolCalls))
	if ok && text != "" {
		parts = append(parts, text)
	}
	for _, tc := range m.ToolCalls {
		if args := ArgumentsText(ctx, tc.Function.Arguments); args != "" {
			parts = append(parts, args)
		}
	}
	if len(parts) == 0 {
		return "", false
	}
	return strings.Join(parts, "\n"), true
}

// ArgumentText is one text of a tool call's arguments, as a check reads it.
// In arguments that are JSON it is a member of an object, read with its key
// (Key, and Value when HasValue: a string as the provider decodes it, or a
// number as written), a member whose value is an object, a list, true,
// false or null, read by its key alone, or an item of a list (or the
// arguments' only value), read by its value alone. Arguments that are not
// JSON (a custom tool's free text) are one text, their Value.
type ArgumentText struct {
	Key      string
	Value    string
	HasValue bool
}

// Text is what a check reads: "key: value", the key alone, or the value
// alone. A pattern that needs a keyword (a passport number, a password)
// matches a member whose key is that keyword.
func (a ArgumentText) Text() string {
	switch {
	case a.HasValue && a.Key != "":
		return a.Key + ": " + a.Value
	case a.HasValue:
		return a.Value
	default:
		return a.Key
	}
}

// ArgumentsText is the text a check reads in a tool call's arguments: the
// Text of each of its texts (ArgumentTexts), one per line.
func ArgumentsText(ctx context.Context, arguments string) string {
	p := argumentsFor(ctx, arguments)
	if p == nil {
		return ""
	}
	p.textOnce.Do(func() {
		switch len(p.texts) {
		case 0:
		case 1:
			p.text = p.texts[0].Text()
		default:
			lines := make([]string, len(p.texts))
			for i, t := range p.texts {
				lines[i] = t.Text()
			}
			p.text = strings.Join(lines, "\n")
		}
	})
	return p.text
}

// ArgumentTexts is each text of a tool call's arguments, in order, at every
// depth up to MaxArgumentsDepth. JSON escapes are decoded, so text written
// with them is read as the provider reads it. Every member of a repeated key
// is read. It is nil for arguments nested deeper than MaxArgumentsDepth,
// which are not read, and when ctx ends before they are read.
func ArgumentTexts(ctx context.Context, arguments string) []ArgumentText {
	p := argumentsFor(ctx, arguments)
	if p == nil || len(p.texts) == 0 {
		return nil
	}
	return append([]ArgumentText(nil), p.texts...) // the checks sharing p read p.texts
}

// RewriteArguments is arguments with each of its texts as f rewrites it (f
// returns the text's key and value, as given when it leaves them). Arguments
// that are JSON stay JSON: a rewritten key or string is written as a JSON
// string, a rewritten number as a string (so a number a check masks is not
// sent), and every byte f leaves inside the arguments' JSON value is kept,
// white space and escapes included, so keys keep their order; white space
// before and after that value is not kept once f rewrites anything. A key f rewrites on a list item or the only value is not
// written: such a text has no key of its own. Arguments that are not JSON
// are one text, rewritten whole. When f rewrites nothing, arguments is
// returned as given, byte for byte, and changed is false; so it is for
// arguments the checks do not read (nested deeper than MaxArgumentsDepth,
// which the guardrail plugin refuses before a check that rewrites runs), and
// when ctx ends before they are read: what is not read is never rewritten.
func RewriteArguments(ctx context.Context, arguments string, f func(ArgumentText) ArgumentText) (rewritten string, changed bool) {
	p := argumentsFor(ctx, arguments)
	if p == nil {
		return arguments, false
	}
	switch p.kind {
	case argumentsEmpty, argumentsTooDeep:
		return arguments, false
	case argumentsText:
		out := f(p.texts[0])
		return out.Value, out.Value != arguments
	}
	var b strings.Builder
	last := p.start
	for i, t := range p.texts {
		got := f(t)
		s := p.spans[i]
		if s.keyEnd > 0 && got.Key != t.Key {
			b.WriteString(arguments[last:s.keyStart])
			b.Write(marshalText(got.Key))
			last, changed = s.keyEnd, true
		}
		if s.valueEnd > 0 && got.Value != t.Value {
			b.WriteString(arguments[last:s.valueStart])
			b.Write(marshalText(got.Value))
			last, changed = s.valueEnd, true
		}
	}
	if !changed {
		return arguments, false
	}
	b.WriteString(arguments[last:p.end])
	return b.String(), true
}

// UnreadableArguments says why the checks cannot read the arguments of a
// tool call in req: they are nested deeper than MaxArgumentsDepth. It is ""
// when every tool call's arguments can be read, and when ctx ends first.
// What it reads is kept for the checks that share ctx.
func UnreadableArguments(ctx context.Context, req *models.ChatCompletionRequest) string {
	if req == nil {
		return ""
	}
	for i := range req.Messages {
		for _, tc := range req.Messages[i].ToolCalls {
			if p := argumentsFor(ctx, tc.Function.Arguments); p != nil && p.kind == argumentsTooDeep {
				// The depth is checked before the arguments are known to be
				// JSON, so free text that begins with more brackets than that
				// is refused the same way, and the message says so.
				return fmt.Sprintf("the gateway cannot read this request: the arguments of a tool call are nested more than %d levels deep or begin with more than %d [ or { brackets", MaxArgumentsDepth, MaxArgumentsDepth)
			}
		}
	}
	return ""
}

// makesToolCalls says whether a message of req makes a tool call with
// arguments: whether its checks have arguments to share (argumentsCache).
func makesToolCalls(req *models.ChatCompletionRequest) bool {
	if req == nil {
		return false
	}
	for i := range req.Messages {
		for _, tc := range req.Messages[i].ToolCalls {
			if tc.Function.Arguments != "" {
				return true
			}
		}
	}
	return false
}

// argumentsKind is what a tool call's arguments are, as the checks read them.
type argumentsKind uint8

const (
	argumentsEmpty   argumentsKind = iota // white space only: nothing to read
	argumentsJSON                         // a JSON value, read text by text
	argumentsText                         // not JSON: one text
	argumentsTooDeep                      // JSON nested deeper than MaxArgumentsDepth: not read
)

// parsedArguments is a tool call's arguments as the checks read them: their
// texts, and for JSON where each text's key and value are (spans, byte
// offsets into the arguments, one per text), between start and end, the
// bounds of the JSON value.
type parsedArguments struct {
	kind       argumentsKind
	texts      []ArgumentText
	spans      []textSpan
	start, end int

	// text is ArgumentsText's, made once for every check that reads it.
	textOnce sync.Once
	text     string
}

// textSpan is where a text's key and its value (a string or a number) are
// in the arguments: [keyStart, keyEnd) and [valueStart, valueEnd), each
// with its quotes. An end of 0 means the text has none.
type textSpan struct {
	keyStart, keyEnd     int
	valueStart, valueEnd int
}

// argumentsCache is what the checks on one request read in its tool calls'
// arguments, by the arguments: each is read once, by the first check (or by
// UnreadableArguments), and the others share it. A check that rewrites
// arguments makes new ones, read once in turn. It is safe for the checks'
// goroutines, a check that outlived its timeout included; what ctx ending
// cut short is not kept.
type argumentsCache struct {
	mu   sync.Mutex
	read map[string]*parsedArguments
}

type argumentsCacheKey struct{}

func newArgumentsCache() *argumentsCache {
	return &argumentsCache{read: make(map[string]*parsedArguments)}
}

// newArgumentsCacheFor is a new argumentsCache when req makes a tool call
// with arguments, else nil: there is nothing to share.
func newArgumentsCacheFor(req *models.ChatCompletionRequest) *argumentsCache {
	if !makesToolCalls(req) {
		return nil
	}
	return newArgumentsCache()
}

// withArgumentsCache is ctx with a new argumentsCache, for the checks of one
// stage of one request.
func withArgumentsCache(ctx context.Context) context.Context {
	return withCache(ctx, newArgumentsCache())
}

// withCache is ctx with c as its argumentsCache; ctx itself when c is nil.
func withCache(ctx context.Context, c *argumentsCache) context.Context {
	if c == nil {
		return ctx
	}
	return context.WithValue(ctx, argumentsCacheKey{}, c)
}

// cacheOf is ctx's argumentsCache, nil when it has none.
func cacheOf(ctx context.Context) *argumentsCache {
	if ctx == nil {
		return nil
	}
	c, _ := ctx.Value(argumentsCacheKey{}).(*argumentsCache)
	return c
}

// argumentsFor is arguments as the checks read them: from ctx's
// argumentsCache when a check sharing ctx read them, else read now (and
// kept there). It is nil when ctx ends before they are read.
func argumentsFor(ctx context.Context, arguments string) *parsedArguments {
	c := cacheOf(ctx)
	if c != nil {
		c.mu.Lock()
		p := c.read[arguments]
		c.mu.Unlock()
		if p != nil {
			return p
		}
	}
	p := parseArguments(ctx, arguments)
	if p != nil && c != nil {
		c.mu.Lock()
		c.read[arguments] = p
		c.mu.Unlock()
	}
	return p
}

// ctxCheckEvery is how many tokens parseArguments reads between two looks
// at ctx.
const ctxCheckEvery = 256

// parseArguments reads arguments in one pass over their bytes, keeping the
// key of each object it is in: JSON is read text by text (ArgumentTexts),
// unless it is nested deeper than MaxArgumentsDepth, which it finds before
// it reads anything past that depth; what is not one JSON value (json.Valid)
// is one text. A string is read as the provider decodes it, its escapes
// decoded. It is nil when ctx ends first.
func parseArguments(ctx context.Context, arguments string) *parsedArguments {
	start := len(arguments) - len(strings.TrimLeftFunc(arguments, unicode.IsSpace))
	s := strings.TrimRightFunc(arguments[start:], unicode.IsSpace)
	if s == "" {
		return &parsedArguments{kind: argumentsEmpty}
	}
	notJSON := &parsedArguments{kind: argumentsText, texts: []ArgumentText{{Value: arguments, HasValue: true}}}

	// frame is an object or a list the pass is in: for an object, whether
	// the next string is a key, and the key of the member being read.
	type frame struct {
		object, wantKey  bool
		key              string
		keyStart, keyEnd int
	}
	p := &parsedArguments{kind: argumentsJSON, start: start, end: start + len(s)}
	var stack []frame
	// member is the text of the member, of the object on top of the stack,
	// whose value starts now, read by its key; the next string is a key.
	member := func() (ArgumentText, textSpan, bool) {
		if len(stack) == 0 || !stack[len(stack)-1].object {
			return ArgumentText{}, textSpan{}, false
		}
		top := &stack[len(stack)-1]
		top.wantKey = true
		return ArgumentText{Key: top.key}, textSpan{keyStart: start + top.keyStart, keyEnd: start + top.keyEnd}, true
	}
	// value reads a string or a number, [i, j) in s, as text.
	value := func(text string, i, j int) {
		span := textSpan{valueStart: start + i, valueEnd: start + j}
		t, ms, ok := member()
		if ok {
			span.keyStart, span.keyEnd = ms.keyStart, ms.keyEnd
		}
		t.Value, t.HasValue = text, true
		p.texts = append(p.texts, t)
		p.spans = append(p.spans, span)
	}

	for i, n := 0, 0; i < len(s); n++ {
		if n%ctxCheckEvery == 0 && ctx != nil && ctx.Err() != nil {
			return nil
		}
		switch c := s[i]; {
		case c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == ',' || c == ':':
			i++
		case c == '{' || c == '[':
			if t, span, ok := member(); ok {
				p.texts = append(p.texts, t)
				p.spans = append(p.spans, span)
			}
			stack = append(stack, frame{object: c == '{', wantKey: c == '{'})
			if len(stack) > MaxArgumentsDepth {
				return &parsedArguments{kind: argumentsTooDeep}
			}
			i++
		case c == '}' || c == ']':
			if len(stack) == 0 {
				return notJSON
			}
			stack = stack[:len(stack)-1]
			i++
		case c == '"':
			j := stringEnd(s, i)
			if j < 0 {
				return notJSON
			}
			text, ok := decodeString(s[i:j])
			if !ok {
				return notJSON
			}
			if len(stack) > 0 && stack[len(stack)-1].wantKey {
				top := &stack[len(stack)-1]
				top.wantKey, top.key, top.keyStart, top.keyEnd = false, text, i, j
			} else {
				value(text, i, j)
			}
			i = j
		case c == '-' || (c >= '0' && c <= '9'):
			j := i + 1
			for j < len(s) && (s[j] >= '0' && s[j] <= '9' || s[j] == '.' || s[j] == 'e' || s[j] == 'E' || s[j] == '+' || s[j] == '-') {
				j++
			}
			value(s[i:j], i, j) // as written
			i = j
		case c == 't' || c == 'f' || c == 'n':
			// true, false or null: a member is read by its key alone.
			j := i + 1
			for j < len(s) && s[j] >= 'a' && s[j] <= 'z' {
				j++
			}
			if t, span, ok := member(); ok {
				p.texts = append(p.texts, t)
				p.spans = append(p.spans, span)
			}
			i = j
		default:
			return notJSON
		}
	}
	// The pass found each text of the arguments if they are JSON; whether
	// they are (one value, every token well formed) is json.Valid's to say,
	// as it is the provider's parser's.
	if !json.Valid([]byte(s)) {
		return notJSON
	}
	return p
}

// stringEnd is the end of the JSON string that starts at s[i] (its opening
// quote): the index after its closing quote, or -1 when it has none.
func stringEnd(s string, i int) int {
	for k := i + 1; k < len(s); {
		d := strings.IndexAny(s[k:], `"\`)
		if d < 0 {
			return -1
		}
		if s[k+d] == '"' {
			return k + d + 1
		}
		k += d + 2 // a backslash and the character it escapes
	}
	return -1
}

// decodeString is the text of raw, a JSON string with its quotes, as a JSON
// parser decodes it: what is between the quotes when it holds no escape and
// is valid UTF-8, else json.Unmarshal's.
func decodeString(raw string) (string, bool) {
	body := raw[1 : len(raw)-1]
	if !strings.Contains(body, `\`) && utf8.ValidString(body) {
		return body, true
	}
	var text string
	if json.Unmarshal([]byte(raw), &text) != nil {
		return "", false
	}
	return text, true
}

// marshalText is s as a JSON string, without HTML escaping, so text is
// written as a check left it.
func marshalText(s string) []byte {
	var buf strings.Builder
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	_ = enc.Encode(s)
	return []byte(strings.TrimSuffix(buf.String(), "\n"))
}
