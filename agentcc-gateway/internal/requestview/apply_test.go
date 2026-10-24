package requestview

import (
	"context"
	"encoding/json"
	"reflect"
	"strings"
	"testing"
)

// split shares a rewritten text among the texts it was read from: texts the
// rewrite left keep their values; a changed text in between takes the rest;
// several changed texts share it at the separator when the rewrite kept every
// one, and otherwise the first takes it all and the others are dropped.
func TestSplit(t *testing.T) {
	for _, tc := range []struct {
		name      string
		values    []string
		sep       string
		rewritten string
		shares    []string
		keep      []bool
	}{
		{"one text", []string{"my ssn 1"}, "\n", "my ssn ***", []string{"my ssn ***"}, []bool{true}},
		{"the middle one changed", []string{"a", "my ssn 1", "c"}, "\n", "a\nmy ssn ***\nc", []string{"a", "my ssn ***", "c"}, []bool{true, true, true}},
		{"the first changed", []string{"x 1", "b"}, " ", "x *** b", []string{"x ***", "b"}, []bool{true, true}},
		{"the last changed", []string{"a", "x 1"}, "\n", "a\nx ***", []string{"a", "x ***"}, []bool{true, true}},
		{"two changed, the separator kept", []string{"a", "x 1", "y 2", "d"}, "\n", "a\nx ***\ny ***\nd",
			[]string{"a", "x ***", "y ***", "d"}, []bool{true, true, true, true}},
		{"two changed, with lines of their own", []string{"x\n1", "y 2"}, "\n", "x\n***\ny ***",
			[]string{"x\n***", "y ***"}, []bool{true, true}},
		{"a match across the separator", []string{"call 555-123", "4567 now"}, "\n", "call *** now",
			[]string{"call *** now", ""}, []bool{true, false}},
		{"nothing changed", []string{"a", "b"}, " ", "a b", []string{"a", "b"}, []bool{true, true}},
		{"a text that is the separator's prefix", []string{"a", "a"}, "\n", "a\n***", []string{"a", "***"}, []bool{true, true}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			shares, keep, err := split(tc.values, tc.sep, tc.rewritten)
			if err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(shares, tc.shares) || !reflect.DeepEqual(keep, tc.keep) {
				t.Errorf("split = %q %v, want %q %v", shares, keep, tc.shares, tc.keep)
			}
			// What the shares that are kept join to is the rewritten text.
			var kept []string
			for i, s := range shares {
				if keep[i] {
					kept = append(kept, s)
				}
			}
			if got := strings.Join(kept, tc.sep); got != tc.rewritten {
				t.Errorf("the kept shares join to %q, want %q", got, tc.rewritten)
			}
		})
	}
}

// applyEdits changes only the values the edits name, writes again only the
// objects and arrays on their paths, in their own order, and keeps every
// other value's bytes.
func TestApplyEdits(t *testing.T) {
	doc := []byte(`{"z":1, "big":12345678901234567890,"m":[{"type":"text","text":"a"},{"type":"text","text":"b"},{"x":"<keep>"}],"s":"old"}`)
	got, err := applyEdits(doc, []edit{
		{at: path{"m", 1}, drop: true},
		{at: path{"m", 0, "text"}, value: "a & <b>"},
		{at: path{"s"}, value: "new"},
	})
	if err != nil {
		t.Fatal(err)
	}
	want := `{"z":1,"big":12345678901234567890,"m":[{"type":"text","text":"a & <b>"},{"x":"<keep>"}],"s":"new"}`
	if string(got) != want {
		t.Errorf("applyEdits =\n %s\nwant\n %s", got, want)
	}
	if _, err := applyEdits(doc, []edit{{at: path{"absent"}, value: "x"}}); err == nil {
		t.Error("an edit of a value the document does not have was applied")
	}
	if _, err := applyEdits(doc, []edit{{at: path{"m", 9, "text"}, value: "x"}}); err == nil {
		t.Error("an edit past the end of a list was applied")
	}
	if _, err := applyEdits(doc, []edit{{at: path{"z"}, value: `{"x":`, json: true}}); err == nil {
		t.Error("a JSON value that is not JSON was applied")
	}
}

// rewrite reads body with v, sets message i's content to the text a check
// rewrote it to, and applies it.
func rewrite(t *testing.T, v *View, rewrites map[int]string) error {
	t.Helper()
	req := readOK(t, v)
	for i, text := range rewrites {
		req.Messages[i].Content, _ = json.Marshal(text)
	}
	return v.ApplyChecks()
}

