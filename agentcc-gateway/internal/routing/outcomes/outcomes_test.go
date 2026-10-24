package outcomes

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing/affinity"
)

type fakeClock struct {
	mu sync.Mutex
	t  time.Time
}

func newClock() *fakeClock { return &fakeClock{t: time.Date(2026, 10, 16, 9, 0, 0, 0, time.UTC)} }

func (c *fakeClock) now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.t
}

func (c *fakeClock) advance(d time.Duration) {
	c.mu.Lock()
	c.t = c.t.Add(d)
	c.mu.Unlock()
}

var bg = context.Background()

func approx(got, want float64) bool { return math.Abs(got-want) < 1e-9 }

func chatResp(content string, finish string, usage *models.Usage, toolCalls ...models.ToolCall) *models.ChatCompletionResponse {
	raw, _ := json.Marshal(content)
	return &models.ChatCompletionResponse{
		Choices: []models.Choice{{Message: models.Message{Role: "assistant", Content: raw, ToolCalls: toolCalls}, FinishReason: finish}},
		Usage:   usage,
	}
}

func TestOutcomeClassifyEmptyAndFiltered(t *testing.T) {
	tool := models.ToolCall{ID: "call_1", Type: "function"}
	zero := &models.Usage{PromptTokens: 10}
	some := &models.Usage{PromptTokens: 10, CompletionTokens: 3}
	for name, c := range map[string]struct {
		resp *models.ChatCompletionResponse
		want Class
		ok   bool
	}{
		"answered":                       {chatResp("hi", "stop", some), ClassOK, true},
		"no completion tokens":           {chatResp("", "stop", zero), ClassEmpty, true},
		"zero tokens but a tool call":    {chatResp("", "stop", zero, tool), ClassOK, true},
		"zero tokens, stopped for tools": {chatResp("", "tool_calls", zero), ClassOK, true},
		"content filter":                 {chatResp("", "content_filter", zero), ClassContentFilter, true},
		"content filter with text":       {chatResp("partial", "content_filter", some), ClassContentFilter, true},
		"stream without usage, no text":  {chatResp("", "stop", nil), ClassEmpty, true},
		"stream without usage, text":     {chatResp("hi", "stop", nil), ClassOK, true},
		"no evidence":                    {&models.ChatCompletionResponse{}, "", false},
		"no response":                    {nil, "", false},
	} {
		got, ok := Classify(c.resp)
		if got != c.want || ok != c.ok {
			t.Errorf("%s: Classify = %q, %v; want %q, %v", name, got, ok, c.want, c.ok)
		}
	}
	filteredSecond := chatResp("hi", "stop", some)
	filteredSecond.Choices = append(filteredSecond.Choices, models.Choice{Index: 1, FinishReason: "content_filter"})
	if got, _ := Classify(filteredSecond); got != ClassContentFilter {
		t.Errorf("a filtered second choice: Classify = %q, want content_filter", got)
	}
}

func TestOutcomeKeyShapes(t *testing.T) {
	for got, want := range map[string]string{
		Key{Org: "o", Agent: "researcher", Step: models.StepAct, Model: "vendor/m"}.String(): "aout:{o}:researcher:act:vendor/m",
		Key{Org: "o", Model: "vendor/m"}.String():                                            "aout:{o}:_:other:vendor/m",
		LastKey("o", "req-1"):                        "aout:last:{o}:req-1",
		PrevKey("o", "run:r1", affinity.ClassVerify): "aout:prev:{o}:run:r1:verify",
	} {
		if got != want {
			t.Errorf("key %q, want %q", got, want)
		}
	}
	if got := HalfLife(86400); got != 12*time.Hour {
		t.Errorf("HalfLife(86400) = %v, want 12h", got)
	}
}

func TestOutcomeDecayHalvesAfterHalfLife(t *testing.T) {
	clock := newClock()
	s := NewStore(nil, 100, clock.now)
	k := Key{Org: "o", Agent: "a", Step: models.StepAct, Model: "vendor/m"}
	hl := HalfLife(3600)
	for range 4 {
		s.Record(bg, k, ClassOK.Outcome(100), hl)
	}
	for range 2 {
		s.Record(bg, k, Outcome{ErrorClass: models.ErrClassUpstream5xx}, hl)
	}
	before := s.Counters(bg, k, hl)
	clock.advance(hl)
	after := s.Counters(bg, k, hl)
	if !approx(before.N, 6) || !approx(after.N, 3) || !approx(after.Errors, 1) || !approx(after.Latency[LatencyBin(100)], 2) {
		t.Fatalf("after one half-life: N %v -> %v, errors %v, latency %v; want 6 -> 3, 1, 2", before.N, after.N, after.Errors, after.Latency[LatencyBin(100)])
	}
	// Rates do not decay; a new sample lands on the decayed counts.
	if st := s.Stats(bg, k, hl); !approx(st.ErrRate, 1.0/3) {
		t.Errorf("ErrRate = %v, want 1/3", st.ErrRate)
	}
	s.Record(bg, k, ClassEmpty.Outcome(100), hl)
	if c := s.Counters(bg, k, hl); !approx(c.N, 4) || !approx(c.Empty, 1) {
		t.Errorf("after one more sample: N %v, empty %v; want 4, 1", c.N, c.Empty)
	}
}

