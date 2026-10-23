package ingestbench

import (
	"math"
	"math/bits"
	"strings"
	"sync"
	"time"
)

// The synthetic workload is a seeded model, not a measured customer
// distribution. Every draw comes from a per-span integer generator seeded by
// (seed, sequence), so the same seed, sequence number and timestamp give the
// same bytes on every platform: no floating point is used for any draw.

// splitmix64 is a small, well-mixed integer generator.
type splitmix struct{ s uint64 }

func (r *splitmix) next() uint64 {
	r.s += 0x9e3779b97f4a7c15
	z := r.s
	z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9
	z = (z ^ (z >> 27)) * 0x94d049bb133111eb
	return z ^ (z >> 31)
}

// below returns a uniform integer in [0, n).
func (r *splitmix) below(n uint64) uint64 {
	hi, _ := bits.Mul64(r.next(), n)
	return hi
}

func seeded(seed int64, stream uint64, index int64) *splitmix {
	r := &splitmix{s: uint64(seed) ^ (stream * 0xd1b54a32d192ed03)}
	r.s ^= uint64(index) * 0x8cb92ba72f3d8dd7
	r.next()
	return r
}

// Generator streams; distinct constants keep span, session and pool draws
// independent of each other.
const (
	streamSpan uint64 = iota + 1
	streamSession
	streamPool
)

// lognormalPermille is the multiplier (x1000) at 64 equal-probability quantiles
// of a lognormal distribution with median 1 and sigma 0.6. Sizes and durations
// draw from it, so they are right-skewed like real prompts, completions and
// latencies, with a bounded maximum (4.265x the median).
var lognormalPermille = [64]uint64{
	234, 303, 347, 383, 413, 441, 466, 490, 513, 535, 556, 577, 597, 618, 638, 657,
	677, 697, 716, 736, 756, 776, 796, 816, 836, 857, 878, 899, 921, 943, 965, 988,
	1012, 1036, 1061, 1086, 1112, 1139, 1167, 1196, 1226, 1257, 1289, 1323, 1359, 1396, 1435, 1477,
	1521, 1569, 1619, 1674, 1733, 1798, 1870, 1950, 2041, 2146, 2270, 2421, 2613, 2878, 3295, 4265,
}

const maxLognormalPermille = 4265

func lognormal(r *splitmix, median uint64) uint64 {
	return max(1, median*lognormalPermille[r.below(64)]/1000)
}

// Payload size classes: the MEDIAN input+output bytes of one span. A span's
// input is 3/4 and output 1/4 of the class median, each drawn independently
// from the lognormal table.
var payloadClassMedian = map[string]uint64{"small": 512, "medium": 4096, "large": 32768}

// maxSpanTextBytes bounds input+output text of one span for a payload profile.
func maxSpanTextBytes(payload string) int64 {
	median := payloadClassMedian[payload]
	if payload == "mixed" {
		median = payloadClassMedian["large"]
	}
	return int64(median * maxLognormalPermille / 1000)
}

var models = []string{"benchmark-model-small", "benchmark-model-medium", "benchmark-model-large"}

// durationMedianMS is the median span duration by kind.
var durationMedianMS = map[string]uint64{"AGENT": 2400, "CHAIN": 1500, "LLM": 900, "TOOL": 120, "RETRIEVER": 250}

var errorMessages = []string{"synthetic tool timeout", "synthetic provider rate limit", "synthetic invalid tool arguments"}

// spanPlan is everything about one span except its timestamps and text bytes.
type spanPlan struct {
	seq          int64
	position     int64 // index within the trace; 0 is the root
	parent       int64 // position of the parent, -1 for the root
	depth        int
	kind         string
	class        string
	model        string
	inputBytes   int
	outputBytes  int
	inputTokens  int64
	outputTokens int64
	duration     time.Duration
	isError      bool
	errorMessage string
	session      int64
	cohort       string
	user         int64
}

