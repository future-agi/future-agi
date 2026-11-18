package mcp

import (
	"bytes"
	"os"
	"os/signal"
	"strconv"
	"sync"
	"syscall"
)

// After Cmd.Wait has collected the group leader, PID 1 also owns its
// orphaned workers. Wait only in that command's process group: a global
// SIGCHLD waiter could steal another exec.Cmd's exit status.
func reapStdioGroup(pgid int) error {
	if os.Getpid() != 1 {
		return nil
	}
	for {
		_, err := syscall.Wait4(-pgid, nil, 0, nil)
		switch err {
		case nil, syscall.EINTR:
			continue
		case syscall.ECHILD:
			return nil
		default:
			return err
		}
	}
}

// While a session is open, a worker that a short-lived helper left behind
// is adopted by PID 1 and becomes a zombie when it exits. Collect such
// workers on SIGCHLD, one PID at a time: only exited members of a group a
// live transport owns, never the group leader (its status belongs to
// Cmd.Wait) and never processes of any other group.
var adoptedWorkers struct {
	once   sync.Once
	mu     sync.Mutex
	groups map[int]struct{}
	kick   chan struct{}
}

// trackStdioGroup registers a started command's group for collection while
// its session is open and returns the function that ends it. The group ID is
// the leader's PID, which stays unreaped until Close, so it cannot be reused.
func trackStdioGroup(pgid int) (untrack func()) {
	if os.Getpid() != 1 {
		return func() {}
	}
	r := &adoptedWorkers
	r.once.Do(func() {
		r.groups = make(map[int]struct{})
		r.kick = make(chan struct{}, 1)
		sig := make(chan os.Signal, 1)
		signal.Notify(sig, syscall.SIGCHLD)
		go func() {
			for {
				select {
				case <-sig:
				case <-r.kick:
				}
				collectAdoptedWorkers()
			}
		}()
	})
	r.mu.Lock()
	r.groups[pgid] = struct{}{}
	r.mu.Unlock()
	// A worker may have exited before registration; scan once now.
	select {
	case r.kick <- struct{}{}:
	default:
	}
	return func() {
		r.mu.Lock()
		delete(r.groups, pgid)
		r.mu.Unlock()
	}
}

func collectAdoptedWorkers() {
	r := &adoptedWorkers
	r.mu.Lock()
	defer r.mu.Unlock()
	if len(r.groups) == 0 {
		return
	}
	entries, err := os.ReadDir("/proc")
	if err != nil {
		return
	}
	for _, e := range entries {
		pid, err := strconv.Atoi(e.Name())
		if err != nil || pid <= 1 {
			continue
		}
		state, ppid, pgid, ok := procStat(pid)
		if !ok || state != 'Z' || ppid != 1 || pid == pgid {
			continue
		}
		if _, owned := r.groups[pgid]; !owned {
			continue
		}
		var ws syscall.WaitStatus
		for {
			if _, err := syscall.Wait4(pid, &ws, syscall.WNOHANG, nil); err != syscall.EINTR {
				break
			}
		}
	}
}

// procStat returns the state, parent and group of pid from /proc/<pid>/stat.
func procStat(pid int) (state byte, ppid, pgid int, ok bool) {
	data, err := os.ReadFile("/proc/" + strconv.Itoa(pid) + "/stat")
	if err != nil {
		return 0, 0, 0, false
	}
	// The command name is parenthesized and may contain spaces or ')'.
	i := bytes.LastIndexByte(data, ')')
	if i < 0 {
		return 0, 0, 0, false
	}
	fields := bytes.Fields(data[i+1:])
	if len(fields) < 3 || len(fields[0]) != 1 {
		return 0, 0, 0, false
	}
	ppid, err1 := strconv.Atoi(string(fields[1]))
	pgid, err2 := strconv.Atoi(string(fields[2]))
	if err1 != nil || err2 != nil {
		return 0, 0, 0, false
	}
	return fields[0][0], ppid, pgid, true
}
