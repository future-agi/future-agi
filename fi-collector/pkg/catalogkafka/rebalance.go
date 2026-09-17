package catalogkafka

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"
)

var ErrAssignmentCheckpointRefresh = errors.New(
	"catalogkafka: assignment checkpoint refresh failed",
)

const maxAssignmentCheckpointRefreshTimeout = 10 * time.Second

// CheckpointLoader is the narrow read-only boundary used before Kafka begins
// fetching a new assignment. DeliveryLedgerCheckpointReader implements it.
type CheckpointLoader interface {
	Load(context.Context) ([]StreamCheckpoint, error)
}

// stickyConsumerError permanently closes a consumer after an assignment seed
// failure. A later successful ledger read must never make the same consumer
// resume, because franz-go may retry group joins internally.
type stickyConsumerError struct {
	mu  sync.Mutex
	err error
}

func (state *stickyConsumerError) Fail(err error) error {
	if state == nil {
		return ErrAssignmentCheckpointRefresh
	}
	state.mu.Lock()
	defer state.mu.Unlock()
	if state.err == nil {
		if err == nil {
			err = errors.New("unspecified checkpoint refresh failure")
		}
		state.err = fmt.Errorf("%w: %w", ErrAssignmentCheckpointRefresh, err)
	}
	return state.err
}

func (state *stickyConsumerError) Err() error {
	if state == nil {
		return nil
	}
	state.mu.Lock()
	defer state.mu.Unlock()
	return state.err
}

type assignmentCheckpointRefresher struct {
	loader    CheckpointLoader
	validator *SequenceValidator
	failure   *stickyConsumerError
}

func newAssignmentCheckpointRefresher(
	loader CheckpointLoader,
	validator *SequenceValidator,
	failure *stickyConsumerError,
) *assignmentCheckpointRefresher {
	return &assignmentCheckpointRefresher{
		loader: loader, validator: validator, failure: failure,
	}
}

// Refresh reloads and atomically merges durable checkpoints for every group
// assignment. Any read or merge error becomes sticky for this consumer.
func (refresh *assignmentCheckpointRefresher) Refresh(ctx context.Context) error {
	if refresh == nil || refresh.loader == nil || refresh.validator == nil || refresh.failure == nil {
		if refresh != nil && refresh.failure != nil {
			return refresh.failure.Fail(errors.New("incomplete assignment checkpoint refresher"))
		}
		return ErrAssignmentCheckpointRefresh
	}
	if err := refresh.failure.Err(); err != nil {
		return err
	}
	if ctx == nil {
		return refresh.failure.Fail(errors.New("nil assignment checkpoint context"))
	}
	if err := ctx.Err(); err != nil {
		return refresh.failure.Fail(err)
	}
	loadContext, cancel := context.WithTimeout(ctx, maxAssignmentCheckpointRefreshTimeout)
	defer cancel()
	checkpoints, err := refresh.loader.Load(loadContext)
	if err != nil {
		return refresh.failure.Fail(fmt.Errorf("load durable checkpoints: %w", err))
	}
	if err := refresh.validator.MergeCheckpoints(checkpoints); err != nil {
		return refresh.failure.Fail(fmt.Errorf("merge durable checkpoints: %w", err))
	}
	return nil
}
