package lifecycle

import (
	"context"
	"errors"
	"github.com/futureagi/agentcc-gateway/internal/models"
	provider "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"github.com/futureagi/agentcc-gateway/internal/providers/video/videotest"
	"github.com/futureagi/agentcc-gateway/internal/video"
	"github.com/redis/go-redis/v9"
	"sync/atomic"
	"testing"
	"time"
)

type outageHook struct{ down atomic.Bool }

func (h *outageHook) DialHook(next redis.DialHook) redis.DialHook { return next }
func (h *outageHook) ProcessPipelineHook(next redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return next
}
func (h *outageHook) ProcessHook(next redis.ProcessHook) redis.ProcessHook {
	return func(ctx context.Context, c redis.Cmder) error {
		if h.down.Load() {
			return errors.New("test Redis transport unavailable")
		}
		return next(ctx, c)
	}
}
func TestRedisOutage_FailClosedThenRecover(t *testing.T) {
	f := setup(t, videotest.Script{})
	j := f.accept(t)
	hook := &outageHook{}
	f.redis.Redis().AddHook(hook)
	hook.down.Store(true)
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Metadata["org_id"] = "org-a"
	_, e := f.s.Accept(context.Background(), rc, provider.Request{Model: "byteplus/" + modelID, Prompt: "x"}, "outage", nil)
	if !errors.Is(e, video.ErrStoreUnavailable) {
		t.Fatal(e)
	}
	if e = f.s.NewWorker().Tick(context.Background()); !errors.Is(e, video.ErrStoreUnavailable) {
		t.Fatal(e)
	}
	hook.down.Store(false)
	f.tick(t)
	if f.job(t, j.ID).Status != video.StatusQueued || f.fake.SubmitCount(j.ID) != 1 {
		t.Fatal("did not recover")
	}
}
func TestReconcile_ProvenAbsentRequiresCapabilityAndOptIn(t *testing.T) {
	for _, allow := range []bool{false, true} {
		t.Run(map[bool]string{false: "disabled", true: "enabled"}[allow], func(t *testing.T) {
			f := setup(t, videotest.Script{Submit: videotest.Reply{Status: 500}, List: videotest.JSONReply(provider.ReconcileResult{ProvenAbsent: true})})
			a := &lookupAdapter{Adapter: f.fake.Adapter()}
			registry := provider.NewRegistry()
			registry.Register(a)
			f.s.adapters = registry
			f.s.cfg.Reconcile.AllowAbsentResubmit = allow
			j := f.accept(t)
			f.tick(t)
			f.clock.Advance(6 * time.Second)
			f.tick(t)
			j = f.job(t, j.ID)
			if allow && j.Status != video.StatusSubmitting {
				t.Fatal(j.Status)
			}
			if !allow && j.Status != video.StatusSubmissionUnknown {
				t.Fatal(j.Status)
			}
		})
	}
}

type lookupAdapter struct{ provider.Adapter }

func (a *lookupAdapter) Capabilities() provider.Capabilities {
	c := a.Adapter.Capabilities()
	c.CorrelationLookup = provider.LookupByToken
	return c
}
