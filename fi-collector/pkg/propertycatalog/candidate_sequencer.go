package propertycatalog

import (
	"context"
	"errors"
	"fmt"
	"sync"

	"github.com/future-agi/future-agi/fi-collector/pkg/catalogkafka"
)

type CandidateAcceptor interface {
	AcceptCandidate(WireCandidate) (exactDuplicate bool, err error)
}

// CandidateSequencer is the only bridge from unsequenced candidate records to
// HotRuntime. Kafka offsets are committed after receipt fsync, never after an
// in-memory queue acceptance. The receipt is removed only after HotRuntime has
// durably spooled the ordered envelope (or proven an exact duplicate).
type CandidateSequencer struct {
	topic     string
	source    catalogkafka.ManualRecordSource
	receipts  *CandidateReceiptStore
	acceptor  CandidateAcceptor
	gaps      chan error
	processMu sync.Mutex
}

func NewCandidateSequencer(
	topic string,
	source catalogkafka.ManualRecordSource,
	receipts *CandidateReceiptStore,
	acceptor CandidateAcceptor,
) (*CandidateSequencer, error) {
	if err := validateTopic(topic); err != nil {
		return nil, err
	}
	if source == nil || receipts == nil || acceptor == nil {
		return nil, errors.New("propertycatalog: candidate sequencer requires source, receipts, and acceptor")
	}
	if receipts.cfg.Topic != topic {
		return nil, errors.New("propertycatalog: candidate sequencer topic does not match receipt store")
	}
	return &CandidateSequencer{
		topic: topic, source: source, receipts: receipts, acceptor: acceptor,
		gaps: make(chan error, 64),
	}, nil
}

func (s *CandidateSequencer) ReplayPending(ctx context.Context) error {
	if s == nil || ctx == nil {
		return errors.New("propertycatalog: candidate replay requires sequencer and context")
	}
	s.processMu.Lock()
	defer s.processMu.Unlock()
	receipts, err := s.receipts.Pending()
	if err != nil {
		return err
	}
	for _, receipt := range receipts {
		if err := ctx.Err(); err != nil {
			return err
		}
		if err := s.processReceipt(receipt); err != nil {
			partition, offset := receipt.Coordinate()
			return fmt.Errorf(
				"propertycatalog: replay candidate partition %d offset %d: %w",
				partition, offset, err,
			)
		}
	}
	return nil
}

// ProcessOne holds the Kafka assignment through receipt fsync and offset
// commit. A commit error leaves the receipt intact. A later replay is safe even
// when the broker actually accepted an ambiguous commit.
func (s *CandidateSequencer) ProcessOne(ctx context.Context) error {
	if s == nil || s.source == nil || s.receipts == nil || s.acceptor == nil || ctx == nil {
		return errors.New("propertycatalog: nil candidate sequencer")
	}
	s.processMu.Lock()
	defer s.processMu.Unlock()
	record, err := s.source.PollOne(ctx)
	defer s.source.AllowRebalance()
	if err != nil {
		return err
	}
	if record.Topic != s.topic {
		return fmt.Errorf("%w: candidate source returned the wrong topic", ErrPoisonRecord)
	}
	receipt, completed, err := s.receipts.Receive(record)
	if err != nil {
		return err
	}
	// This commit is deliberately before processing but after the fsync receipt.
	// A crash after it cannot lose work; startup replay owns the receipt.
	if err := s.source.Commit(ctx, record); err != nil {
		return fmt.Errorf("propertycatalog: commit durably received candidate: %w", err)
	}
	if completed {
		return nil
	}
	return s.processReceipt(receipt)
}

func (s *CandidateSequencer) processReceipt(receipt CandidateReceipt) error {
	completed, err := s.receipts.Completed(receipt)
	if err != nil {
		return err
	}
	if !completed {
		if _, err := s.acceptor.AcceptCandidate(receipt.Candidate()); err != nil {
			var notAdmitted *CandidateNotAdmittedError
			if errors.As(err, &notAdmitted) {
				// The canonical span is already durable. Persist skip completion
				// before reporting success so restart cannot livelock on a dark or
				// canary workspace. Reconciliation is the recovery path.
				if completeErr := s.receipts.CompleteNotAdmitted(receipt, notAdmitted.Reason); completeErr != nil {
					return fmt.Errorf(
						"propertycatalog: persist non-admitted candidate completion: %w",
						completeErr,
					)
				}
				s.reportGap(fmt.Errorf(
					"propertycatalog: skipped canonical candidate; reconciliation required: %w",
					notAdmitted,
				))
				return nil
			}
			return err
		}
	}
	return s.receipts.Complete(receipt)
}

// SkippedCandidates is a monotonic process-local metric for candidates whose
// durable receipts were completed at an explicit rollout boundary.
func (s *CandidateSequencer) SkippedCandidates() uint64 {
	if s == nil {
		return 0
	}
	return s.receipts.SkippedTotal()
}

// Gaps reports durably completed candidates intentionally skipped at a
// rollout boundary. It never reports transient, poison, or conflict failures:
// those remain returned from ProcessOne/Run and therefore fail closed.
func (s *CandidateSequencer) Gaps() <-chan error {
	if s == nil {
		return nil
	}
	return s.gaps
}

func (s *CandidateSequencer) reportGap(err error) {
	if err == nil {
		return
	}
	select {
	case s.gaps <- err:
	default:
	}
}

func (s *CandidateSequencer) Run(ctx context.Context) error {
	if s == nil || ctx == nil {
		return errors.New("propertycatalog: candidate sequencer run requires a context")
	}
	if err := s.ReplayPending(ctx); err != nil {
		return err
	}
	for {
		if err := s.ProcessOne(ctx); err != nil {
			return err
		}
	}
}

func (s *CandidateSequencer) Close() {
	if s != nil && s.source != nil {
		s.source.Close()
	}
}
