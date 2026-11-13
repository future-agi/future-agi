package ingestbench

import (
	"context"
	"fmt"
	"math"
	"regexp"
	"sync"
	"time"
)

// Recovery measurement. A fault is a disruption of the system under test during
// the load: a collector restart or crash, or a ClickHouse pause. The TOOL never
// performs one on infrastructure: a test harness that owns its collector or a
// proxy in front of ClickHouse injects it through Options.Faults, and an
// operator who stops something on an isolated stack declares the window with
// --declare-fault (the tool records it as declared, not observed). Either way
// the same exact-ID verification decides what was lost, and the recovery
// figures say how long the system took to come back.

// Fault is a harness-injected disruption: Inject runs At after the load
// starts, Restore Duration later.
type Fault struct {
	Name     string
	At       time.Duration
	Duration time.Duration
	Inject   func(context.Context) error
	Restore  func(context.Context) error
}

// DeclaredFault is a disruption the operator performs outside the tool.
type DeclaredFault struct {
	Name     string        `json:"name"`
	At       time.Duration `json:"at_ns"`
	Duration time.Duration `json:"duration_ns"`
}

var faultName = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,62}$`)

func validateFault(name string, at, duration, load time.Duration) error {
	if !faultName.MatchString(name) || at < 0 || duration <= 0 || at+duration > load {
		return fmt.Errorf("fault %q needs a lowercase name, at >= 0, duration > 0 and must end inside the load window", name)
	}
	return nil
}

// FaultEvent is the telemetry line of a fault starting or ending.
type FaultEvent struct {
	Name   string `json:"name"`
	Phase  string `json:"phase"`
	Source string `json:"source"`
	Error  string `json:"error,omitempty"`
}

// ackResolution is the resolution of the acknowledgement series recovery uses.
const ackResolution = 100 * time.Millisecond

// ackSeries counts acknowledged requests by finish time in 100 ms buckets.
type ackSeries struct {
	buckets []int32
}

func (a *ackSeries) add(offset time.Duration) {
	if offset < 0 {
		return
	}
	i := int(offset / ackResolution)
	if i > int((25*time.Hour+10*time.Minute)/ackResolution) {
		return
	}
	for len(a.buckets) <= i {
		a.buckets = append(a.buckets, 0)
	}
	a.buckets[i]++
}

// firstAfter is an upper bound on the time from t to the first acknowledgement
// finishing after t (the end of its bucket), considering only buckets that start
// at or after t.
func (a *ackSeries) firstAfter(t time.Duration) (time.Duration, bool) {
	start := int((t + ackResolution - 1) / ackResolution)
	for i := start; i < len(a.buckets); i++ {
		if a.buckets[i] > 0 {
			return time.Duration(i+1)*ackResolution - t, true
		}
	}
	return 0, false
}

type faultRecorder struct {
	mu      sync.Mutex
	records []FaultResult
}

// runFaults injects every harness fault on its schedule and returns when all
// are restored. Errors are recorded, never hidden.
func runFaults(ctx context.Context, faults []Fault, start time.Time, sink *telemetrySink, rec *faultRecorder) {
	var wg sync.WaitGroup
	for _, f := range faults {
		wg.Add(1)
		go func(f Fault) {
			defer wg.Done()
			r := FaultResult{Name: f.Name, Source: "harness-injected"}
			if !waitUntil(ctx, start.Add(f.At)) {
				r.Error = "run ended before the fault was injected"
				rec.add(r)
				return
			}
			r.StartS = time.Since(start).Seconds()
			if err := f.Inject(ctx); err != nil {
				r.Error = "inject: " + err.Error()
			}
			_ = sink.write(telemetryEvent{Kind: "fault", Fault: &FaultEvent{Name: f.Name, Phase: "injected", Source: r.Source, Error: r.Error}})
			waitUntil(ctx, start.Add(f.At+f.Duration))
			// Restore even when the run was cancelled: a harness must not leave
			// the system under test broken.
			if err := f.Restore(context.WithoutCancel(ctx)); err != nil {
				r.Error += "; restore: " + err.Error()
			}
			r.EndS = time.Since(start).Seconds()
			_ = sink.write(telemetryEvent{Kind: "fault", Fault: &FaultEvent{Name: f.Name, Phase: "restored", Source: r.Source, Error: r.Error}})
			rec.add(r)
		}(f)
	}
	wg.Wait()
}

func (r *faultRecorder) add(f FaultResult) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.records = append(r.records, f)
}

func (r *faultRecorder) all() []FaultResult {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]FaultResult(nil), r.records...)
}

// FaultResult is one fault and how the system recovered from it. Offsets are
// seconds from load start; "after end" figures are measured from the fault's
// end (restore). A nil figure was not observed within the run.
type FaultResult struct {
	Name   string  `json:"name"`
	Source string  `json:"source"`
	StartS float64 `json:"start_offset_seconds"`
	EndS   float64 `json:"end_offset_seconds"`
	Error  string  `json:"error,omitempty"`
	// Outcomes of the requests SCHEDULED inside the fault window.
	RequestsScheduledDuring ScheduledOutcomes `json:"requests_scheduled_during_fault"`
	// FirstAckAfterEndS: until the first acknowledgement after the fault
	// ended (upper bound, 100 ms resolution).
	FirstAckAfterEndS *float64 `json:"first_acknowledgement_after_end_seconds"`
	// Backlog: the pre-fault p95 of buffered rows is the baseline; drained is
	// the first sample after the end at or below it.
	BacklogBaselineRows    *int64   `json:"backlog_baseline_rows,omitempty"`
	BacklogPeakRows        *int64   `json:"backlog_peak_rows,omitempty"`
	BacklogDrainedAfterEnd *float64 `json:"backlog_back_to_baseline_after_end_seconds"`
	// Sentinels scheduled before the fault ended: when the last of them became
	// visible (0 when all were visible by the end), and how many never did.
	EarlierSentinels         int      `json:"earlier_acknowledged_sentinels"`
	EarlierSentinelsUnseen   int      `json:"earlier_sentinels_never_visible"`
	EarlierVisibleAfterEndS  *float64 `json:"earlier_sentinels_all_visible_after_end_seconds"`
	FirstNewVisibleAfterEndS *float64 `json:"first_later_sentinel_visible_after_end_seconds"`
	// RecoverySeconds is the largest of the acknowledgement, backlog and
	// earlier-sentinel figures; nil means not recovered within the run.
	RecoverySeconds    *float64 `json:"recovery_seconds"`
	Recovered          bool     `json:"recovered"`
	NotRecoveredReason string   `json:"not_recovered_reason,omitempty"`
	// From the exact-ID verification: acknowledged (not partial) spans missing
	// in requests scheduled before this fault ended and after the previous
	// fault ended (or the run start).
	AcknowledgedMissing *uint64 `json:"acknowledged_but_missing_spans_up_to_end,omitempty"`
}

// FaultLossWindow holds exact fully-acknowledged loss for one fault's reporting
// window. The window ends at EndS (inclusive) and starts after the previous end,
// or at zero for the earliest end. Equal ends assign to the first listed fault;
// the others have empty windows. Faults need not be ordered by end time.
// Stored separately from compacted/truncated missing ranges, including in the
// verification checkpoint, so a resume cannot lose attribution precision.
type FaultLossWindow struct {
	Name         string  `json:"name"`
	EndS         float64 `json:"end_offset_seconds"`
	MissingSpans uint64  `json:"acknowledged_missing_spans"`
}

func (a *verifyAccum) trackFaultLoss(scheduled float64, missing uint64) {
	best := -1
	for i, f := range a.FaultLoss {
		if scheduled <= f.EndS && (best < 0 || f.EndS < a.FaultLoss[best].EndS) {
			best = i
		}
	}
	if best >= 0 {
		a.FaultLoss[best].MissingSpans += missing
	}
}

const recoveryMethod = "faults injected by the harness or declared by the operator; loss from the exact-ID verification, recovery from acknowledgements, the collector backlog and read-visible sentinels"

type RecoveryResult struct {
	Method string        `json:"method"`
	Faults []FaultResult `json:"faults"`
	// Run-level integrity from the verification.
	AcknowledgedMissing uint64 `json:"acknowledged_but_missing_spans"`
	VisibleDuplicates   uint64 `json:"duplicate_rows_visible"`
	AmbiguousPersisted  uint64 `json:"ambiguous_but_persisted_spans"`
	// A crash can lose at most the rows buffered when it happened, which the
	// admission budget bounds: max_pending_rows, plus one request when the
	// requests are larger than the whole budget (the collector admits one such
	// request while it is below the budget), per fault. The bound is a property
	// of the collector design, stated so a crash case can be checked against
	// it; it is not a measurement.
	LossBound       int64  `json:"acknowledged_loss_bound_rows,omitempty"`
	LossBoundSource string `json:"acknowledged_loss_bound_source,omitempty"`
	LossWithinBound *bool  `json:"acknowledged_loss_within_bound,omitempty"`
	// DeadLetteredRows: rows the collector's writer gave up on and wrote to its
	// dead-letter file during the run (from /healthz, when sampled). Such rows
	// are acknowledged and missing from ClickHouse until an operator replays
	// them.
	DeadLetteredRows *uint64 `json:"collector_rows_dead_lettered,omitempty"`
	Complete         bool    `json:"verification_complete"`
}

// Passed is the recovery acceptance rule: a complete verification found no
// acknowledged span missing, no stored span of a refused request and no row
// outside the run's identities, and every fault was injected and restored
// without error and recovered within the run.
func (r *RecoveryResult) Passed() bool {
	if r == nil || !r.Complete || r.AcknowledgedMissing != 0 || len(r.Faults) == 0 {
		return false
	}
	for _, f := range r.Faults {
		if f.Error != "" || !f.Recovered {
			return false
		}
	}
	return true
}

func f64(v float64) *float64 { return &v }

// analyseRecovery fills the observed recovery figures of each fault. s.mu must
// be held.
func analyseRecovery(c Config, faults []FaultResult, s *runState, start time.Time, samples []CollectorSample) []FaultResult {
	out := make([]FaultResult, len(faults))
	for i, f := range faults {
		end := time.Duration(f.EndS * float64(time.Second))
		f.RequestsScheduledDuring = s.ledger.outcomesScheduled(c, f.StartS, f.EndS)
		if d, ok := s.acks.firstAfter(end); ok {
			f.FirstAckAfterEndS = f64(d.Seconds())
		}
		if len(samples) > 0 {
			baseline, n := bufferedQuantile(samples, 0, f.StartS, .95)
			if n > 0 {
				f.BacklogBaselineRows = &baseline
				drained, ok := firstAtOrBelow(samples, f.EndS, baseline)
				peakTo := math.Inf(1)
				if ok {
					f.BacklogDrainedAfterEnd = f64(drained - f.EndS)
					peakTo = drained
				}
				peak := peakBuffered(samples, f.StartS, peakTo)
				f.BacklogPeakRows = &peak
			}
		}
		var lastSeen time.Time
		var firstNew time.Time
		endAt := start.Add(end)
		for _, st := range s.sentinels {
			if !st.acked {
				continue
			}
			if st.due.Before(endAt) {
				f.EarlierSentinels++
				if st.seenAt.IsZero() {
					f.EarlierSentinelsUnseen++
				} else if st.seenAt.After(lastSeen) {
					lastSeen = st.seenAt
				}
			} else if !st.seenAt.IsZero() && (firstNew.IsZero() || st.seenAt.Before(firstNew)) {
				firstNew = st.seenAt
			}
		}
		if f.EarlierSentinels > 0 && f.EarlierSentinelsUnseen == 0 {
			f.EarlierVisibleAfterEndS = f64(max(0, lastSeen.Sub(endAt).Seconds()))
		}
		if !firstNew.IsZero() {
			f.FirstNewVisibleAfterEndS = f64(firstNew.Sub(endAt).Seconds())
		}
		f.RecoverySeconds, f.Recovered, f.NotRecoveredReason = recoveryTime(f, len(samples) > 0)
		out[i] = f
	}
	return out
}

// recoveryTime is the largest of the observed recovery components, or why the
// system did not recover within the run. All three components must be
// observed: a fault whose backlog was never sampled (no collector health URL)
// or had no sample before it (no baseline) is not recovered, because its drain
// is unknown.
func recoveryTime(f FaultResult, backlogObserved bool) (*float64, bool, string) {
	switch {
	case f.FirstAckAfterEndS == nil:
		return nil, false, "no request was acknowledged after the fault ended"
	case !backlogObserved:
		return nil, false, "the collector backlog was not observed (no collector health URL), so its drain after the fault is unknown"
	case f.BacklogBaselineRows == nil:
		return nil, false, "no collector backlog sample before the fault, so its pre-fault baseline and drain are unknown"
	case f.BacklogDrainedAfterEnd == nil:
		return nil, false, "the collector backlog did not return to its pre-fault baseline"
	case f.EarlierSentinels == 0:
		return nil, false, "no acknowledged sentinel was scheduled before the fault ended"
	case f.EarlierSentinelsUnseen > 0:
		return nil, false, fmt.Sprintf("%d acknowledged sentinels scheduled before the fault ended never became visible", f.EarlierSentinelsUnseen)
	}
	return f64(max(*f.FirstAckAfterEndS, *f.EarlierVisibleAfterEndS, *f.BacklogDrainedAfterEnd)), true, ""
}

// ScheduledOutcomes counts the requests scheduled inside a window by outcome
// class; NotSent are planned requests of the window that were never sent.
type ScheduledOutcomes struct {
	Acknowledged         int64 `json:"acknowledged"`
	PartialRejection     int64 `json:"partial_rejection"`
	RejectedRetryable    int64 `json:"whole_request_rejected_retryable"`
	RejectedNonRetryable int64 `json:"whole_request_rejected_non_retryable"`
	Ambiguous            int64 `json:"ambiguous"`
	NotSent              int64 `json:"not_sent"`
}

// outcomesScheduled counts requests by class whose scheduled send is inside
// [fromS, toS] seconds of the run.
func (l *outcomeLedger) outcomesScheduled(c Config, fromS, toS float64) ScheduledOutcomes {
	var o ScheduledOutcomes
	first := int64(math.Ceil(fromS * float64(c.Rate) / float64(l.batch)))
	last := int64(math.Floor(toS * float64(c.Rate) / float64(l.batch)))
	planned := (c.PlannedSpans() + l.batch - 1) / l.batch
	for r := max(first, 0); r <= last && r < planned; r++ {
		switch l.class(r) {
		case outcomeAcknowledged:
			o.Acknowledged++
		case outcomePartial:
			o.PartialRejection++
		case outcomeRejectedRetryable:
			o.RejectedRetryable++
		case outcomeRejectedNonRetryable:
			o.RejectedNonRetryable++
		case outcomeAmbiguous:
			o.Ambiguous++
		default:
			o.NotSent++
		}
	}
	return o
}

// attachVerification adds the verification's integrity figures. A recovery
// with a contract violation or unexpected rows is not complete evidence.
func (r *RecoveryResult) attachVerification(c Config, v Verification, backlog BacklogResult, budget BudgetCheck) {
	r.Complete = v.Complete && !v.ContractViolation && v.UnexpectedRows == 0
	r.AcknowledgedMissing = v.MissingBy.AcknowledgedMissing
	r.VisibleDuplicates = v.DuplicateRows
	r.AmbiguousPersisted = v.PersistedBy.AmbiguousPersisted
	if budget.AvailablePendingRows > 0 && len(r.Faults) > 0 {
		perFault := budget.AvailablePendingRows
		if int64(c.BatchSize) > perFault {
			perFault += int64(c.BatchSize)
		}
		r.LossBound, r.LossBoundSource = int64(len(r.Faults))*perFault, budget.Source
		within := v.Complete && int64(v.MissingBy.AcknowledgedMissing) <= r.LossBound
		r.LossWithinBound = &within
	}
	if backlog.Status == "measured" || backlog.Status == "measured-with-gaps" {
		dead := backlog.RowsDeadLettered
		r.DeadLetteredRows = &dead
	}
	for i := range r.Faults {
		r.Faults[i].AcknowledgedMissing = nil
	}
	// Old checkpoints/results lack these per-request accumulators. Never
	// reconstruct exact counts from compacted ranges or report absent data as 0.
	if !v.Complete || len(v.FaultLoss) != len(r.Faults) {
		return
	}
	for i, f := range r.Faults {
		if v.FaultLoss[i].Name != f.Name || v.FaultLoss[i].EndS != f.EndS {
			return
		}
	}
	for i := range r.Faults {
		n := v.FaultLoss[i].MissingSpans
		r.Faults[i].AcknowledgedMissing = &n
	}
}
