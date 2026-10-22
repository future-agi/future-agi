package mcp

import (
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"syscall"
)

// ErrStdioSandboxUnavailable is returned by the sandbox launcher: this build
// does not host stdio servers in a sandbox.
var ErrStdioSandboxUnavailable = errors.New("stdio_sandbox_unavailable: sandboxed stdio servers are not available in this build")

// DefaultStdioBaseEnv returns the variables a stdio child gets from the host
// when ClientConfig.BaseEnv is nil (the mcp.stdio.base_env default).
func DefaultStdioBaseEnv() []string { return []string{"PATH", "HOME", "LANG", "TMPDIR"} }

// StdioSpec is what a launcher starts. The command and args come only from
// operator YAML through ClientConfig; no request or org config carries one.
type StdioSpec struct {
	ServerID string
	Command  string
	Args     []string
	// Env is the child's whole environment (NAME=value); nothing is added.
	Env []string
}

// StdioLauncher starts the child process of a stdio upstream. The child is
// tied to no context: it runs until its owner shuts it down.
type StdioLauncher interface {
	// Name is the launcher's mcp.stdio.launcher value.
	Name() string
	Launch(spec StdioSpec) (StdioProcess, error)
}

// StdioProcess is a running stdio child.
type StdioProcess interface {
	Pid() int
	Stdin() io.WriteCloser
	Stdout() io.Reader
	Stderr() io.Reader
	// Exited is closed once the child has exited and been reaped.
	Exited() <-chan struct{}
	// Signal sends sig to the child's whole process group. A group that has
	// already gone is not an error.
	Signal(sig syscall.Signal) error
	// Release closes the parent's pipe ends and removes the child's working
	// directory. Call it once the child has exited.
	Release()
}

// StdioLauncherFor maps an mcp.stdio.launcher value to its launcher; "" is
// the local launcher.
func StdioLauncherFor(name string) (StdioLauncher, error) {
	switch name {
	case "", "local":
		return LocalStdioLauncher{}, nil
	case "sandbox":
		return SandboxStdioLauncher{}, nil
	}
	return nil, fmt.Errorf("unknown stdio launcher %q (want local or sandbox)", name)
}

// SandboxStdioLauncher refuses every launch: this build does not host stdio
// servers in Sandboxes.
type SandboxStdioLauncher struct{}

func (SandboxStdioLauncher) Name() string { return "sandbox" }

func (SandboxStdioLauncher) Launch(StdioSpec) (StdioProcess, error) {
	return nil, ErrStdioSandboxUnavailable
}

// LocalStdioLauncher runs the child on this host, in its own process group,
// with a fresh working directory under the host's temp dir.
type LocalStdioLauncher struct{}

func (LocalStdioLauncher) Name() string { return "local" }

func (LocalStdioLauncher) Launch(spec StdioSpec) (StdioProcess, error) {
	// Resolve before Dir changes, so a relative command still means what it
	// meant relative to the gateway.
	path, err := resolveStdioCommand(spec.Command)
	if err != nil {
		return nil, err
	}
	dir, err := os.MkdirTemp("", "agentcc-mcp-"+tempDirLabel(spec.ServerID)+"-")
	if err != nil {
		return nil, fmt.Errorf("stdio working directory: %w", err)
	}
	var pipes [6]*os.File // stdin r/w, stdout r/w, stderr r/w
	closeAll := func() {
		for _, f := range pipes {
			if f != nil {
				f.Close()
			}
		}
		os.RemoveAll(dir)
	}
	for i := 0; i < len(pipes); i += 2 {
		if pipes[i], pipes[i+1], err = os.Pipe(); err != nil {
			closeAll()
			return nil, fmt.Errorf("stdio pipe: %w", err)
		}
	}

	cmd := exec.Command(path, spec.Args...)
	cmd.Env = append([]string{}, spec.Env...) // never nil: nil would inherit the gateway's environment
	cmd.Dir = dir
	cmd.Stdin, cmd.Stdout, cmd.Stderr = pipes[0], pipes[3], pipes[5]
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	if err := cmd.Start(); err != nil {
		closeAll()
		return nil, fmt.Errorf("start command: %w", err)
	}
	// The child holds its own ends now.
	pipes[0].Close()
	pipes[3].Close()
	pipes[5].Close()

	p := &localStdioProcess{
		cmd:    cmd,
		dir:    dir,
		stdin:  pipes[1],
		stdout: pipes[2],
		stderr: pipes[4],
		exited: make(chan struct{}),
	}
	go func() {
		cmd.Wait() // the files are *os.File, so Wait closes nothing the readers use
		close(p.exited)
	}()
	return p, nil
}

type localStdioProcess struct {
	cmd    *exec.Cmd
	dir    string
	stdin  *os.File
	stdout *os.File
	stderr *os.File
	exited chan struct{}
}

func (p *localStdioProcess) Pid() int                { return p.cmd.Process.Pid }
func (p *localStdioProcess) Stdin() io.WriteCloser   { return p.stdin }
func (p *localStdioProcess) Stdout() io.Reader       { return p.stdout }
func (p *localStdioProcess) Stderr() io.Reader       { return p.stderr }
func (p *localStdioProcess) Exited() <-chan struct{} { return p.exited }

func (p *localStdioProcess) Signal(sig syscall.Signal) error {
	// Setpgid made the child its group's leader, so the group id is its pid.
	if err := syscall.Kill(-p.cmd.Process.Pid, sig); err != nil && !errors.Is(err, syscall.ESRCH) {
		return err
	}
	return nil
}

func (p *localStdioProcess) Release() {
	p.stdin.Close()
	p.stdout.Close()
	p.stderr.Close()
	if err := os.RemoveAll(p.dir); err != nil {
		slog.Warn("mcp stdio: cannot remove the server's working directory", "dir", p.dir, "error", err)
	}
}

// resolveStdioCommand returns the absolute path of command, looked up in
// PATH when it is a bare name.
func resolveStdioCommand(command string) (string, error) {
	if command == "" {
		return "", errors.New("stdio server has no command")
	}
	path, err := exec.LookPath(command)
	if err != nil {
		return "", fmt.Errorf("stdio command: %w", err)
	}
	return filepath.Abs(path)
}

// tempDirLabel keeps a server id safe and short for a directory name.
func tempDirLabel(serverID string) string {
	label := strings.Map(func(r rune) rune {
		if r == '-' || r == '_' || ('a' <= r && r <= 'z') || ('A' <= r && r <= 'Z') || ('0' <= r && r <= '9') {
			return r
		}
		return '_'
	}, serverID)
	if len(label) > 32 {
		label = label[:32]
	}
	return label
}

// buildStdioEnv is the child's whole environment: the base names copied from
// the host, then the fromHost names, then values; a later step wins on a
// repeated name, and a host name that is unset stays unset.
func buildStdioEnv(base, fromHost []string, values map[string]string) []string {
	env := map[string]string{}
	var order []string
	set := func(name, value string) {
		if name == "" || strings.ContainsAny(name, "=\x00") {
			return
		}
		if _, seen := env[name]; !seen {
			order = append(order, name)
		}
		env[name] = value
	}
	for _, names := range [][]string{base, fromHost} {
		for _, name := range names {
			if value, ok := os.LookupEnv(name); ok {
				set(name, value)
			}
		}
	}
	names := make([]string, 0, len(values))
	for name := range values {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		set(name, values[name])
	}
	out := make([]string, 0, len(order))
	for _, name := range order {
		out = append(out, name+"="+env[name])
	}
	return out
}
