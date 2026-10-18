package metrics

import (
	"fmt"
	"slices"
	"sort"
	"strings"
	"sync/atomic"

	"github.com/futureagi/agentcc-gateway/internal/video/capability"
)

type gauge struct {
	help  string
	value atomic.Int64
}
type videoDefinition struct {
	kind, help string
	labels     []string
}

var videoDefinitions = map[string]videoDefinition{
	"video_jobs_total":                 {"counter", "Accepted video jobs and lifecycle transitions", []string{"service", "model", "status"}},
	"video_state_duration_seconds":     {"histogram", "Time spent in a video state", []string{"state"}},
	"video_poll_requests_total":        {"counter", "Video provider poll requests", []string{"service", "outcome"}},
	"video_submission_unknown_total":   {"counter", "Video submissions with unknown outcomes", nil},
	"video_unresolved_total":           {"counter", "Video reconciliation deadlines exhausted", nil},
	"video_result_copy_failures_total": {"counter", "Video jobs with unavailable output", nil},
	"video_reserved_micros":            {"gauge", "Reserved video microdollars across shared storage", nil},
	"video_unsettled_micros":           {"gauge", "Unsettled video microdollars across shared storage", nil},
	"video_queue_age_seconds":          {"histogram", "Age of video work when claimed", nil},
	"video_lease_takeovers_total":      {"counter", "Video leases recovered after owner loss", nil},
}

func (r *Registry) registerVideo() {
	for name, d := range videoDefinitions {
		switch d.kind {
		case "counter":
			c := &CounterVec{help: d.help}
			if len(d.labels) == 0 {
				c.values.Store("", &counterEntry{})
			}
			r.counters.Store(name, c)
		case "histogram":
			h := newHistogram(d.help)
			h.buckets = []float64{1, 5, 10, 30, 60, 120, 300, 600, 1800, 3600, 7200}
			r.histograms.Store(name, h)
		case "gauge":
			r.gauges.Store(name, &gauge{help: d.help})
		}
	}
}
func videoLabels(d videoDefinition, labels map[string]string) map[string]string {
	out := make(map[string]string, len(d.labels))
	for _, name := range d.labels {
		value := labels[name]
		valid := false
		switch name {
		case "service":
			valid = value == "byteplus"
		case "model":
			valid = slices.Contains(capability.NewRegistry().Models("byteplus"), strings.TrimPrefix(value, "byteplus/"))
			if valid {
				value = strings.TrimPrefix(value, "byteplus/")
			}
		case "state", "status":
			valid = slices.Contains([]string{"submitting", "submission_unknown", "queued", "running", "completed", "failed", "cancelled"}, value)
		case "outcome":
			valid = slices.Contains([]string{"success", "error", "rate_limited", "schema_error", "timeout"}, value)
		}
		if !valid {
			value = "unknown"
		}
		out[name] = value
	}
	return out
}

// VideoEvent deliberately discards tenant, request and caller-defined labels.
func (r *Registry) VideoEvent(name string, labels map[string]string) {
	d, ok := videoDefinitions[name]
	if !ok || d.kind != "counter" {
		return
	}
	r.CounterInc(name, d.help, videoLabels(d, labels))
}
func (r *Registry) VideoObserve(name string, labels map[string]string, seconds float64) {
	d, ok := videoDefinitions[name]
	if !ok || d.kind != "histogram" {
		return
	}
	r.HistogramObserve(name, d.help, videoLabels(d, labels), max(seconds, 0))
}
func (r *Registry) VideoBalances(reserved, unsettled int64) {
	for name, value := range map[string]int64{"video_reserved_micros": reserved, "video_unsettled_micros": unsettled} {
		if g, ok := r.gauges.Load(name); ok {
			g.(*gauge).value.Store(value)
		}
	}
}
func (r *Registry) renderGauges(b *strings.Builder) {
	names := []string{}
	r.gauges.Range(func(k, v any) bool { names = append(names, k.(string)); return true })
	sort.Strings(names)
	for _, name := range names {
		raw, _ := r.gauges.Load(name)
		g := raw.(*gauge)
		fmt.Fprintf(b, "# HELP %s %s\n# TYPE %s gauge\n%s %d\n", name, g.help, name, name, g.value.Load())
	}
}
