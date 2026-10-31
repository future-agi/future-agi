package ingestbench

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"runtime"
	"strings"
	"syscall"
	"time"

	"golang.org/x/sys/unix"
)

// ReadProbe describes an actual read-only application API operation. Auth is
// loaded from a named environment variable, never stored in the result.
// POST is allowed only with an explicit read_only flag for query-style APIs.
type ReadProbe struct {
	Name         string          `json:"name"`
	URL          string          `json:"url"`
	Method       string          `json:"method"`
	Body         json.RawMessage `json:"body,omitempty"`
	HeadersEnv   string          `json:"headers_env"`
	ReadOnly     bool            `json:"read_only"`
	ExpectStatus int             `json:"expect_status"`
	ExpectKey    string          `json:"expect_json_key"`
	IntervalMS   int             `json:"interval_ms"`
}

func (p ReadProbe) validate() error {
	switch p.Name {
	case "trace-list", "attribute-filter", "session-view", "dashboard":
	default:
		return fmt.Errorf("unknown application probe category %q", p.Name)
	}
	if (p.Method != "GET" && p.Method != "POST") || !p.ReadOnly || p.HeadersEnv == "" || p.ExpectStatus != 200 || p.ExpectKey == "" || p.IntervalMS < 1000 || len(p.Body) > 1<<20 {
		return fmt.Errorf("read probe %s needs read_only GET/POST, auth env, expected HTTP 200 + top-level JSON key, interval >= 1000ms and body <= 1MiB", p.Name)
	}
	return nil
}

func HeadersFromEnv(name string) (http.Header, error) {
	h := http.Header{}
	if name == "" {
		return h, nil
	}
	value, ok := os.LookupEnv(name)
	if !ok || value == "" {
		return nil, fmt.Errorf("headers environment variable %s is unset", name)
	}
	var input map[string]string
	if err := json.Unmarshal([]byte(value), &input); err != nil || len(input) == 0 {
		return nil, fmt.Errorf("headers environment variable %s must contain a nonempty JSON object", name)
	}
	for k, v := range input {
		if strings.ContainsAny(k+v, "\r\n") || strings.EqualFold(k, "Host") || strings.EqualFold(k, "Content-Length") {
			return nil, fmt.Errorf("invalid header in %s", name)
		}
		h.Set(k, v)
	}
	return h, nil
}

func runReadProbe(ctx context.Context, client *http.Client, p ReadProbe, headers http.Header) ProbeResult {
	r := ProbeResult{Name: p.Name, Kind: "authenticated_application_http", Status: "measured", HTTPStatuses: map[int]int64{}}
	good, bad, censored := &histogram{}, &histogram{}, &histogram{lowerBound: true}
	ticker := time.NewTicker(time.Duration(p.IntervalMS) * time.Millisecond)
	defer ticker.Stop()
	for {
		if ctx.Err() != nil {
			break
		}
		start := time.Now()
		req, err := http.NewRequestWithContext(ctx, p.Method, p.URL, bytes.NewReader(p.Body))
		if err != nil {
			r.Errors++
			break
		}
		req.Header = headers.Clone()
		if len(p.Body) > 0 {
			req.Header.Set("Content-Type", "application/json")
		}
		resp, err := client.Do(req)
		success := false
		requestErr := err
		if err == nil {
			r.HTTPStatuses[resp.StatusCode]++
			body, readErr := readBounded(resp.Body, 8<<20)
			requestErr = readErr
			resp.Body.Close()
			r.ResponseBytes += int64(len(body))
			var payload map[string]json.RawMessage
			if readErr == nil && resp.StatusCode == p.ExpectStatus && json.Unmarshal(body, &payload) == nil {
				_, success = payload[p.ExpectKey]
			}
		}
		// The normal load-window deadline censors an in-flight read; it is not
		// a server failure. A client timeout while the window is still open,
		// or an actual HTTP/error payload, remains an error.
		if ctx.Err() != nil && (errors.Is(requestErr, context.Canceled) || errors.Is(requestErr, context.DeadlineExceeded)) {
			r.Censored++
			censored.add(time.Since(start))
			break
		}
		if success {
			r.Successes++
			good.add(time.Since(start))
		} else {
			r.Errors++
			bad.add(time.Since(start))
		}
		select {
		case <-ctx.Done():
		case <-ticker.C:
		}
	}
	r.Latency, r.FailureLatency, r.CensoredElapsed = good.summary(), bad.summary(), censored.summary()
	if r.Censored > 0 {
		r.Status = "measured-with-censoring"
	}
	if r.Errors > 0 {
		r.Status = "errors"
	}
	if r.Successes+r.Errors+r.Censored == 0 {
		r.Status = "not-executed"
	}
	return r
}

