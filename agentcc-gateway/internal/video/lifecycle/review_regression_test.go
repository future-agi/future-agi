package lifecycle

import (
	"bytes"
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"image"
	"image/png"
	"io"
	"net"
	"net/http/httptest"
	"os"
	"os/exec"
	"strings"
	"sync"
	"testing"
	"time"

	budgettracker "github.com/futureagi/agentcc-gateway/internal/budget"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/metrics"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	budgetplugin "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	provider "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"github.com/futureagi/agentcc-gateway/internal/providers/video/videotest"
	"github.com/futureagi/agentcc-gateway/internal/video"
	"github.com/futureagi/agentcc-gateway/internal/video/artifacts"
	"github.com/futureagi/agentcc-gateway/internal/video/capability"
	"github.com/redis/go-redis/v9"
)

type lostCreateReply struct {
	once  sync.Once
	fired bool
}

func (h *lostCreateReply) DialHook(next redis.DialHook) redis.DialHook { return next }
func (h *lostCreateReply) ProcessPipelineHook(next redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return next
}
func (h *lostCreateReply) ProcessHook(next redis.ProcessHook) redis.ProcessHook {
	return func(ctx context.Context, cmd redis.Cmder) error {
		err := next(ctx, cmd)
		if err == nil && (cmd.Name() == "eval" || cmd.Name() == "evalsha") && len(cmd.Args()) == 19 && fmt.Sprint(cmd.Args()[2]) == "11" && fmt.Sprint(cmd.Args()[15]) == "1" {
			h.once.Do(func() { h.fired = true; err = errors.New("create committed; reply lost") })
		}
		return err
	}
}
func imageRequest(t *testing.T) provider.Request {
	t.Helper()
	var b bytes.Buffer
	if err := png.Encode(&b, image.NewRGBA(image.Rect(0, 0, 400, 400))); err != nil {
		t.Fatal(err)
	}
	return provider.Request{Model: "openrouter/" + orModel, Prompt: "image regression", DurationSeconds: 4, Resolution: "480p", Inputs: []provider.Input{{Role: provider.FirstFrame, MediaType: "image/png", Source: provider.Source{Data: base64.StdEncoding.EncodeToString(b.Bytes())}}}}
}
func videoContext() *models.RequestContext {
	rc := models.AcquireRequestContext()
	rc.EndpointType = "video"
	rc.Metadata["org_id"] = "org-a"
	rc.Metadata["key_org_id"] = "org-a"
	rc.Metadata["auth_key_id"] = "key-a"
	rc.Metadata["key_type"] = "byok"
	return rc
}
func TestReviewLostReplyPreservesCommittedIngress(t *testing.T) {
	f := setupOpenRouter(t, videotest.Script{})
	hook := &lostCreateReply{}
	f.redis.Redis().AddHook(hook)
	rc := videoContext()
	defer rc.Release()
	idem := video.NewID()
	r, err := f.s.Accept(context.Background(), rc, imageRequest(t), idem, nil)
	if err != nil {
		t.Fatal(err)
	}
	if !hook.fired || len(r.Job.Ingress) != 1 {
		t.Fatal("lost reply not exercised")
	}
	if _, err = f.s.blobs.Stat(context.Background(), r.Job.Ingress[0].Key); err != nil {
		t.Fatalf("committed ingress lost: %v", err)
	}
	replay, err := f.s.Accept(context.Background(), rc, imageRequest(t), idem, nil)
	if err != nil || replay.Job.ID != r.Job.ID {
		t.Fatalf("replay: %v", err)
	}
	if _, err = f.s.blobs.Stat(context.Background(), r.Job.Ingress[0].Key); err != nil {
		t.Fatal("replay deleted owning job media", err)
	}
	f.tick(t)
	j := f.job(t, r.Job.ID)
	if j.Status != video.StatusQueued || f.fake.SubmitCount(j.ID) != 1 {
		t.Fatalf("job=%s submissions=%d", j.Status, f.fake.SubmitCount(j.ID))
	}
}

