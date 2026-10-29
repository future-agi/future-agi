package contracttest

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"
	"time"
)

type usage struct {
	In    int     `json:"in"`
	Out   uint8   `json:"out"`
	Ratio float64 `json:"ratio"`
}

type strictErr struct{}

func (*strictErr) UnmarshalJSON(b []byte) error {
	if string(b) == `"bad"` {
		return errors.New("bad value")
	}
	return nil
}

type typed struct {
	Name    string            `json:"name"`
	Kind    string            `json:"kind"`
	OK      bool              `json:"ok"`
	Usage   *usage            `json:"usage"`
	Tags    []string          `json:"tags"`
	ByKey   map[string]usage  `json:"by_key"`
	Count   int               `json:"count,string"`
	When    time.Time         `json:"when"`
	Custom  strictErr         `json:"custom"`
	Raw     json.RawMessage   `json:"raw"`
	Any     any               `json:"any"`
	Meta    map[string]any    `json:"meta"`
	Input   json.RawMessage   `json:"input"`
	Cites   json.RawMessage   `json:"cites"`
	Delta   json.RawMessage   `json:"delta"`
	Extra   json.RawMessage   `json:"extra"`
	Bytes   []byte            `json:"bytes"`
	Number  json.Number       `json:"number"`
	Nothing *string           `json:"nothing"`
	Nested  map[string][]bool `json:"nested"`
}

type cite struct {
	URL string `json:"url"`
}

type textDelta struct {
	Type string `json:"type"`
	Text string `json:"text"`
}

func testStrict() *Strict {
	return &Strict{
		Docs: func(c Case) []Doc { return []Doc{{Root: "$", Data: c.Body, Type: reflect.TypeFor[typed]()}} },
		Enums: []EnumSpec{
			{Spec: "Kind", Paths: []string{"$.kind", "$.delta.type"}, Values: []string{"a", "text_delta", "other_delta"}},
			{Spec: "EventType", Paths: []string{"*:$.type"}, Values: []string{"start", "stop"}},
		},
		Decoded: []Decoded{
			{Path: "$.cites", Type: reflect.TypeFor[[]cite]()},
			{Path: "$.delta", Tag: "type", Types: map[string]reflect.Type{"text_delta": reflect.TypeFor[textDelta]()}},
		},
		Watched: []Watched{{Path: "$.extra", Keys: []string{"known"}}},
		Free:    []string{"$.input"},
		Ignore:  map[string]string{"$.usage.protocol": "always 0"},
	}
}

func report(t *testing.T, s *Strict, body string) Report {
	t.Helper()
	rep, err := s.Report(Case{Body: []byte(body)})
	if err != nil {
		t.Fatal(err)
	}
	return rep
}

func TestReport_TypeMismatches(t *testing.T) {
	rep := report(t, testStrict(), `{
		"name": 7,
		"ok": "yes",
		"usage": {"in": 1.5, "out": 300, "ratio": "x", "protocol": 0},
		"tags": "solo",
		"by_key": {"a": {"in": "1"}},
		"count": 12,
		"when": 5,
		"custom": "bad",
		"raw": {"anything": [1]},
		"any": "x",
		"bytes": 3,
		"number": "4.5",
		"nothing": null,
		"nested": {"k": [true, "no"]}
	}`)
	var got []string
	for _, m := range rep.Mismatches {
		got = append(got, m.String())
	}
	want := []string{
		`$.by_key.*.in: got string, struct wants integer`,
		`$.bytes: got number 3, struct wants string`,
		`$.count: got number 12, struct wants string (the field is tagged ",string")`,
		`$.custom: got string, struct wants a value contracttest.strictErr accepts (bad value)`,
		`$.name: got number 7, struct wants string`,
		`$.nested.*[]: got string, struct wants bool`,
		`$.ok: got string, struct wants bool`,
		`$.tags: got string, struct wants array`,
		`$.usage.in: got number 1.5, struct wants integer (int)`,
		`$.usage.out: got number 300, struct wants integer (uint8)`,
		`$.usage.ratio: got string, struct wants number`,
		`$.when: got number 5, struct wants a value time.Time accepts (Time.UnmarshalJSON: input is not a JSON string)`,
	}
	if !slices.Equal(got, want) {
		t.Errorf("mismatches =\n  %s\nwant\n  %s", strings.Join(got, "\n  "), strings.Join(want, "\n  "))
	}
	if len(rep.Unknown) != 0 {
		t.Errorf("unknown = %q; a mismatch is not an unknown field, and $.usage.protocol is ignored", rep.Unknown)
	}
	if len(rep.DecodeErrors) != 0 {
		t.Errorf("decode errors %q: mismatches explain them", rep.DecodeErrors)
	}
}

