package catalogwriter

import (
	"context"
	"errors"
	"fmt"
	"time"
)

// PendingDelivery is the transport-neutral representation of one validated
// spool envelope. WireJob is a defensive copy: a handler cannot mutate the
// durable envelope, the writer's accounting, or a later retry.
type PendingDelivery struct {
	ID        string
	CreatedAt time.Time
	WireJob   WireJob
}

// DeliveryHandler durably acknowledges one complete catalog job. Returning
// nil is the acknowledgement that permits ReplayTo to remove the spool
// envelope. Returning an error leaves this envelope and every later envelope
// intact for an ordered retry.
type DeliveryHandler interface {
	DeliverCatalogJob(context.Context, PendingDelivery) error
}

// PermanentDeliveryError identifies a valid durable envelope that can never
// be accepted by the configured transport. ReplayTo quarantines only this
// explicit classification; every other error remains retryable and preserves
// ordered head-of-line blocking.
type PermanentDeliveryError struct {
	Err error
}

func (e *PermanentDeliveryError) Error() string {
	return fmt.Sprintf("catalogwriter: permanent delivery failure: %v", e.Err)
}

func (e *PermanentDeliveryError) Unwrap() error { return e.Err }

// ReplayTo drains validated spool envelopes through a transport-neutral
// handler in deterministic oldest/name order. It performs no catalog insert
// or progress acknowledgement itself. Replay and ReplayTo share the same
// worker lock, so one envelope cannot be delivered concurrently by the direct
// and transport-neutral paths. Explicit permanent failures are atomically
// quarantined and reported after all later deliverable envelopes are tried;
// transient failures still stop immediately in oldest/name order.
func (w *Writer) ReplayTo(ctx context.Context, handler DeliveryHandler) (ReplayResult, error) {
	if w == nil || !w.cfg.Enabled {
		return ReplayResult{}, nil
	}
	if handler == nil {
		return ReplayResult{}, errors.New("catalogwriter: ReplayTo requires a delivery handler")
	}
	w.replayMu.Lock()
	defer w.replayMu.Unlock()
	if err := w.acquireAdmission(ctx); err != nil {
		return ReplayResult{}, err
	}
	files, err := w.spool.enumerate(w.cfg.MaxSpoolFiles)
	w.releaseAdmission()
	if err != nil {
		return ReplayResult{}, err
	}
	result := ReplayResult{}
	var replayErr error
	for _, file := range files {
		result.Attempted++
		if err := w.deliverPending(ctx, file, handler); err != nil {
			var permanent *PermanentDeliveryError
			if !errors.As(err, &permanent) {
				return result, errors.Join(replayErr, err)
			}
			moved, quarantineErr := w.quarantinePending(ctx, file)
			if moved {
				result.Quarantined++
				replayErr = errors.Join(replayErr, err)
			}
			if quarantineErr != nil {
				return result, errors.Join(replayErr, quarantineErr)
			}
			continue
		}
		result.Delivered++
	}
	return result, replayErr
}

func (w *Writer) deliverPending(
	ctx context.Context, pending pendingFile, handler DeliveryHandler,
) error {
	envelope, err := w.spool.load(pending, w.maxEnvelopeBytes())
	if err != nil {
		return err
	}
	if err := w.validateJob(envelope.Job); err != nil {
		return fmt.Errorf("catalogwriter: invalid pending job %s: %w", envelope.ID, err)
	}
	if len(envelope.Job.metadata.Projects) == 0 {
		return &PermanentDeliveryError{Err: fmt.Errorf(
			"pending job %s has no project scope", envelope.ID,
		)}
	}
	delivery := PendingDelivery{
		ID:        envelope.ID,
		CreatedAt: envelope.CreatedAt,
		WireJob:   ExportWireJob(envelope.Job),
	}
	if err := handler.DeliverCatalogJob(ctx, delivery); err != nil {
		return fmt.Errorf("catalogwriter: deliver %s: %w", envelope.ID, err)
	}
	if err := w.acquireAdmission(ctx); err != nil {
		return fmt.Errorf("catalogwriter: finalize delivered envelope %s: %w", envelope.ID, err)
	}
	removed, removeErr := w.spool.remove(pending)
	if removed {
		w.spoolFiles--
		w.spoolBytes -= pending.size
		if w.spoolFiles < 0 || w.spoolBytes < 0 {
			// Preserve the same fail-closed accounting invariant as Replay.
			// Restart reconstructs the exact counters from the spool directory.
			w.spoolFiles = w.cfg.MaxSpoolFiles
			w.spoolBytes = w.cfg.MaxSpoolBytes
			removeErr = errors.Join(removeErr, errors.New("catalogwriter: spool accounting underflow"))
		}
	}
	w.releaseAdmission()
	if removeErr != nil {
		return fmt.Errorf("catalogwriter: remove delivered envelope %s: %w", envelope.ID, removeErr)
	}
	return nil
}

func (w *Writer) quarantinePending(ctx context.Context, pending pendingFile) (bool, error) {
	if err := w.acquireAdmission(ctx); err != nil {
		return false, fmt.Errorf("catalogwriter: acquire quarantine admission: %w", err)
	}
	moved, err := w.spool.quarantine(pending)
	w.releaseAdmission()
	if err != nil {
		return moved, fmt.Errorf("catalogwriter: finalize permanent envelope %s: %w", pending.name, err)
	}
	return moved, nil
}
