package ingestbench

import (
	"fmt"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"time"

	"google.golang.org/genproto/googleapis/rpc/errdetails"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// outcomeClass is what one export request did to ALL of its spans, as far as
// the client can know from the response. Every attempted request ends in
// exactly one class, so the span counters add up to the attempted spans.
type outcomeClass uint8

const (
	// outcomeNotSent: no request was recorded for this request index.
	outcomeNotSent outcomeClass = iota
	// outcomeAcknowledged: a valid OTLP success response with no rejected spans.
	outcomeAcknowledged
	// outcomePartial: a valid OTLP success response with partial_success
	// rejected_spans > 0. Which spans were rejected is not identified.
	outcomePartial
	// outcomeRejectedRetryable: the server refused the whole request with a
	// retryable status (HTTP 429/503, gRPC RESOURCE_EXHAUSTED with RetryInfo).
	// Per OTLP nothing of it was accepted.
	outcomeRejectedRetryable
	// outcomeRejectedNonRetryable: the server refused the whole request with a
	// status OTLP clients must not retry (HTTP 4xx such as 400/401/413/415,
	// gRPC INVALID_ARGUMENT, RESOURCE_EXHAUSTED without RetryInfo, ...).
	outcomeRejectedNonRetryable
	// outcomeAmbiguous: the client cannot tell whether the request was
	// processed: transport error, timeout, gateway error, 5xx other than 503,
	// a malformed success response, or a gRPC code a client can also produce
	// locally (UNAVAILABLE, DEADLINE_EXCEEDED, ...).
	outcomeAmbiguous
)

func (c outcomeClass) String() string {
	switch c {
	case outcomeAcknowledged:
		return "acknowledged"
	case outcomePartial:
		return "partial_rejection"
	case outcomeRejectedRetryable:
		return "whole_request_rejected_retryable"
	case outcomeRejectedNonRetryable:
		return "whole_request_rejected_non_retryable"
	case outcomeAmbiguous:
		return "ambiguous"
	default:
		return "not_sent"
	}
}

// exportOutcome is the classified response of one request.
type exportOutcome struct {
	class outcomeClass
	// rejected is partial_success.rejected_spans (outcomePartial only).
	rejected int64
	// partialMessage: partial_success carried a message (a warning when
	// rejected is 0).
	partialMessage bool
	// retryHint: HTTP Retry-After or gRPC RetryInfo was present.
	retryHint bool
	// retryAfter is the parsed server delay when retryHint is set.
	retryAfter time.Duration
	// statusLabel is "http:<code>", "grpc:<Code>" or "transport-error".
	statusLabel string
	// successStatus: HTTP 200 or gRPC OK, regardless of body validity.
	successStatus bool
}

// classifyHTTP classifies an OTLP/HTTP response. decodeOK reports whether a
// 200 body decoded as an ExportTraceServiceResponse in the request's encoding.
func classifyHTTP(statusCode int, header http.Header, decodeOK bool, rejected int64, partialMessage bool, n int) exportOutcome {
	o := exportOutcome{statusLabel: "http:" + strconv.Itoa(statusCode), successStatus: statusCode == http.StatusOK}
	switch {
	case statusCode == http.StatusOK:
		if !decodeOK || rejected < 0 || rejected > int64(n) {
			o.class = outcomeAmbiguous
			return o
		}
		o.partialMessage = partialMessage
		if rejected > 0 {
			o.class, o.rejected = outcomePartial, rejected
		} else {
			o.class = outcomeAcknowledged
		}
	case statusCode == http.StatusTooManyRequests || statusCode == http.StatusServiceUnavailable:
		// The OTLP throttling responses: the server did not accept the request.
		o.class = outcomeRejectedRetryable
		if header != nil {
			o.retryAfter, o.retryHint = parseRetryAfter(header.Get("Retry-After"), time.Now())
		}
	case statusCode >= 400 && statusCode < 500:
		// Every other 4xx is a definitive, non-retryable rejection per OTLP.
		o.class = outcomeRejectedNonRetryable
	default:
		// 1xx/3xx (redirects are never followed), other 2xx, 500, 502, 504 and
		// other 5xx: the request may or may not have been processed.
		o.class = outcomeAmbiguous
	}
	return o
}

// parseRetryAfter accepts both forms of RFC 9110 Retry-After.
func parseRetryAfter(v string, now time.Time) (time.Duration, bool) {
	v = strings.TrimSpace(v)
	if v == "" {
		return 0, false
	}
	if secs, err := strconv.ParseInt(v, 10, 64); err == nil && secs >= 0 {
		return time.Duration(secs) * time.Second, true
	}
	if at, err := http.ParseTime(v); err == nil {
		return max(0, at.Sub(now)), true
	}
	return 0, false
}

// classifyGRPC classifies an OTLP/gRPC result. err is the Export error.
func classifyGRPC(err error, rejected int64, partialMessage bool, n int) exportOutcome {
	if err == nil {
		o := exportOutcome{statusLabel: "grpc:" + codes.OK.String(), successStatus: true}
		if rejected < 0 || rejected > int64(n) {
			o.class = outcomeAmbiguous
			return o
		}
		o.partialMessage = partialMessage
		if rejected > 0 {
			o.class, o.rejected = outcomePartial, rejected
		} else {
			o.class = outcomeAcknowledged
		}
		return o
	}
	st, isStatus := status.FromError(err)
	if !isStatus {
		return exportOutcome{class: outcomeAmbiguous, statusLabel: "transport-error"}
	}
	o := exportOutcome{statusLabel: "grpc:" + st.Code().String()}
	switch st.Code() {
	case codes.ResourceExhausted:
		// OTLP: retryable only when the server supplied RetryInfo. Either way
		// the server refused the request.
		o.class = outcomeRejectedNonRetryable
		for _, d := range st.Details() {
			if info, ok := d.(*errdetails.RetryInfo); ok {
				o.class, o.retryHint = outcomeRejectedRetryable, true
				if info.GetRetryDelay() != nil {
					o.retryAfter = info.GetRetryDelay().AsDuration()
				}
			}
		}
	case codes.InvalidArgument, codes.NotFound, codes.AlreadyExists, codes.PermissionDenied,
		codes.FailedPrecondition, codes.Unimplemented, codes.Unauthenticated:
		o.class = outcomeRejectedNonRetryable
	default:
		// UNAVAILABLE and DEADLINE_EXCEEDED are also produced by the client for
		// transport failures; INTERNAL/UNKNOWN/DATA_LOSS/ABORTED/OUT_OF_RANGE
		// can follow processing. None proves the request was not processed.
		o.class = outcomeAmbiguous
	}
	return o
}

// maxLedgerRequests bounds the per-request outcome ledger (one byte per
// request). Validate refuses a plan that needs more.
const maxLedgerRequests = 1 << 26

// outcomeLedger records the outcome class of every request. Request k covers
// spans [k*batch, min((k+1)*batch, attempted)), because the producer cuts
// batches in sequence order; that makes the ledger a dense byte slice.
type outcomeLedger struct {
	batch   int64
	classes []outcomeClass
	// partial maps a partial-rejection request index to its rejected count.
	partial map[int64]int64
}

func newOutcomeLedger(batch int64) *outcomeLedger {
	return &outcomeLedger{batch: batch, partial: map[int64]int64{}}
}

func (l *outcomeLedger) record(seq int64, o exportOutcome) error {
	if l.batch <= 0 || seq < 0 || seq%l.batch != 0 {
		return fmt.Errorf("request does not start on a batch boundary")
	}
	idx := seq / l.batch
	if idx >= maxLedgerRequests {
		return fmt.Errorf("outcome ledger is full")
	}
	for int64(len(l.classes)) <= idx {
		l.classes = append(l.classes, outcomeNotSent)
	}
	if l.classes[idx] != outcomeNotSent {
		return fmt.Errorf("request %d recorded twice", idx)
	}
	l.classes[idx] = o.class
	if o.class == outcomePartial {
		l.partial[idx] = o.rejected
	}
	return nil
}

func (l *outcomeLedger) class(request int64) outcomeClass {
	if request < 0 || request >= int64(len(l.classes)) {
		return outcomeNotSent
	}
	return l.classes[request]
}

// ledgerRun is a run-length encoded stretch of requests with one class.
type ledgerRun struct {
	Start int64        `json:"start_request"`
	Count int64        `json:"requests"`
	Class outcomeClass `json:"class"`
}

type ledgerPartial struct {
	Request  int64 `json:"request"`
	Rejected int64 `json:"rejected_spans"`
}

// ledgerSnapshot is the serialised ledger: compact when outcomes are uniform.
type ledgerSnapshot struct {
	Batch   int64           `json:"batch_size"`
	Runs    []ledgerRun     `json:"runs"`
	Partial []ledgerPartial `json:"partial"`
}

func (l *outcomeLedger) snapshot() ledgerSnapshot {
	s := ledgerSnapshot{Batch: l.batch, Runs: []ledgerRun{}, Partial: []ledgerPartial{}}
	for i, c := range l.classes {
		if n := len(s.Runs); n > 0 && s.Runs[n-1].Class == c {
			s.Runs[n-1].Count++
			continue
		}
		s.Runs = append(s.Runs, ledgerRun{Start: int64(i), Count: 1, Class: c})
	}
	for idx, rejected := range l.partial {
		s.Partial = append(s.Partial, ledgerPartial{Request: idx, Rejected: rejected})
	}
	sort.Slice(s.Partial, func(i, j int) bool { return s.Partial[i].Request < s.Partial[j].Request })
	return s
}

func ledgerFromSnapshot(s ledgerSnapshot) (*outcomeLedger, error) {
	if s.Batch <= 0 {
		return nil, fmt.Errorf("invalid ledger batch size")
	}
	l := newOutcomeLedger(s.Batch)
	var next int64
	for _, run := range s.Runs {
		if run.Start != next || run.Count <= 0 || run.Class > outcomeAmbiguous || next+run.Count > maxLedgerRequests {
			return nil, fmt.Errorf("invalid ledger run")
		}
		for i := int64(0); i < run.Count; i++ {
			l.classes = append(l.classes, run.Class)
		}
		next += run.Count
	}
	for _, p := range s.Partial {
		if l.class(p.Request) != outcomePartial || p.Rejected <= 0 || p.Rejected > s.Batch {
			return nil, fmt.Errorf("invalid ledger partial entry")
		}
		l.partial[p.Request] = p.Rejected
	}
	for idx, c := range l.classes {
		if _, ok := l.partial[int64(idx)]; c == outcomePartial && !ok {
			return nil, fmt.Errorf("partial request without a rejected count")
		}
	}
	return l, nil
}
