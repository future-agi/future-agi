package ingestbench

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"math"
	"net/url"
	"os"
	"os/exec"
	"regexp"
	"runtime"
	"runtime/debug"
	"strconv"
	"strings"
	"time"

	"golang.org/x/sys/unix"
)

// Provenance identifies exactly what was measured, with what, and how.
type Provenance struct {
	CommandLine []string       `json:"command_line"`
	Generator   GeneratorBuild `json:"generator_build"`
	Host        HostInfo       `json:"generator_host"`
	ClickHouse  ClickHouseInfo `json:"clickhouse"`
	Collector   CollectorInfo  `json:"collector"`
	SUT         SUTInfo        `json:"system_under_test"`
}

type GeneratorBuild struct {
	GoVersion         string `json:"go_version"`
	Module            string `json:"module,omitempty"`
	VCSRevision       string `json:"vcs_revision,omitempty"`
	VCSTime           string `json:"vcs_time,omitempty"`
	VCSModified       string `json:"vcs_modified,omitempty"`
	DeclaredRevision  string `json:"declared_revision,omitempty"`
	BenchSchema       string `json:"result_schema"`
	ExecutableRunMode string `json:"executable"`
}

type HostInfo struct {
	OS           string `json:"os"`
	Arch         string `json:"arch"`
	Kernel       string `json:"kernel,omitempty"`
	CPUModel     string `json:"cpu_model,omitempty"`
	LogicalCPUs  int    `json:"logical_cpus"`
	GOMAXPROCS   int    `json:"gomaxprocs"`
	MemoryBytes  uint64 `json:"memory_bytes,omitempty"`
	Scope        string `json:"scope"`
	ProbeFailure string `json:"probe_failure,omitempty"`
}

type ClusterInfo struct {
	Cluster  string `json:"cluster"`
	Shards   uint64 `json:"shards"`
	Replicas uint64 `json:"replicas"`
}

type ClickHouseInfo struct {
	Version          string             `json:"version,omitempty"`
	Timezone         string             `json:"timezone,omitempty"`
	UptimeSeconds    uint64             `json:"uptime_seconds,omitempty"`
	TableEngine      string             `json:"table_engine,omitempty"`
	SortingKey       string             `json:"sorting_key,omitempty"`
	PrimaryKey       string             `json:"primary_key,omitempty"`
	PartitionKey     string             `json:"partition_key,omitempty"`
	Clusters         []ClusterInfo      `json:"clusters"`
	DeclaredTopology string             `json:"declared_topology"`
	Hardware         ClickHouseHardware `json:"server_hardware"`
	// CreateTableSHA256 identifies the table DDL (create_table_query).
	CreateTableSHA256 string `json:"create_table_query_sha256,omitempty"`
	// SessionInsertSettings are insert-related settings of the VERIFICATION
	// user's session; the collector's writer sets async_insert itself (see the
	// collector's /healthz writer block) and its user profile may differ.
	SessionInsertSettings map[string]string `json:"session_insert_settings,omitempty"`
	// MergeTreeSettings are server-level MergeTree defaults that decide what an
	// acknowledged insert means on disk (fsync) and insert deduplication. A
	// table-level override appears in the table DDL, not here.
	MergeTreeSettings map[string]string `json:"merge_tree_settings,omitempty"`
	SettingsError     string            `json:"settings_error,omitempty"`
	// Storage of the whole table on the queried server, before the load and
	// after verification. It includes every other run's data.
	StorageBefore *TableStorage `json:"table_storage_before_load,omitempty"`
	StorageAfter  *TableStorage `json:"table_storage_after_verification,omitempty"`
	Error         string        `json:"error,omitempty"`
}

// TableStorage sums the table's active parts on the server answering the
// verification connection (one node of a cluster; a Distributed table has
// none of its own).
type TableStorage struct {
	Scope             string `json:"scope"`
	Rows              uint64 `json:"rows"`
	BytesOnDisk       uint64 `json:"bytes_on_disk"`
	UncompressedBytes uint64 `json:"data_uncompressed_bytes"`
	ActiveParts       uint64 `json:"active_parts"`
	Error             string `json:"error,omitempty"`
}

const tableStorageQuery = "SELECT sum(rows) AS rows, sum(bytes_on_disk) AS bytes, sum(data_uncompressed_bytes) AS uncompressed, count() AS parts" +
	" FROM system.parts WHERE database = {db:String} AND table = {table:String} AND active"

