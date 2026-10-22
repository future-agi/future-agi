package async

import (
	"errors"
	"sync"
)

// push's refusals: the queue as a whole is full, the org already has its
// share of it queued, or the worker has stopped.
var (
	errQueueFull    = errors.New("async job queue is full")
	errOrgQueueFull = errors.New("this organization already has its share of the async job queue queued; wait for its jobs to finish")
	errQueueClosed  = errors.New("async worker is stopped")
)

// fairQueue holds the IDs of queued jobs per org and hands them to the
// workers in turn. All orgs' jobs share one pool of workers, so the queue
// is what keeps one org from holding all of them:
//
//   - a worker takes the next org in rotation that has a job queued and
//     fewer than perOrgRunning jobs running, so one org's backlog never
//     delays another org's next job, and no org runs more than
//     perOrgRunning jobs at once however many it queues;
//   - an org may have at most perOrgQueued jobs queued, of the capacity the
//     queue holds in all, so one org cannot fill the queue and have every
//     other org's jobs refused.
//
// The no-org tenant ("") is one org here, as it is in the Store.
type fairQueue struct {
	mu   sync.Mutex
	cond *sync.Cond

	capacity      int
	perOrgQueued  int
	perOrgRunning int

	queued  map[string][]string // org → its queued job IDs, oldest first
	order   []string            // orgs with a job queued, next turn first
	running map[string]int      // org → its jobs running now
	total   int                 // jobs queued, all orgs
	closed  bool
}

func newFairQueue(capacity, perOrgQueued, perOrgRunning int) *fairQueue {
	q := &fairQueue{
		capacity:      capacity,
		perOrgQueued:  perOrgQueued,
		perOrgRunning: perOrgRunning,
		queued:        make(map[string][]string),
		running:       make(map[string]int),
	}
	q.cond = sync.NewCond(&q.mu)
	return q
}

// push queues job id for org. It queues nothing and returns one of the
// errors above when the queue is full, org has its share queued, or the
// queue is closed.
func (q *fairQueue) push(org, id string) error {
	q.mu.Lock()
	defer q.mu.Unlock()
	switch {
	case q.closed:
		return errQueueClosed
	case q.total >= q.capacity:
		return errQueueFull
	case len(q.queued[org]) >= q.perOrgQueued:
		return errOrgQueueFull
	}
	if len(q.queued[org]) == 0 {
		q.order = append(q.order, org)
	}
	q.queued[org] = append(q.queued[org], id)
	q.total++
	q.cond.Broadcast()
	return nil
}

// next blocks until a queued job may run, and returns it with its org,
// counted as running. The caller calls done(org) when the job is over,
// whether or not it ran. ok is false once the queue is closed.
func (q *fairQueue) next() (org, id string, ok bool) {
	q.mu.Lock()
	defer q.mu.Unlock()
	for {
		if q.closed {
			return "", "", false
		}
		for i, o := range q.order {
			if q.running[o] >= q.perOrgRunning {
				continue
			}
			ids := q.queued[o]
			id = ids[0]
			ids[0] = ""
			// o leaves its place in the rotation, and goes to the back if
			// it still has jobs queued.
			q.order = append(q.order[:i], q.order[i+1:]...)
			if len(ids) == 1 {
				delete(q.queued, o)
			} else {
				q.queued[o] = ids[1:]
				q.order = append(q.order, o)
			}
			q.running[o]++
			q.total--
			return o, id, true
		}
		q.cond.Wait()
	}
}

// done records that one of org's jobs taken by next is over, freeing its
// turn for org's next queued job.
func (q *fairQueue) done(org string) {
	q.mu.Lock()
	defer q.mu.Unlock()
	if q.running[org] <= 1 {
		delete(q.running, org)
	} else {
		q.running[org]--
	}
	q.cond.Broadcast()
}

// close makes every next return, now and later, and push refuse.
func (q *fairQueue) close() {
	q.mu.Lock()
	defer q.mu.Unlock()
	q.closed = true
	q.cond.Broadcast()
}
