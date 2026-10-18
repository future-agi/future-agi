package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"math"
	"net"
	"os"
	"runtime"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/metrics"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	provider "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"github.com/futureagi/agentcc-gateway/internal/providers/video/videotest"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/video"
	"github.com/futureagi/agentcc-gateway/internal/video/artifacts"
	"github.com/futureagi/agentcc-gateway/internal/video/capability"
	"github.com/futureagi/agentcc-gateway/internal/video/lifecycle"
	"github.com/redis/go-redis/v9"
)

const (
	loadVideoModel          = "dreamina-seedance-2-5-260628"
	copyCount               = 8
	copyBytes         int64 = 200_000_000 // Decimal MB; no payload-sized allocations.
	maxCopyHeapGrowth       = 64 << 20
)

// redisCommands counts commands sent by this client, including pipeline members
// and EVALSHA misses/retries, but excluding commands executed inside Lua scripts.
// It is independent of other clients and never resets server-wide statistics.
type redisCommands struct{ n atomic.Int64 }

func (h *redisCommands) DialHook(next redis.DialHook) redis.DialHook { return next }
func (h *redisCommands) ProcessHook(next redis.ProcessHook) redis.ProcessHook {
	return func(ctx context.Context, cmd redis.Cmder) error { h.n.Add(1); return next(ctx, cmd) }
}
func (h *redisCommands) ProcessPipelineHook(next redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return func(ctx context.Context, cmds []redis.Cmder) error { h.n.Add(int64(len(cmds))); return next(ctx, cmds) }
}

// loadAdapter keeps the existing fake's HTTP submit/poll and duplicate detector.
// Only the large-copy phase replaces Fetch with a paced synthetic stream through
// the actual lifecycle verifier, disk store, checksums, fsync and settlement.
type loadAdapter struct {
	provider.Adapter
	large   atomic.Bool
	active  atomic.Int64
	peak    atomic.Int64
	bytes   atomic.Int64
	started atomic.Int64
	ready   chan struct{}
}

func (a *loadAdapter) Submit(ctx context.Context, j *provider.JobView, c provider.Correlation) (provider.SubmitResult, error) {
	r, err := a.Adapter.Submit(ctx, j, c)
	if err == nil {
		r.ProviderJobID = "fake-" + j.ID
	}
	return r, err
}
func (a *loadAdapter) Fetch(ctx context.Context, ref provider.ProviderRef, index int) (io.ReadCloser, provider.FetchMeta, error) {
	if !a.large.Load() {
		return a.Adapter.Fetch(ctx, ref, index)
	}
	n := a.active.Add(1)
	for old := a.peak.Load(); n > old && !a.peak.CompareAndSwap(old, n); old = a.peak.Load() {
	}
	if a.started.Add(1) == copyCount {
		close(a.ready)
	}
	select {
	case <-ctx.Done():
		a.active.Add(-1)
		return nil, provider.FetchMeta{}, ctx.Err()
	case <-a.ready:
	}
	return &syntheticVideo{ctx: ctx, owner: a, header: syntheticHeader(), left: copyBytes}, provider.FetchMeta{ContentType: "video/mp4", Bytes: copyBytes}, nil
}

func syntheticHeader() []byte {
	// An open-ended mdat atom makes the zero-filled remainder a valid container.
	return append(videotest.MP4(), []byte("\x00\x00\x00\x00mdat")...)
}

type syntheticVideo struct {
	ctx    context.Context
	owner  *loadAdapter
	header []byte
	left   int64
	closed bool
}

func (r *syntheticVideo) Read(p []byte) (int, error) {
	if len(p) == 0 {
		return 0, nil
	}
	if err := r.ctx.Err(); err != nil {
		return 0, err
	}
	if r.left == 0 {
		return 0, io.EOF
	}
	// Keep the measurement long enough for periodic samples, with a bounded
	// 32 KiB chunk even if a downstream implementation requests a larger one.
	n := int(min(int64(len(p)), r.left, 32<<10))
	clear(p[:n])
	if len(r.header) > 0 {
		used := copy(p[:n], r.header)
		r.header = r.header[used:]
	}
	r.left -= int64(n)
	r.owner.bytes.Add(int64(n))
	timer := time.NewTimer(time.Millisecond)
	defer timer.Stop()
	select {
	case <-r.ctx.Done():
		return 0, r.ctx.Err()
	case <-timer.C:
		return n, nil
	}
}
func (r *syntheticVideo) Close() error {
	if !r.closed {
		r.closed = true
		r.owner.active.Add(-1)
	}
	return nil
}