func tableStorage(ctx context.Context, ch clickHouse) TableStorage {
	t := TableStorage{Scope: "active parts of the whole table on the queried server, including data of other runs"}
	body, err := ch.query(ctx, tableStorageQuery, map[string]string{"db": ch.cfg.Database, "table": ch.cfg.Table})
	if err != nil {
		t.Error = err.Error()
		return t
	}
	var row struct {
		Rows         *uint64 `json:"rows"`
		Bytes        *uint64 `json:"bytes"`
		Uncompressed *uint64 `json:"uncompressed"`
		Parts        *uint64 `json:"parts"`
	}
	if decodeOne(body, &row) != nil || row.Rows == nil || row.Bytes == nil || row.Uncompressed == nil || row.Parts == nil {
		t.Error = "invalid table storage response"
		return t
	}
	t.Rows, t.BytesOnDisk, t.UncompressedBytes, t.ActiveParts = *row.Rows, *row.Bytes, *row.Uncompressed, *row.Parts
	return t
}

const sessionInsertSettingsQuery = "SELECT name, value FROM system.settings WHERE name IN ('async_insert', 'wait_for_async_insert', 'async_insert_deduplicate'," +
	" 'insert_deduplicate', 'insert_quorum', 'insert_quorum_parallel', 'distributed_foreground_insert') ORDER BY name"

const mergeTreeSettingsQuery = "SELECT name, value FROM system.merge_tree_settings WHERE name IN ('fsync_after_insert', 'fsync_part_directory'," +
	" 'non_replicated_deduplication_window', 'replicated_deduplication_window', 'replicated_deduplication_window_seconds') ORDER BY name"

var errInvalidSettings = errors.New("invalid or empty settings response")

// nameValues reads a name/value listing.
func nameValues(ctx context.Context, ch clickHouse, query string) (map[string]string, error) {
	body, err := ch.query(ctx, query, nil)
	if err != nil {
		return nil, err
	}
	out := map[string]string{}
	d := json.NewDecoder(bytes.NewReader(body))
	for {
		var row struct {
			Name  *string `json:"name"`
			Value *string `json:"value"`
		}
		err := d.Decode(&row)
		if err == io.EOF {
			break
		}
		if err != nil || row.Name == nil || row.Value == nil {
			return nil, errInvalidSettings
		}
		out[*row.Name] = *row.Value
	}
	if len(out) == 0 {
		return nil, errInvalidSettings
	}
	return out, nil
}

// ClickHouseHardware is what the ClickHouse server reports about the machine
// (or container) it runs on. Zero values are omitted: they mean the server did
// not report the metric (for example no cgroup limit), never a measured zero.
type ClickHouseHardware struct {
	Scope             string  `json:"scope"`
	OSMemoryBytes     uint64  `json:"os_memory_total_bytes,omitempty"`
	CGroupMaxCPU      float64 `json:"cgroup_max_cpu,omitempty"`
	CGroupMemoryBytes uint64  `json:"cgroup_memory_total_bytes,omitempty"`
	MaxThreads        string  `json:"max_threads_setting,omitempty"`
	Error             string  `json:"error,omitempty"`
}

// clickHouseHardwareQuery reads the server's own view of its memory and CPU
// limits from system tables (read-only).
const clickHouseHardwareQuery = "SELECT (SELECT value FROM system.settings WHERE name = 'max_threads') AS max_threads," +
	" sumIf(value, metric = 'OSMemoryTotal') AS os_memory_total, sumIf(value, metric = 'CGroupMaxCPU') AS cgroup_max_cpu," +
	" sumIf(value, metric = 'CGroupMemoryTotal') AS cgroup_memory_total" +
	" FROM system.asynchronous_metrics WHERE metric IN ('OSMemoryTotal', 'CGroupMaxCPU', 'CGroupMemoryTotal')"

func clickHouseHardware(ctx context.Context, ch clickHouse) ClickHouseHardware {
	h := ClickHouseHardware{Scope: "the ClickHouse server answering the verification connection (one node of a cluster)"}
	body, err := ch.query(ctx, clickHouseHardwareQuery, nil)
	if err != nil {
		h.Error = err.Error()
		return h
	}
	var row struct {
		MaxThreads  *string  `json:"max_threads"`
		OSMemory    *float64 `json:"os_memory_total"`
		CGroupCPU   *float64 `json:"cgroup_max_cpu"`
		CGroupBytes *float64 `json:"cgroup_memory_total"`
	}
	if decodeOne(body, &row) != nil || row.MaxThreads == nil || row.OSMemory == nil || row.CGroupCPU == nil || row.CGroupBytes == nil {
		h.Error = "invalid server hardware response"
		return h
	}
	h.MaxThreads = strings.Trim(*row.MaxThreads, "'")
	h.OSMemoryBytes, h.CGroupMaxCPU, h.CGroupMemoryBytes = byteCount(*row.OSMemory), max(0, *row.CGroupCPU), byteCount(*row.CGroupBytes)
	if h.OSMemoryBytes == 0 {
		h.Error = "the server does not report OSMemoryTotal"
	}
	return h
}