// ApplyChecks writes a rewritten message's text into the body it was read
// from, and the body reads back as the checks left the request; a message no
// check rewrote changes nothing.
func TestApplyChecks_WritesTheRewriteIntoTheBody(t *testing.T) {
	for _, tc := range []struct {
		name     string
		view     *View
		rewrites map[int]string
		want     string
	}{
		{"a message's second text block",
			Anthropic([]byte(`{"model":"m","messages":[{"role":"user","content":[{"type":"text","text":"hi"},{"type":"text","text":"ssn 1","cache_control":{"type":"ephemeral"}}]}]}`)),
			map[int]string{0: "hi\nssn ***"},
			`{"model":"m","messages":[{"role":"user","content":[{"type":"text","text":"hi"},{"type":"text","text":"ssn ***","cache_control":{"type":"ephemeral"}}]}]}`},
		{"a match across two blocks merges them into the first",
			Anthropic([]byte(`{"messages":[{"role":"user","content":[{"type":"text","text":"call 555-123"},{"type":"text","text":"4567 now"},{"type":"image","source":{"type":"url","url":"u"}}]}]}`)),
			map[int]string{0: "call *** now"},
			`{"messages":[{"role":"user","content":[{"type":"text","text":"call *** now"},{"type":"image","source":{"type":"url","url":"u"}}]}]}`},
		{"a Gemini function response",
			Gemini([]byte(`{"contents":[{"parts":[{"functionResponse":{"name":"f","response":{"r":"ssn 1","n":7}}}]}]}`), "m", false),
			map[int]string{0: `{"r":"ssn ***","n":7}`},
			`{"contents":[{"parts":[{"functionResponse":{"name":"f","response":{"r":"ssn ***","n":7}}}]}]}`},
		{"a Responses prompt variable and an output",
			Responses([]byte(`{"prompt":{"id":"p","variables":{"v":"ssn 1"}},"input":[{"type":"function_call_output","call_id":"c","output":"ssn 2"}]}`)),
			map[int]string{0: "ssn ***", 1: "ssn ***"},
			`{"prompt":{"id":"p","variables":{"v":"ssn ***"}},"input":[{"type":"function_call_output","call_id":"c","output":"ssn ***"}]}`},
		{"no rewrite",
			Anthropic([]byte(`{"messages":[{"role":"user","content":"as sent"}]}`)),
			nil,
			`{"messages":[{"role":"user","content":"as sent"}]}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if err := rewrite(t, tc.view, tc.rewrites); err != nil {
				t.Fatalf("ApplyChecks: %v", err)
			}
			if got := string(tc.view.Body()); got != tc.want {
				t.Errorf("body\n got %s\nwant %s", got, tc.want)
			}
			if err := tc.view.ApplyChecks(); err != nil || string(tc.view.Body()) != tc.want {
				t.Errorf("a second ApplyChecks changed the body: %v %s", err, tc.view.Body())
			}
		})
	}
}

// A rewrite that cannot be written into the body is an error, and the body
// is left as it was.
func TestApplyChecks_RefusesWhatItCannotWrite(t *testing.T) {
	for _, tc := range []struct {
		name     string
		view     *View
		rewrites map[int]string
		why      string
	}{
		{"a match across two fields of one block",
			Anthropic([]byte(`{"messages":[{"role":"user","content":[{"type":"document","title":"call 555-123","source":{"type":"text","data":"4567 now"}}]}]}`)),
			map[int]string{0: "call *** now"}, "cannot be written"},
		{"a function response the rewrite makes other than JSON",
			Gemini([]byte(`{"contents":[{"parts":[{"functionResponse":{"name":"f","response":{"n":2345678901}}}]}]}`), "m", false),
			map[int]string{0: `{"n":***}`}, "not JSON of its type"},
		{"a rewrite of a message with no text of the request",
			Anthropic([]byte(`{"messages":[{"role":"assistant","content":[{"type":"tool_use","id":"t","name":"f","input":{}}]}]}`)),
			map[int]string{0: "injected"}, "holds no text"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			before := string(tc.view.Body())
			err := rewrite(t, tc.view, tc.rewrites)
			if err == nil || !strings.Contains(err.Error(), tc.why) {
				t.Errorf("ApplyChecks error %v, want one that says %q", err, tc.why)
			}
			if string(tc.view.Body()) != before {
				t.Errorf("the body changed: %s", tc.view.Body())
			}
		})
	}
	t.Run("a check that changed the number of messages", func(t *testing.T) {
		v := Anthropic([]byte(`{"messages":[{"role":"user","content":"a"}]}`))
		req := readOK(t, v)
		req.Messages = append(req.Messages, req.Messages[0])
		if err := v.ApplyChecks(); err == nil {
			t.Error("ApplyChecks accepted a request with a message added")
		}
	})
	t.Run("an unreadable body has nothing to apply", func(t *testing.T) {
		v := Anthropic([]byte(`{"messages":7}`))
		v.ChatRequest(context.Background())
		if err := v.ApplyChecks(); err != nil {
			t.Errorf("ApplyChecks: %v", err)
		}
	})
}
