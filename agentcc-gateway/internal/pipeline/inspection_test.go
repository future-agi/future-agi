package pipeline

import (
	"context"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

type inspectionSeamPlugin struct {
	requestContextCapturePlugin
	operation, input, output int
	seen                     *models.InspectionState
	principal                string
}

func (p *inspectionSeamPlugin) capture(ctx context.Context, rc *models.RequestContext) {
	p.seen = rc.Inspection
	if models.GetRequestContext(ctx) != rc {
		panic("missing pipeline context")
	}
	_, p.principal = rc.Inspection.Principal()
}
func (p *inspectionSeamPlugin) AdmitSelectedOperation(ctx context.Context, rc *models.RequestContext) *models.APIError {
	p.capture(ctx, rc)
	p.operation++
	return nil
}
func (p *inspectionSeamPlugin) AdmitSelectedRequest(ctx context.Context, rc *models.RequestContext) *models.APIError {
	p.capture(ctx, rc)
	p.input++
	return nil
}
func (p *inspectionSeamPlugin) AdmitSelectedOutput(ctx context.Context, rc *models.RequestContext) *models.APIError {
	p.capture(ctx, rc)
	p.output++
	return nil
}

type inspectionAuthGate struct {
	requestContextCapturePlugin
	denied bool
}

func (p *inspectionAuthGate) Name() string       { return "synthetic-auth" }
func (p *inspectionAuthGate) Priority() int      { return 1 }
func (p *inspectionAuthGate) IsAccessGate() bool { return true }
func (p *inspectionAuthGate) ProcessRequest(_ context.Context, rc *models.RequestContext) PluginResult {
	if p.denied {
		return ResultError(models.ErrForbidden("synthetic access denial"))
	}
	rc.Inspection.BindPrincipal("org", "authenticated-reader")
	return ResultContinue()
}

func TestInspectionPipelineSelectedSeamsDoNotRunGeneralPlugins(t *testing.T) {
	p := &inspectionSeamPlugin{}
	e := NewEngine(p)
	state := models.NewInspectionState(models.InspectionContract{Operation: "metadata.get"})
	ctx := models.WithInspection(context.Background(), state)
	rc := models.AcquireRequestContext()
	defer rc.Release()
	if err := e.AdmitSelectedOperation(ctx, rc); err != nil {
		t.Fatal(err)
	}
	if err := e.AdmitSelectedOutput(ctx, rc); err != nil {
		t.Fatal(err)
	}
	if p.operation != 1 || p.output != 1 || p.input != 0 || p.preContext != nil || p.postContext != nil || p.seen != state || rc.Inspection != state {
		t.Fatalf("selected seams ran general plugins or lost state: %+v", p)
	}
}

func TestInspectionCheckAccessAuthenticatesBeforeSelectedChecks(t *testing.T) {
	for _, denied := range []bool{false, true} {
		p := &inspectionSeamPlugin{}
		e := NewEngine(p, &inspectionAuthGate{denied: denied})
		rc := models.AcquireRequestContext()
		err := e.CheckAccess(context.Background(), rc)
		if denied {
			if err == nil || p.input != 0 {
				t.Fatalf("failed access ran selected admission: err=%v calls=%d", err, p.input)
			}
		} else if err != nil || p.input != 1 || p.principal != "authenticated-reader" || p.preContext != nil {
			t.Fatalf("selected admission did not follow auth: err=%v plugin=%+v", err, p)
		}
		rc.Release()
	}
}

func TestInspectionPipelineNeverInfersContractFromEndpointType(t *testing.T) {
	for _, seam := range []string{"process", "access", "post"} {
		t.Run(seam, func(t *testing.T) {
			e := NewEngine()
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.EndpointType = "chat"
			rc.Metadata["inspection_complete"] = "true"
			switch seam {
			case "process":
				e.Process(context.Background(), rc, func(context.Context, *models.RequestContext) error { return nil })
			case "access":
				e.CheckAccess(context.Background(), rc)
			case "post":
				e.RunPostPlugins(context.Background(), rc)
			}
			if rc.Inspection == nil || rc.Inspection.Contract().Operation != "" || rc.Inspection.Contract().Input != models.InspectionUnknown {
				t.Fatal("pipeline fabricated operation support")
			}
		})
	}
}

func TestInspectionPipelineAttachesContextAtEveryBoundary(t *testing.T) {
	for _, seam := range []string{"process", "access", "post"} {
		t.Run(seam, func(t *testing.T) {
			e := NewEngine()
			rc := models.AcquireRequestContext()
			defer rc.Release()
			state := models.NewInspectionState(models.InspectionContract{Operation: "explicit.internal"})
			ctx := models.WithInspection(context.Background(), state)
			switch seam {
			case "process":
				e.Process(ctx, rc, func(context.Context, *models.RequestContext) error { return nil })
			case "access":
				e.CheckAccess(ctx, rc)
			case "post":
				e.RunPostPlugins(ctx, rc)
			}
			if rc.Inspection != state {
				t.Fatal("pipeline lost dispatch state")
			}
		})
	}
}
