package propertycatalog

import (
	"context"
	"errors"
	"fmt"
	"strconv"
	"time"

	"golang.org/x/sys/unix"
)

// ExactInsertSender must make ONE request, with these exact durable bytes,
// endpoint, query ID, dedup token and settings. A nil error means the complete
// response was read and validated, not merely HTTP 200. Transport retries and
// redirects must be disabled. This function is never called for a sent attempt.
type ExactInsertSender func(context.Context, ExactWriteAttempt) error

// DurableWriteExecutor orders journal -> send -> ACK receipt -> all-member
// coverage -> completion. The proof adapter is mandatory, including standalone.
// The executor deliberately has no environment switch or retry-write loop.
type DurableWriteExecutor struct {
	policy  *WritePolicy
	journal *WriteAttemptJournal
	proof   CatalogWriteProof
	send    ExactInsertSender
}

func NewDurableWriteExecutor(policy *WritePolicy, journal *WriteAttemptJournal, proof CatalogWriteProof, send ExactInsertSender) (*DurableWriteExecutor, error) {
	if policy == nil || journal == nil || journal.directory == nil || proof == nil || send == nil {
		return nil, errors.New("propertycatalog: durable writer requires topology, shared journal, proof and one-shot sender")
	}
	return &DurableWriteExecutor{policy: policy, journal: journal, proof: proof, send: send}, nil
}

// Execute accepts immutable logical identity/bytes. On restart it uses the
// saved settings and query ID, NOT a newly computed timeout or a new token.
// proofOnly is required for duplicate shortcuts; a missing receipt cannot send.
func (w *DurableWriteExecutor) Execute(ctx context.Context, intent ExactWriteAttempt, proofOnly bool) error {
	if w == nil || ctx == nil || ctx.Err() != nil {
		return errors.New("propertycatalog: durable INSERT requires a live context")
	}
	if _, bounded := ctx.Deadline(); !bounded {
		return errors.New("propertycatalog: durable INSERT/proof requires a deadline")
	}
	if err := w.policy.RequireDestination(w.policy.admission.Environment, w.policy.admission.Database, intent.Endpoint); err != nil {
		return err
	}
	if intent.TopologySHA256 != w.policy.admission.TopologySHA256 {
		return errors.New("propertycatalog: attempt targets another admitted topology")
	}
	lease, err := w.journal.Lock(intent.Token)
	if err != nil {
		return err
	}
	defer lease.Close()
	attempt, err := lease.Load()
	if err != nil && !errors.Is(err, unix.ENOENT) {
		return err
	}
	if errors.Is(err, unix.ENOENT) {
		if proofOnly {
			return fmt.Errorf("%w: no durable receipt for duplicate", ErrWriteUnresolved)
		}
		intent.Settings, err = w.policy.InsertSettings(ctx)
		if err != nil {
			return err
		}
		attempt, err = lease.Prepare(intent)
		if err != nil {
			return err
		}
	} else {
		intent.Settings = cloneAttempt(attempt).Settings
		if !sameAttemptIntent(attempt, intent) {
			return errors.New("propertycatalog: replay conflicts with exact durable attempt")
		}
	}
	quorum := 0
	if w.policy.admission.Family == "replicated" {
		quorum = len(w.policy.admission.Members)
	}
	if attempt.Settings["insert_quorum"] != strconv.Itoa(quorum) {
		return errors.New("propertycatalog: weaker historical quorum is not upgraded by a duplicate ACK")
	}
	if err := w.proof.Attest(ctx, w.policy); err != nil {
		return err
	}
	if attempt.State == AttemptPrepared {
		if proofOnly {
			return fmt.Errorf("%w: duplicate has no dispatched INSERT", ErrWriteUnresolved)
		}
		if err := ctx.Err(); err != nil {
			return err
		}
		deadline, _ := ctx.Deadline()
		quorumMS, parseErr := strconv.ParseInt(attempt.Settings["insert_quorum_timeout"], 10, 64)
		if parseErr != nil || quorumMS <= 0 || time.Duration(quorumMS)*time.Millisecond >= time.Until(deadline) {
			return errors.New("propertycatalog: saved exact INSERT timeout does not fit remaining delivery budget")
		}
		// Persist sent BEFORE crossing the transport boundary. A process death
		// between this fsync and send is conservatively unresolved, never replayed.
		attempt, err = lease.Advance(attempt, AttemptSent)
		if err != nil {
			return err
		}
		if err := w.send(ctx, cloneAttempt(attempt)); err != nil {
			return errors.Join(ErrWriteUnresolved, err)
		}
		attempt, err = lease.Acknowledge(attempt, sha256Hex([]byte("complete-http-ack:"+attempt.RecordSHA256)))
		if err != nil {
			return err
		}
	}
	if attempt.State == AttemptSent {
		settlement, err := w.proof.Resolve(ctx, w.policy, cloneAttempt(attempt))
		if err != nil {
			return errors.Join(ErrWriteUnresolved, err)
		}
		if settlement.Outcome != "settled_success" {
			// Positive abort evidence belongs to coordinated repair. Missing
			// evidence is not abort, and neither authorizes an INSERT here.
			return fmt.Errorf("%w: settlement=%q", ErrWriteUnresolved, settlement.Outcome)
		}
		if settlement.QueryID != attempt.QueryID || settlement.BodySHA256 != attempt.BodySHA256 ||
			settlement.TopologySHA256 != attempt.TopologySHA256 || !isLowerSHA256(settlement.WitnessSHA256) {
			return errors.New("propertycatalog: settlement witness is not bound to the exact attempt")
		}
		attempt, err = lease.Acknowledge(attempt, settlement.WitnessSHA256)
		if err != nil {
			return err
		}
	}
	// Even complete receipts cross the live topology and coverage boundary;
	// losing a replica cannot be hidden by local journal state or Kafka seeding.
	if err := w.proof.Cover(ctx, w.policy, cloneAttempt(attempt)); err != nil {
		return err
	}
	if err := w.proof.Attest(ctx, w.policy); err != nil {
		return err
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	if attempt.State == AttemptAcknowledged {
		_, err = lease.Advance(attempt, AttemptComplete)
	}
	return err
}
