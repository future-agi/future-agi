package ingestbench

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"sort"
	"strings"
	"time"
)

// Read-visible lag is measured with sentinels: the first span of every k-th
// export request of the load itself (no extra traffic), k chosen so a run has at
// most MaxSentinels of them. While the run goes on (and through the settle
// window) the tool polls ClickHouse for every sentinel not seen yet, so each
// sentinel's visibility instant is bracketed:
//
//	start of the last poll that did not see it  <  visible  <=  end of the first poll that saw it
//
// Lag is reported from the SCHEDULED send time of the sentinel's request (the
// open-loop schedule), so a generator or collector stall that delays the send is
// part of the lag instead of being hidden (coordinated omission). The upper bound
// is also reported from the actual send.

// maxPollIDsPerTick bounds one poll tick. Pending IDs rotate in sequence order,
// including after query errors, so permanent losses cannot starve newer IDs.
const maxPollIDsPerTick = 4096

// pollChunk is the number of sentinel IDs per poll query.
const pollChunk = 256

type sentinel struct {
	seq      int64
	due      time.Time // scheduled send of its request
	sent     time.Time // actual send
	built    time.Time // span timestamps: end = built, start >= built - maxSpanDuration
	acked    bool
	lastMiss time.Time // start of the latest query that did not see it
	seenAt   time.Time // end of the first query that saw it
}

// sentinelStride is k: every k-th request contributes a sentinel.
func (c Config) sentinelStride() int64 {
	requests := (c.PlannedSpans() + int64(c.BatchSize) - 1) / int64(c.BatchSize)
	return max(int64(1), (requests+int64(c.MaxSentinels)-1)/int64(c.MaxSentinels))
}

// lagWindow is the width of the per-window lag summaries: a 24th of the run,
// between 10 seconds and one hour (so a 24-hour run reports each hour).
func (c Config) lagWindow() time.Duration {
	return min(max(c.Duration/24, 10*time.Second), time.Hour)
}

// LagWindow summarises the acknowledged sentinels SCHEDULED inside one window
// of the run.
type LagWindow struct {
	StartS float64     `json:"start_offset_seconds"`
	EndS   float64     `json:"end_offset_seconds"`
	Seen   int64       `json:"seen"`
	Unseen int64       `json:"unseen"`
	Upper  Percentiles `json:"upper_bound_from_scheduled"`
}

type LagResult struct {
	Method string `json:"method"`
	// Registered counts every sentinel sent; DroppedNotAcknowledged those
	// whose request was refused, partially rejected or ambiguous (not expected
	// to appear, so not a lag sample). Sampled counts the acknowledged ones.
	Registered             int64   `json:"registered_sentinels"`
	DroppedNotAcknowledged int64   `json:"dropped_not_acknowledged_sentinels"`
	Sampled                int     `json:"sampled_spans"`
	Unseen                 int     `json:"unseen_sampled_spans"`
	QueryErrors            int64   `json:"query_errors"`
	Stride                 int64   `json:"sentinel_request_stride"`
	MaxSentinels           int     `json:"max_sentinels"`
	PollIntervalMS         float64 `json:"poll_interval_ms"`
	// Latency is the upper bound from the ACTUAL send (the v2 figure);
	// FromScheduled is the upper bound from the scheduled send and is the
	// headline; LowerFromScheduled brackets it from below. The true lag of
	// every sentinel lies between its two bounds.
	Latency            Percentiles       `json:"visibility_lag_upper_bound"`
	FromScheduled      Percentiles       `json:"read_visible_lag_upper_bound_from_scheduled_send"`
	LowerFromScheduled Percentiles       `json:"read_visible_lag_lower_bound_from_scheduled_send"`
	Buckets            []HistogramBucket `json:"upper_bound_from_scheduled_buckets"`
	PollQuery          Percentiles       `json:"poll_query_latency"`
	WindowSeconds      float64           `json:"window_seconds"`
	Windows            []LagWindow       `json:"windows"`
}

// registerSentinel records a sentinel when its request is sent. s.mu must be
// held.
func (s *runState) registerSentinel(seq int64, due, sent, built time.Time) {
	st := &sentinel{seq: seq, due: due, sent: sent, built: built}
	s.sentinels[seq] = st
	s.pending[seq] = st
	s.sentinelsRegistered++
}

// sentinelFinished keeps the sentinel of a fully acknowledged request and drops
// the sentinel of any other outcome. s.mu must be held.
func (s *runState) sentinelFinished(seq int64, acknowledged bool) {
	st, ok := s.sentinels[seq]
	if !ok {
		return
	}
	if acknowledged {
		st.acked = true
		return
	}
	if !st.seenAt.IsZero() {
		s.seen--
	}
	delete(s.sentinels, seq)
	delete(s.pending, seq)
	s.sentinelsDropped++
}

// monitor polls ClickHouse for unseen sentinels until ctx ends.
func (s *runState) monitor(ctx context.Context, ch clickHouse) {
	ticker := time.NewTicker(ch.cfg.PollInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			s.pollOnce(ctx, ch)
		}
	}
}

type pollTarget struct {
	seq   int64
	built time.Time
}

