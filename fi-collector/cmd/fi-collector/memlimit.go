package main

import (
	"log/slog"
	"os"
	"path/filepath"
	"runtime/debug"
	"strconv"
	"strings"
)

// cgroupRoot is where the container's cgroup is mounted.
const cgroupRoot = "/sys/fs/cgroup"

// applyContainerMemoryLimit sets the Go memory limit to 90% of the
// container's memory limit when GOMEMLIMIT is not set, so that the GC works
// to stay under it and the ingest budget (pkg/server, derived from the Go
// memory limit) fits the container. With neither, nothing is set and the
// budget falls back to a fixed size, which the server logs.
func applyContainerMemoryLimit(log *slog.Logger, gomemlimit, root string) {
	if gomemlimit != "" {
		return // the runtime has already applied it
	}
	limit, source := containerMemoryLimit(root)
	if limit <= 0 {
		return
	}
	set := limit / 10 * 9
	debug.SetMemoryLimit(set)
	log.Info("Go memory limit set from the container's memory limit (GOMEMLIMIT is unset)",
		"container_limit_mib", limit>>20, "go_memory_limit_mib", set>>20, "source", source)
}

// containerMemoryLimit reads the memory limit of the cgroup at root (v2, then
// v1), or 0 when there is none.
func containerMemoryLimit(root string) (int64, string) {
	if b, err := os.ReadFile(filepath.Join(root, "memory.max")); err == nil {
		if n, err := strconv.ParseInt(strings.TrimSpace(string(b)), 10, 64); err == nil && n > 0 {
			return n, "cgroup v2 memory.max"
		}
		return 0, "" // "max": no limit
	}
	if b, err := os.ReadFile(filepath.Join(root, "memory", "memory.limit_in_bytes")); err == nil {
		// v1 writes a page-rounded maximum int64 for no limit.
		if n, err := strconv.ParseInt(strings.TrimSpace(string(b)), 10, 64); err == nil && n > 0 && n < 1<<60 {
			return n, "cgroup v1 memory.limit_in_bytes"
		}
	}
	return 0, ""
}
