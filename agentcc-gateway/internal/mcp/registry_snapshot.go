package mcp

// snapshotTool fills an unused, preallocated result slot without copying atomic
// values. The caller holds the registry read lock for metadata; usage counters
// are independently loaded because RecordCall does not take that lock. As with
// ListMCPTools, Tool's schema and annotation references remain shallow copies.
// Do not append populated slots to a growing slice: that would copy the atomics.
func snapshotTool(dst, src *RegisteredTool) {
	dst.Server = src.Server
	dst.OrigName = src.OrigName
	dst.Name = src.Name
	dst.Tool = src.Tool
	dst.Upstream = src.Upstream
	dst.Title = src.Title
	dst.Stats.CallCount.Store(src.Stats.CallCount.Load())
	dst.Stats.ErrorCount.Store(src.Stats.ErrorCount.Load())
	dst.Stats.TotalNanos.Store(src.Stats.TotalNanos.Load())
	dst.Stats.LastCalled.Store(src.Stats.LastCalled.Load())
}