type Node struct {
	Name string `json:"name"`
	URL  string `json:"url"`
}

type ResourceSnapshot struct {
	At     time.Time      `json:"at"`
	Source string         `json:"source"`
	Values map[string]any `json:"values,omitempty"`
	Error  string         `json:"error,omitempty"`
}

func localResources() ResourceSnapshot {
	r := ResourceSnapshot{At: time.Now().UTC(), Source: "load-generator-host-not-collector", Values: map[string]any{
		"os": runtime.GOOS, "arch": runtime.GOARCH, "go_version": runtime.Version(), "logical_cpus": runtime.NumCPU(),
	}}
	var mem runtime.MemStats
	runtime.ReadMemStats(&mem)
	r.Values["generator_heap_bytes"] = mem.HeapAlloc
	r.Values["generator_runtime_reserved_bytes"] = mem.Sys
	var usage syscall.Rusage
	if syscall.Getrusage(syscall.RUSAGE_SELF, &usage) == nil {
		r.Values["generator_user_cpu_seconds"] = float64(usage.Utime.Sec) + float64(usage.Utime.Usec)/1e6
		r.Values["generator_system_cpu_seconds"] = float64(usage.Stime.Sec) + float64(usage.Stime.Usec)/1e6
		rss := usage.Maxrss
		if runtime.GOOS != "darwin" {
			rss *= 1024
		}
		r.Values["generator_peak_rss_bytes"] = rss
	}
	var disk unix.Statfs_t
	if unix.Statfs(".", &disk) == nil {
		r.Values["working_filesystem_total_bytes"] = uint64(disk.Blocks) * uint64(disk.Bsize)
		r.Values["working_filesystem_available_bytes"] = uint64(disk.Bavail) * uint64(disk.Bsize)
	}
	return r
}

func resourceSnapshots(ctx context.Context, ch clickHouse) []ResourceSnapshot {
	out := []ResourceSnapshot{localResources()}
	for _, node := range ch.cfg.Nodes {
		c := ch
		c.cfg.ClickHouseURL = node.URL
		for _, item := range []struct{ name, query string }{
			{"system.asynchronous_metrics", "SELECT metric, value FROM system.asynchronous_metrics WHERE metric LIKE '%CPU%' OR metric LIKE '%Memory%' OR metric LIKE '%LoadAverage%' OR metric LIKE '%CGroup%' OR metric LIKE '%Uptime%'"},
			{"system.disks", "SELECT name, total_space, free_space, unreserved_space FROM system.disks"},
			{"system.metrics", "SELECT metric, value FROM system.metrics WHERE metric IN ('MemoryTracking', 'Query', 'Merge', 'BackgroundPoolTask', 'DistributedSend')"},
			{"table_storage", "SELECT table, sum(rows) AS rows, sum(bytes_on_disk) AS physical_bytes, sum(data_uncompressed_bytes) AS uncompressed_bytes FROM system.parts WHERE active AND database = {db:String} GROUP BY table"},
		} {
			r := ResourceSnapshot{At: time.Now().UTC(), Source: "clickhouse-node:" + node.Name + ":" + item.name}
			body, err := c.query(ctx, item.query, map[string]string{"db": c.cfg.Database})
			if err != nil {
				r.Error = err.Error()
			} else {
				rows := []map[string]any{}
				d := json.NewDecoder(bytes.NewReader(body))
				for {
					var row map[string]any
					err := d.Decode(&row)
					if err == io.EOF {
						break
					}
					if err != nil {
						r.Error = "invalid node metrics JSON"
						break
					}
					rows = append(rows, row)
				}
				r.Values = map[string]any{"rows": rows}
			}
			out = append(out, r)
		}
	}
	if len(ch.cfg.Containers) > 0 {
		args := []string{"stats", "--no-stream", "--format", "{{json .}}", "--"}
		args = append(args, ch.cfg.Containers...)
		cmdCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
		body, err := exec.CommandContext(cmdCtx, "docker", args...).Output()
		cancel()
		r := ResourceSnapshot{At: time.Now().UTC(), Source: "docker-stats-explicit-containers"}
		if err != nil {
			r.Error = "docker stats unavailable"
		} else if len(body) > 1<<20 {
			r.Error = "docker stats output too large"
		} else {
			r.Values = map[string]any{"json_lines": string(body), "units": "Docker CLI CPU%, MemUsage, BlockIO are strings with displayed units; BlockIO is cumulative bytes, not disk capacity"}
		}
		out = append(out, r)
	}
	return out
}
