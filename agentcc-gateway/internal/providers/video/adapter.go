package video

import (
	"context"
	"io"
)

// Adapter implementations must be safe for concurrent calls from any replica.
// Prepare produces correlation material before any billable provider call.
// Fetch callers own and must close the returned stream.
type Adapter interface {
	Capabilities() Capabilities
	Prepare(context.Context, *JobView) (Correlation, error)
	Submit(context.Context, *JobView, Correlation) (SubmitResult, error)
	Poll(context.Context, ProviderRef) (Observation, error)
	Fetch(context.Context, ProviderRef, int) (io.ReadCloser, FetchMeta, error)
	Cancel(context.Context, ProviderRef) (CancelResult, error)
	Reconcile(context.Context, *JobView, Correlation) (ReconcileResult, error)
}