func TestOutcomeStatsRatesAndMedian(t *testing.T) {
	s := NewStore(nil, 100, newClock().now)
	k := Key{Org: "o", Model: "vendor/m"}
	hl := HalfLife(86400)
	for _, ms := range []int64{100, 100, 100, 2000} {
		s.Record(bg, k, ClassOK.Outcome(ms), hl)
	}
	s.Record(bg, k, ClassEmpty.Outcome(150), hl)
	s.Record(bg, k, ClassContentFilter.Outcome(150), hl)
	s.Record(bg, k, Outcome{ErrorClass: models.ErrClassTimeout, LatencyMs: 30000}, hl)
	s.Judge(bg, "o", "g1", Last{Model: "vendor/m"}, VerdictVerifyFailed, hl) // no Last record: not counted
	st := s.Stats(bg, k, hl)
	if !approx(st.N, 7) || !approx(st.ErrRate, 1.0/7) || !approx(st.EmptyRate, 1.0/7) || !approx(st.FilterRate, 1.0/7) || st.VerifyFailedRate != 0 {
		t.Fatalf("Stats = %+v", st)
	}
	if st.P50Ms < 95 || st.P50Ms > 160 { // within the 100 ms bin, errors excluded
		t.Errorf("P50Ms = %v, want about 100", st.P50Ms)
	}
}

func TestOutcomeJudgeCountsOneVerdictPerGeneration(t *testing.T) {
	s := NewStore(nil, 100, newClock().now)
	hl := HalfLife(86400)
	act := Last{Agent: "a", Run: "r1", Step: models.StepAct, Model: "vendor/m", Class: ClassOK}
	s.Remember(bg, "o", "g1", act)
	if s.Judge(bg, "o", "g1", act, VerdictOK, hl) {
		t.Error("ok changed a counter")
	}
	if !s.Judge(bg, "o", "g1", act, VerdictVerifyFailed, hl) {
		t.Fatal("verify_failed was not counted")
	}
	if s.Judge(bg, "o", "g1", act, VerdictBad, hl) || s.Judge(bg, "o", "g1", act, VerdictVerifyFailed, hl) {
		t.Error("a second negative verdict for one generation was counted")
	}
	if c := s.Counters(bg, act.Key("o"), hl); !approx(c.VerifyFailed, 1) || c.N != 0 {
		t.Errorf("counters %+v, want verify_failed 1 and no samples", c)
	}

	empty := Last{Agent: "a", Step: models.StepAct, Model: "vendor/m", Class: ClassEmpty}
	s.Remember(bg, "o", "g2", empty)
	if s.Judge(bg, "o", "g2", empty, VerdictEmpty, hl) {
		t.Error("empty was counted again for a response classified empty")
	}
	if !s.Judge(bg, "o", "g2", empty, VerdictVerifyFailed, hl) {
		t.Error("a verdict skipped as already counted used up the generation's claim")
	}
	s.Remember(bg, "o", "g3", act)
	if !s.Judge(bg, "o", "g3", act, VerdictRetried, hl) {
		t.Error("retried was not counted")
	}
	if c := s.Counters(bg, act.Key("o"), hl); !approx(c.Empty, 1) || !approx(c.VerifyFailed, 2) {
		t.Errorf("counters %+v, want empty 1 and verify_failed 2", c)
	}
}

// A generation's Last record lives at most an hour. The
// previous-request pointer lives as long, since it is only useful while the
// record it names is kept.
func TestOutcomeLastAndPrevExpireAfterAnHour(t *testing.T) {
	if LastTTL > time.Hour {
		t.Fatalf("LastTTL = %v, want at most 1h", LastTTL)
	}
	clock := newClock()
	s := NewStore(nil, 100, clock.now)
	s.Remember(bg, "o", "g1", Last{Model: "vendor/m"})
	s.SetPrev(bg, "o", "s1", affinity.ClassMain, "g1")
	clock.advance(LastTTL - time.Second)
	if _, ok := s.Last(bg, "o", "g1"); !ok {
		t.Fatal("the Last record expired early")
	}
	if id, ok := s.Prev(bg, "o", "s1", affinity.ClassMain); !ok || id != "g1" {
		t.Fatalf("Prev = %q, %v; want g1", id, ok)
	}
	clock.advance(time.Second)
	if _, ok := s.Last(bg, "o", "g1"); ok {
		t.Error("the Last record outlived its TTL")
	}
	if _, ok := s.Prev(bg, "o", "s1", affinity.ClassMain); ok {
		t.Error("the previous-generation pointer outlived its TTL")
	}

	// Measured on the clock, not the constant: an hour and a second after a
	// write, nothing is left.
	clock = newClock()
	s = NewStore(nil, 100, clock.now)
	s.Remember(bg, "o", "g2", Last{Model: "vendor/m"})
	s.SetPrev(bg, "o", "s2", affinity.ClassMain, "g2")
	clock.advance(time.Hour + time.Second)
	if _, ok := s.Last(bg, "o", "g2"); ok {
		t.Error("the Last record outlived an hour")
	}
	if _, ok := s.Prev(bg, "o", "s2", affinity.ClassMain); ok {
		t.Error("the previous-generation pointer outlived an hour")
	}
}

