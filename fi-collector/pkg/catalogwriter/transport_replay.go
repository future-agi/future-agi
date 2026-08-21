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

// ReplayTo drains validated spool envelopes through a transport-neutral
// handler in deterministic oldest/name order. It performs no catalog insert
// or progress acknowledgement itself. Replay and ReplayTo share the same
// worker lock, so one envelope cannot be delivered concurrently by the direct
// and transport-neutral paths.
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
	for _, file := range files {
		result.Attempted++
		if err := w.deliverPending(ctx, file, handler); err != nil {
			return result, err
		}
		result.Delivered++
	}
	return result, nil
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
