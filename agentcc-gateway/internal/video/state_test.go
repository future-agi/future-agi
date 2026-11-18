package video

import "testing"

func TestTransitionTable(t *testing.T) {
	cases := []struct {
		from, phase, to, next string
		ok                    bool
	}{{StatusSubmitting, PhasePrepared, StatusSubmitting, PhaseCalling, true}, {StatusSubmitting, PhasePrepared, StatusQueued, PhaseReceived, false}, {StatusSubmitting, PhasePrepared, StatusCancelled, PhasePrepared, true}, {StatusSubmitting, PhaseCalling, StatusSubmissionUnknown, PhaseCalling, true}, {StatusSubmissionUnknown, PhaseCalling, StatusRunning, PhaseReceived, true}, {StatusQueued, PhaseReceived, StatusRunning, PhaseReceived, true}, {StatusRunning, PhaseReceived, StatusQueued, PhaseReceived, false}, {StatusCompleted, PhaseReceived, StatusRunning, PhaseReceived, false}, {StatusFailed, PhaseReceived, StatusCompleted, PhaseReceived, false}, {StatusCancelled, PhaseReceived, StatusCancelled, PhaseReceived, true}, {"unknown", "", StatusRunning, "", false}}
	for _, tc := range cases {
		t.Run(tc.from+"_"+tc.to, func(t *testing.T) {
			if err := ValidateTransition(tc.from, tc.phase, tc.to, tc.next); (err == nil) != tc.ok {
				t.Fatal(err)
			}
		})
	}
	for _, s := range []string{StatusSubmitting, StatusSubmissionUnknown, StatusQueued, StatusRunning, StatusCompleted, StatusFailed, StatusCancelled} {
		terminal := s == StatusCompleted || s == StatusFailed || s == StatusCancelled
		if IsTerminal(s) != terminal {
			t.Fatal(s)
		}
	}
}