// treeParent returns the parent position and depth of a span position in a
// k-ary layout capped at TreeDepth: a span that would be deeper is attached to
// the nearest ancestor at depth TreeDepth-1.
func (c Config) treeParent(position int64) (parent int64, depth int) {
	if position == 0 {
		return -1, 0
	}
	fan := int64(c.FanOut)
	depthOf := func(p int64) int {
		d := 0
		for p > 0 {
			p = (p - 1) / fan
			d++
		}
		return d
	}
	parent = (position - 1) / fan
	pd := depthOf(parent)
	for pd > c.TreeDepth-1 {
		parent = (parent - 1) / fan
		pd--
	}
	return parent, pd + 1
}

func (c Config) ppm(fraction float64) uint64 {
	return uint64(math.Round(fraction * 1e6))
}

func (c Config) plan(seq int64) spanPlan {
	r := seeded(c.Seed, streamSpan, seq)
	p := spanPlan{seq: seq, position: seq % c.SpansPerTrace}
	p.parent, p.depth = c.treeParent(p.position)
	switch {
	case p.depth == 0:
		p.kind = "AGENT"
	case p.depth < c.TreeDepth && p.position*int64(c.FanOut)+1 < c.SpansPerTrace:
		p.kind = "CHAIN"
	default:
		switch roll := r.below(100); {
		case roll < 60:
			p.kind = "LLM"
		case roll < 85:
			p.kind = "TOOL"
		default:
			p.kind = "RETRIEVER"
		}
	}
	p.class = c.Payload
	if p.class == "mixed" {
		switch roll := r.below(1000000); {
		case roll < 700000:
			p.class = "small"
		case roll < 950000:
			p.class = "medium"
		default:
			p.class = "large"
		}
	}
	median := payloadClassMedian[p.class]
	p.inputBytes = int(lognormal(r, median*3/4))
	p.outputBytes = int(lognormal(r, median/4))
	if p.kind == "LLM" {
		switch roll := r.below(100); {
		case roll < 50:
			p.model = models[0]
		case roll < 85:
			p.model = models[1]
		default:
			p.model = models[2]
		}
		bpt := uint64(math.Round(c.BytesPerToken * 1000))
		p.inputTokens = int64((uint64(p.inputBytes)*1000 + bpt - 1) / bpt)
		p.outputTokens = int64((uint64(p.outputBytes)*1000 + bpt - 1) / bpt)
	}
	p.duration = time.Duration(min(lognormal(r, durationMedianMS[p.kind]*1000), uint64(maxSpanDuration/time.Microsecond))) * time.Microsecond
	if r.below(1000000) < c.ppm(c.ErrorRate) {
		p.isError = true
		p.errorMessage = errorMessages[r.below(uint64(len(errorMessages)))]
	}
	p.session = seq / (c.SpansPerTrace * c.TracesPerSession)
	s := seeded(c.Seed, streamSession, p.session)
	p.user = int64(s.below(1000))
	p.cohort = "control"
	if s.below(1000000) < c.ppm(c.CohortVariantFraction) {
		p.cohort = "variant"
	}
	return p
}

// maxSpanDuration bounds every synthetic span, so verification time windows can
// use a fixed margin around a request's generation time.
const maxSpanDuration = 30 * time.Second

// textPool is a seeded pool of distinct word-like blocks. Spans assemble their
// text from pool blocks, so texts differ from span to span but share vocabulary
// and repeated passages the way prompts built from templates do.
type textPool struct {
	blocks []string
	bytes  int64
}

var poolCache sync.Map // key poolKey -> *textPool

type poolKey struct {
	seed   int64
	blocks int
}

