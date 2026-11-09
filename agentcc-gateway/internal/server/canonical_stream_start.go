package server

import (
	"context"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// awaitCanonicalStreamStart waits for admission without committing HTTP headers.
// Adapters enqueue a terminal error before closing chunks. Buffered output must
// win over that error: it may carry usage, even if the upstream already failed.
// The returned error channel retains a terminal error consumed during startup.
func awaitCanonicalStreamStart(ctx context.Context, chunks <-chan models.StreamChunk, errs <-chan error) (*models.StreamChunk, <-chan error, error) {
	contextError := func() error {
		if ctx.Err() == context.DeadlineExceeded {
			// The deadline can beat the adapter's timeout error to this select.
			// Keep the same HTTP 504 rather than rendering a raw context error as 500.
			return models.ErrGatewayTimeout("upstream stream timed out before sending any chunks")
		}
		return ctx.Err()
	}
	readChunk := func(chunk models.StreamChunk, ok bool) (*models.StreamChunk, <-chan error, error) {
		if ok {
			return &chunk, errs, nil
		}
		select {
		case err := <-errs:
			if err != nil {
				return nil, errs, err
			}
		default:
		}
		if err := contextError(); err != nil {
			return nil, errs, err
		}
		return nil, errs, models.ErrUpstreamProvider(http.StatusBadGateway, "upstream stream closed before sending any chunks")
	}
	for {
		// Prefer a chunk already queued to a terminal error queued after it.
		select {
		case chunk, ok := <-chunks:
			return readChunk(chunk, ok)
		default:
		}
		select {
		case chunk, ok := <-chunks:
			return readChunk(chunk, ok)
		case err, ok := <-errs:
			if !ok {
				errs = nil
				continue
			}
			if err != nil {
				// Output may have arrived between the first select and this
				// error. Do not turn billable output into a pre-stream refusal.
				select {
				case chunk, ok := <-chunks:
					if ok {
						tail := make(chan error, 1)
						tail <- err
						close(tail)
						return &chunk, tail, nil
					}
				default:
				}
				return nil, errs, err
			}
		case <-ctx.Done():
			// As with a terminal error, retain output queued concurrently with
			// cancellation so its usage is still metered by the handler.
			select {
			case chunk, ok := <-chunks:
				return readChunk(chunk, ok)
			default:
			}
			return nil, errs, contextError()
		}
	}
}
