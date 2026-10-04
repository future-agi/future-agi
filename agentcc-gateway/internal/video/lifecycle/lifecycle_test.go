package lifecycle

import (
	"context"
	"errors"
	"fmt"
	"os"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	provider "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"github.com/futureagi/agentcc-gateway/internal/providers/video/videotest"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/video"
	"github.com/futureagi/agentcc-gateway/internal/video/artifacts"
	"github.com/futureagi/agentcc-gateway/internal/video/capability"
)

const modelID = "dreamina-seedance-2-5-260628"

type testClock struct {
	mu  sync.Mutex
	now time.Time
}

func (c *testClock) Now() time.Time          { c.mu.Lock(); defer c.mu.Unlock(); return c.now }
func (c *testClock) Advance(d time.Duration) { c.mu.Lock(); c.now = c.now.Add(d); c.mu.Unlock() }

type fixture struct {
	s      *Service
	clock  *testClock
	fake   *videotest.FakeProvider
	redis  *redisstate.Client
	budget *redisstate.BudgetStore
	prefix string
}

func setup(t *testing.T, script videotest.Script) *fixture {
	t.Helper()
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Skip("TEST_REDIS_ADDR unset")
	}
	c, e := redisstate.NewClient(redisstate.Config{Address: addr, PoolSize: 120, Timeout: time.Second})
	if e != nil {
		t.Fatal(e)
	}
	prefix := fmt.Sprintf("test:batch3:%s:%d:", t.Name(), time.Now().UnixNano())
	t.Cleanup(func() {
		keys, e := c.Redis().Keys(context.Background(), prefix+"*").Result()
		if e == nil && len(keys) > 0 {
			c.Redis().Del(context.Background(), keys...)
		}
		c.Close()
	})
	b := redisstate.NewBudgetStore(c, prefix+"budget:")
	clock := &testClock{now: time.Now().UTC().Truncate(time.Second)}
	cfg := config.DefaultConfig().Video
	cfg.Enabled = true
	cfg.Submit.SyncWait = 0
	cfg.Limits.OrgMaxActiveJobs = 100
	cfg.Providers = map[string]config.VideoProviderConfig{"byteplus": {Enabled: true, Models: []string{modelID}, Region: "ap-southeast-1", AccountRef: "test-account", TariffRevision: capability.BytePlusRevision, Limits: config.VideoProviderLimits{SubmitRPM: 1000, PollQPS: 1000, MaxActiveTasks: 100}, Deadlines: config.VideoProviderDeadlines{Submit: time.Second, Read: time.Second, Run: time.Hour, Reconcile: time.Second}, Poll: config.VideoProviderPoll{BaseInterval: time.Second}}}
	if script.Capabilities.Service == "" {
		script.Capabilities = capability.BytePlus()
	}
	if len(script.Submit.Body) == 0 && script.Submit.Status == 0 {
		script.Submit = videotest.JSONReply(provider.SubmitResult{ProviderJobID: "upstream-1", ProviderState: "queued", Normalized: provider.StateQueued})
	}
	script.Capabilities.PollMinInterval = time.Second
	f := videotest.NewFakeProvider(script)
	t.Cleanup(f.Close)
	adapters := provider.NewRegistry()
	if e = adapters.Register(f.Adapter()); e != nil {
		t.Fatal(e)
	}
	blobs, e := artifacts.NewDisk(t.TempDir(), cfg.Artifacts.MaxBytes)
	if e != nil {
		t.Fatal(e)
	}
	st := video.NewRedisStore(c, video.StoreOptions{Prefix: prefix, Budget: b, Now: clock.Now})
	s, e := New(Options{Config: cfg, Store: st, Redis: c, Budget: b, Adapters: adapters, Artifacts: blobs, Clock: clock, Prefix: prefix, Engine: pipeline.NewEngine(costplugin.New(true, nil, nil, nil))})
	if e != nil {
		t.Fatal(e)
	}
	return &fixture{s: s, clock: clock, fake: f, redis: c, budget: b, prefix: prefix}
}
func (f *fixture) accept(t *testing.T) *video.VideoJob {
	t.Helper()
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.EndpointType = "video"
	rc.Metadata["org_id"] = "org-a"
	rc.Metadata["auth_key_id"] = "key-a"
	rc.Metadata["key_type"] = "byok"
	r, e := f.s.Accept(context.Background(), rc, provider.Request{Model: "byteplus/" + modelID, Prompt: "private prompt sentinel", DurationSeconds: 5}, video.NewID(), []video.BudgetReservation{{Level: "org", Period: "total", Limit: 1000}})
	if e != nil {
		t.Fatal(e)
	}
	return r.Job
}
func (f *fixture) job(t *testing.T, id string) *video.VideoJob {
	t.Helper()
	j, e := f.s.store.GetAccounting(context.Background(), id)
	if e != nil {
		t.Fatal(e)
	}
	return j
}
func (f *fixture) tick(t *testing.T) {
	t.Helper()
	if e := f.s.NewWorker().Tick(context.Background()); e != nil {
		t.Fatal(e)
	}
}
func completed() provider.Observation {
	return provider.Observation{Normalized: provider.StateCompleted, ProviderState: "succeeded", Outputs: []provider.OutputRef{{ContentType: "video/mp4"}}, Usage: &provider.Usage{Lines: []provider.UsageLine{{Unit: provider.VideoTokens, Quantity: 1000, Source: provider.UsageProviderReported}}}}
}
func TestSubmit_QueuedOnlyAfterReceipt(t *testing.T) {
	f := setup(t, videotest.Script{})
	j := f.accept(t)
	if j.Status != video.StatusSubmitting || j.ProviderJobID != "" {
		t.Fatalf("before receipt: %+v", j)
	}
	f.tick(t)
	j = f.job(t, j.ID)
	if j.Status != video.StatusQueued || j.ProviderJobID == "" || j.Phase != video.PhaseReceived {
		t.Fatalf("after receipt: %+v", j)
	}
}
func TestComplete_CopiesAndValidates(t *testing.T) {
	f := setup(t, videotest.Script{Poll: []videotest.Reply{videotest.JSONReply(completed())}, Fetch: videotest.Reply{Body: videotest.MP4()}})
	j := f.accept(t)
	f.tick(t)
	f.clock.Advance(2 * time.Second)
	f.tick(t)
	j = f.job(t, j.ID)
	if j.Status != video.StatusCompleted || len(j.Artifacts) != 1 || j.Artifacts[0].State != video.ArtifactAvailable || j.SettlementState != video.SettlementSettled {
		t.Fatalf("completion: %+v", j)
	}
	total, _, ok := f.budget.GetSpend(j.OrgID, "org", "", "total")
	if !ok || total != float64(j.SettledMicros)/1e6 {
		t.Fatalf("budget=%f actual=%d", total, j.SettledMicros)
	}
	f.clock.Advance(time.Hour)
	f.tick(t)
	total2, _, _ := f.budget.GetSpend(j.OrgID, "org", "", "total")
	if total2 != total {
		t.Fatal("duplicate settlement")
	}
}
func TestCrash_AfterReceiptBeforeSave(t *testing.T) {
	caps := capability.BytePlus()
	f := setup(t, videotest.Script{Capabilities: caps, List: videotest.JSONReply(provider.ReconcileResult{Found: true, ProviderJobID: "upstream-1"})})
	j := f.accept(t)
	f.s.hooks = HookFunc(func(point string) error {
		if point == "submit.received" {
			return ErrCrash
		}
		return nil
	})
	if e := f.s.NewWorker().Tick(context.Background()); !errors.Is(e, ErrCrash) {
		t.Fatalf("crash=%v", e)
	}
	j = f.job(t, j.ID)
	if j.Phase != video.PhaseCalling {
		t.Fatal(j.Phase)
	}
	// Redis lease expiry is wall time; deleting precisely this test lease models expiry.
	f.redis.Redis().Del(context.Background(), f.prefix+"lease:"+j.ID)
	f.s.hooks = nil
	f.tick(t)
	f.clock.Advance(2 * time.Second)
	f.tick(t)
	j = f.job(t, j.ID)
	if j.ProviderJobID != "upstream-1" || f.fake.SubmitCount(j.ID) != 1 {
		t.Fatalf("recovery status=%s submits=%d", j.Status, f.fake.SubmitCount(j.ID))
	}
}
func TestWorkerUnavailable_SubmitDeadline(t *testing.T) {
	f := setup(t, videotest.Script{})
	j := f.accept(t)
	f.clock.Advance(time.Minute)
	f.tick(t)
	j = f.job(t, j.ID)
	if j.Status != video.StatusFailed || j.Error.Code != "submit_deadline_exceeded" || j.SettlementState != video.SettlementReleased || f.fake.SubmitCount(j.ID) != 0 {
		t.Fatalf("deadline: %+v", j)
	}
}

