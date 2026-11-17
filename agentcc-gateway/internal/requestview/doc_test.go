package requestview

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"strings"
	"testing"
)

// doc decodes an object or a list as json.Unmarshal into
// map[string]json.RawMessage or []json.RawMessage does: the same keys (a
// repeated key's last value, escapes decoded, invalid UTF-8 as U+FFFD) and
// the same values, byte for byte, white space around them left out.
func TestDoc_DecodesAsJSONUnmarshalDoes(t *testing.T) {
	objects := []string{
		`{}`,
		` { "a" : 1 , "b":"x\"y" ,"c":[1, {"d":null}] } `,
		`{"k":1,"k":{"x":[true]},"z":"last","k":"again"}`,
		`{"\u0074ext":"escaped key","t\"q":"quote in key","\ud83d\ude00":"surrogates"}`,
		"{\"bad\xffkey\":1}",
		`{"n":-1.5e+10,"t":true,"f":false,"nil":null,"s":"","e":{},"l":[]}`,
		"{\n\t\"tabs\"\t:\n\t[ 1 ,\n2 ]\r\n}",
	}
	for _, body := range objects {
		var want map[string]json.RawMessage
		if err := json.Unmarshal([]byte(body), &want); err != nil {
			t.Fatalf("%q: %v", body, err)
		}
		d := newDoc(context.Background(), []byte(body))
		got, ok := d.object(d.body)
		if !ok || !reflect.DeepEqual(got, want) {
			t.Errorf("%q: object = %v (%v), want %v", body, got, ok, want)
		}
		keys, _, ok := d.members(d.body)
		dec := json.NewDecoder(strings.NewReader(body))
		_, _ = dec.Token()
		var wantKeys []string
		for dec.More() {
			tok, _ := dec.Token()
			k, _ := tok.(string)
			wantKeys = append(wantKeys, k)
			var skip json.RawMessage
			_ = dec.Decode(&skip)
		}
		if !ok || len(keys) != len(wantKeys) || (len(keys) > 0 && !reflect.DeepEqual(keys, wantKeys)) {
			t.Errorf("%q: members' keys %q, want %q in the body's order", body, keys, wantKeys)
		}
	}
	lists := []string{`[]`, ` [ 1 , "a" , {"b":[2]} , [ ] , null ] `, `["\u00e9", -0, 1e3]`}
	for _, body := range lists {
		var want []json.RawMessage
		if err := json.Unmarshal([]byte(body), &want); err != nil {
			t.Fatalf("%q: %v", body, err)
		}
		d := newDoc(context.Background(), []byte(body))
		got, ok := d.array(d.body)
		if !ok || !reflect.DeepEqual(got, want) {
			t.Errorf("%q: array = %q (%v), want %q", body, got, ok, want)
		}
	}
	// A string decodes as json.Unmarshal decodes it, on the plain path and
	// the escaped one; what json.Unmarshal refuses, asString refuses.
	for _, raw := range []string{`""`, `"plain text"`, ` "with space around" `, `"\u00e9 \n \""`, "\"caf\xc3\xa9\"", "\"bad \xff utf8\"",
		"\"tab\tinside\"", `"a" "b"`, `"unterminated`, `"a"b"`} {
		var want string
		wantErr := json.Unmarshal([]byte(raw), &want)
		got, ok := asString(json.RawMessage(raw))
		if ok != (wantErr == nil) || got != want {
			t.Errorf("asString(%q) = %q, %v; json.Unmarshal: %q, %v", raw, got, ok, want, wantErr)
		}
	}

	// What is not one JSON value decodes nothing, as json.Unmarshal fails.
	for _, body := range []string{``, `{"a":1`, `{"a":1}{}`, `{"a":1} x`, `[1,]`, `{"a" 1}`, `nul`} {
		d := newDoc(context.Background(), []byte(body))
		if _, ok := d.object(d.body); ok {
			t.Errorf("%q decoded as an object", body)
		}
		if _, ok := d.array(d.body); ok {
			t.Errorf("%q decoded as a list", body)
		}
	}
	// A value that is not a slice of the body is not decoded: every value a
	// reader decodes comes from the body (no slower path behind it).
	d := newDoc(context.Background(), []byte(`{"a":{"b":1}}`))
	if _, ok := d.object(json.RawMessage(`{"b":1}`)); ok {
		t.Error("decoded a value that is not part of the body")
	}
}

// One read steps over each byte of the body at most four times, jumping
// over each object and list a value holds: an object or a list is counted,
// then decoded, and an item reader decodes an item and then walks it as one
// it does not know. So a read is linear in the body however deeply it is
// nested; the index pass reads each byte twice more (it counts, then
// records, the objects and lists).
func TestDoc_EachByteIsScannedOnce(t *testing.T) {
	text, _ := json.Marshal(bigText(64 << 10))
	bodies := map[string]struct {
		read func(*doc) (*chat, error)
		body string
	}{
		"responses, an item 60 deep": {readResponses,
			`{"model":"m","input":[{"role":"user","content":"hello"},{"type":"zz_unknown","x":` + nest(60, `{"text":`+string(text)+`}`) + `}]}`},
		"responses, sixteen items 9000 deep": {readResponses,
			`{"model":"m","input":[` + strings.TrimSuffix(strings.Repeat(`{"type":"zz_unknown","x":`+nest(9000, string(text))+`},`, 16), ",") + `]}`},
		"messages, 29 tool results in tool results": {readAnthropic,
			`{"model":"m","max_tokens":1,"messages":[{"role":"user","content":[` + strings.Repeat(`{"type":"tool_result","tool_use_id":"t","content":[`, 29) +
				`{"type":"text","text":` + string(text) + `}` + strings.Repeat(`]}`, 29) + `]}]}`},
		"generateContent, a function response 55 deep": {readGemini,
			`{"contents":[{"role":"user","parts":[{"functionResponse":{"name":"f","response":` + nest(55, `{"text":`+string(text)+`}`) + `}}]}]}`},
	}
	for name, b := range bodies {
		t.Run(name, func(t *testing.T) {
			d := newDoc(context.Background(), []byte(b.body))
			if !d.valid {
				t.Fatal("not indexed")
			}
			if _, err := b.read(d); err != nil {
				t.Fatal(err)
			}
			if d.scanned > 4*len(b.body) {
				t.Errorf("the read stepped over %d bytes of a %d-byte body, want at most four times the body", d.scanned, len(b.body))
			}
		})
	}
}