type videoLoad struct {
	service     *lifecycle.Service
	store       *video.RedisStore
	budget      *redisstate.BudgetStore
	client      *redisstate.Client
	counter     *redisCommands
	fake        *videotest.FakeProvider
	adapter     *loadAdapter
	prefix      string
	concurrency int
}

type videoBatch struct {
	ids                    []string
	submit, replay, status []float64
	commands               int64
	duration               time.Duration
}

func runVideoLoad(jobs, concurrency int, timeout time.Duration) error {
	if jobs < 1 || concurrency < 1 || timeout <= 0 {
		return errors.New("positive video-jobs, c and video-timeout required")
	}
	addr := os.Getenv("TEST_REDIS_ADDR")
	host, _, err := net.SplitHostPort(addr)
	if err != nil || net.ParseIP(host) == nil || !net.ParseIP(host).IsLoopback() {
		return errors.New("TEST_REDIS_ADDR must be an explicit loopback IP:port")
	}
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	client, err := redisstate.NewClient(redisstate.Config{Address: addr, PoolSize: max(64, concurrency*2), Timeout: 5 * time.Second})
	if err != nil {
		return err
	}
	defer client.Close()
	prefix := fmt.Sprintf("loadtest:video:%d:%s:", time.Now().UnixNano(), video.NewID())
	defer func() {
		cleanup, stop := context.WithTimeout(context.Background(), 30*time.Second)
		defer stop()
		if err := cleanVideoKeys(cleanup, client.Redis(), prefix); err != nil {
			fmt.Fprintf(os.Stderr, "Cleanup failed for %s: %v\n", prefix, err)
		}
	}()
	// All generated artifacts stay under the current directory, never /tmp.
	dir, err := os.MkdirTemp(".", "loadtest-video-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(dir)
	cfg := config.DefaultConfig().Video
	cfg.Enabled = true
	cfg.Submit.SyncWait = 0
	cfg.Submit.Deadline = timeout
	cfg.Poll.MaxConcurrentTotal = 32
	cfg.Poll.PerProviderMaxConcurrent = 32
	cfg.Copy.MaxConcurrent = copyCount
	cfg.Limits.OrgMaxActiveJobs = jobs + copyCount
	cfg.Limits.OrgSubmitRPM = 4 * (jobs + copyCount)
	cfg.Providers = map[string]config.VideoProviderConfig{"byteplus": {
		Enabled: true, Models: []string{loadVideoModel}, Region: "ap-southeast-1", AccountRef: "local-fake",
		TariffRevision: capability.BytePlusRevision,
		Limits:         config.VideoProviderLimits{SubmitRPM: 4 * (jobs + copyCount), PollQPS: 4 * (jobs + copyCount), MaxActiveTasks: jobs + copyCount},
		Deadlines:      config.VideoProviderDeadlines{Submit: 30 * time.Second, Read: 30 * time.Second, Run: time.Hour, Reconcile: time.Minute},
		Poll:           config.VideoProviderPoll{BaseInterval: time.Second},
	}}
	caps := capability.BytePlus()
	caps.PollMinInterval = time.Second // Synthetic workload, not BytePlus's live polling floor.
	fake := videotest.NewFakeProvider(videotest.Script{
		Capabilities: caps,
		Submit:       videotest.JSONReply(provider.SubmitResult{Normalized: provider.StateQueued, ProviderState: "queued", ProviderJobID: "fake"}),
		Poll: []videotest.Reply{videotest.JSONReply(provider.Observation{Normalized: provider.StateCompleted, ProviderState: "succeeded",
			Outputs: []provider.OutputRef{{ContentType: "video/mp4"}},
			Usage:   &provider.Usage{Lines: []provider.UsageLine{{Unit: provider.VideoTokens, Quantity: 1000, Source: provider.UsageProviderReported}}},
		})},
		Fetch: videotest.Reply{Body: videotest.MP4()},
	})
	defer fake.Close()
	adapter := &loadAdapter{Adapter: fake.Adapter(), ready: make(chan struct{})}
	registry := provider.NewRegistry()
	if err := registry.Register(adapter); err != nil {
		return err
	}
	blobs, err := artifacts.NewDisk(dir, cfg.Artifacts.MaxBytes)
	if err != nil {
		return err
	}
	defer blobs.Close()
	budget := redisstate.NewBudgetStore(client, prefix+"budget:")
	store := video.NewRedisStore(client, video.StoreOptions{Prefix: prefix, Budget: budget})
	service, err := lifecycle.New(lifecycle.Options{Config: cfg, Store: store, Redis: client, Budget: budget,
		Adapters: registry, Artifacts: blobs, Prefix: prefix, Engine: pipeline.NewEngine(costplugin.New(true, nil, nil, nil))})
	if err != nil {
		return err
	}
	service.SetTelemetry(metrics.NewRegistry(), nil, "video-loadtest") // Includes shared-ledger gauge scans on every tick.
	if err := service.InitializeKillSwitch(ctx); err != nil {
		return err
	}
	counter := &redisCommands{}
	client.Redis().AddHook(counter)
	load := &videoLoad{service: service, store: store, budget: budget, client: client, counter: counter, fake: fake, adapter: adapter, prefix: prefix, concurrency: concurrency}
	oldLogger := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelWarn})))
	defer slog.SetDefault(oldLogger)
	fmt.Printf("Video fake-provider load: jobs=%d clients=%d workers=2 redis=%s runtime=%s %s/%s cpus=%d\n", jobs, concurrency, addr, runtime.Version(), runtime.GOOS, runtime.GOARCH, runtime.NumCPU())
	batch, err := load.batch(ctx, "small", jobs)
	if err != nil {
		return err
	}
	if err := load.reportBatch(ctx, batch); err != nil {
		return err
	}
	if err := load.reportStorage(ctx, jobs); err != nil {
		return err
	}
	fmt.Println("Starting eight concurrent synthetic 200 MB lifecycle copies")
	adapter.large.Store(true)
	runtime.GC()
	memory := startCopyMemory(adapter)
	large, runErr := load.batch(ctx, "large", copyCount)
	report := memory.stop()
	if runErr != nil {
		return runErr
	}
	if err := load.checkJobs(ctx, large.ids, copyBytes); err != nil {
		return err
	}
	if _, duplicates, err := load.submitCounts(large.ids); err != nil || duplicates != 0 {
		return fmt.Errorf("large-copy submit counts: duplicates=%d: %w", duplicates, err)
	}
	fmt.Printf("Copies: completed=%d bytes_each=%d total_bytes=%d peak_concurrent=%d elapsed=%s\n", len(large.ids), copyBytes, adapter.bytes.Load(), adapter.peak.Load(), large.duration.Round(time.Millisecond))
	report.print()
	if adapter.peak.Load() != copyCount || adapter.bytes.Load() != copyCount*copyBytes {
		return errors.New("eight full concurrent copies were not observed")
	}
	if report.peakHeap-report.baseline.HeapAlloc > maxCopyHeapGrowth {
		return errors.New("copy heap growth exceeded fixed 64 MiB envelope")
	}
	fmt.Println("PASS: all jobs completed and settled; duplicate submits=0; eight 200 MB copies stayed within 64 MiB incremental Go heap")
	return nil
}

