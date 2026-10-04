package video

import (
	"errors"
	"fmt"
)

const (
	StatusSubmitting        = "submitting"
	StatusSubmissionUnknown = "submission_unknown"
	StatusQueued            = "queued"
	StatusRunning           = "running"
	StatusCompleted         = "completed"
	StatusFailed            = "failed"
	StatusCancelled         = "cancelled"
	PhasePrepared           = "prepared"
	PhaseCalling            = "calling"
	PhaseReceived           = "received"
	SettlementReserved      = "reserved"
	SettlementSettled       = "settled"
	SettlementReleased      = "released"
	SettlementUnsettled     = "unsettled"
	ReconcileNone           = "none"
	ReconcilePending        = "pending"
	ReconcileResolved       = "resolved"
	ReconcileUnresolved     = "unresolved"
	ArtifactPending         = "pending"
	ArtifactCopying         = "copying"
	ArtifactAvailable       = "available"
	ArtifactUnavailable     = "unavailable"
	ArtifactExpired         = "expired"
	ArtifactDeleted         = "deleted"
)

var ErrIllegalTransition = errors.New("video_illegal_transition")

func IsTerminal(status string) bool {
	return status == StatusCompleted || status == StatusFailed || status == StatusCancelled
}
func validState(s string) bool {
	switch s {
	case StatusSubmitting, StatusSubmissionUnknown, StatusQueued, StatusRunning, StatusCompleted, StatusFailed, StatusCancelled:
		return true
	}
	return false
}

// ValidateTransition is mirrored in the fenced Redis write script. Same-state
// writes permit artifact/accounting updates but never regress submission phase.
func ValidateTransition(from, phase, to, nextPhase string) error {
	allowed := false
	if validState(from) && validState(to) {
		if from == to {
			allowed = from != StatusSubmitting || phase == nextPhase || phase == PhasePrepared && nextPhase == PhaseCalling
		} else {
			switch from {
			case StatusSubmitting:
				if phase == PhasePrepared {
					allowed = to == StatusCancelled || to == StatusFailed
				} else if phase == PhaseCalling {
					allowed = to == StatusQueued || to == StatusRunning || to == StatusCompleted || to == StatusFailed || to == StatusSubmissionUnknown || to == StatusCancelled
				}
			case StatusSubmissionUnknown:
				allowed = to == StatusQueued || to == StatusRunning || to == StatusCompleted || to == StatusFailed || to == StatusCancelled
			case StatusQueued:
				allowed = to == StatusRunning || to == StatusCompleted || to == StatusFailed || to == StatusCancelled
			case StatusRunning:
				allowed = to == StatusCompleted || to == StatusFailed || to == StatusCancelled
			}
		}
	}
	if !allowed {
		return fmt.Errorf("%w: %s/%s -> %s/%s", ErrIllegalTransition, from, phase, to, nextPhase)
	}
	return nil
}
func validateJob(j *VideoJob) error {
	if j == nil || j.ID == "" || !validState(j.Status) {
		return fmt.Errorf("invalid video job")
	}
	if j.Status == StatusSubmitting && j.Phase != PhasePrepared && j.Phase != PhaseCalling {
		return fmt.Errorf("invalid submitting phase")
	}
	if (j.Status == StatusQueued || j.Status == StatusRunning || j.Status == StatusCompleted) && j.ProviderJobID == "" {
		return fmt.Errorf("provider receipt required")
	}
	if j.Status == StatusCompleted && j.DeletedAt == nil {
		ready := false
		for _, a := range j.Artifacts {
			if a.State == ArtifactAvailable || a.State == ArtifactUnavailable || a.State == ArtifactExpired || a.State == ArtifactDeleted {
				ready = true
			}
		}
		if !ready {
			return fmt.Errorf("completed job requires available or unavailable artifact")
		}
	}
	if j.ReservedMicros < 0 || j.SettledMicros < 0 {
		return fmt.Errorf("negative video accounting")
	}
	return nil
}
func protected(j *VideoJob) bool {
	return !j.IsTerminal() || j.SettlementState == SettlementReserved || j.SettlementState == SettlementUnsettled || j.ReconcileState == ReconcilePending || j.ReconcileState == ReconcileUnresolved
}
func stripDeleted(j *VideoJob) {
	if j.DeletedAt == nil {
		return
	}
	j.Prompt = ""
	j.RequestCanonical = nil
	j.ClientMetadata = nil
	j.Artifacts = nil
	j.Videos = nil
	j.RemixSourceID = ""
}

func validateCompletion(old, next *VideoJob) error {
	if old.Status != StatusCompleted && next.Status == StatusCompleted && next.DeletedAt == nil {
		for _, a := range next.Artifacts {
			if a.State == ArtifactAvailable || a.State == ArtifactUnavailable {
				return nil
			}
		}
		return fmt.Errorf("completion requires copied or unavailable artifact")
	}
	return nil
}
