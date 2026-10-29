//go:build darwin || linux

package server

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"
	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"github.com/redis/go-redis/v9"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/status"
)

type authenticatedLoadReport struct {
	HTTP, GRPC                      string
	PeakHeap, RetainedHeap, PeakRSS uint64
	Validations, Creates            int64
	Stats                           IngestStats
}

// Opt-in, separate-process calibration. Only the collector and deterministic
// persistence resolver run in the child; clients, slow storage and Redis fixture
// run in the parent. This measures process RSS, not a container or a hard bound.
func TestAuthenticatedLoadWorker(t *testing.T) {
	dir := os.Getenv("FI_AUTH_LOAD_WORKER")
	if dir == "" {
		t.Skip("private subprocess worker")
	}
	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	r := &ingestResolver{}
	a, err := auth.NewWithResolver(auth.Config{CacheTTL: 20 * time.Millisecond, WarmTTL: 50 * time.Millisecond}, r, nil, log)
	if err != nil {
		t.Fatal(err)
	}
	rdb := redis.NewClient(&redis.Options{Addr: os.Getenv("FI_AUTH_LOAD_REDIS")})
	defer rdb.Close()
	usage := auth.NewUsageEmitter(rdb, nil, log, 1000)
	writer, err := chwriter.New(chwriter.Config{URL: os.Getenv("FI_AUTH_LOAD_CH"), RequestTimeout: 3 * time.Second, MaxRetries: 1, DeadLetterFile: filepath.Join(dir, "dead.jsonl")})
	if err != nil {
		t.Fatal(err)
	}
	report := authenticatedLoadReport{HTTP: testTCPAddr(t), GRPC: testTCPAddr(t)}
	s := New(Config{HTTPAddr: report.HTTP, GRPCAddr: report.GRPC, BatchMaxRows: 512, BatchMaxAge: 50 * time.Millisecond}, writer, a, usage, NoopMetering{}, WithLogger(log))
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- s.Run(ctx) }()
	if !waitPort(report.HTTP, 2*time.Second) || !waitPort(report.GRPC, 2*time.Second) {
		t.Fatal("worker listeners")
	}
	ready, _ := json.Marshal(report)
	if err := os.WriteFile(filepath.Join(dir, "ready.json"), ready, 0600); err != nil {
		t.Fatal(err)
	}
	ticker := time.NewTicker(10 * time.Millisecond)
	defer ticker.Stop()
	for {
		var m runtime.MemStats
		runtime.ReadMemStats(&m)
		report.PeakHeap = max(report.PeakHeap, m.HeapAlloc)
		if _, err := os.Stat(filepath.Join(dir, "stop")); err == nil {
			break
		}
		select {
		case <-ctx.Done():
			t.Fatal("worker deadline")
		case <-ticker.C:
		}
	}
	cancel()
	if err := <-done; err != nil && !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
	report.Stats = s.IngestStats()
	report.Validations, report.Creates = r.validations.Load(), r.creates.Load()
	runtime.GC()
	var m runtime.MemStats
	runtime.ReadMemStats(&m)
	report.RetainedHeap = m.HeapAlloc
	var ru syscall.Rusage
	if err := syscall.Getrusage(syscall.RUSAGE_SELF, &ru); err != nil {
		t.Fatal(err)
	}
	report.PeakRSS = uint64(ru.Maxrss)
	if runtime.GOOS != "darwin" {
		report.PeakRSS *= 1024
	}
	out, _ := json.Marshal(report)
	if err := os.WriteFile(filepath.Join(dir, "result.json"), out, 0600); err != nil {
		t.Fatal(err)
	}
}

func attributeHeavyTraces(n int) ptrace.Traces {
	return calTraces(1, 1, n, func(rs ptrace.ResourceSpans, sp ptrace.Span, _ int) {
		rs.Resource().Attributes().PutStr("project_name", "heavy")
		sp.SetName("attribute-heavy")
		for i := 0; i < 50; i++ {
			sp.Attributes().PutStr(fmt.Sprintf("attribute.%02d", i), "twelve-bytes")
		}
	})
}

// The work pool, not just byte/span limits, determines whether a shape fits.
func TestPriceAttributeHeavyStandalone(t *testing.T) {
	for _, n := range []int{4096, 8192} {
		body := calBody(t, attributeHeavyTraces(n), false)
		shape, err := scanOTLPProto(body)
		if err != nil {
			t.Fatal(err)
		}
		limits := resolveIngestLimits(Config{}, 512<<20)
		t.Logf("spans=%d attributes_per_span=50 body=%d price=%d work_pool=%d", n, len(body), shape.price().total(), limits.work)
		if int64(len(body)) > limits.maxBody {
			t.Fatal("fixture exceeds body cap")
		}
		if (shape.price().total() > limits.work) != (n == 8192) {
			t.Fatalf("unexpected fit for %d spans", n)
		}
	}
}