func (l *videoLoad) batch(ctx context.Context, name string, n int) (videoBatch, error) {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	b := videoBatch{ids: make([]string, n), submit: make([]float64, n), replay: make([]float64, n)}
	start, before := time.Now(), l.counter.n.Load()
	// Two independently owned workers compete for the same durable queues.
	var workers sync.WaitGroup
	errCh := make(chan error, 2)
	for range 2 {
		worker := l.service.NewWorker()
		workers.Add(1)
		go func() {
			defer workers.Done()
			ticker := time.NewTicker(250 * time.Millisecond)
			defer ticker.Stop()
			for {
				if err := worker.Tick(ctx); err != nil {
					if ctx.Err() == nil {
						errCh <- err
						cancel()
					}
					return
				}
				select {
				case <-ctx.Done():
					return
				case <-ticker.C:
				}
			}
		}()
	}
	finish := func() { cancel(); workers.Wait() }
	defer finish()
	err := parallelVideo(ctx, n, l.concurrency, func(i int) error {
		rc := models.AcquireRequestContext()
		defer rc.Release()
		rc.EndpointType = "video"
		rc.Metadata["org_id"] = "load-org"
		rc.Metadata["auth_key_id"] = "load-key"
		rc.Metadata["key_type"] = "byok"
		req := provider.Request{Model: "byteplus/" + loadVideoModel, Prompt: "synthetic load test", DurationSeconds: 5}
		key := fmt.Sprintf("%s-%d", name, i)
		scopes := []video.BudgetReservation{{Level: "org", Period: "total", Limit: 1e9}, {Level: "key", Key: "load-key", Period: "total", Limit: 1e9}}
		t := time.Now()
		accepted, err := l.service.Accept(ctx, rc, req, key, scopes)
		b.submit[i] = float64(time.Since(t)) / float64(time.Millisecond)
		if err != nil {
			return fmt.Errorf("accept %d: %w", i, err)
		}
		if accepted.Replay {
			return fmt.Errorf("new key %d replayed", i)
		}
		b.ids[i] = accepted.Job.ID
		t = time.Now()
		replay, err := l.service.Accept(ctx, rc, req, key, scopes)
		b.replay[i] = float64(time.Since(t)) / float64(time.Millisecond)
		if err != nil {
			return fmt.Errorf("replay %d: %w", i, err)
		}
		if !replay.Replay || replay.Job.ID != accepted.Job.ID {
			return fmt.Errorf("replay %d changed job", i)
		}
		return nil
	})
	if err == nil {
		pending := make([]bool, n)
		for i := range pending {
			pending[i] = true
		}
		remaining := n
		for remaining > 0 && err == nil {
			latencies := make([]float64, n)
			err = parallelVideo(ctx, n, l.concurrency, func(i int) error {
				if !pending[i] {
					return nil
				}
				t := time.Now()
				j, e := l.service.Get(ctx, b.ids[i], "load-org")
				latencies[i] = float64(time.Since(t)) / float64(time.Millisecond)
				if e != nil {
					return e
				}
				if j.IsTerminal() && j.Status != video.StatusCompleted {
					return fmt.Errorf("job %s failed: %s %+v", j.ID, j.Status, j.Error)
				}
				if j.Status == video.StatusCompleted && j.SettlementState == video.SettlementSettled {
					pending[i] = false
				}
				return nil
			})
			remaining = 0
			for i, latency := range latencies {
				if latency > 0 {
					b.status = append(b.status, latency)
				}
				if pending[i] {
					remaining++
				}
			}
			if remaining > 0 && err == nil {
				timer := time.NewTimer(250 * time.Millisecond)
				select {
				case <-ctx.Done():
					err = ctx.Err()
				case <-timer.C:
				}
				timer.Stop()
			}
		}
	}
	finish()
	select {
	case workerErr := <-errCh:
		return b, fmt.Errorf("worker: %w", workerErr)
	default:
	}
	b.commands = l.counter.n.Load() - before
	b.duration = time.Since(start)
	return b, err
}

