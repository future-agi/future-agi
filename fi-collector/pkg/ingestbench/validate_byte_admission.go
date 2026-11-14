package ingestbench

import "fmt"

// validateByteLimits checks only the known model, never opaque /healthz metadata.
// Subtract after checking positive bounds, so even hostile int64 pool values
// cannot overflow into an apparently exact partition.
func validateByteLimits(l CollectorLimits) error {
	if l.IngestBudgetBytes <= 0 || l.WirePoolBytes <= 0 || l.WorkPoolBytes <= 0 || l.WorkPoolBytes >= l.IngestBudgetBytes || l.WirePoolBytes != l.IngestBudgetBytes-l.WorkPoolBytes {
		return fmt.Errorf("invalid observed byte-pool limits: positive wire_pool_bytes and work_pool_bytes must exactly partition ingest_budget_bytes")
	}
	if l.MaxPendingRows != 0 {
		return fmt.Errorf("byte-leases-v1 max_pending_rows must be zero; rows are observational, not an admission cap")
	}
	return nil
}

func validateByteAdmission(r Result) error {
	col := r.Provenance.Collector
	b := col.Budget
	if col.Limits != nil && col.Limits.AdmissionModel == "byte-leases-v1" {
		if err := validateByteLimits(*col.Limits); err != nil {
			return fmt.Errorf("provenance.collector.limits: %w", err)
		}
		if b.AdmissionModel != "byte-leases-v1" {
			return fmt.Errorf("provenance.collector.budget_check admission_model disagrees with observed byte limits")
		}
	}
	if b.AdmissionModel == "byte-leases-v1" {
		if col.Limits == nil || col.Limits.AdmissionModel != b.AdmissionModel || b.Assessment != "not-assessed" || b.Sufficient || b.BatchFits || b.RequiredPendingRows != 0 || b.AvailablePendingRows != 0 {
			return fmt.Errorf("provenance.collector.budget_check requires observed byte limits and not-assessed sufficiency without row sizing")
		}
	}
	backlog := r.Backlog
	// FailedSamples and KeptSamples were bounded against Samples first. We do
	// not know which failed samples were retained, so use both known upper bounds.
	if backlog.BytePoolSamples > backlog.KeptSamples || backlog.BytePoolSamples > backlog.Samples-backlog.FailedSamples {
		return fmt.Errorf("collector_backlog.byte_pool_samples must be a subset of retained successful samples")
	}
	if backlog.BytePoolSamples == 0 && (backlog.PeakWorkBytes != 0 || backlog.PeakWireBytes != 0 || backlog.IngestBudgetBytes != 0) {
		return fmt.Errorf("collector_backlog.byte_pool_samples must be positive for byte peaks or a sampled budget")
	}
	if backlog.BytePoolSamples > 0 && backlog.IngestBudgetBytes <= 0 {
		return fmt.Errorf("collector_backlog.ingest_budget_bytes must be positive for byte pool samples")
	}
	// Peaks span the run, while IngestBudgetBytes is the LAST successful byte
	// sample. Restarts can change pools; comparing the peaks to it would reject
	// real diagnostics. Per-sample pressure is checked when parsing health.
	return nil
}