func TestCrash_SettlementRecordBeforeSave(t *testing.T) {
	f := setup(t, videotest.Script{Poll: []videotest.Reply{videotest.JSONReply(completed())}, Fetch: videotest.Reply{Body: videotest.MP4()}})
	j := f.accept(t)
	f.tick(t)
	f.clock.Advance(6 * time.Second)
	f.s.hooks = HookFunc(func(point string) error {
		if point == "settle.recorded" {
			return ErrCrash
		}
		return nil
	})
	if e := f.s.NewWorker().Tick(context.Background()); !errors.Is(e, ErrCrash) {
		t.Fatal(e)
	}
	before, _, _ := f.budget.GetSpend(j.OrgID, "org", "", "total")
	f.redis.Redis().Del(context.Background(), f.prefix+"lease:"+j.ID)
	f.s.hooks = nil
	f.tick(t)
	after, _, _ := f.budget.GetSpend(j.OrgID, "org", "", "total")
	j = f.job(t, j.ID)
	if after != before || j.SettlementState != video.SettlementSettled {
		t.Fatalf("before=%f after=%f state=%s", before, after, j.SettlementState)
	}
}
func TestCrash_WindowsNeverBlindResubmit(t *testing.T) {
	for _, point := range []string{"submit.prepared", "submit.call", "submit.sent", "submit.saved"} {
		t.Run(point, func(t *testing.T) {
			f := setup(t, videotest.Script{List: videotest.JSONReply(provider.ReconcileResult{Found: true, ProviderJobID: "upstream-1"})})
			j := f.accept(t)
			f.s.hooks = HookFunc(func(p string) error {
				if p == point {
					return ErrCrash
				}
				return nil
			})
			if e := f.s.NewWorker().Tick(context.Background()); !errors.Is(e, ErrCrash) {
				t.Fatal(e)
			}
			f.redis.Redis().Del(context.Background(), f.prefix+"lease:"+j.ID)
			f.s.hooks = nil
			f.tick(t)
			f.clock.Advance(6 * time.Second)
			f.tick(t)
			want := 1
			if point == "submit.call" {
				want = 0
			}
			if n := f.fake.SubmitCount(j.ID); n != want {
				t.Fatalf("submits=%d want %d", n, want)
			}
		})
	}
}
func TestRunDeadline_TimeoutPersistsAndLateSuccess(t *testing.T) {
	f := setup(t, videotest.Script{Poll: []videotest.Reply{videotest.JSONReply(completed())}})
	j := f.accept(t)
	f.tick(t)
	j = f.job(t, j.ID)
	deadline := j.RunBy
	f.clock.Advance(2 * time.Hour)
	f.tick(t)
	j = f.job(t, j.ID)
	if j.Status != video.StatusFailed || j.Error.Code != "provider_timeout" || !j.UpstreamMayContinue || !j.RunBy.Equal(deadline) {
		t.Fatalf("timeout=%s %v", j.Status, j.Error)
	}
	f.clock.Advance(6 * time.Minute)
	f.tick(t)
	j = f.job(t, j.ID)
	if j.Status != video.StatusFailed || j.LateSuccessAt == nil || len(j.Artifacts) != 0 || j.SettlementState != video.SettlementSettled || !j.RunBy.Equal(deadline) {
		t.Fatalf("late success=%s late=%v settlement=%s", j.Status, j.LateSuccessAt, j.SettlementState)
	}
}
func TestCopy_FailureKeepsCompletedAndBillable(t *testing.T) {
	for _, fetch := range []videotest.Reply{{Status: 403}, {Body: []byte("not a video")}} {
		t.Run(fmt.Sprint(fetch.Status), func(t *testing.T) {
			f := setup(t, videotest.Script{Poll: []videotest.Reply{videotest.JSONReply(completed())}, Fetch: fetch})
			j := f.accept(t)
			f.tick(t)
			for i := 0; i < 4; i++ {
				f.clock.Advance(10 * time.Second)
				f.tick(t)
			}
			j = f.job(t, j.ID)
			if j.Status != video.StatusCompleted || j.Artifacts[0].State != video.ArtifactUnavailable || j.SettlementState != video.SettlementSettled || j.CopyAttempts != 3 || j.DownloadAttempts != 3 || f.fake.SubmitCount(j.ID) != 1 {
				t.Fatalf("status=%s settlement=%s attempts=%d/%d", j.Status, j.SettlementState, j.CopyAttempts, j.DownloadAttempts)
			}
		})
	}
}
func TestBudget_UnknownBillabilityStaysUnsettled(t *testing.T) {
	f := setup(t, videotest.Script{Poll: []videotest.Reply{videotest.JSONReply(provider.Observation{Normalized: provider.StateFailed, ProviderState: "failed"})}})
	j := f.accept(t)
	f.tick(t)
	f.clock.Advance(6 * time.Second)
	f.tick(t)
	j = f.job(t, j.ID)
	if j.SettlementState != video.SettlementUnsettled || j.Status != video.StatusFailed {
		t.Fatalf("status=%s settlement=%s", j.Status, j.SettlementState)
	}
	total, _, _ := f.budget.GetSpend(j.OrgID, "org", "", "total")
	if total != float64(j.ReservedMicros)/1e6 {
		t.Fatal(total)
	}
}
func TestSubmit_401IsProviderAccessDenied(t *testing.T) {
	f := setup(t, videotest.Script{Submit: videotest.Reply{Status: 401}})
	j := f.accept(t)
	f.tick(t)
	j = f.job(t, j.ID)
	if j.Error == nil || j.Error.Code != "provider_access_denied" || j.UpstreamMayContinue || j.SettlementState != video.SettlementReleased {
		t.Fatalf("status=%s err=%v", j.Status, j.Error)
	}
}
func TestPoll_ErrorsBackoff(t *testing.T) {
	for _, tc := range []struct {
		name     string
		reply    videotest.Reply
		failures int
	}{{"429", videotest.Reply{Status: 429, Header: map[string][]string{"Retry-After": {"40"}}}, 0}, {"5xx", videotest.Reply{Status: 503}, 1}, {"schema", videotest.Reply{Body: []byte("broken")}, 1}} {
		t.Run(tc.name, func(t *testing.T) {
			f := setup(t, videotest.Script{Poll: []videotest.Reply{tc.reply}})
			j := f.accept(t)
			f.tick(t)
			f.clock.Advance(6 * time.Second)
			f.tick(t)
			j = f.job(t, j.ID)
			if j.PollFailures != tc.failures || !j.NextPollAt.After(f.clock.Now()) || j.Status != video.StatusQueued || f.fake.SubmitCount(j.ID) != 1 {
				t.Fatalf("poll failures=%d next=%s status=%s", j.PollFailures, j.NextPollAt, j.Status)
			}
			if tc.name == "429" && j.NextPollAt.Sub(f.clock.Now()) < 40*time.Second {
				t.Fatal("Retry-After not honored")
			}
		})
	}
}
func TestPoll_UnknownStatusThreshold(t *testing.T) {
	f := setup(t, videotest.Script{Poll: []videotest.Reply{videotest.JSONReply(provider.Observation{Normalized: "unknown"})}})
	j := f.accept(t)
	f.tick(t)
	for i := 0; i < 3; i++ {
		f.clock.Advance(time.Minute)
		f.tick(t)
	}
	j = f.job(t, j.ID)
	if j.Status != video.StatusFailed || j.Error.Code != "adapter_schema_error" {
		t.Fatalf("%s %v", j.Status, j.Error)
	}
}
func TestReconcile_HeuristicNeverProvesAbsence(t *testing.T) {
	f := setup(t, videotest.Script{Submit: videotest.Reply{Status: 500}, List: videotest.JSONReply(provider.ReconcileResult{ProvenAbsent: true})})
	f.s.cfg.Reconcile.AllowAbsentResubmit = true
	j := f.accept(t)
	f.tick(t)
	f.clock.Advance(10 * time.Second)
	f.tick(t)
	if f.fake.SubmitCount(j.ID) != 1 {
		t.Fatal("heuristic resubmitted")
	}
	f.clock.Advance(time.Hour)
	f.tick(t)
	j = f.job(t, j.ID)
	if j.Status != video.StatusFailed || j.Error.Code != "submission_unresolved" || j.RetrySafe || j.SettlementState != video.SettlementUnsettled {
		t.Fatalf("%s %v", j.Status, j.Error)
	}
}
func TestCancel_PreSubmitExcludesRacingSubmit(t *testing.T) {
	f := setup(t, videotest.Script{})
	j := f.accept(t)
	var wg sync.WaitGroup
	wg.Add(2)
	go func() {
		defer wg.Done()
		_, e := f.s.Cancel(context.Background(), j.ID, j.OrgID)
		if e != nil && !errors.Is(e, video.ErrLeaseHeld) {
			t.Error(e)
		}
	}()
	go func() {
		defer wg.Done()
		if e := f.s.NewWorker().Tick(context.Background()); e != nil {
			t.Error(e)
		}
	}()
	wg.Wait()
	j = f.job(t, j.ID)
	if j.CancelScope == "pre_submit" && f.fake.SubmitCount(j.ID) != 0 {
		t.Fatal("cancelled prepared job submitted")
	}
	if f.fake.SubmitCount(j.ID) > 1 {
		t.Fatal("duplicate submit")
	}
}
func TestCancel_ReceiptTimeoutAndCompletionRace(t *testing.T) {
	for _, tc := range []struct {
		name  string
		reply videotest.Reply
	}{{"receipt", videotest.JSONReply(provider.CancelResult{State: provider.CancelRequested})}, {"timeout", videotest.Reply{Delay: 2 * time.Second}}} {
		t.Run(tc.name, func(t *testing.T) {
			f := setup(t, videotest.Script{Cancel: tc.reply, Poll: []videotest.Reply{videotest.JSONReply(completed())}, Fetch: videotest.Reply{Body: videotest.MP4()}})
			j := f.accept(t)
			f.tick(t)
			j, e := f.s.Cancel(context.Background(), j.ID, j.OrgID)
			if e != nil {
				t.Fatal(e)
			}
			if j.Status == video.StatusCancelled || j.CancelState != "requested" || j.SettlementState != video.SettlementReserved {
				t.Fatalf("%s %s", j.Status, j.CancelState)
			}
			f.clock.Advance(6 * time.Second)
			f.tick(t)
			j = f.job(t, j.ID)
			if j.Status != video.StatusCompleted || j.Artifacts[0].State != video.ArtifactAvailable {
				t.Fatal(j.Status)
			}
		})
	}
}
func TestCancel_Capabilities(t *testing.T) {
	for _, tc := range []struct {
		kind  provider.CancelKind
		state provider.CancelState
		want  string
	}{{provider.CancelNone, provider.CancelNotAvailable, "cancel_unsupported"}, {provider.CancelQueuedOnly, provider.CancelNotAvailable, "cancel_not_available"}, {provider.CancelQueuedOnly, provider.CancelConfirmed, ""}} {
		t.Run(string(tc.kind)+string(tc.state), func(t *testing.T) {
			caps := capability.BytePlus()
			caps.CancelSupport = tc.kind
			f := setup(t, videotest.Script{Capabilities: caps, Cancel: videotest.JSONReply(provider.CancelResult{State: tc.state, ReleasesCharge: true})})
			j := f.accept(t)
			f.tick(t)
			got, e := f.s.Cancel(context.Background(), j.ID, j.OrgID)
			if tc.want != "" {
				if e == nil || APIError(e).Code != tc.want {
					t.Fatal(e)
				}
				return
			}
			if e != nil || got.Status != video.StatusCancelled || got.SettlementState != video.SettlementReleased {
				t.Fatalf("%v %v", got, e)
			}
		})
	}
}
func TestDelete_LateSuccessCannotResurrect(t *testing.T) {
	f := setup(t, videotest.Script{Poll: []videotest.Reply{videotest.JSONReply(completed())}})
	j := f.accept(t)
	f.tick(t)
	if _, e := f.s.Delete(context.Background(), j.ID, j.OrgID); e != nil {
		t.Fatal(e)
	}
	f.clock.Advance(6 * time.Second)
	f.tick(t)
	j = f.job(t, j.ID)
	if j.DeletedAt == nil || len(j.Artifacts) != 0 || j.RequestCanonical != nil || j.SettlementState != video.SettlementSettled {
		t.Fatalf("deleted=%v artifacts=%d settled=%s", j.DeletedAt, len(j.Artifacts), j.SettlementState)
	}
}
func TestKillSwitch_BlocksNewSpendOnly(t *testing.T) {
	f := setup(t, videotest.Script{Poll: []videotest.Reply{videotest.JSONReply(completed())}, Fetch: videotest.Reply{Body: videotest.MP4()}})
	j := f.accept(t)
	f.tick(t)
	if e := f.s.SetKillSwitch(context.Background(), true); e != nil {
		t.Fatal(e)
	}
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Metadata["org_id"] = "org-a"
	_, e := f.s.Accept(context.Background(), rc, provider.Request{Model: "byteplus/" + modelID, Prompt: "x"}, "new", nil)
	if e == nil || APIError(e).Code != "video_submissions_disabled" {
		t.Fatal(e)
	}
	f.clock.Advance(6 * time.Second)
	f.tick(t)
	j = f.job(t, j.ID)
	if j.Status != video.StatusCompleted {
		t.Fatal(j.Status)
	}
}
func TestRestart_ReplicaTakeoverDuringPoll(t *testing.T) {
	f := setup(t, videotest.Script{Poll: []videotest.Reply{videotest.JSONReply(completed())}, Fetch: videotest.Reply{Body: videotest.MP4()}})
	j := f.accept(t)
	f.tick(t)
	j = f.job(t, j.ID)
	deadline := j.RunBy
	f.clock.Advance(6 * time.Second)
	f.s.hooks = HookFunc(func(p string) error {
		if p == "poll.received" {
			return ErrCrash
		}
		return nil
	})
	if e := f.s.NewWorker().Tick(context.Background()); !errors.Is(e, ErrCrash) {
		t.Fatal(e)
	}
	f.redis.Redis().PExpire(context.Background(), f.prefix+"lease:"+j.ID, time.Millisecond)
	time.Sleep(5 * time.Millisecond)
	f.s.hooks = nil
	f.tick(t)
	j = f.job(t, j.ID)
	if j.Status != video.StatusCompleted || !deadline.Equal(j.RunBy) || f.fake.SubmitCount(j.ID) != 1 {
		t.Fatal(j.Status)
	}
}
func TestBudget_ConcurrentReservationsCannotOverspend(t *testing.T) {
	f := setup(t, videotest.Script{})
	var wg sync.WaitGroup
	accepted := make(chan string, 50)
	for i := 0; i < 50; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.Metadata["org_id"] = "org-a"
			r, e := f.s.Accept(context.Background(), rc, provider.Request{Model: "byteplus/" + modelID, Prompt: "x"}, video.NewID(), []video.BudgetReservation{{Level: "org", Period: "total", Limit: 10}})
			if e == nil {
				accepted <- r.Job.ID
			} else if !errors.Is(e, video.ErrBudgetExceeded) {
				t.Error(e)
			}
		}()
	}
	wg.Wait()
	close(accepted)
	count := len(accepted)
	total, _, ok := f.budget.GetSpend("org-a", "org", "", "total")
	if !ok || count != 8 || total > 10 || total != float64(count)*1.1556 {
		t.Fatalf("accepted=%d spend=%f", count, total)
	}
}

