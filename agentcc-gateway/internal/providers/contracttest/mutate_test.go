package contracttest

import (
	"bytes"
	"reflect"
	"strings"
	"testing"
)

type mutEnvelope struct {
	Type  string     `json:"type"`
	Index int        `json:"index"`
	Delta *textDelta `json:"delta"`
}

func mutStrict() *Strict {
	return &Strict{
		Docs: func(c Case) []Doc {
			if !c.IsStream {
				return []Doc{{Root: "$", Data: c.Body, Type: reflect.TypeFor[typed]()}}
			}
			var docs []Doc
			for _, ev := range ParseSSE(c.Body) {
				docs = append(docs, Doc{Root: ev.Name + ":$", Data: ev.Data, Type: reflect.TypeFor[mutEnvelope]()})
			}
			return docs
		},
		Enums: []EnumSpec{
			{Spec: "Kind", Paths: []string{"$.kind"}, Values: []string{"a"}},
			{Spec: "Events", Paths: []string{"*:$.type"}, Values: []string{"start", "delta"}},
		},
		Free: []string{"$.input"},
	}
}

const mutBody = `{"name": "n", "kind": "a", "ok": true, "usage": {"in": 1, "ratio": 0.5}, "tags": ["x"], "raw": {"free": 1}, "input": {"q": 1}}`

func mutCase(body string, stream bool) Case {
	return Case{Name: "basic", Model: "acme-1", Body: []byte(body), IsStream: stream, Status: 200, Meta: &Meta{dir: "acme-1"}}
}

func TestAssertMutationsDetected_StrictCheckCatchesAll(t *testing.T) {
	s := mutStrict()
	var st MutationStats
	ft := runFake(func(tb *fakeTB) {
		st = s.AssertMutationsDetected(tb, mutCase(mutBody, false), func(tb testing.TB, c Case) { s.Check(tb, c) })
	})
	if len(ft.errors) != 0 {
		t.Fatalf("errors: %q", ft.errors)
	}
	// Objects $ and $.usage (key + flip), leaves name, kind, ok, usage.in
	// (flip + fraction), usage.ratio, tags (array flip), tags[], and the
	// enum at $.kind. $.raw is opaque, $.input free.
	if st.Generated != 13 || st.Detected != 13 || len(st.Opaque) != 1 || st.Opaque[0] != "$.raw" {
		t.Errorf("stats = %+v", st)
	}
}

func TestAssertMutationsDetected_ReportsBlindSpots(t *testing.T) {
	s := mutStrict()
	ft := runFake(func(tb *fakeTB) {
		// A check that only audits unknown keys misses type flips and enums.
		s.AssertMutationsDetected(tb, mutCase(mutBody, false), func(tb testing.TB, c Case) {
			rep, _ := s.Report(c)
			if len(rep.Unknown) > 0 {
				tb.Errorf("unknown %q", rep.Unknown)
			}
		})
	})
	if len(ft.errors) != 1 || !strings.Contains(ft.errors[0], "went unnoticed") ||
		!strings.Contains(ft.errors[0], "type flip at $.name (string)") || !strings.Contains(ft.errors[0], "enum probe at $.kind") ||
		strings.Contains(ft.errors[0], "unknown key") {
		t.Errorf("errors: %q", ft.errors)
	}
}

func TestAssertMutationsDetected_ControlMustPass(t *testing.T) {
	s := mutStrict()
	ft := runFake(func(tb *fakeTB) {
		s.AssertMutationsDetected(tb, mutCase(mutBody, false), func(tb testing.TB, c Case) { tb.Fatalf("always") })
	})
	if len(ft.errors) != 1 || !strings.Contains(ft.errors[0], "fails on the unmutated body") {
		t.Errorf("errors: %q", ft.errors)
	}
}

func TestAssertMutationsDetected_Stream(t *testing.T) {
	s := mutStrict()
	body := "event: start\ndata: {\"type\":\"start\",\"index\":0}\n\n" +
		"event: delta\ndata: {\"type\":\"delta\",\"index\":0,\"delta\":{\"type\":\"t\",\"text\":\"hi\"}}\n\n" +
		"event: delta\ndata: {\"type\":\"delta\",\"index\":1,\"delta\":{\"type\":\"t\",\"text\":\"yo\"}}\n\n"
	var bodies [][]byte
	var st MutationStats
	ft := runFake(func(tb *fakeTB) {
		st = s.AssertMutationsDetected(tb, mutCase(body, true), func(tb testing.TB, c Case) {
			bodies = append(bodies, c.Body)
			s.Check(tb, c)
		})
	})
	if len(ft.errors) != 0 {
		t.Fatalf("errors: %q", ft.errors)
	}
	// start:$ (key, flip), start:$.type (flip, enum), start:$.index (flip,
	// fraction); the same six for delta:$, plus delta:$.delta (key, flip)
	// and its two leaves; and the unknown event. Repeats of a path are
	// tried once.
	if st.Generated != 17 || st.Detected != 17 {
		t.Errorf("stats = %+v", st)
	}
	if !bytes.Contains(bodies[1], []byte("event: __probe__\ndata: {\"type\":\"__probe__\"}")) {
		t.Errorf("first mutant should add an unknown event:\n%s", bodies[1])
	}

	s.MaxMutations = 5
	ft = runFake(func(tb *fakeTB) {
		st = s.AssertMutationsDetected(tb, mutCase(body, true), func(tb testing.TB, c Case) { s.Check(tb, c) })
	})
	if len(ft.errors) != 0 || st.Generated != 5 || st.Candidates != 17 {
		t.Errorf("sampled: %+v %q", st, ft.errors)
	}
}

func TestAssertMutationsDetected_DocsMustMatchEvents(t *testing.T) {
	s := mutStrict()
	s.Docs = func(c Case) []Doc { return []Doc{{Root: "$", Data: c.Body}} }
	ft := runFake(func(tb *fakeTB) {
		s.AssertMutationsDetected(tb, mutCase("data: {}\n\ndata: {}\n\n", true), func(testing.TB, Case) {})
	})
	if !ft.fatal || !strings.Contains(ft.errors[0], "one per event") {
		t.Errorf("errors: %q", ft.errors)
	}
}

func TestEncodeSSE(t *testing.T) {
	events := []Event{{Name: "a", Data: []byte("{\"x\":1}")}, {Data: []byte("line1\nline2")}}
	got := ParseSSE(EncodeSSE(events))
	if len(got) != 2 || got[0].Name != "a" || string(got[0].Data) != `{"x":1}` || string(got[1].Data) != "line1\nline2" {
		t.Errorf("round trip = %q", got)
	}
}