func TestReport_DecodeErrorWithoutMismatch(t *testing.T) {
	// The walk stops at an unmarshaler holding an object, so only the full
	// decode sees its error.
	type wrapper struct {
		Inner failingObject `json:"inner"`
	}
	s := &Strict{Docs: func(c Case) []Doc { return []Doc{{Root: "$", Data: c.Body, Type: reflect.TypeFor[wrapper]()}} }}
	rep := report(t, s, `{"inner": {"x": 1}}`)
	if len(rep.DecodeErrors) != 1 || !strings.Contains(rep.DecodeErrors[0], "object rejected") {
		t.Errorf("decode errors = %q", rep.DecodeErrors)
	}
	if !slices.Equal(rep.Opaque, []string{"$.inner"}) {
		t.Errorf("opaque = %q", rep.Opaque)
	}
}

type failingObject struct{}

func (*failingObject) UnmarshalJSON([]byte) error { return errors.New("object rejected") }

func TestReport_OpaqueDeclaredFreeIgnored(t *testing.T) {
	rep := report(t, testStrict(), `{
		"raw": {"a": 1},
		"any": [1, 2],
		"kind": "a",
		"meta": {"other": {"deep": true}},
		"input": {"arbitrary": {"kind": "zzz"}},
		"cites": [{"url": "u", "title": "new"}],
		"delta": {"type": "text_delta", "text": "hi", "added": 1},
		"extra": {"known": 1, "surprise": 2},
		"usage": {"in": 1, "protocol": 0, "fresh": 1}
	}`)
	if want := []string{"$.any", "$.meta.*", "$.raw"}; !slices.Equal(rep.Opaque, want) {
		t.Errorf("opaque = %q, want %q", rep.Opaque, want)
	}
	if want := []string{"$.cites[].title", "$.delta.added", "$.extra.surprise", "$.usage.fresh"}; !slices.Equal(rep.Unknown, want) {
		t.Errorf("unknown = %q, want %q", rep.Unknown, want)
	}
	if _, ok := rep.Shape["$.input.arbitrary"]; ok {
		t.Errorf("a Free region's insides must not be in the shape")
	}
	if rep.Shape["$.input"] != KindObject || rep.Shape["$.meta.*.deep"] != KindBool || rep.Shape["$.cites[].url"] != KindString {
		t.Errorf("shape = %v", rep.Shape)
	}
}

func TestReport_DecodedAsOpaqueType(t *testing.T) {
	// A declaration whose type is opaque itself must not loop: the region
	// stays opaque, and a Decoded path inside it still applies.
	s := &Strict{
		Docs: func(c Case) []Doc { return []Doc{{Root: "$", Data: c.Body, Type: reflect.TypeFor[failingObject]()}} },
		Decoded: []Decoded{
			{Path: "$", Type: reflect.TypeFor[failingObject]()},
			{Path: "$.choices", Type: reflect.TypeFor[[]cite]()},
		},
	}
	rep := report(t, s, `{"choices": [{"url": "u", "new": 1}]}`)
	if !slices.Equal(rep.Opaque, []string{"$"}) || !slices.Equal(rep.Unknown, []string{"$.choices[].new"}) {
		t.Errorf("opaque %q, unknown %q", rep.Opaque, rep.Unknown)
	}
}

func TestReport_TaggedUnion(t *testing.T) {
	s := testStrict()
	rep := report(t, s, `{"delta": {"type": "other_delta", "x": 1}}`)
	if want := []string{"$.delta [type=other_delta]"}; !slices.Equal(rep.Opaque, want) {
		t.Errorf("opaque = %q, want %q", rep.Opaque, want)
	}
	rep = report(t, s, `{"delta": "flat"}`)
	if len(rep.Mismatches) != 1 || rep.Mismatches[0].Path != "$.delta" {
		t.Errorf("a union that is not an object is a mismatch: %v", rep.Mismatches)
	}
	rep = report(t, s, `{"delta": {"text": "no tag"}}`)
	if len(rep.Mismatches) != 1 || rep.Mismatches[0].String() != `$.delta.type: got null or missing, struct wants string (the union's tag)` {
		t.Errorf("a union without its tag is a mismatch: %v", rep.Mismatches)
	}
	rep = report(t, s, `{"cites": [{"url": 5}]}`)
	if len(rep.Mismatches) != 1 || rep.Mismatches[0].Path != "$.cites[].url" {
		t.Errorf("a Decoded region is type-checked: %v", rep.Mismatches)
	}
}

