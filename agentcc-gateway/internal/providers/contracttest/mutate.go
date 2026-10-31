package contracttest

import (
	"bytes"
	"encoding/json"
	"fmt"
	"runtime"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// probeValue is what the mutation self-test injects: a key, a string, an
// enum value, an event type. No provider uses it.
const probeValue = "__probe__"

// defaultMaxMutations bounds one case's self-test.
const defaultMaxMutations = 400

// MutationStats is what one AssertMutationsDetected run did.
type MutationStats struct {
	// Candidates is how many mutations the case's report yielded, and
	// Generated how many of them ran (after sampling down to MaxMutations).
	Candidates, Generated int
	// Detected is how many made the check fail.
	Detected int
	// Undetected describes the rest.
	Undetected []string
	// Opaque are the case's opaque regions, which no mutation touches.
	Opaque []string
}

// AssertMutationsDetected proves that check — the provider's whole per-case
// check, goldens aside — notices drift in every part of c's body the strict
// walk claims to watch. It derives mutations from c's Report:
//
//   - an unknown key injected into every object a struct (or a Watched
//     declaration) models;
//   - a type flip of every modeled node (string <-> number, bool/object/array
//     -> string), and a fractional number in every integer field;
//   - "__probe__" at every path an EnumSpec covers;
//   - for a stream, an extra event of an undeclared type.
//
// Each distinct (mutation, path, Go type) is tried once, at its first
// occurrence. Opaque and Free regions are not mutated. It runs check on the
// unmutated body first (a control that must pass), then on each mutant, with
// a testing.TB that records failures instead of reporting them, and fails t
// for every mutant the check let through. Mutants are not live, use a copy of
// c.Meta that records nothing, and skip shape diffs and stale-gap checks, so
// they cannot disturb the real case's bookkeeping. check must not compare
// goldens: a golden is not a drift detector in live mode.
func (s *Strict) AssertMutationsDetected(t testing.TB, c Case, check func(t testing.TB, c Case)) MutationStats {
	t.Helper()
	var st MutationStats
	if c.mutant {
		return st
	}
	rep, err := s.Report(c)
	if err != nil {
		t.Fatalf("%v", err)
	}
	st.Opaque = rep.Opaque

	codec := SSE
	if s.Codec != nil {
		codec = *s.Codec
	}
	stream := c.IsStream && !c.IsError()
	base := c.Body
	var events []Event
	if stream {
		events = codec.Decode(c.Body)
		docs := s.Docs(c)
		if len(docs) != len(events) {
			t.Fatalf("contracttest: Strict.Docs returned %d documents for a stream of %d events; "+
				"the mutation self-test needs one per event, in Codec order", len(docs), len(events))
		}
		for i := range docs {
			if !bytes.Equal(docs[i].Data, events[i].Data) {
				t.Fatalf("contracttest: Strict.Docs document %d is not the data of event %d", i, i)
			}
		}
		base = codec.Encode(events)
	}

	type mutant struct {
		desc string
		body []byte
	}
	var muts []mutant
	if stream && len(events) > 0 {
		// First, so sampling always keeps it.
		probe := Event{Data: []byte(`{"type":"` + probeValue + `"}`)}
		if events[0].Name != "" {
			probe.Name = probeValue
		}
		withProbe := append(append(append([]Event{}, events[:1]...), probe), events[1:]...)
		muts = append(muts, mutant{fmt.Sprintf("%s %q after the first event", mutEvent, probeValue), codec.Encode(withProbe)})
	}
	for _, tg := range rep.targets {
		body, err := mutateBody(base, events, codec, tg)
		if err != nil {
			t.Errorf("contracttest: mutating %s at %s: %v", tg.kind, tg.path, err)
			continue
		}
		muts = append(muts, mutant{fmt.Sprintf("%s at %s (%s)", tg.kind, tg.path, tg.want), body})
	}
	st.Candidates = len(muts)
	max := s.MaxMutations
	if max <= 0 {
		max = defaultMaxMutations
	}
	if len(muts) > max {
		sampled := make([]mutant, max)
		for i := range sampled {
			sampled[i] = muts[i*len(muts)/max]
		}
		muts = sampled
	}
	st.Generated = len(muts)

	if failed, msg := runProbe(t, check, mutantCase(c, base)); failed {
		t.Errorf("contracttest: mutation self-test: the check fails on the unmutated body, so mutations prove nothing: %s", msg)
		return st
	}
	for _, m := range muts {
		if failed, _ := runProbe(t, check, mutantCase(c, m.body)); failed {
			st.Detected++
		} else {
			st.Undetected = append(st.Undetected, m.desc)
		}
	}

	sampled := ""
	if st.Generated < st.Candidates {
		sampled = fmt.Sprintf(", sampled from %d", st.Candidates)
	}
	t.Logf("mutation self-test: %d of %d mutations detected%s; %d opaque regions not mutated",
		st.Detected, st.Generated, sampled, len(st.Opaque))
	if len(st.Undetected) > 0 {
		t.Errorf("mutation self-test: %d of %d mutations went unnoticed, so these parts of the response are not actually watched:\n  %s\n"+
			"Wire the missing check into the provider's check function, or declare the region Free or opaque if nothing reads it.",
			len(st.Undetected), st.Generated, strings.Join(st.Undetected, "\n  "))
	}
	if c.Meta.suite != nil {
		c.Meta.suite.noteMutations(st)
	}
	return st
}

// mutantCase is c with body, marked as a mutant.
func mutantCase(c Case, body []byte) Case {
	m := c
	m.Body = body
	m.mutant = true
	m.Meta = c.Meta.mutantCopy()
	if c.Request != nil {
		var req models.ChatCompletionRequest
		if err := json.Unmarshal(c.RawRequest, &req); err == nil {
			m.Request = &req
		}
	}
	return m
}

// mutateBody applies tg to the document it was found in.
func mutateBody(body []byte, events []Event, codec Codec, tg target) ([]byte, error) {
	if tg.doc < 0 {
		return mutateJSON(body, tg)
	}
	data, err := mutateJSON(events[tg.doc].Data, tg)
	if err != nil {
		return nil, err
	}
	out := append([]Event(nil), events...)
	out[tg.doc] = Event{Name: events[tg.doc].Name, Data: data}
	return codec.Encode(out), nil
}

func mutateJSON(raw []byte, tg target) ([]byte, error) {
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		return nil, err
	}
	v, err := setAt(v, tg.ptr, func(node any) (any, error) {
		switch tg.kind {
		case mutUnknownKey:
			obj, ok := node.(map[string]any)
			if !ok {
				return nil, fmt.Errorf("not an object")
			}
			cp := make(map[string]any, len(obj)+1)
			for k, x := range obj {
				cp[k] = x
			}
			cp[probeValue] = json.Number("1")
			return cp, nil
		case mutTypeFlip:
			if tg.json == KindString {
				return json.Number("12345"), nil
			}
			return probeValue, nil
		case mutFraction:
			return json.Number("1.5"), nil
		default: // mutEnum
			return probeValue, nil
		}
	})
	if err != nil {
		return nil, err
	}
	var b bytes.Buffer
	enc := json.NewEncoder(&b)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(v); err != nil {
		return nil, err
	}
	return bytes.TrimSuffix(b.Bytes(), []byte("\n")), nil
}