func TestReviewKnownUnsentRetryAndCapacity(t *testing.T) {
	cost := 0.0461
	f := setupOpenRouter(t, videotest.Script{Poll: []videotest.Reply{videotest.JSONReply(orCompleted(&cost))}, Fetch: videotest.Reply{Body: videotest.MP4()}})
	f.s.cfg.Limits.OrgMaxActiveJobs = 1
	j := f.acceptOpenRouter(t)
	old := f.fake.Server
	addr := old.Listener.Addr().String()
	handler := old.Config.Handler
	old.Close()
	if err := f.s.NewWorker().Tick(context.Background()); err != nil {
		t.Fatalf("known-unsent transition: %v", err)
	}
	j = f.job(t, j.ID)
	if j.Status != video.StatusSubmitting || j.Phase != video.PhasePrepared || j.UpstreamMayContinue {
		t.Fatalf("unsafe retry state: %+v", j)
	}
	listener, err := net.Listen("tcp", addr)
	if err != nil {
		t.Fatal(err)
	}
	recovered := httptest.NewUnstartedServer(handler)
	recovered.Listener = listener
	recovered.Start()
	t.Cleanup(recovered.Close)
	f.clock.Advance(2 * time.Second)
	j = f.runToTerminal(t, j.ID)
	if j.Status != video.StatusCompleted || j.SettlementState != video.SettlementSettled || f.fake.SubmitCount(j.ID) != 1 {
		t.Fatalf("recovery state=%s settlement=%s submits=%d", j.Status, j.SettlementState, f.fake.SubmitCount(j.ID))
	}
	total, _, ok := f.budget.GetSpend(j.OrgID, "org", "", "total")
	if !ok || total != cost {
		t.Fatalf("reservation not reconciled: %v", total)
	}
	f.acceptOpenRouter(t) // the first job no longer holds the single active slot
}

func TestReviewGlobalAndTenantSettlement(t *testing.T) {
	for _, tenantScopes := range []bool{false, true} {
		t.Run(fmt.Sprint(tenantScopes), func(t *testing.T) {
			cost := 0.1 // larger than estimate and the global ceiling: next request must stop
			f := setupOpenRouter(t, videotest.Script{Poll: []videotest.Reply{videotest.JSONReply(orCompleted(&cost))}, Fetch: videotest.Reply{Body: videotest.MP4()}})
			lc := config.BudgetLevelConfig{Limit: 0.06, Period: "total"}
			cfg := config.BudgetsConfig{Enabled: true, DefaultPeriod: "total", Org: &lc, Teams: map[string]config.BudgetLevelConfig{"team": lc}, Users: map[string]config.BudgetLevelConfig{"owner": lc}, Keys: map[string]config.BudgetLevelConfig{"key-name": lc}, Tags: map[string]config.BudgetLevelConfig{"project:regression": lc}}
			tracker := budgettracker.NewTracker(cfg)
			bp := budgetplugin.New(tracker, nil, true, nil)
			bp.SetRedisBudget(f.budget)
			bp.ShareGlobalBudgets() // video gateways share global levels with the ledger
			f.s.engine = pipeline.NewEngine(costplugin.New(true, nil, nil, nil), bp)
			rc := videoContext()
			defer rc.Release()
			rc.Model = "openrouter/" + orModel
			rc.Metadata["key_team"] = "team"
			rc.Metadata["auth_key_owner"] = "owner"
			rc.Metadata["auth_key_name"] = "key-name"
			rc.Metadata["tag:project"] = "regression"
			var scopes []video.BudgetReservation
			if tenantScopes {
				scopes = []video.BudgetReservation{{Level: "org", Period: "total", Limit: 1}}
			}
			var accepted video.AcceptResult
			err := f.s.engine.Process(context.Background(), rc, func(ctx context.Context, rc *models.RequestContext) error {
				var err error
				off := false
				accepted, err = f.s.Accept(ctx, rc, provider.Request{Model: rc.Model, Prompt: "budget regression", DurationSeconds: 4, Resolution: "480p", AspectRatio: "16:9", Audio: &off}, video.NewID(), scopes)
				return err
			})
			if err != nil {
				t.Fatal(err)
			}
			reserved, _, _ := f.budget.GetSpend("", "global_org", "", "total")
			if reserved != float64(accepted.Job.ReservedMicros)/1e6 {
				t.Errorf("global acceptance reservation=%v want %v", reserved, accepted.Job.EstimateUSD)
			}
			f.tick(t)
			f.clock.Advance(6 * time.Second)
			once := true
			f.s.hooks = HookFunc(func(point string) error {
				if point == "settle.recorded" && once {
					once = false
					return ErrCrash
				}
				return nil
			})
			err = f.s.NewWorker().Tick(context.Background())
			if !errors.Is(err, ErrCrash) {
				t.Fatalf("settlement crash not reached: %v", err)
			}
			f.redis.Redis().Del(context.Background(), f.prefix+"lease:"+accepted.Job.ID)
			f.s.hooks = nil
			f.clock.Advance(6 * time.Second)
			j := f.runToTerminal(t, accepted.Job.ID)
			if j.SettlementState != video.SettlementSettled || j.Cost != cost {
				t.Fatalf("settlement=%s cost=%v", j.SettlementState, j.Cost)
			}
			for _, scope := range [][2]string{{"org", ""}, {"team", "team"}, {"user", "owner"}, {"key", "key-name"}, {"tag", "project:regression"}} {
				got, _ := tracker.GetSpend(scope[0], scope[1])
				if got != cost {
					t.Errorf("%s budget=%v want %v", scope, got, cost)
				}
			}
			if tenantScopes {
				got, _, _ := f.budget.GetSpend("org-a", "org", "", "total")
				if got != cost {
					t.Errorf("tenant duplicated/lost: %v", got)
				}
			}
			// Ordinary responses share these same global counters. A later
			// Postgres seed includes video logs and must not count them twice.
			tracker.RecordSpend("team", "owner", "key-name", rc.Model, map[string]string{"project": "regression"}, 0.02)
			tracker.SeedSpend("org", "", 0.12, map[string]float64{rc.Model: 0.12})
			got, _ := tracker.GetSpend("org", "")
			if got < 0.119999 || got > 0.120001 {
				t.Errorf("ordinary spend/seed duplicated video: %v", got)
			}
			// A new process/tracker reads the shared ledger rather than resetting it.
			fresh := budgetplugin.New(budgettracker.NewTracker(cfg), nil, true, nil)
			fresh.SetRedisBudget(f.budget)
			fresh.ShareGlobalBudgets() // video gateways share global levels with the ledger
			if result := fresh.ProcessRequest(context.Background(), rc); result.Error == nil || result.Error.Status != 429 {
				t.Errorf("next request allowed: %+v", result)
			}
		})
	}
}