func TestReport_Enums(t *testing.T) {
	s := testStrict()
	s.Docs = func(c Case) []Doc {
		var docs []Doc
		for _, ev := range ParseSSE(c.Body) {
			docs = append(docs, Doc{Root: ev.Name + ":$", Data: ev.Data, Type: nil})
		}
		return docs
	}
	rep, err := s.Report(Case{IsStream: true, Body: []byte("event: start\ndata: {\"type\":\"start\"}\n\nevent: boom\ndata: {\"type\":\"boom\"}\n\nevent: stop\ndata: {\"type\":7}\n\n")})
	if err != nil {
		t.Fatal(err)
	}
	want := []string{
		`boom:$.type: "boom" is not one of EventType's values ["start" "stop"]`,
		`stop:$.type: got number, want one of EventType's string values`,
	}
	if !slices.Equal(rep.EnumViolations, want) {
		t.Errorf("violations =\n  %s\nwant\n  %s", strings.Join(rep.EnumViolations, "\n  "), strings.Join(want, "\n  "))
	}
	if want := []string{"boom:$", "start:$", "stop:$"}; !slices.Equal(rep.Opaque, want) {
		t.Errorf("an undecoded document is opaque at its root: %q", rep.Opaque)
	}

	rep = report(t, testStrict(), `{"kind": null, "delta": {"type": "text_delta"}}`)
	if len(rep.EnumViolations) != 0 {
		t.Errorf("null is not a value, and text_delta is declared: %q", rep.EnumViolations)
	}
}

func TestReport_NotJSON(t *testing.T) {
	rep := report(t, testStrict(), `<html>502</html>`)
	if !slices.Equal(rep.Unknown, []string{"$"}) {
		t.Errorf("a body that is not JSON is unknown at the root: %q", rep.Unknown)
	}
}

func TestStrict_BadDeclarations(t *testing.T) {
	for name, s := range map[string]*Strict{
		"decoded without type": {Decoded: []Decoded{{Path: "$.x"}}},
		"tag without types":    {Decoded: []Decoded{{Path: "$.x", Tag: "type"}}},
		"declared twice":       {Free: []string{"$.x"}, Watched: []Watched{{Path: "$.x", Keys: []string{"a"}}}},
		"not a path":           {Free: []string{"x"}},
		"enum without values":  {Enums: []EnumSpec{{Paths: []string{"$.x"}}}},
	} {
		s.Docs = func(Case) []Doc { return nil }
		if _, err := s.Report(Case{}); err == nil {
			t.Errorf("%s: want an error", name)
		}
	}
}

func TestShapeDiff(t *testing.T) {
	base := Shape{"$": KindObject, "$.a": KindString, "$.b": KindNumber | KindNull, "$.c": KindNull, "$.gone": KindObject, "$.gone.x": KindBool, "$.d": KindString}
	cur := Shape{"$": KindObject, "$.a": KindNumber, "$.b": KindNumber, "$.c": KindString, "$.new": KindArray, "$.d": KindNull}
	d := ShapeDiff(base, cur)
	if !slices.Equal(d.Added, []string{"$.new"}) || !slices.Equal(d.Removed, []string{"$.gone", "$.gone.x"}) ||
		!slices.Equal(d.TypeChanged, []string{"$.a", "$.c", "$.d"}) {
		t.Errorf("diff = %+v", d)
	}
	if !ShapeDiff(base, base).Empty() {
		t.Error("a shape differs from itself")
	}
}

// strictMeta is a meta.json for the Check tests.
const strictMeta = `{
  "provider": "acme",
  "model": "%s",
  "source": "recorded",
  "recorded_at": "2026-10-28",
  "cloned_from": "%s",
  "notes": "test",
  "allowed_unknown_fields": [],
  "allowed_unknown_shapes": [],
  "allowed_type_mismatches": [{"path": "$.name", "reason": "known gap: test"}],
  "optional_fields": [{"path": "$.usage", "reason": "sometimes absent"}]
}`

func writeModel(t *testing.T, root, model, clonedFrom, body string) {
	t.Helper()
	meta := strings.Replace(strings.Replace(strictMeta, "%s", model, 1), "%s", clonedFrom, 1)
	if clonedFrom == "" {
		meta = strings.Replace(meta, `"cloned_from": "",`, "", 1)
	}
	writeFile(t, filepath.Join(root, model, MetaFile), meta)
	writeFile(t, filepath.Join(root, model, "basic", RequestFile), `{"model":"`+model+`","messages":[]}`)
	writeFile(t, filepath.Join(root, model, "basic", ResponseJSONFile), body)
}

