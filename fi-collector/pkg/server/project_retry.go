package server

import (
	"context"
	"errors"
	"net/http"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/grpc/codes"
)

// One internal restart, only for a lost authentication generation. Persistence
// failures and admission refusals still go back to the exporter as retryable.
const maxProjectAuthRestarts = 1

type preparedExport struct {
	ctx                     context.Context
	req                     ptraceotlp.ExportRequest
	routed                  auth.RouteOutcome
	rejectedSpans           int64
	organization, workspace string
	projects, hidden        map[string]struct{}
}

type exportDecoder func() (ptraceotlp.ExportRequest, error)

// prepareExport owns the pre-enqueue boundary for both transports. decode must
// return a new request from the original, immutable wire bytes on EVERY call:
// routing may have renamed resources or removed spans before the stamp fails.
// The failed attempt's pdata is unreachable before decoding again. Keep the
// original wire/work leases: they are global, not tenant leases, and cover the
// same input shape throughout. Never acquire a second copy's lease while holding
// the first, or reset the shared lease-wait budget. Recheck the fresh tenant's
// quota before restarting; conversion, enqueue and billing happen only once.
func (s *Server) prepareExport(ctx context.Context, decode exportDecoder, headers auth.ProjectNameValues) (preparedExport, *rejection) {
	for attempt := 0; ; attempt++ {
		prepared, err := s.prepareExportAttempt(ctx, decode, headers)
		if err == nil {
			return prepared, nil
		}
		var changed *auth.ProjectAuthChangedError
		if !errors.As(err, &changed) || attempt == maxProjectAuthRestarts {
			return preparedExport{}, projectPreparationRejection(err)
		}
		var authErr error
		ctx, authErr = s.auth.Reauthenticate(ctx)
		if authErr != nil {
			if errors.Is(authErr, auth.ErrUnauthenticated) {
				return preparedExport{}, &rejection{reason: "auth", status: http.StatusUnauthorized, code: codes.Unauthenticated, msg: "authentication failed"}
			}
			return preparedExport{}, rejectProjectUnavailable
		}
		if check, ok := s.checkUsage(ctx); !ok {
			return preparedExport{}, quotaRejection(check.Reason)
		}
	}
}

func (s *Server) prepareExportAttempt(ctx context.Context, decode exportDecoder, headers auth.ProjectNameValues) (preparedExport, error) {
	// All attempts share the caller's deadline and cancellation, including a
	// cache-hit reauthentication. Do not enqueue after either has ended.
	if err := ctx.Err(); err != nil {
		return preparedExport{}, err
	}
	req, err := decode()
	if err != nil {
		return preparedExport{}, &rejection{reason: "malformed", msg: err.Error()}
	}
	if _, err := auth.ApplyProjectNameHeader(req.Traces(), headers); err != nil {
		return preparedExport{}, &rejection{reason: "project", msg: err.Error()}
	}
	out := preparedExport{ctx: ctx, req: req}
	if result := auth.FromContext(ctx); result != nil {
		out.organization, out.workspace = result.OrgID, result.WorkspaceID
		ck := auth.CacheKeyFromContext(ctx)
		// Check even if routing would drop everything (Stamp skips empty
		// requests). A stale key must not acknowledge a permanent routing drop.
		if err := s.auth.ResolveProjectsForKey(ctx, ck, result, nil); err != nil {
			return preparedExport{}, err
		}
		out.routed, err = s.routeSimulation(ctx, result, req.Traces())
		if err != nil {
			var changed *auth.ProjectAuthChangedError
			if errors.As(err, &changed) {
				return preparedExport{}, err
			}
			return preparedExport{}, &rejection{reason: "routing", kind: rejectBusy, retryAfter: "5", logged: true, msg: "simulation routing temporarily unavailable"}
		}
		before := req.Traces().SpanCount()
		dropped, err := auth.StampResourceAttrs(ctx, s.auth, ck, req.Traces(), result)
		if err != nil {
			return preparedExport{}, err
		}
		out.rejectedSpans = int64(before - req.Traces().SpanCount())
		if dropped > 0 {
			s.log.Warn("dropped ResourceSpans with unresolvable project", "dropped", dropped)
		}
		// Recheck after routing/stamping, including all-drop outcomes. Never
		// carry an old generation's routing, drops or tenant sidecars forward.
		if err := s.auth.ResolveProjectsForKey(ctx, ck, result, nil); err != nil {
			return preparedExport{}, err
		}
		var scoped bool
		out.projects, scoped = result.ProjectIDsInWorkspace(out.workspace)
		out.hidden, _ = result.HiddenProjectIDsInWorkspace(out.workspace)
		if !scoped {
			return preparedExport{}, &rejection{reason: "project", msg: "auth project/workspace scope mismatch"}
		}
	}
	if err := ctx.Err(); err != nil {
		return preparedExport{}, err
	}
	return out, nil
}

func projectPreparationRejection(err error) *rejection {
	var rej *rejection
	switch {
	case errors.As(err, &rej):
		return rej
	case errors.Is(err, auth.ErrProjectResolutionUnavailable), errors.Is(err, context.Canceled), errors.Is(err, context.DeadlineExceeded):
		return rejectProjectUnavailable
	case errors.Is(err, auth.ErrTooManyNewProjects):
		return rejectNewProjects
	default:
		return &rejection{reason: "project", msg: "auth stamp: " + err.Error()}
	}
}

// One deadline spans body reception, project resolution and any auth restart.
// An earlier caller deadline always wins. No per-attempt timeout is renewed.
func ingestContext(ctx context.Context) (context.Context, context.CancelFunc) {
	return context.WithTimeout(ctx, 30*time.Second)
}