type exitAfterPut struct{ artifacts.Store }

func (s exitAfterPut) Put(ctx context.Context, key string, r io.Reader, m artifacts.Meta) (artifacts.Meta, error) {
	meta, err := s.Store.Put(ctx, key, r, m)
	if err == nil {
		os.Exit(37)
	}
	return meta, err
}
func TestReviewIngressCrashIsSweptAfterRestart(t *testing.T) {
	if root := os.Getenv("VIDEO_CRASH_ROOT"); root != "" {
		f := setupOpenRouter(t, videotest.Script{})
		disk, err := artifacts.NewDisk(root, f.s.cfg.Artifacts.MaxBytes)
		if err != nil {
			t.Fatal(err)
		}
		f.s.blobs = exitAfterPut{disk}
		f.s.sweeper = artifacts.NewSweeper(f.redis, disk, os.Getenv("VIDEO_CRASH_PREFIX")+"artifacts:")
		res, err := capability.NewRegistry().Normalize(imageRequest(t), "global")
		if err != nil {
			t.Fatal(err)
		}
		_, err = f.s.ingress(context.Background(), "video_crash", &res, time.Now().Add(-time.Minute))
		t.Fatalf("did not terminate at blob commit: %v", err)
	}
	f := setupOpenRouter(t, videotest.Script{})
	root := t.TempDir()
	cmd := exec.Command(os.Args[0], "-test.run=^TestReviewIngressCrashIsSweptAfterRestart$")
	cmd.Env = append(os.Environ(), "VIDEO_CRASH_ROOT="+root, "VIDEO_CRASH_PREFIX="+f.prefix)
	err := cmd.Run()
	var exit *exec.ExitError
	if !errors.As(err, &exit) || exit.ExitCode() != 37 {
		t.Fatalf("crash process=%v", err)
	}
	disk, err := artifacts.NewDisk(root, f.s.cfg.Artifacts.MaxBytes)
	if err != nil {
		t.Fatal(err)
	}
	key := "video/video_crash/input-0"
	if _, err = disk.Stat(context.Background(), key); err != nil {
		t.Fatal("commit not reached", err)
	}
	sweeper := artifacts.NewSweeper(f.redis, disk, f.prefix+"artifacts:")
	n, err := sweeper.Sweep(context.Background(), 32, f.s.expireArtifact)
	if err != nil || n != 1 {
		t.Fatalf("orphan not recovered after process death: swept=%d err=%v", n, err)
	}
	if _, err = disk.Stat(context.Background(), key); !errors.Is(err, artifacts.ErrNotFound) {
		t.Fatal("abandoned upload retained", err)
	}
}