func parallelVideo(ctx context.Context, n, concurrency int, fn func(int) error) error {
	var wg sync.WaitGroup
	var next atomic.Int64
	var once sync.Once
	var first error
	for range min(n, concurrency) {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for {
				i := int(next.Add(1) - 1)
				if i >= n {
					return
				}
				err := ctx.Err()
				if err == nil {
					err = fn(i)
				}
				if err != nil {
					once.Do(func() { first = err })
					return
				}
			}
		}()
	}
	wg.Wait()
	return first
}

func (l *videoLoad) submitCounts(ids []string) (total, duplicate int, err error) {
	for _, id := range ids {
		n := l.fake.SubmitCount(id)
		total += n
		duplicate += max(0, n-1)
		if n != 1 {
			err = fmt.Errorf("job %s submitted %d times", id, n)
		}
	}
	return
}
func (l *videoLoad) checkJobs(ctx context.Context, ids []string, size int64) error {
	var micros int64
	for _, id := range ids {
		j, err := l.store.GetAccounting(ctx, id)
		if err != nil {
			return err
		}
		if j.Status != video.StatusCompleted || j.SettlementState != video.SettlementSettled || len(j.Artifacts) != 1 || j.Artifacts[0].State != video.ArtifactAvailable || j.Artifacts[0].Bytes != size {
			return fmt.Errorf("job %s not fully delivered/settled", id)
		}
		micros += j.SettledMicros
	}
	// The small phase is the entire ledger here, so both budget scopes must equal
	// the sum of settled jobs, including all idempotent client retries.
	if !l.adapter.large.Load() {
		for _, scope := range []struct{ level, key string }{{"org", ""}, {"key", "load-key"}} {
			spend, _, ok := l.budget.GetSpend("load-org", scope.level, scope.key, "total")
			if !ok || math.Abs(spend-float64(micros)/1e6) > 1e-6 {
				return fmt.Errorf("%s spend %.9f does not match settled %.9f", scope.level, spend, float64(micros)/1e6)
			}
		}
	}
	return nil
}
func (l *videoLoad) reportBatch(ctx context.Context, b videoBatch) error {
	if err := l.checkJobs(ctx, b.ids, int64(len(videotest.MP4()))); err != nil {
		return err
	}
	total, duplicate, err := l.submitCounts(b.ids)
	if err != nil {
		return err
	}
	sort.Float64s(b.submit)
	sort.Float64s(b.replay)
	sort.Float64s(b.status)
	fmt.Printf("Lifecycle: completed=%d settled=%d replayed=%d upstream_submits=%d duplicate_submits=%d elapsed=%s\n", len(b.ids), len(b.ids), len(b.replay), total, duplicate, b.duration.Round(time.Millisecond))
	fmt.Printf("Latency (service boundary, ms): submit_p95=%.3f replay_p95=%.3f status_p95=%.3f status_samples=%d\n", percentile(b.submit, 95), percentile(b.replay, 95), percentile(b.status, 95), len(b.status))
	fmt.Printf("Redis client commands: total=%d per_job=%.3f (includes replays, status polling, workers, gauge scans; Lua internals excluded)\n", b.commands, float64(b.commands)/float64(len(b.ids)))
	before, start := l.counter.n.Load(), time.Now()
	if err := l.service.RefreshMetrics(ctx); err != nil {
		return err
	}
	fmt.Printf("Shared gauge scan: jobs=%d commands=%d elapsed=%s\n", len(b.ids), l.counter.n.Load()-before, time.Since(start).Round(time.Microsecond))
	return nil
}