func TestTransitions_DuplicateSuccessAndLateRunning(t *testing.T) {
	f := setup(t, videotest.Script{Poll: []videotest.Reply{videotest.JSONReply(completed())}, Fetch: videotest.Reply{Body: videotest.MP4()}})
	j := f.accept(t)
	f.tick(t)
	f.clock.Advance(6 * time.Second)
	f.tick(t)
	before, _, _ := f.budget.GetSpend(j.OrgID, "org", "", "total")
	for _, obs := range []provider.Observation{completed(), {Normalized: provider.StateRunning, ProviderState: "running"}} {
		l, e := f.s.store.Lease(context.Background(), j.ID, video.NewID(), time.Minute)
		if e != nil {
			t.Fatal(e)
		}
		j = f.job(t, j.ID)
		e = f.s.observe(context.Background(), j, l, obs)
		f.s.store.Release(context.Background(), l)
		if e != nil {
			t.Fatal(e)
		}
	}
	j = f.job(t, j.ID)
	after, _, _ := f.budget.GetSpend(j.OrgID, "org", "", "total")
	if before != after || j.Status != video.StatusCompleted || j.Artifacts[0].State != video.ArtifactAvailable {
		t.Fatal(j.Status, before, after)
	}
}
func TestRetention_LocalExpiry410(t *testing.T) {
	f := setup(t, videotest.Script{Poll: []videotest.Reply{videotest.JSONReply(completed())}, Fetch: videotest.Reply{Body: videotest.MP4()}})
	j := f.accept(t)
	f.tick(t)
	f.clock.Advance(6 * time.Second)
	f.tick(t)
	f.clock.Advance(25 * time.Hour)
	_, e := f.s.Content(context.Background(), j.ID, j.OrgID, 0, "")
	if e == nil || APIError(e).Code != "output_expired" {
		t.Fatal(e)
	}
}
func TestAccountRateAndSemaphoreSharedAcrossReplicas(t *testing.T) {
	f := setup(t, videotest.Script{})
	j := f.accept(t)
	p := f.s.cfg.Providers["byteplus"]
	p.Limits.SubmitRPM = 1
	f.s.cfg.Providers["byteplus"] = p
	other, e := New(Options{Config: f.s.cfg, Store: f.s.store, Redis: f.redis, Budget: f.budget, Adapters: f.s.adapters, Artifacts: f.s.blobs, Clock: f.clock, Prefix: f.prefix})
	if e != nil {
		t.Fatal(e)
	}
	if ok, _, e := f.s.rate(context.Background(), j, "submit"); e != nil || !ok {
		t.Fatal(ok, e)
	}
	if ok, _, e := other.rate(context.Background(), j, "submit"); e != nil || ok {
		t.Fatal(ok, e)
	}
	f.s.cfg.Poll.PerProviderMaxConcurrent = 1
	other.cfg.Poll.PerProviderMaxConcurrent = 1
	e = f.s.withPermit(context.Background(), j, "poll", func(ctx context.Context) error {
		return other.withPermit(ctx, j, "poll", func(context.Context) error { t.Error("second replica exceeded semaphore"); return nil })
	})
	if !errors.Is(e, errLimited) {
		t.Fatal(e)
	}
}
func TestActiveLimitsAreAtomic(t *testing.T) {
	f := setup(t, videotest.Script{})
	f.s.cfg.Limits.OrgMaxActiveJobs = 1
	var wg sync.WaitGroup
	accepted := make(chan string, 20)
	for i := 0; i < 20; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.Metadata["org_id"] = "org-a"
			r, e := f.s.Accept(context.Background(), rc, provider.Request{Model: "byteplus/" + modelID, Prompt: "x"}, video.NewID(), []video.BudgetReservation{{Level: "org", Period: "total", Limit: 1000}})
			if e == nil {
				accepted <- r.Job.ID
			} else if !errors.Is(e, video.ErrActiveLimit) {
				t.Error(e)
			}
		}()
	}
	wg.Wait()
	close(accepted)
	if len(accepted) != 1 {
		t.Fatal(len(accepted))
	}
	total, _, _ := f.budget.GetSpend("org-a", "org", "", "total")
	if total != 1.1556 {
		t.Fatal(total)
	}
	for id := range accepted {
		if _, e := f.s.Cancel(context.Background(), id, "org-a"); e != nil {
			t.Fatal(e)
		}
	}
	f.accept(t)
}
func TestGetExpiresPreparedWithoutWorker(t *testing.T) {
	f := setup(t, videotest.Script{})
	j := f.accept(t)
	f.clock.Advance(time.Minute)
	got, e := f.s.Get(context.Background(), j.ID, j.OrgID)
	if e != nil || got.Status != video.StatusFailed || got.SettlementState != video.SettlementReleased {
		t.Fatal(got, e)
	}
	if f.fake.SubmitCount(j.ID) != 0 {
		t.Fatal("status submitted upstream")
	}
}

