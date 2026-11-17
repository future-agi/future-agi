// Package outcomes keeps what happened to an org's model calls, per agent,
// step and model: how many answered, failed, came back empty or filtered,
// how often the caller said a result failed verification, and how fast the
// answers came. Auto routing penalises models with bad outcomes; the
// outcomes plugin records every response, and callers report verdicts with
// POST /v1/feedback or x-agentcc-prev-outcome on their next call.
//
// Counters live in Redis hashes aout:{<org>}:<agent|_>:<step>:<model> and
// decay with a half-life of half the org's outcome window, so old samples
// weigh less. Every key of an org shares the hash tag {<org>}.
package outcomes

import (
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing/affinity"
)

// Outcome is one response's result: answered (OK), empty, stopped by the
// provider's content filter, or failed with an error class. LatencyMs is how
// long the answer took.
type Outcome struct {
	OK            bool
	Empty         bool
	ContentFilter bool
	ErrorClass    models.ErrorClass
	LatencyMs     int64
}

// Key names one counters hash: an org's model as one agent's step used it.
type Key struct {
	Org   string
	Agent string // "" is stored as "_"
	Step  models.Step
	Model string
}

// String is the Redis key of k.
func (k Key) String() string {
	agent := k.Agent
	if agent == "" {
		agent = "_"
	}
	step := k.Step
	if step == "" {
		step = models.StepOther
	}
	return "aout:{" + k.Org + "}:" + agent + ":" + string(step) + ":" + k.Model
}

// Last is what the gateway remembers of a generation for the verdicts that
// may follow it: who made the call, for which step, the model that answered,
// and how the response was classified. Judged is set once a negative verdict
// has been counted.
type Last struct {
	Agent  string
	Run    string
	Step   models.Step
	Model  string
	Class  Class
	Judged bool
}

// Key is the counters hash the generation's verdicts count in.
func (l Last) Key(org string) Key {
	return Key{Org: org, Agent: l.Agent, Step: l.Step, Model: l.Model}
}

// LastKey is the Redis key of a generation's Last record.
func LastKey(org, requestID string) string { return "aout:last:{" + org + "}:" + requestID }

// PrevKey is the Redis key of a subject's latest request in a class of
// steps: the request an x-agentcc-prev-outcome header on the subject's next
// call of that class reports on. It is the gateway's own
// pointer, separate from the affinity pin, which routing may refresh before
// the call completes.
func PrevKey(org, subject string, class affinity.Class) string {
	return "aout:prev:{" + org + "}:" + subject + ":" + string(class)
}

// Verdicts a caller can report on a generation. Feedback takes ok, bad and
// verify_failed; x-agentcc-prev-outcome takes ok, empty, verify_failed and
// retried.
const (
	VerdictOK           = "ok"
	VerdictBad          = "bad"
	VerdictEmpty        = "empty"
	VerdictVerifyFailed = "verify_failed"
	VerdictRetried      = "retried"
)

// Where a verdict came from (outcome-event-v1 source).
const (
	SourceFeedback    = "feedback"
	SourcePrevOutcome = "prev_outcome"
)

// How long each record lives. LastTTL bounds both per-request records, the
// Last record and the previous-request pointer: a pointer is only useful
// while the record it names is kept. It is capped at an hour, so
// a verdict (POST /v1/feedback or x-agentcc-prev-outcome) must come within
// an hour of its generation.
const (
	CountersTTL = 7 * 24 * time.Hour
	LastTTL     = time.Hour
)

// HalfLife is the counters' half-life for an outcome window of
// windowSeconds: half the window, and at least one second.
func HalfLife(windowSeconds int) time.Duration {
	return max(time.Duration(windowSeconds)*time.Second/2, time.Second)
}