func videoKeys(ctx context.Context, c redis.UniversalClient, prefix string) ([]string, error) {
	var keys []string
	var cursor uint64
	for {
		part, next, err := c.Scan(ctx, cursor, prefix+"*", 512).Result()
		if err != nil {
			return nil, err
		}
		keys = append(keys, part...)
		cursor = next
		if cursor == 0 {
			break
		}
	}
	// SCAN may return a key more than once.
	sort.Strings(keys)
	return compactKeys(keys), nil
}
func compactKeys(keys []string) []string {
	out := keys[:0]
	for _, key := range keys {
		if len(out) == 0 || out[len(out)-1] != key {
			out = append(out, key)
		}
	}
	return out
}
func cleanVideoKeys(ctx context.Context, c redis.UniversalClient, prefix string) error {
	keys, err := videoKeys(ctx, c, prefix)
	if err != nil {
		return err
	}
	for len(keys) > 0 {
		n := min(256, len(keys))
		if err := c.Del(ctx, keys[:n]...).Err(); err != nil {
			return err
		}
		keys = keys[n:]
	}
	return nil
}
func (l *videoLoad) reportStorage(ctx context.Context, jobs int) error {
	keys, err := videoKeys(ctx, l.client.Redis(), l.prefix)
	if err != nil {
		return err
	}
	var bytes, budgetBytes, receipts, persistent int64
	for _, key := range keys {
		n, err := l.client.Redis().MemoryUsage(ctx, key).Result()
		if errors.Is(err, redis.Nil) {
			continue
		}
		if err != nil {
			return err
		}
		bytes += n
		if strings.HasPrefix(key, l.prefix+"budget:") {
			budgetBytes += n
			fields, err := l.client.Redis().HKeys(ctx, key).Result()
			if err != nil {
				return err
			}
			for _, field := range fields {
				if strings.HasPrefix(field, "op:video") {
					receipts++
				}
			}
			ttl, err := l.client.Redis().TTL(ctx, key).Result()
			if err != nil {
				return err
			}
			if ttl < 0 {
				persistent++
			}
		}
	}
	fmt.Printf("Redis owned state: keys=%d bytes=%d bytes_per_job=%.1f budget_bytes=%d receipt_fields=%d persistent_budget_hashes=%d\n", len(keys), bytes, float64(bytes)/float64(jobs), budgetBytes, receipts, persistent)
	return nil
}