func TestAuthenticatedLoadCalibration(t *testing.T) {
	if os.Getenv("FI_AUTH_CALIBRATION") != "1" || raceEnabled {
		t.Skip("opt-in non-race process calibration")
	}
	for _, limit := range []string{"512MiB", "900MiB"} {
		for _, scenario := range []string{"normal-512", "heavy-8192", "heavy-8192-long-timeout", "oversized", "wire-over-cap"} {
			if limit == "512MiB" && scenario == "heavy-8192-long-timeout" {
				continue
			}
			t.Run(limit+"/"+scenario, func(t *testing.T) {
				grpcDeadline := 5 * time.Second
				if scenario == "heavy-8192-long-timeout" {
					grpcDeadline = 15 * time.Second
				}
				dir := t.TempDir()
				// External fixtures never retain insert bodies. Count stamped rows while
				// reading JSONEachRow; emulate 50ms/insert + roughly 50MB/s storage latency.
				var stored, badRows atomic.Int64
				ch := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					scan := bufio.NewScanner(r.Body)
					scan.Buffer(make([]byte, 64<<10), 1<<20)
					var size int64
					for scan.Scan() {
						size += int64(len(scan.Bytes()) + 1)
						if strings.Contains(r.URL.Query().Get("query"), "spans") {
							var row map[string]any
							if json.Unmarshal(scan.Bytes(), &row) != nil || row["project_id"] != calProject {
								badRows.Add(1)
							}
							stored.Add(1)
						}
					}
					time.Sleep(50*time.Millisecond + time.Duration(float64(size)/(50*1024*1024)*float64(time.Second)))
					w.WriteHeader(200)
				}))
				defer ch.Close()
				mr := miniredis.RunT(t)
				exe, err := os.Executable()
				if err != nil {
					t.Fatal(err)
				}
				child := exec.Command(exe, "-test.run=^TestAuthenticatedLoadWorker$", "-test.timeout=50s")
				child.Env = append(os.Environ(), "GOMEMLIMIT="+limit, "GOMAXPROCS=2", "FI_AUTH_LOAD_WORKER="+dir, "FI_AUTH_LOAD_CH="+ch.URL, "FI_AUTH_LOAD_REDIS="+mr.Addr())
				var output bytes.Buffer
				child.Stdout = &output
				child.Stderr = &output
				if err := child.Start(); err != nil {
					t.Fatal(err)
				}
				defer func() {
					if child.ProcessState == nil {
						_ = child.Process.Kill()
						_ = child.Wait()
					}
				}()
				var ready authenticatedLoadReport
				deadline := time.Now().Add(5 * time.Second)
				for {
					b, err := os.ReadFile(filepath.Join(dir, "ready.json"))
					if err == nil {
						if json.Unmarshal(b, &ready) != nil {
							t.Fatal("ready JSON")
						}
						break
					}
					if time.Now().After(deadline) {
						t.Fatal("worker readiness timed out")
					}
					time.Sleep(10 * time.Millisecond)
				}
				conn, err := grpc.NewClient(ready.GRPC, grpc.WithTransportCredentials(insecure.NewCredentials()))
				if err != nil {
					t.Fatal(err)
				}
				defer conn.Close()
				client := &http.Client{Timeout: 10 * time.Second}
				defer client.CloseIdleConnections()
				post := func(body []byte, compressed bool) int {
					req, _ := http.NewRequest(http.MethodPost, "http://"+ready.HTTP+"/v1/traces", bytes.NewReader(body))
					req.Header.Set("X-Api-Key", "k")
					req.Header.Set("X-Secret-Key", "s")
					req.Header.Set("Content-Type", "application/x-protobuf")
					if compressed {
						req.Header.Set("Content-Encoding", "gzip")
					}
					resp, err := client.Do(req)
					if err != nil {
						return 0
					}
					io.Copy(io.Discard, resp.Body)
					resp.Body.Close()
					return resp.StatusCode
				}
				// Exercise failed-name caching alongside authenticated successes without
				// retaining arbitrary input names. The unit suite covers transient recovery.
				for i := 0; i < 2; i++ {
					if post(authenticatedBody(t, "uncreatable-load"), false) != 200 {
						t.Fatal("constraint fixture")
					}
				}
				var body []byte
				switch scenario {
				case "normal-512":
					body = calBody(t, calTraces(1, 1, 512, llmSpan(2000, 800)), false)
				case "heavy-8192", "heavy-8192-long-timeout":
					body = calBody(t, attributeHeavyTraces(8192), false)
				case "wire-over-cap":
					body = tracesOfSize(t, "oversized", 17<<20)
				default:
					body = emptySpans(t, maxSpansPerRequest+1)
				}
				compressedBody := gzipBytes(t, body)
				wantReject := scenario == "wire-over-cap" || scenario == "oversized" || (scenario == "heavy-8192" && limit == "512MiB")
				var mu sync.Mutex
				codesSeen := map[string]int{}
				var durations []time.Duration
				var wg sync.WaitGroup
				for sender := 0; sender < 4; sender++ {
					wg.Add(1)
					go func(sender int) {
						defer wg.Done()
						for i := 0; i < 4; i++ {
							start := time.Now()
							var result string
							if sender%2 == 0 {
								if sender == 2 {
									result = fmt.Sprint(post(compressedBody, true))
								} else {
									result = fmt.Sprint(post(body, false))
								}
							} else {
								ctx, cancel := context.WithTimeout(withKeys(context.Background()), grpcDeadline)
								err := exportRaw(conn, ctx, body)
								cancel()
								result = status.Code(err).String()
							}
							mu.Lock()
							codesSeen[result]++
							durations = append(durations, time.Since(start))
							mu.Unlock()
						}
					}(sender)
				}
				wg.Wait()
				if err := os.WriteFile(filepath.Join(dir, "stop"), nil, 0600); err != nil {
					t.Fatal(err)
				}
				if err := child.Wait(); err != nil {
					t.Fatalf("worker: %v\n%s", err, output.String())
				}
				b, err := os.ReadFile(filepath.Join(dir, "result.json"))
				if err != nil {
					t.Fatal(err)
				}
				var report authenticatedLoadReport
				if json.Unmarshal(b, &report) != nil {
					t.Fatal("report JSON")
				}
				sort.Slice(durations, func(i, j int) bool { return durations[i] < durations[j] })
				rc := redis.NewClient(&redis.Options{Addr: mr.Addr()})
				defer rc.Close()
				events, err := rc.XRange(context.Background(), "usage:events", "-", "+").Result()
				if err != nil {
					t.Fatal(err)
				}
				t.Logf("limit=%s scenario=%s grpc_deadline=%s http_timeout=10s clients=4(1-http,1-http-gzip,2-grpc) requests=16 slow_write=50ms+50MiB/s body_bytes=%d codes=%v p50=%v max=%v stored_rows=%d usage_events=%d report=%s", limit, scenario, grpcDeadline, len(body), codesSeen, durations[len(durations)/2], durations[len(durations)-1], stored.Load(), len(events), b)
				if badRows.Load() != 0 {
					t.Fatalf("unstamped rows=%d", badRows.Load())
				}
				if report.Validations == 0 || report.Creates == 0 {
					t.Fatal("auth/resolution bypassed")
				}
				if report.Stats.Wire.InUseBytes != 0 || report.Stats.Work.InUseBytes != 0 {
					t.Fatal("leases retained after drain")
				}
				if wantReject {
					if codesSeen["413"] != 8 || codesSeen[codes.InvalidArgument.String()] != 8 || stored.Load() != 0 || len(events) != 0 {
						t.Fatal("expected permanent rejection without storage or billing")
					}
				} else {
					if scenario != "heavy-8192" && (codesSeen["200"] != 8 || codesSeen[codes.OK.String()] != 8 || len(events) != 16) {
						t.Fatal("expected 16 successful authenticated usage emissions")
					}
					if scenario == "heavy-8192" {
						for code := range codesSeen {
							if code != "200" && code != "OK" && code != "DeadlineExceeded" && code != "Unavailable" {
								t.Fatalf("unexpected response: %s", code)
							}
						}
						if len(events) < codesSeen["200"]+codesSeen["OK"] || len(events) > 16 {
							t.Fatal("usage does not cover acknowledged batches")
						}
					}
					spans := int64(512)
					if strings.HasPrefix(scenario, "heavy-8192") {
						spans = 8192
					}
					if stored.Load() != spans*int64(len(events)) {
						t.Fatalf("stored=%d want=%d", stored.Load(), spans*int64(len(events)))
					}
				}
			})
		}
	}
}
