package metrics

import (
	"fmt"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

// Registry holds all Prometheus-compatible metrics.
type Registry struct {
	startTime  time.Time
	counters   sync.Map // name → *CounterVec
	histograms sync.Map // name → *Histogram
}

// NewRegistry creates a new metrics registry.
func NewRegistry() *Registry {
	return &Registry{startTime: time.Now()}
}

// CounterInc increments a counter by 1.
func (r *Registry) CounterInc(name, help string, labels map[string]string) {
	r.CounterAdd(name, help, labels, 1)
}

// CounterAdd adds a value to a counter.
func (r *Registry) CounterAdd(name, help string, labels map[string]string, v int64) {
	if v <= 0 {
		return
	}
	raw, _ := r.counters.LoadOrStore(name, &CounterVec{help: help})
	cv := raw.(*CounterVec)
	key := labelsKey(labels)
	rawVal, _ := cv.values.LoadOrStore(key, &counterEntry{labels: labels})
	entry := rawVal.(*counterEntry)
	entry.value.Add(v)
}

// HistogramObserve records a value in a histogram.
func (r *Registry) HistogramObserve(name, help string, labels map[string]string, value float64) {
	raw, _ := r.histograms.LoadOrStore(name, newHistogram(help))
	h := raw.(*Histogram)
	h.observe(labels, value)
}

// Render outputs all metrics in Prometheus text exposition format.
func (r *Registry) Render() string {
	var b strings.Builder

	// Uptime gauge.
	uptime := time.Since(r.startTime).Seconds()
	b.WriteString("# HELP agentcc_uptime_seconds Seconds since gateway start\n")
	b.WriteString("# TYPE agentcc_uptime_seconds gauge\n")
	fmt.Fprintf(&b, "agentcc_uptime_seconds %.1f\n", uptime)

	// Counters sorted by name.
	var counterNames []string
	r.counters.Range(func(key, _ any) bool {
		counterNames = append(counterNames, key.(string))
		return true
	})
	sort.Strings(counterNames)

	for _, name := range counterNames {
		raw, _ := r.counters.Load(name)
		cv := raw.(*CounterVec)
		fmt.Fprintf(&b, "# HELP %s %s\n", name, cv.help)
		fmt.Fprintf(&b, "# TYPE %s counter\n", name)

		entries := cv.sortedEntries()
		for _, e := range entries {
			lbl := formatLabels(e.labels)
			if lbl == "" {
				fmt.Fprintf(&b, "%s %d\n", name, e.value.Load())
			} else {
				fmt.Fprintf(&b, "%s{%s} %d\n", name, lbl, e.value.Load())
			}
		}
	}

	// Histograms sorted by name.
	var histNames []string
	r.histograms.Range(func(key, _ any) bool {
		histNames = append(histNames, key.(string))
		return true
	})
	sort.Strings(histNames)

	for _, name := range histNames {
		raw, _ := r.histograms.Load(name)
		h := raw.(*Histogram)
		h.render(&b, name)
	}

	return b.String()
}

// CounterVec is a counter with label dimensions.
type CounterVec struct {
	help   string
	values sync.Map // labelsKey → *counterEntry
}

type counterEntry struct {
	labels map[string]string
	value  atomic.Int64
}

func (cv *CounterVec) sortedEntries() []*counterEntry {
	var entries []*counterEntry
	cv.values.Range(func(_, val any) bool {
		entries = append(entries, val.(*counterEntry))
		return true
	})
	sort.Slice(entries, func(i, j int) bool {
		return labelsKey(entries[i].labels) < labelsKey(entries[j].labels)
	})
	return entries
}

// Histogram tracks value distributions in buckets.
type Histogram struct {
	help    string
	buckets []float64 // upper bounds
	mu      sync.Mutex
	entries map[string]*histogramEntry
}

type histogramEntry struct {
	labels  map[string]string
	buckets []atomic.Int64 // count per bucket
	count   atomic.Int64
	sum     atomic.Int64 // microseconds for precision
}

// Default latency buckets (ms).
var defaultBuckets = []float64{5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000}

func newHistogram(help string) *Histogram {
	return &Histogram{
		help:    help,
		buckets: defaultBuckets,
		entries: make(map[string]*histogramEntry),
	}
}

func (h *Histogram) observe(labels map[string]string, value float64) {
	key := labelsKey(labels)

	h.mu.Lock()
	entry, ok := h.entries[key]
	if !ok {
		entry = &histogramEntry{
			labels:  labels,
			buckets: make([]atomic.Int64, len(h.buckets)),
		}
		h.entries[key] = entry
	}
	h.mu.Unlock()

	entry.count.Add(1)
	entry.sum.Add(int64(value * 1000)) // store as microseconds

	// Increment only the first matching bucket; rendering does cumulative.
	for i, bound := range h.buckets {
		if value <= bound {
			entry.buckets[i].Add(1)
			break
		}
	}
}

func (h *Histogram) render(b *strings.Builder, name string) {
	fmt.Fprintf(b, "# HELP %s %s\n", name, h.help)
	fmt.Fprintf(b, "# TYPE %s histogram\n", name)

	h.mu.Lock()
	// Sort entries for deterministic output.
	keys := make([]string, 0, len(h.entries))
	for k := range h.entries {
		keys = append(keys, k)
	}
	sort.Strings(keys)

	entries := make([]*histogramEntry, len(keys))
	for i, k := range keys {
		entries[i] = h.entries[k]
	}
	h.mu.Unlock()

	for _, entry := range entries {
		lbl := formatLabels(entry.labels)
		var cumulative int64
		for i, bound := range h.buckets {
			cumulative += entry.buckets[i].Load()
			if lbl == "" {
				fmt.Fprintf(b, "%s_bucket{le=\"%.0f\"} %d\n", name, bound, cumulative)
			} else {
				fmt.Fprintf(b, "%s_bucket{%s,le=\"%.0f\"} %d\n", name, lbl, bound, cumulative)
			}
		}
		count := entry.count.Load()
		sumMs := float64(entry.sum.Load()) / 1000.0
		if lbl == "" {
			fmt.Fprintf(b, "%s_bucket{le=\"+Inf\"} %d\n", name, count)
			fmt.Fprintf(b, "%s_sum %.3f\n", name, sumMs)
			fmt.Fprintf(b, "%s_count %d\n", name, count)
		} else {
			fmt.Fprintf(b, "%s_bucket{%s,le=\"+Inf\"} %d\n", name, lbl, count)
			fmt.Fprintf(b, "%s_sum{%s} %.3f\n", name, lbl, sumMs)
			fmt.Fprintf(b, "%s_count{%s} %d\n", name, lbl, count)
		}
	}
}

// labelsKey produces a deterministic string key from label pairs.
func labelsKey(labels map[string]string) string {
	if len(labels) == 0 {
		return ""
	}
	keys := make([]string, 0, len(labels))
	for k := range labels {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	var b strings.Builder
	for i, k := range keys {
		if i > 0 {
			b.WriteByte(',')
		}
		b.WriteString(k)
		b.WriteByte('=')
		b.WriteString(labels[k])
	}
	return b.String()
}

// formatLabels formats labels for Prometheus output (key="value").
func formatLabels(labels map[string]string) string {
	if len(labels) == 0 {
		return ""
	}
	keys := make([]string, 0, len(labels))
	for k := range labels {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	var b strings.Builder
	for i, k := range keys {
		if i > 0 {
			b.WriteByte(',')
		}
		fmt.Fprintf(&b, "%s=\"%s\"", k, labels[k])
	}
	return b.String()
}