func TestCheck_AllowlistsAndStaleness(t *testing.T) {
	setUpdate(t, false) // the missing opaque golden must fail, not be written
	root := t.TempDir()
	writeModel(t, root, "acme-1", "", `{"name": 1, "tags": "x", "kind": "b"}`)
	s := testStrict()
	ft := runFake(func(tb *fakeTB) {
		for _, c := range discover(tb, "acme", root, "", false) {
			s.Check(tb, c)
		}
	})
	if len(ft.errors) != 3 || !strings.Contains(ft.errors[0], "$.tags: got string") || strings.Contains(ft.errors[0], "$.name") ||
		!strings.Contains(ft.errors[1], `$.kind: "b" is not one of Kind's values`) || !strings.Contains(ft.errors[2], OpaqueGoldenFile) {
		t.Errorf("errors = %q", ft.errors)
	}

	writeModel(t, root, "acme-1", "", `{"name": "fixed"}`)
	ft = runFake(func(tb *fakeTB) {
		for _, c := range discover(tb, "acme", root, "", false) {
			s.Check(tb, c)
		}
	})
	if len(ft.errors) != 2 || !strings.Contains(ft.errors[0], "allowed_type_mismatches entries no case produced") ||
		!strings.Contains(ft.errors[1], OpaqueGoldenFile) {
		t.Errorf("want a stale type-mismatch entry and a missing opaque golden, got %q", ft.errors)
	}
}

func TestCheck_OpaqueGolden(t *testing.T) {
	root := t.TempDir()
	writeModel(t, root, "acme-1", "", `{"name": 1, "raw": {"a": 1}}`)
	s := testStrict()
	run := func() *fakeTB {
		return runFake(func(tb *fakeTB) {
			for _, c := range discover(tb, "acme", root, "", false) {
				s.Check(tb, c)
			}
		})
	}
	setUpdate(t, true)
	ft := run()
	setUpdate(t, false)
	if len(ft.errors) != 0 {
		t.Fatalf("update: %q", ft.errors)
	}
	got, err := os.ReadFile(filepath.Join(root, OpaqueGoldenFile))
	if err != nil || string(got) != "[\n  \"$.raw\"\n]\n" {
		t.Fatalf("opaque golden = %q, %v", got, err)
	}
	writeModel(t, root, "acme-1", "", `{"name": 1, "raw": {"a": 1}, "any": 1}`)
	if ft := run(); len(ft.errors) != 1 || !strings.Contains(ft.errors[0], `"$.any"`) {
		t.Errorf("a new opaque region must change the golden: %q", ft.errors)
	}
}

// The shape diffs read the committed fixtures from DefaultRoot, relative to
// the test's working directory.
func TestCheck_ShapeDiffs(t *testing.T) {
	repo := t.TempDir()
	t.Chdir(repo)
	root := filepath.FromSlash(DefaultRoot)
	writeModel(t, root, "acme-1", "", `{"name": "a", "tags": ["x"], "usage": {"in": 1}, "ok": true}`)
	// acme-2 was cloned from acme-1: it lost "tags", "ok" became a string,
	// "usage" (optional) is gone, and it has a new "count".
	writeModel(t, root, "acme-2", "acme-1", `{"name": "a", "ok": "true", "count": "1"}`)

	s := testStrict()
	ft := runFake(func(tb *fakeTB) {
		for _, c := range discover(tb, "acme", root, "", false) {
			if c.Model == "acme-2" {
				s.Check(tb, c)
			}
		}
	})
	var diff string
	for _, e := range ft.errors {
		if strings.Contains(e, "response shape differs") {
			diff = e
		}
	}
	for _, want := range []string{"$.ok: string, was bool", "$.tags: removed (was array)", "same case of acme-1"} {
		if !strings.Contains(diff, want) {
			t.Errorf("cloned diff lacks %q:\n%s", want, diff)
		}
	}
	for _, unwanted := range []string{"$.tags[]", "$.usage"} {
		if strings.Contains(diff, unwanted) {
			t.Errorf("cloned diff must not list %q (implied, or optional):\n%s", unwanted, diff)
		}
	}

	// Live: a recording of acme-1 is diffed against the committed one.
	live := t.TempDir()
	writeFile(t, filepath.Join(live, "acme-1", "basic", ResponseJSONFile), `{"name": "a", "tags": ["x"], "ok": true, "usage": {"in": 1, "out": 2}}`)
	ft = runFake(func(tb *fakeTB) {
		for _, c := range discover(tb, "acme", live, root, true) {
			s.Check(tb, c)
		}
	})
	if len(ft.errors) != 0 {
		t.Errorf("a live recording with only a new modeled field must pass: %q", ft.errors)
	}
	writeFile(t, filepath.Join(live, "acme-1", "basic", ResponseJSONFile), `{"name": "a", "ok": true}`)
	ft = runFake(func(tb *fakeTB) {
		for _, c := range discover(tb, "acme", live, root, true) {
			s.Check(tb, c)
		}
	})
	if len(ft.errors) != 1 || !strings.Contains(ft.errors[0], "$.tags: removed") || !strings.Contains(ft.errors[0], "committed fixture") {
		t.Errorf("live diff: %q", ft.errors)
	}
}