// byteCount converts a reported byte metric, treating values that cannot be a
// real size (negative, not finite or beyond int64) as not reported.
func byteCount(v float64) uint64 {
	if !(v > 0) || v >= math.MaxInt64 {
		return 0
	}
	return uint64(v)
}

type SUTInfo struct {
	Revision          string `json:"revision"`
	RevisionSource    string `json:"revision_source"`
	DeclaredRevision  string `json:"declared_revision,omitempty"`
	CollectorRevision string `json:"collector_reported_revision,omitempty"`
	RevisionsAgree    *bool  `json:"revisions_agree,omitempty"`
	Notes             string `json:"notes,omitempty"`
}

var secretArg = regexp.MustCompile(`(?i)(password|passwd|secret|token|api[-_]?key|authorization|credential)`)

// RedactArgs returns the command line with anything credential-like removed:
// values of flags whose name looks secret, key=value pairs with a secret-like
// key, and URL userinfo or credential-like query parameters. Flags that only
// NAME an environment variable (for example --otlp-headers-env) are kept.
func RedactArgs(args []string) []string {
	out := make([]string, 0, len(args))
	redactNext := false
	for _, arg := range args {
		if redactNext {
			out = append(out, "<redacted>")
			redactNext = false
			continue
		}
		if !strings.HasPrefix(arg, "-") {
			if u, err := url.Parse(arg); err == nil && u.Scheme != "" && u.Host != "" {
				out = append(out, redactURL(arg))
			} else if name, _, ok := strings.Cut(arg, "="); ok && secretArg.MatchString(name) {
				out = append(out, name+"=<redacted>")
			} else {
				out = append(out, arg)
			}
			continue
		}
		flagName, value, hasValue := strings.Cut(arg, "=")
		name := strings.TrimLeft(flagName, "-")
		if secretArg.MatchString(name) && !strings.HasSuffix(strings.ToLower(name), "-env") {
			if hasValue {
				out = append(out, flagName+"=<redacted>")
			} else {
				out = append(out, arg)
				redactNext = true
			}
			continue
		}
		if hasValue {
			out = append(out, flagName+"="+redactURL(value))
		} else {
			out = append(out, arg)
		}
	}
	return out
}

func redactURL(s string) string {
	u, err := url.Parse(s)
	if err != nil || u.Scheme == "" || u.Host == "" {
		return s
	}
	if u.User != nil {
		u.User = url.User("redacted")
	}
	q := u.Query()
	changed := false
	for k := range q {
		if secretArg.MatchString(k) || strings.Contains(strings.ToLower(k), "key") {
			q.Set(k, "redacted")
			changed = true
		}
	}
	if changed {
		u.RawQuery = q.Encode()
	}
	return u.String()
}

func generatorBuild(declared string) GeneratorBuild {
	g := GeneratorBuild{GoVersion: runtime.Version(), DeclaredRevision: declared, BenchSchema: SchemaVersion, ExecutableRunMode: "go-binary"}
	if bi, ok := debug.ReadBuildInfo(); ok {
		g.Module = bi.Main.Path
		for _, s := range bi.Settings {
			switch s.Key {
			case "vcs.revision":
				g.VCSRevision = s.Value
			case "vcs.time":
				g.VCSTime = s.Value
			case "vcs.modified":
				g.VCSModified = s.Value
			}
		}
	}
	if strings.HasSuffix(os.Args[0], ".test") {
		g.ExecutableRunMode = "go-test-binary"
	}
	return g
}

// hostInfo describes the GENERATOR host, never the collector or ClickHouse.
func hostInfo(ctx context.Context) HostInfo {
	h := HostInfo{OS: runtime.GOOS, Arch: runtime.GOARCH, LogicalCPUs: runtime.NumCPU(), GOMAXPROCS: runtime.GOMAXPROCS(0), Scope: "load-generator-host-not-collector"}
	var uts unix.Utsname
	if unix.Uname(&uts) == nil {
		h.Kernel = strings.TrimRight(string(bytes.TrimRight(uts.Release[:], "\x00")), " ")
	}
	switch runtime.GOOS {
	case "darwin":
		h.CPUModel = sysctl(ctx, "machdep.cpu.brand_string")
		if v, err := strconv.ParseUint(sysctl(ctx, "hw.memsize"), 10, 64); err == nil {
			h.MemoryBytes = v
		}
	case "linux":
		if f, err := os.Open("/proc/cpuinfo"); err == nil {
			s := bufio.NewScanner(io.LimitReader(f, 1<<20))
			for s.Scan() {
				if k, v, ok := strings.Cut(s.Text(), ":"); ok && strings.TrimSpace(k) == "model name" {
					h.CPUModel = strings.TrimSpace(v)
					break
				}
			}
			f.Close()
		}
		if f, err := os.Open("/proc/meminfo"); err == nil {
			s := bufio.NewScanner(io.LimitReader(f, 1<<20))
			for s.Scan() {
				fields := strings.Fields(s.Text())
				if len(fields) >= 2 && fields[0] == "MemTotal:" {
					if kb, err := strconv.ParseUint(fields[1], 10, 64); err == nil {
						h.MemoryBytes = kb * 1024
					}
					break
				}
			}
			f.Close()
		}
	}
	if h.CPUModel == "" || h.MemoryBytes == 0 {
		h.ProbeFailure = "CPU model or memory size unavailable on this OS"
	}
	return h
}