// setAt replaces the node at ptr (string keys, int indices) with f's result,
// copying the containers on the way so the original is untouched.
func setAt(v any, ptr []any, f func(any) (any, error)) (any, error) {
	if len(ptr) == 0 {
		return f(v)
	}
	switch tok := ptr[0].(type) {
	case string:
		obj, ok := v.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("no object at %q", tok)
		}
		child, err := setAt(obj[tok], ptr[1:], f)
		if err != nil {
			return nil, err
		}
		cp := make(map[string]any, len(obj))
		for k, x := range obj {
			cp[k] = x
		}
		cp[tok] = child
		return cp, nil
	case int:
		arr, ok := v.([]any)
		if !ok || tok >= len(arr) {
			return nil, fmt.Errorf("no array element %d", tok)
		}
		child, err := setAt(arr[tok], ptr[1:], f)
		if err != nil {
			return nil, err
		}
		cp := append([]any(nil), arr...)
		cp[tok] = child
		return cp, nil
	}
	return nil, fmt.Errorf("bad pointer token %v", ptr[0])
}

// runProbe runs check on c with a probeTB and reports whether it failed, and
// how, first.
func runProbe(t testing.TB, check func(testing.TB, Case), c Case) (bool, string) {
	p := &probeTB{TB: t}
	isolated(func() { check(p, c) })
	for {
		p.mu.Lock()
		n := len(p.cleanups)
		if n == 0 {
			p.mu.Unlock()
			break
		}
		fn := p.cleanups[n-1]
		p.cleanups = p.cleanups[:n-1]
		p.mu.Unlock()
		isolated(fn)
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.failed, p.first
}

// isolated runs f on its own goroutine, so FailNow's runtime.Goexit ends only
// f.
func isolated(f func()) {
	done := make(chan struct{})
	go func() {
		defer close(done)
		f()
	}()
	<-done
}

// probeTB is a testing.TB that records failures instead of reporting them.
// Anything it does not override (Name, TempDir, Context) reaches the real
// test.
type probeTB struct {
	testing.TB

	mu       sync.Mutex
	failed   bool
	skipped  bool
	first    string
	cleanups []func()
}

func (p *probeTB) fail(msg string) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if !p.failed {
		p.first = msg
	}
	p.failed = true
}

func (p *probeTB) Helper()                        {}
func (p *probeTB) Log(...any)                     {}
func (p *probeTB) Logf(string, ...any)            {}
func (p *probeTB) Error(args ...any)              { p.fail(fmt.Sprint(args...)) }
func (p *probeTB) Errorf(format string, a ...any) { p.fail(fmt.Sprintf(format, a...)) }
func (p *probeTB) Fail()                          { p.fail("Fail called") }
func (p *probeTB) FailNow()                       { p.fail("FailNow called"); runtime.Goexit() }
func (p *probeTB) Fatal(args ...any)              { p.fail(fmt.Sprint(args...)); runtime.Goexit() }
func (p *probeTB) Fatalf(format string, a ...any) {
	p.fail(fmt.Sprintf(format, a...))
	runtime.Goexit()
}
func (p *probeTB) Skip(...any)          { p.SkipNow() }
func (p *probeTB) Skipf(string, ...any) { p.SkipNow() }
func (p *probeTB) SkipNow() {
	p.mu.Lock()
	p.skipped = true
	p.mu.Unlock()
	runtime.Goexit()
}

func (p *probeTB) Failed() bool {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.failed
}

func (p *probeTB) Skipped() bool {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.skipped
}

func (p *probeTB) Cleanup(fn func()) {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.cleanups = append(p.cleanups, fn)
}

// noteMutations adds one case's self-test to the suite's summary.
func (s *suite) noteMutations(st MutationStats) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.mutCases++
	s.mutRun += st.Generated
	s.mutDetected += st.Detected
	for _, p := range st.Opaque {
		if s.mutOpaque == nil {
			s.mutOpaque = map[string]bool{}
		}
		s.mutOpaque[p] = true
	}
}