func TestAttachTerminalFailureSettlesOnly(t *testing.T) {
	f := setup(t, videotest.Script{Submit: videotest.Reply{Status: 500}, Poll: []videotest.Reply{videotest.JSONReply(completed())}, Fetch: videotest.Reply{Body: videotest.MP4()}})
	j := f.accept(t)
	f.tick(t)
	f.clock.Advance(time.Hour)
	f.tick(t)
	j = f.job(t, j.ID)
	if j.Status != video.StatusFailed {
		t.Fatal(j.Status)
	}
	attached, e := f.s.Attach(context.Background(), j.ID, "cgt-attached")
	if e != nil || attached.Status != video.StatusFailed {
		t.Fatal(attached, e)
	}
	f.tick(t)
	j = f.job(t, j.ID)
	if j.Status != video.StatusFailed || j.LateSuccessAt == nil || j.SettlementState != video.SettlementSettled || len(j.Artifacts) != 0 || j.DownloadAttempts != 0 {
		t.Fatalf("status=%s late=%v settlement=%s artifacts=%d", j.Status, j.LateSuccessAt, j.SettlementState, len(j.Artifacts))
	}
}
func TestKillSwitchStartupPreservesRuntimeValue(t *testing.T) {
	f := setup(t, videotest.Script{})
	ctx := context.Background()
	if e := f.s.SetKillSwitch(ctx, true); e != nil {
		t.Fatal(e)
	}
	if e := f.s.InitializeKillSwitch(ctx); e != nil {
		t.Fatal(e)
	}
	if on, e := f.s.Killed(ctx); e != nil || !on {
		t.Fatal("replica restart cleared runtime kill switch", on, e)
	}
}