type historyReads struct {
	mu    sync.Mutex
	count int
}

func (h *historyReads) DialHook(next redis.DialHook) redis.DialHook { return next }
func (h *historyReads) ProcessPipelineHook(next redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return func(ctx context.Context, cmds []redis.Cmder) error {
		for _, cmd := range cmds {
			if cmd.Name() == "hgetall" && strings.Contains(fmt.Sprint(cmd.Args()), "history-") {
				h.mu.Lock()
				h.count++
				h.mu.Unlock()
			}
		}
		return next(ctx, cmds)
	}
}
func (h *historyReads) ProcessHook(next redis.ProcessHook) redis.ProcessHook {
	return func(ctx context.Context, cmd redis.Cmder) error {
		if cmd.Name() == "hgetall" && strings.Contains(fmt.Sprint(cmd.Args()), "history-") {
			h.mu.Lock()
			h.count++
			h.mu.Unlock()
		}
		return next(ctx, cmd)
	}
}
func TestReviewWorkerDoesNotReadRetainedHistory(t *testing.T) {
	f := setup(t, videotest.Script{})
	f.s.SetTelemetry(metrics.NewRegistry(), nil, "")
	for i := 0; i < 400; i++ {
		j := &video.VideoJob{ID: fmt.Sprintf("history-%d", i), OrgID: "org-a", Status: video.StatusFailed, SettlementState: video.SettlementReleased, CreatedAt: f.clock.Now(), ExpiresAt: f.clock.Now().Add(time.Hour)}
		if err := f.s.store.Create(j); err != nil {
			t.Fatal(err)
		}
	}
	active := f.accept(t)
	h := &historyReads{}
	f.redis.Redis().AddHook(h)
	f.tick(t)
	if f.job(t, active.ID).Status != video.StatusQueued {
		t.Fatal("active work not processed")
	}
	if h.count != 0 {
		t.Fatalf("worker loaded %d historical jobs for gauges", h.count)
	}
	if err := f.s.RefreshMetrics(context.Background()); err != nil {
		t.Fatal(err)
	}
	if h.count > 128 {
		t.Fatalf("one metrics page read %d records", h.count)
	}
}

// A failed expiry registration cannot allow even the first blob commit.
func TestReviewIngressTrackingFailsClosed(t *testing.T) {
	f := setupOpenRouter(t, videotest.Script{})
	if err := f.redis.Redis().Set(context.Background(), f.prefix+"artifacts:expire", "wrong-type", 0).Err(); err != nil {
		t.Fatal(err)
	}
	disk := &countPuts{Store: f.s.blobs}
	f.s.blobs = disk
	rc := videoContext()
	defer rc.Release()
	if _, err := f.s.Accept(context.Background(), rc, imageRequest(t), video.NewID(), nil); err == nil {
		t.Fatal("tracking failure accepted")
	}
	if disk.puts != 0 {
		t.Fatalf("committed %d uploads before tracking failed", disk.puts)
	}
}

type countPuts struct {
	artifacts.Store
	puts int
}

func (s *countPuts) Put(ctx context.Context, key string, r io.Reader, m artifacts.Meta) (artifacts.Meta, error) {
	s.puts++
	return s.Store.Put(ctx, key, r, m)
}