// A read looks at the request's context as it goes: one whose context has
// ended reads nothing, one whose context ends while it reads stops there,
// and either says why and is kept (the body is not read again).
func TestView_ChatRequestStopsWhenTheContextEnds(t *testing.T) {
	ended, cancel := context.WithCancel(context.Background())
	cancel()
	v := Responses([]byte(`{"model":"m","input":"hello"}`))
	req, reason := v.ChatRequest(ended)
	if req != nil || !strings.Contains(reason, "the request ended before the gateway read it") {
		t.Fatalf("read under an ended context: %v, %q", req != nil, reason)
	}
	if req, again := v.ChatRequest(context.Background()); req != nil || again != reason {
		t.Errorf("a later call read the body again: %v, %q", req != nil, again)
	}

	// A context that ends after the read has started: many items, decoded
	// one by one.
	items := strings.TrimSuffix(strings.Repeat(`{"role":"user","content":[{"type":"input_text","text":"hello"}]},`, 4096), ",")
	ctx := &endsAfter{Context: context.Background(), looks: 3}
	v = Responses([]byte(`{"model":"m","input":[` + items + `]}`))
	req, reason = v.ChatRequest(ctx)
	if req != nil || !strings.Contains(reason, "the request ended before the gateway read it") {
		t.Errorf("read on after its context ended: %v, %q", req != nil, reason)
	}
	if ctx.n <= ctx.looks {
		t.Errorf("the read looked at its context %d times, want it to look while it reads", ctx.n)
	}
}

// endsAfter is a context that ends once it has been looked at looks times.
type endsAfter struct {
	context.Context
	looks, n int
}

func (c *endsAfter) Err() error {
	c.n++
	if c.n > c.looks {
		return context.Canceled
	}
	return nil
}

// A body read only in part (a value nested more than MaxDepth deep) takes no
// rewrite: the plugin refuses such a request under a check that rewrites
// it, and a rewrite that gets here anyway is refused, the body unchanged.
func TestApplyChecks_RefusesARewriteOfABodyReadInPart(t *testing.T) {
	body := []byte(`{"model":"m","input":[{"role":"user","content":"my ssn is 123-45-6789"},{"type":"zz_unknown","x":` + nest(70, `"x"`) + `}]}`)
	v := Responses(body)
	req, reason := v.ChatRequest(context.Background())
	if req == nil || reason == "" {
		t.Fatalf("want the body read in part: %v, %q", req != nil, reason)
	}
	req.Messages[0].Content, _ = json.Marshal("my ssn is ***")
	err := v.ApplyChecks()
	if err == nil || !strings.Contains(err.Error(), "cannot read in full") {
		t.Errorf("ApplyChecks = %v, want a refusal", err)
	}
	if !bytes.Equal(v.Body(), body) {
		t.Error("the body changed")
	}
}

// canonicalJSON writes 64 objects one inside another, and stops at the
// 65th, inside 64 others: errTooDeep, not a value cut short.
func TestCanonicalJSON_StopsAtTheLimit(t *testing.T) {
	for _, tc := range []struct {
		levels int
		err    error
	}{{64, nil}, {65, errTooDeep}} {
		body := []byte(nest(tc.levels, `"x"`))
		d := newDoc(context.Background(), body)
		got, err := d.canonicalJSON(d.body, nil)
		if !errors.Is(err, tc.err) || (err == nil && !bytes.Equal(got, body)) {
			t.Errorf("%d levels: %s, %v; want %v", tc.levels, got, err, tc.err)
		}
	}
}

// appendJSONString writes a string byte for byte as marshal (encoding/json
// without HTML escaping) does: the readers write a message's content with
// it, and a rewrite back into the body.
func TestAppendJSONString_WritesAsMarshalDoes(t *testing.T) {
	var all strings.Builder
	for c := 0; c < 0x80; c++ {
		all.WriteByte(byte(c))
	}
	for _, s := range []string{"", "plain", `"quoted" and \backslashed\`, "<b>&amp;</b>", "tab\tnew\nline\rcr\bbs\fff",
		"\x00\x01\x1f\x7f", all.String(), "café 世界 \U0001F600", "bad \xff\xfe utf8 \xe2\x82", "line para end",
		strings.Repeat("a\"b", 1000)} {
		if got, want := string(appendJSONString(nil, s)), string(marshal(s)); got != want {
			t.Errorf("appendJSONString(%q) = %s, marshal = %s", s, got, want)
		}
		if got, want := string(marshalString(s)), string(marshal(s)); got != want {
			t.Errorf("marshalString(%q) = %s, marshal = %s", s, got, want)
		}
	}
}