func sysctl(ctx context.Context, name string) string {
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, "sysctl", "-n", name).Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}

// clickHouseInfo reads version, table definition and cluster layout. All
// queries are read-only system-table reads.
func clickHouseInfo(ctx context.Context, ch clickHouse) ClickHouseInfo {
	info := ClickHouseInfo{DeclaredTopology: ch.cfg.Topology, Clusters: []ClusterInfo{}}
	body, err := ch.query(ctx, "SELECT version() AS version, timezone() AS timezone, uptime() AS uptime", nil)
	if err != nil {
		info.Error = err.Error()
		return info
	}
	var server struct {
		Version  string `json:"version"`
		Timezone string `json:"timezone"`
		Uptime   uint64 `json:"uptime"`
	}
	if decodeOne(body, &server) != nil {
		info.Error = "invalid version response"
		return info
	}
	info.Version, info.Timezone, info.UptimeSeconds = server.Version, server.Timezone, server.Uptime
	info.Hardware = clickHouseHardware(ctx, ch)
	if settings, err := nameValues(ctx, ch, sessionInsertSettingsQuery); err == nil {
		info.SessionInsertSettings = settings
	} else {
		info.SettingsError = "session insert settings: " + err.Error()
	}
	if settings, err := nameValues(ctx, ch, mergeTreeSettingsQuery); err == nil {
		info.MergeTreeSettings = settings
	} else {
		info.SettingsError = strings.TrimPrefix(info.SettingsError+"; merge tree settings: "+err.Error(), "; ")
	}
	body, err = ch.query(ctx, "SELECT engine, sorting_key, primary_key, partition_key, create_table_query FROM system.tables WHERE database = {db:String} AND name = {table:String}", map[string]string{"db": ch.cfg.Database, "table": ch.cfg.Table})
	if err == nil {
		var table struct {
			Engine       string `json:"engine"`
			SortingKey   string `json:"sorting_key"`
			PrimaryKey   string `json:"primary_key"`
			PartitionKey string `json:"partition_key"`
			Create       string `json:"create_table_query"`
		}
		if decodeOne(body, &table) == nil {
			info.TableEngine, info.SortingKey, info.PrimaryKey, info.PartitionKey = table.Engine, table.SortingKey, table.PrimaryKey, table.PartitionKey
			if table.Create != "" {
				sum := sha256.Sum256([]byte(table.Create))
				info.CreateTableSHA256 = hex.EncodeToString(sum[:])
			}
		} else {
			info.Error = "table definition not found"
		}
	} else {
		info.Error = err.Error()
	}
	body, err = ch.query(ctx, "SELECT cluster, uniqExact(shard_num) AS shards, max(replica_num) AS replicas FROM system.clusters GROUP BY cluster ORDER BY cluster LIMIT 64", nil)
	if err == nil {
		d := json.NewDecoder(bytes.NewReader(body))
		for {
			var c ClusterInfo
			if d.Decode(&c) != nil {
				break
			}
			info.Clusters = append(info.Clusters, c)
		}
	}
	return info
}

// resolveSUT decides which revision the result names as the system under
// test. The generator's own revision is NOT the system under test.
func resolveSUT(declared string, collector CollectorInfo, notes string) (SUTInfo, []string) {
	s := SUTInfo{DeclaredRevision: declared, CollectorRevision: collectorRevision(collector.Build), Notes: notes}
	var warnings []string
	switch {
	case s.DeclaredRevision != "" && s.CollectorRevision != "":
		agree := revisionsMatch(s.DeclaredRevision, s.CollectorRevision)
		s.RevisionsAgree = &agree
		s.Revision, s.RevisionSource = s.CollectorRevision, "collector /healthz build (declared --revision agrees)"
		if !agree {
			s.RevisionSource = "collector /healthz build (CONFLICTS with declared --revision)"
			warnings = append(warnings, "declared --revision "+s.DeclaredRevision+" differs from the revision the collector reports ("+s.CollectorRevision+")")
		}
	case s.CollectorRevision != "":
		s.Revision, s.RevisionSource = s.CollectorRevision, "collector /healthz build"
	case s.DeclaredRevision != "":
		s.Revision, s.RevisionSource = s.DeclaredRevision, "declared by operator (--revision); not reported by the collector"
	}
	return s, warnings
}