func TestRestartNeverChangesPinnedProviderAccount(t *testing.T) {
	f := setup(t, videotest.Script{Poll: []videotest.Reply{videotest.JSONReply(completed())}, Fetch: videotest.Reply{Body: videotest.MP4()}})
	j := f.accept(t)
	f.tick(t)
	p := f.s.cfg.Providers["byteplus"]
	p.AccountRef = "different-account"
	f.s.cfg.Providers["byteplus"] = p
	f.clock.Advance(6 * time.Second)
	f.tick(t)
	j = f.job(t, j.ID)
	if j.Status != video.StatusQueued || j.AccountRef != "test-account" || j.Observation != nil {
		t.Fatal("job polled against a different provider account", j.Status)
	}
}

func TestCopyMissingAccountStillHonorsDeadline(t *testing.T) {
	f := setup(t, videotest.Script{})
	j := f.accept(t)
	f.tick(t)
	j = f.job(t, j.ID)
	l, err := f.s.store.Lease(context.Background(), j.ID, "test", time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	o := completed()
	j.Observation = &o
	f.s.priceObservation(j, o)
	j.CopyBy = f.clock.Now().Add(-time.Second)
	if err = f.s.store.Save(context.Background(), j, l); err != nil {
		t.Fatal(err)
	}
	p := f.s.cfg.Providers["byteplus"]
	p.AccountRef = "different-account"
	f.s.cfg.Providers["byteplus"] = p
	if err = f.s.copyResult(context.Background(), j, l); err != nil {
		t.Fatal(err)
	}
	j = f.job(t, j.ID)
	if j.Status != video.StatusCompleted || len(j.Artifacts) != 1 || j.Artifacts[0].State != video.ArtifactUnavailable || j.SettlementState != video.SettlementSettled {
		t.Fatalf("status=%s artifacts=%v settlement=%s", j.Status, j.Artifacts, j.SettlementState)
	}
}