func (c Config) pool() *textPool {
	key := poolKey{c.Seed, c.TextPoolBlocks}
	if p, ok := poolCache.Load(key); ok {
		return p.(*textPool)
	}
	r := seeded(c.Seed, streamPool, 0)
	const consonants, vowels = "bcdfghjklmnprstvwz", "aeiou"
	vocab := make([]string, 4096)
	for i := range vocab {
		var b strings.Builder
		for s := 0; s < 1+int(r.below(4)); s++ {
			b.WriteByte(consonants[r.below(uint64(len(consonants)))])
			b.WriteByte(vowels[r.below(uint64(len(vowels)))])
			if r.below(3) == 0 {
				b.WriteByte(consonants[r.below(uint64(len(consonants)))])
			}
		}
		vocab[i] = b.String()
	}
	p := &textPool{blocks: make([]string, c.TextPoolBlocks)}
	for i := range p.blocks {
		target := 64 + int(r.below(961))
		var b strings.Builder
		b.Grow(target + 16)
		for b.Len() < target {
			// Product of two uniforms skews toward frequent (low-index) words.
			idx := r.below(uint64(len(vocab))) * r.below(uint64(len(vocab))) / uint64(len(vocab))
			b.WriteString(vocab[idx])
			switch r.below(20) {
			case 0:
				b.WriteString(". ")
			case 1:
				b.WriteString(", ")
			case 2:
				b.WriteString("\n")
			default:
				b.WriteByte(' ')
			}
		}
		p.blocks[i] = b.String()
		p.bytes += int64(len(p.blocks[i]))
	}
	actual, _ := poolCache.LoadOrStore(key, p)
	return actual.(*textPool)
}

// text returns exactly n bytes of ASCII text for one span field.
func (c Config) text(seq int64, field uint64, n int) string {
	pool := c.pool()
	r := seeded(c.Seed^int64(field), streamSpan, seq)
	var b strings.Builder
	b.Grow(n + 1024)
	for b.Len() < n {
		b.WriteString(pool.blocks[r.below(uint64(len(pool.blocks)))])
	}
	return b.String()[:n]
}

// valueHistogram is a bounded log-linear histogram of non-negative integers:
// exact below 16, then 16 sub-buckets per power of two (<= 6.25% relative
// error, reported as the bucket's upper edge, i.e. never understated).
type valueHistogram struct {
	counts [1024]int64
	n      int64
	sum    float64
	min    int64
	max    int64
}

func histogramBucket(v int64) int {
	if v < 16 {
		return int(max(v, 0))
	}
	e := bits.Len64(uint64(v)) - 1
	m := (uint64(v) >> (e - 4)) & 15
	return 16 + (e-4)*16 + int(m)
}

func histogramUpper(b int) int64 {
	if b < 16 {
		return int64(b)
	}
	e := (b-16)/16 + 4
	m := int64((b - 16) % 16)
	return ((16+m+1)<<(e-4) - 1)
}

func (h *valueHistogram) add(v int64) {
	v = max(v, 0)
	h.counts[histogramBucket(v)]++
	if h.n == 0 || v < h.min {
		h.min = v
	}
	h.max = max(h.max, v)
	h.n++
	h.sum += float64(v)
}

// Distribution is a realised distribution summary of generated values.
type Distribution struct {
	Count int64   `json:"count"`
	Min   int64   `json:"min"`
	Mean  float64 `json:"mean"`
	P50   int64   `json:"p50_upper_bound"`
	P95   int64   `json:"p95_upper_bound"`
	P99   int64   `json:"p99_upper_bound"`
	Max   int64   `json:"max"`
}

func (h *valueHistogram) summary() Distribution {
	d := Distribution{Count: h.n, Min: h.min, Max: h.max}
	if h.n == 0 {
		return d
	}
	d.Mean = h.sum / float64(h.n)
	q := func(f float64) int64 {
		target := int64(math.Ceil(f * float64(h.n)))
		var seen int64
		for b, n := range h.counts {
			seen += n
			if seen >= target {
				return min(histogramUpper(b), h.max)
			}
		}
		return h.max
	}
	d.P50, d.P95, d.P99 = q(.5), q(.95), q(.99)
	return d
}