// downBackend fails every call.
type downBackend struct{}

var errDown = errors.New("backend down")

func (downBackend) AddOutcome(context.Context, string, Delta, int64, int64, time.Duration) error {
	return errDown
}
func (downBackend) OutcomeCounters(context.Context, string) (Counters, bool, error) {
	return Counters{}, false, errDown
}
func (downBackend) SetLast(context.Context, string, Last, time.Duration) error { return errDown }
func (downBackend) GetLast(context.Context, string) (Last, bool, error) {
	return Last{}, false, errDown
}
func (downBackend) ClaimJudgement(context.Context, string) (bool, bool, error) {
	return false, false, errDown
}
func (downBackend) SetPrev(context.Context, string, string, time.Duration) error { return errDown }
func (downBackend) GetPrev(context.Context, string) (string, bool, error)        { return "", false, errDown }

func TestOutcomeBackendDownFallsBackToMemory(t *testing.T) {
	s := NewStore(downBackend{}, 100, newClock().now)
	hl := HalfLife(86400)
	k := Key{Org: "o", Step: models.StepAct, Model: "vendor/m"}
	s.Record(bg, k, ClassOK.Outcome(50), hl)
	last := Last{Step: models.StepAct, Model: "vendor/m", Class: ClassOK}
	s.Remember(bg, "o", "g1", last)
	s.SetPrev(bg, "o", "s1", affinity.ClassMain, "g1")
	if got, ok := s.Last(bg, "o", "g1"); !ok || got != last {
		t.Fatalf("Last = %+v, %v", got, ok)
	}
	if id, _ := s.Prev(bg, "o", "s1", affinity.ClassMain); id != "g1" {
		t.Fatalf("Prev = %q", id)
	}
	if !s.Judge(bg, "o", "g1", last, VerdictVerifyFailed, hl) || s.Judge(bg, "o", "g1", last, VerdictVerifyFailed, hl) {
		t.Fatal("the in-memory claim did not count exactly one verdict")
	}
	if c := s.Counters(bg, k, hl); !approx(c.N, 1) || !approx(c.VerifyFailed, 1) {
		t.Fatalf("counters %+v, want one sample and one failed verification", c)
	}
}

func TestOutcomeEventIDMatchesPythonUUID5(t *testing.T) {
	// python3 -c 'import uuid; print(uuid.uuid5(uuid.NAMESPACE_URL,
	//   "agentcc:outcome:org-a:req-1:feedback:1792056614201"))'
	got := EventID("org-a", "req-1", SourceFeedback, time.UnixMilli(1792056614201))
	if want := "a8ccecb2-32df-5223-a600-d12f0b9285d0"; got != want {
		t.Errorf("EventID = %s, want %s", got, want)
	}
}

func TestOutcomeEventCarriesNoContent(t *testing.T) {
	score := 0.25
	ev := NewEvent("org-a", "req-1", VerdictVerifyFailed, SourceFeedback, &score,
		Last{Agent: "researcher", Run: "R2", Step: models.StepAct, Model: "vendor/m"}, time.UnixMilli(1792056614201))
	raw, _ := json.Marshal(ev)
	var fields map[string]any
	_ = json.Unmarshal(raw, &fields)
	want := map[string]any{"event_id": "a8ccecb2-32df-5223-a600-d12f0b9285d0", "org_id": "org-a", "generation_id": "req-1",
		"outcome": "verify_failed", "source": "feedback", "score": 0.25, "agent_run_id": "R2", "agent_id": "researcher",
		"agent_step": "act", "model": "vendor/m", "ts_ms": float64(1792056614201)}
	if len(fields) != len(want) {
		t.Fatalf("event fields %v, want %v", fields, want)
	}
	for k, v := range want {
		if fields[k] != v {
			t.Errorf("%s = %v, want %v", k, fields[k], v)
		}
	}
}