type copyMemory struct {
	adapter                      *loadAdapter
	baseline                     runtime.MemStats
	peakHeap, peakInuse, peakSys uint64
	samples                      int
	quarterHeap                  [4]uint64
	quarterSamples               [4]int
	done, stopped                chan struct{}
}

func startCopyMemory(a *loadAdapter) *copyMemory {
	m := &copyMemory{adapter: a, done: make(chan struct{}), stopped: make(chan struct{})}
	runtime.ReadMemStats(&m.baseline)
	m.peakHeap = m.baseline.HeapAlloc
	m.peakInuse = m.baseline.HeapInuse
	m.peakSys = m.baseline.Sys
	go func() {
		defer close(m.stopped)
		ticker := time.NewTicker(20 * time.Millisecond)
		defer ticker.Stop()
		for {
			select {
			case <-m.done:
				m.sample()
				return
			case <-ticker.C:
				m.sample()
			}
		}
	}()
	return m
}
func (m *copyMemory) sample() {
	var s runtime.MemStats
	runtime.ReadMemStats(&s)
	m.samples++
	m.peakHeap = max(m.peakHeap, s.HeapAlloc)
	m.peakInuse = max(m.peakInuse, s.HeapInuse)
	m.peakSys = max(m.peakSys, s.Sys)
	if m.adapter.active.Load() > 0 {
		quarter := min(3, int(m.adapter.bytes.Load()*4/(copyCount*copyBytes)))
		m.quarterHeap[quarter] += s.HeapAlloc
		m.quarterSamples[quarter]++
	}
}
func (m *copyMemory) stop() *copyMemory { close(m.done); <-m.stopped; return m }
func (m *copyMemory) print() {
	runtime.GC()
	var after runtime.MemStats
	runtime.ReadMemStats(&after)
	const mib = 1024 * 1024
	fmt.Printf("Copy Go memory (MiB): baseline_heap=%.2f peak_heap=%.2f peak_heap_delta=%.2f post_gc_heap=%.2f peak_heap_inuse=%.2f peak_sys=%.2f samples=%d interval=20ms\n", float64(m.baseline.HeapAlloc)/mib, float64(m.peakHeap)/mib, float64(m.peakHeap-m.baseline.HeapAlloc)/mib, float64(after.HeapAlloc)/mib, float64(m.peakInuse)/mib, float64(m.peakSys)/mib, m.samples)
	for i, n := range m.quarterSamples {
		if n > 0 {
			fmt.Printf("Copy progress %d-%d%%: mean_heap_mib=%.2f samples=%d\n", i*25, (i+1)*25, float64(m.quarterHeap[i])/float64(n)/mib, n)
		}
	}
}