// WorkloadStats are the REALISED distributions of the attempted spans, so a
// report states what was sent rather than what was configured.
type WorkloadStats struct {
	Spans               int64            `json:"spans"`
	Traces              int64            `json:"traces_touched"`
	Sessions            int64            `json:"sessions_touched"`
	SpansByKind         map[string]int64 `json:"spans_by_kind"`
	SpansByPayloadClass map[string]int64 `json:"spans_by_payload_class"`
	SpansByDepth        map[int]int64    `json:"spans_by_depth"`
	ErrorSpans          int64            `json:"error_spans"`
	ErrorFraction       float64          `json:"error_fraction"`
	VariantCohortSpans  int64            `json:"variant_cohort_spans"`
	VariantFraction     float64          `json:"variant_cohort_fraction"`
	InputBytes          Distribution     `json:"input_text_bytes"`
	OutputBytes         Distribution     `json:"output_text_bytes"`
	InputTokens         Distribution     `json:"llm_input_tokens"`
	OutputTokens        Distribution     `json:"llm_output_tokens"`
	DurationMS          Distribution     `json:"span_duration_ms"`
	RequestBodyBytes    Distribution     `json:"request_uncompressed_body_bytes"`
	RequestWireBytes    Distribution     `json:"request_wire_body_bytes"`
	TextPoolBlocks      int              `json:"text_pool_blocks"`
	TextPoolBytes       int64            `json:"text_pool_distinct_bytes"`
	// Generator health: request build time and how late each dispatch was
	// against its schedule. A large dispatch delay means the generator (or the
	// in-flight limit), not the collector, limited the achieved rate.
	BuildWorkers  int         `json:"generator_build_workers"`
	BuildLatency  Percentiles `json:"generator_request_build_latency"`
	DispatchDelay Percentiles `json:"dispatch_delay_behind_schedule"`
}

type workloadAccumulator struct {
	spans, errors, variant int64
	lastTrace, lastSession int64
	traces, sessions       int64
	kinds, classes         map[string]int64
	depths                 map[int]int64
	in, out, inTok, outTok valueHistogram
	duration, body, wire   valueHistogram
}

func newWorkloadAccumulator() *workloadAccumulator {
	return &workloadAccumulator{lastTrace: -1, lastSession: -1, kinds: map[string]int64{}, classes: map[string]int64{}, depths: map[int]int64{}}
}

// addSpans must be called in sequence order (the producer is sequential).
func (w *workloadAccumulator) addSpans(c Config, plans []spanPlan) {
	for _, p := range plans {
		w.spans++
		w.kinds[p.kind]++
		w.classes[p.class]++
		w.depths[p.depth]++
		if p.isError {
			w.errors++
		}
		if p.cohort == "variant" {
			w.variant++
		}
		if t := p.seq / c.SpansPerTrace; t != w.lastTrace {
			w.traces++
			w.lastTrace = t
		}
		if p.session != w.lastSession {
			w.sessions++
			w.lastSession = p.session
		}
		w.in.add(int64(p.inputBytes))
		w.out.add(int64(p.outputBytes))
		if p.kind == "LLM" {
			w.inTok.add(p.inputTokens)
			w.outTok.add(p.outputTokens)
		}
		w.duration.add(p.duration.Milliseconds())
	}
}

func (w *workloadAccumulator) addRequest(uncompressed, wire int) {
	w.body.add(int64(uncompressed))
	w.wire.add(int64(wire))
}

func (w *workloadAccumulator) stats(c Config) WorkloadStats {
	s := WorkloadStats{Spans: w.spans, Traces: w.traces, Sessions: w.sessions, SpansByKind: w.kinds, SpansByPayloadClass: w.classes,
		SpansByDepth: w.depths, ErrorSpans: w.errors, VariantCohortSpans: w.variant,
		InputBytes: w.in.summary(), OutputBytes: w.out.summary(), InputTokens: w.inTok.summary(), OutputTokens: w.outTok.summary(),
		DurationMS: w.duration.summary(), RequestBodyBytes: w.body.summary(), RequestWireBytes: w.wire.summary(), TextPoolBlocks: c.TextPoolBlocks}
	if w.spans > 0 {
		s.ErrorFraction = float64(w.errors) / float64(w.spans)
		s.VariantFraction = float64(w.variant) / float64(w.spans)
	}
	if c.TextPoolBlocks > 0 {
		s.TextPoolBytes = c.pool().bytes
	}
	return s
}