func (s *runState) pollOnce(ctx context.Context, ch clickHouse) {
	s.mu.Lock()
	targets := make([]pollTarget, 0, len(s.pending))
	for seq, st := range s.pending {
		targets = append(targets, pollTarget{seq: seq, built: st.built})
	}
	next := s.nextPollSeq
	s.mu.Unlock()
	if len(targets) == 0 {
		return
	}
	sort.Slice(targets, func(i, j int) bool { return targets[i].seq < targets[j].seq })
	first := sort.Search(len(targets), func(i int) bool { return targets[i].seq >= next })
	selected := make([]pollTarget, min(len(targets), maxPollIDsPerTick))
	for i := range selected {
		selected[i] = targets[(first+i)%len(targets)]
	}
	for lo := 0; lo < len(selected) && ctx.Err() == nil; lo += pollChunk {
		chunk := selected[lo:min(lo+pollChunk, len(selected))]
		// Advance only over attempted chunks, even if their query fails. Failed
		// queries do not update lastMiss or discard unseen identities.
		s.mu.Lock()
		s.nextPollSeq = chunk[len(chunk)-1].seq + 1
		s.mu.Unlock()
		began := time.Now()
		seen, err := ch.observe(ctx, chunk)
		ended := time.Now()
		s.mu.Lock()
		if err != nil {
			if ctx.Err() == nil {
				s.lagErrors++
			}
			s.mu.Unlock()
			continue
		}
		s.pollLatency.add(ended.Sub(began))
		for _, target := range chunk {
			st, ok := s.pending[target.seq]
			if !ok {
				continue
			}
			if seen[target.seq] {
				st.seenAt = ended
				delete(s.pending, target.seq)
				s.seen++
			} else if began.After(st.lastMiss) {
				st.lastMiss = began
			}
		}
		s.mu.Unlock()
	}
}

// observe reports which sentinels are visible. The start_time window lets
// ClickHouse prune by the toStartOfHour(start_time) primary key column instead
// of scanning the project for every poll.
func (ch clickHouse) observe(ctx context.Context, targets []pollTarget) (map[int64]bool, error) {
	params := ch.params()
	ids := make([]string, 0, len(targets))
	traces := make([]string, 0, len(targets))
	byID := map[string]int64{}
	lo, hi := targets[0].built, targets[0].built
	for _, target := range targets {
		id := spanID(target.seq)
		ids = append(ids, id)
		traces = append(traces, ch.cfg.traceString(target.seq))
		byID[id] = target.seq
		if target.built.Before(lo) {
			lo = target.built
		}
		if target.built.After(hi) {
			hi = target.built
		}
	}
	// IDs are generated hexadecimal/UUID values, not user strings.
	params["ids"] = "['" + strings.Join(ids, "','") + "']"
	params["traces"] = "['" + strings.Join(traces, "','") + "']"
	params["time_lo"] = formatCHTime(lo.Add(-maxSpanDuration - windowSlack))
	params["time_hi"] = formatCHTime(hi.Add(windowSlack))
	body, err := ch.query(ctx, "SELECT DISTINCT id"+ch.scope()+" AND id IN {ids:Array(String)} AND trace_id IN {traces:Array(String)}"+
		" AND start_time >= {time_lo:DateTime64(6, 'UTC')} AND start_time < {time_hi:DateTime64(6, 'UTC')}", params)
	if err != nil {
		return nil, err
	}
	seen := map[int64]bool{}
	d := json.NewDecoder(bytes.NewReader(body))
	for {
		var row struct {
			ID string `json:"id"`
		}
		err := d.Decode(&row)
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, fmt.Errorf("invalid visibility response")
		}
		if seq, ok := byID[row.ID]; ok {
			seen[seq] = true
		}
	}
	return seen, nil
}

// lagResult summarises the sentinels. s.mu must be held.
func (s *runState) lagResult(c Config, start time.Time) LagResult {
	r := LagResult{Method: "sentinel_write_then_poll_acknowledged_only", QueryErrors: s.lagErrors,
		Registered: s.sentinelsRegistered, DroppedNotAcknowledged: s.sentinelsDropped,
		Stride: c.sentinelStride(), MaxSentinels: c.MaxSentinels, PollIntervalMS: float64(c.PollInterval) / float64(time.Millisecond),
		WindowSeconds: c.lagWindow().Seconds(), PollQuery: s.pollLatency.summary(), Windows: []LagWindow{}}
	actual, upper, lower := &histogram{}, &histogram{}, &histogram{lowerBound: true}
	type window struct {
		seen, unseen int64
		upper        histogram
	}
	windows := map[int64]*window{}
	width := c.lagWindow()
	for _, st := range s.sentinels {
		if !st.acked {
			continue
		}
		r.Sampled++
		idx := int64(max(0, st.due.Sub(start)) / width)
		w := windows[idx]
		if w == nil {
			w = &window{}
			windows[idx] = w
		}
		if st.seenAt.IsZero() {
			r.Unseen++
			w.unseen++
			continue
		}
		w.seen++
		actual.add(st.seenAt.Sub(st.sent))
		upper.add(st.seenAt.Sub(st.due))
		w.upper.add(st.seenAt.Sub(st.due))
		// It cannot have become visible before it was sent, nor before the
		// last query that missed it started.
		floor := st.sent
		if st.lastMiss.After(floor) && st.lastMiss.Before(st.seenAt) {
			floor = st.lastMiss
		}
		lower.add(floor.Sub(st.due))
	}
	r.Latency, r.FromScheduled, r.LowerFromScheduled, r.Buckets = actual.summary(), upper.summary(), lower.summary(), upper.buckets()
	keys := make([]int64, 0, len(windows))
	for k := range windows {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool { return keys[i] < keys[j] })
	for _, k := range keys {
		w := windows[k]
		r.Windows = append(r.Windows, LagWindow{StartS: (time.Duration(k) * width).Seconds(), EndS: (time.Duration(k+1) * width).Seconds(),
			Seen: w.seen, Unseen: w.unseen, Upper: w.upper.summary()})
	}
	return r
}
