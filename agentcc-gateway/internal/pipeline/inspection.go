package pipeline

import (
	"context"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// These optional seams run only selected-key policy, never org/static checks,
// billing, or observers. They do not turn guardrails into an AccessGate.
type selectedOperationAdmission interface {
	AdmitSelectedOperation(context.Context, *models.RequestContext) *models.APIError
}

type selectedRequestAdmission interface {
	AdmitSelectedRequest(context.Context, *models.RequestContext) *models.APIError
}

type selectedOutputAdmission interface {
	AdmitSelectedOutput(context.Context, *models.RequestContext) *models.APIError
}

func attachInspection(ctx context.Context, rc *models.RequestContext) {
	if rc.Inspection == nil {
		rc.Inspection = models.InspectionFromContext(ctx)
	}
	if rc.Inspection == nil {
		// Direct/internal callers must declare their operation explicitly. In
		// particular, the pooled EndpointType default is not shape evidence.
		rc.Inspection = models.NewInspectionState(models.InspectionContract{})
	}
}

// AdmitSelectedOperation checks the declared operation after authentication and
// before dispatch. Supported stages remain pending; this does not run checks.
func (e *Engine) AdmitSelectedOperation(ctx context.Context, rc *models.RequestContext) *models.APIError {
	attachInspection(ctx, rc)
	ctx = models.WithRequestContext(ctx, rc)
	for _, p := range e.plugins {
		if admission, ok := p.(selectedOperationAdmission); ok {
			if err := admission.AdmitSelectedOperation(ctx, rc); err != nil {
				return err
			}
		}
	}
	return nil
}

// AdmitSelectedRequest rechecks selected input policy for an actual changed
// provider attempt, without replaying auth, billing or unrelated plugins.
func (e *Engine) AdmitSelectedRequest(ctx context.Context, rc *models.RequestContext) *models.APIError {
	attachInspection(ctx, rc)
	ctx = models.WithRequestContext(ctx, rc)
	for _, p := range e.plugins {
		if admission, ok := p.(selectedRequestAdmission); ok {
			if err := admission.AdmitSelectedRequest(ctx, rc); err != nil {
				return err
			}
		}
	}
	return nil
}

// AdmitSelectedOutput checks a nonbillable stored read using the current reader
// bound to Inspection, without replaying provider accounting/post observers.
func (e *Engine) AdmitSelectedOutput(ctx context.Context, rc *models.RequestContext) *models.APIError {
	attachInspection(ctx, rc)
	ctx = models.WithRequestContext(ctx, rc)
	for _, p := range e.plugins {
		if admission, ok := p.(selectedOutputAdmission); ok {
			if err := admission.AdmitSelectedOutput(ctx, rc); err != nil {
				return err
			}
		}
	}
	return nil
}
