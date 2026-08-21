package catalogwriter

import (
	"context"
	"errors"
	"sync"
)

// AsyncSubmitter moves JSON encoding and WAL fsync off the sole canonical span
// flusher. Its queue is deliberately bounded; saturation returns an explicit
// SubmissionGapError carrying immutable metadata, never a silent drop.
type AsyncSubmitter struct {
	writer *Writer
	queue  chan Job
	gaps   chan error
	stop   chan struct{}
	once   sync.Once
	wg     sync.WaitGroup
}

// AttributeCatalogWriter combines bounded staging with asynchronous WAL
// ownership transfer for server.WithAttributeCatalogWriter.
type AttributeCatalogWriter struct {
	Writer    *Writer
	Submitter *AsyncSubmitter
}

func (w *AttributeCatalogWriter) StageCanonicalSpans(
	rows []map[string]any,
) (Job, StageReport) {
	if w == nil || w.Writer == nil {
		return Job{}, StageReport{InputSpans: len(rows)}
	}
	return w.Writer.StageCanonicalSpans(rows)
}

func (w *AttributeCatalogWriter) StageCanonicalSpansByProject(
	rows []map[string]any,
) []StagedProjectJob {
	if w == nil || w.Writer == nil {
		return nil
	}
	return w.Writer.StageCanonicalSpansByProject(rows)
}

func (w *AttributeCatalogWriter) Enqueue(job Job) error {
	if w == nil || w.Submitter == nil {
		return errors.New("catalogwriter: nil attribute catalog submitter")
	}
	return w.Submitter.Enqueue(job)
}

// NewAsyncSubmitter creates a dormant actor. Call Run exactly once before
// Enqueue. queueDepth and gapDepth are hard memory bounds.
func NewAsyncSubmitter(writer *Writer, queueDepth, gapDepth int) (*AsyncSubmitter, error) {
	if writer == nil || !writer.Enabled() {
		return nil, errors.New("catalogwriter: async submitter requires enabled writer")
	}
	if queueDepth <= 0 || gapDepth <= 0 {
		return nil, errors.New("catalogwriter: async submitter requires positive queue and gap bounds")
	}
	return &AsyncSubmitter{
		writer: writer, queue: make(chan Job, queueDepth), gaps: make(chan error, gapDepth),
		stop: make(chan struct{}),
	}, nil
}

// Enqueue transfers a staged immutable job without blocking. On saturation it
// returns a metadata-carrying gap that must keep the epoch unqualified.
func (a *AsyncSubmitter) Enqueue(job Job) error {
	if a == nil {
		return errors.New("catalogwriter: nil async submitter")
	}
	select {
	case <-a.stop:
		return submissionGap("enqueue catalog WAL", job, errors.New("catalog submitter is stopped"))
	default:
	}
	select {
	case <-a.stop:
		return submissionGap("enqueue catalog WAL", job, errors.New("catalog submitter is stopped"))
	case a.queue <- job:
		return nil
	default:
		return submissionGap("enqueue catalog WAL", job, errors.New("bounded catalog submit queue full"))
	}
}

// Gaps exposes asynchronous WAL failures to monitoring/coverage coordination.
// The channel is never closed; consumers stop with their runtime context.
func (a *AsyncSubmitter) Gaps() <-chan error { return a.gaps }

// Run starts one worker. Repeated calls are no-ops. Submit itself remains
// serialized by Writer admission and retains exact fsync/cap semantics.
func (a *AsyncSubmitter) Run(ctx context.Context) {
	if a == nil {
		return
	}
	a.once.Do(func() {
		a.wg.Add(1)
		go func() {
			defer a.wg.Done()
			defer close(a.stop)
			for {
				if ctx.Err() != nil {
					a.drainAccepted()
					return
				}
				select {
				case <-ctx.Done():
					a.drainAccepted()
					return
				case job := <-a.queue:
					a.submit(ctx, job)
				}
			}
		}()
	})
}

func (a *AsyncSubmitter) drainAccepted() {
	for {
		select {
		case job := <-a.queue:
			a.submit(context.Background(), job)
		default:
			return
		}
	}
}

func (a *AsyncSubmitter) submit(ctx context.Context, job Job) {
	if err := a.writer.Submit(ctx, job); err != nil {
		select {
		case a.gaps <- err:
		default:
			// A full gap channel is itself observable by the caller's missing
			// drain/frozen-stream sequence. Runtime remains development-only.
		}
	}
}

func (a *AsyncSubmitter) Wait() {
	if a != nil {
		a.wg.Wait()
	}
}
