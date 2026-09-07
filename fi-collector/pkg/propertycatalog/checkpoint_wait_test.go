package propertycatalog

import (
	"context"
	"errors"
	"testing"
	"time"
)

type checkpointLoadFunc func(context.Context) ([]StreamCheckpoint, error)

func (f checkpointLoadFunc) LoadCheckpoints(ctx context.Context) ([]StreamCheckpoint, error) {
	return f(ctx)
}

func TestOnlyValidatedOpenIncompleteInventoryIsRetryable(t *testing.T) {
	for _, terminal := range []bool{false, true} {
		rows := validCheckpointInventory(t, terminal)
		_, err := validateCheckpointInventory(rows[:len(rows)-1])
		if err == nil || errors.Is(err, ErrCheckpointInventoryPending) == terminal {
			t.Fatalf("terminal=%v err=%v", terminal, err)
		}
	}
	rows := validCheckpointInventory(t, false)
	rows[1].StreamProjectionVersion++
	if _, err := validateCheckpointInventory(rows[:len(rows)-1]); err == nil || errors.Is(err, ErrCheckpointInventoryPending) {
		t.Fatalf("corrupt stream was classified pending: %v", err)
	}
	rows = validCheckpointInventory(t, false)
	rows[1].StreamProducerStreamID = testWorkspace
	if _, err := validateCheckpointInventory(rows[:len(rows)-1]); err == nil || errors.Is(err, ErrCheckpointInventoryPending) {
		t.Fatalf("unplanned stream was classified pending: %v", err)
	}
}

func TestCheckpointWaitReloadsBeforeReturningAndDoesNotMaskCorruption(t *testing.T) {
	calls := 0
	result, err := AwaitCheckpointInventory(context.Background(), checkpointLoadFunc(func(context.Context) ([]StreamCheckpoint, error) {
		calls++
		if calls == 1 {
			return nil, ErrCheckpointInventoryPending
		}
		return []StreamCheckpoint{}, nil
	}))
	if err != nil || result == nil || calls != 2 {
		t.Fatalf("result=%v calls=%d err=%v", result, calls, err)
	}
	corrupt := errors.New("conflicting projection evidence")
	calls = 0
	_, err = AwaitCheckpointInventory(context.Background(), checkpointLoadFunc(func(context.Context) ([]StreamCheckpoint, error) {
		calls++
		return nil, corrupt
	}))
	if !errors.Is(err, corrupt) || calls != 1 {
		t.Fatal("corrupt checkpoint was retried or ignored")
	}
}

func TestCheckpointWaitHonorsCallerDeadlineWithoutEmptyFallback(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Millisecond)
	defer cancel()
	result, err := AwaitCheckpointInventory(ctx, checkpointLoadFunc(func(context.Context) ([]StreamCheckpoint, error) {
		return nil, ErrCheckpointInventoryPending
	}))
	if result != nil || !errors.Is(err, ErrCheckpointInventoryPending) || !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("result=%v err=%v", result, err)
	}
}
