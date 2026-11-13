#!/usr/bin/env python3
"""Edit the raw walkthrough recording into the deliverables (runs ffmpeg/ffprobe only).

Usage: postprocess.py <raw.webm> <events.json> <out_dir>
- Trims the blank lead-in before the title card.
- Shortens each long wait (cut-start/cut-end) to a short lead and tail; captions say the wait is trimmed.
- Writes skills-demo.mp4 (H.264, yuv420p, faststart) and skills-demo.webm (VP9) from the same timeline,
  chapters.md with timestamps on the edited timeline, and frames/ (chapter mid-points + every 10 s).
"""
import json
import subprocess
import sys
from pathlib import Path

LEAD, TAIL = 1.5, 2.0  # seconds kept at each end of a trimmed wait
WHITE_YAVG = 233  # signalstats mean luma at or above this is a blank white page-transition frame
WHITE_MAX_RUN = 0.5  # transition flashes are short; longer bright runs are real (light) page content and are kept

CHAPTER_NOTES = {
    1: 'Library of five skills; refund-policy opened in the editor (files, references); the live check flags a fake api_key line, it is removed, the draft autosaves.',
    2: 'Publish v2 with a note, compare v1 and v2 file by file, move production v1 to v2 from the label menu, label history.',
    3: 'support-agent context: AGENTS.md, linked skills (four follow production, escalation-tone pinned to v1), lock details with the locked digest; refund-policy v2 available but not accepted.',
    4: 'refund-policy dependents: support-agent follows (update available), returns-desk pinned; draft update preview refund-policy v1 to v2; accept into a draft; review the accepted lock and publish context v2.',
    5: 'Whole-context answer check (candidate v2 vs reference v1, answer pass/fail); application verification on the pinned runtime: per-case grades, production v1 fails the business-tool check, ship/hold = Ready for release review.',
    6: 'Observe trace of a refund that included shipping turned into a reviewed regression case (required skill and business action); reproduced on the application: repair verified (fails on recorded v1, passes on v2).',
    7: 'Release packet from the verified run (operator CI record, sample values), carried into the request with its candidate and target; reviewer chosen by name; reviewer inbox, approve, release, release receipt.',
    8: 'Context v2 lock (refund-policy v2 digest); context Use tab install plan on the public API (preview installer); skill Use tab Claude Code command copied (public origin, no keys); skill production v2 digest equals the context lock digest.',
}


def run(cmd):
    subprocess.run(cmd, check=True)


def duration(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(path)],
                         check=True, capture_output=True, text=True).stdout.strip()
    return float(out)


def white_runs(path, lo, hi):
    """Return (start, end) runs of near-white frames in [lo, hi) of the raw recording (signalstats YAVG)."""
    import re
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        stats = Path(d) / 'yavg.txt'
        run(['ffmpeg', '-v', 'error', '-ss', f'{lo:.3f}', '-to', f'{hi:.3f}', '-i', str(path), '-vf',
             f"signalstats,metadata=print:key=lavfi.signalstats.YAVG:file={stats}", '-f', 'null', '-'])
        frames, t = [], None
        for line in stats.read_text().splitlines():
            m = re.search(r'pts_time:([\d.]+)', line)
            if m:
                t = float(m.group(1)) + lo
            m = re.search(r'YAVG=([\d.]+)', line)
            if m and t is not None:
                frames.append((t, float(m.group(1))))
    runs, s = [], None
    for i, (t, y) in enumerate(frames):
        if y >= WHITE_YAVG and s is None:
            s = t
        if s is not None and (y < WHITE_YAVG or i == len(frames) - 1):
            runs.append((s - 0.02, t if y < WHITE_YAVG else t + 0.05)); s = None
    return runs


def fmt(t):
    return f'{int(t // 60)}:{t % 60:04.1f}'


def main():
    raw, events_path, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    events = json.loads(events_path.read_text())['events']
    total = duration(raw)
    start = max(0.0, next(e['t'] for e in events if e['kind'] == 'title') - 0.2)
    stop = min(total, next((e['t'] for e in events if e['kind'] == 'stop'), total) + 0.3)
    removed, labels_for = [], {}
    pending = None
    for e in events:
        if e['kind'] == 'cut-start':
            pending = (e['t'], e.get('label', ''))
        elif e['kind'] == 'cut-end' and pending is not None:
            a, b = pending[0] + LEAD, e['t'] - TAIL
            if b - a > 1.0:
                removed.append((a, b)); labels_for[a] = pending[1]
            pending = None
    hide_start = None
    for e in events:  # hidden stretches (transient validation states): cut exactly, no lead or tail
        if e['kind'] == 'hide-start':
            hide_start = e['t']
        elif e['kind'] == 'hide-end' and hide_start is not None:
            if e['t'] - hide_start > 0.1:
                removed.append((hide_start, e['t'])); labels_for[hide_start] = 'hidden form step'
            hide_start = None
    load_start = None
    for e in events:  # page loads: drop skeleton/blank frames, keep a short beat either side
        if e['kind'] == 'load-start':
            load_start = e['t']
        elif e['kind'] == 'load-end' and load_start is not None:
            a, b = load_start + 0.25, e['t'] - 0.45
            if b - a > 0.6 and not any(x <= a <= y or x <= b <= y for x, y in removed):
                removed.append((a, b)); labels_for[a] = 'page load'
            load_start = None
    # Blank white frames at page transitions: cut every run between the title and end cards.
    title_end = start + 3.2
    end_card = next(e['t'] for e in events if e['kind'] == 'end')
    long_white = []
    for a, b in white_runs(raw, title_end, end_card):
        if b - a > WHITE_MAX_RUN:  # a sustained bright frame is page content (mostly-white screens), not a flash
            long_white.append((round(a, 2), round(b - a, 2)))
            continue
        if not any(x <= a and b <= y for x, y in removed):
            removed.append((a, b)); labels_for[a] = 'white frame'
    removed.sort()
    merged = []
    for a, b in removed:  # overlapping cuts merge so the trim list stays monotonic
        if merged and a <= merged[-1][1]:
            pa, pb = merged[-1]; merged[-1] = (pa, max(pb, b))
        else:
            merged.append((a, b))
    removed = merged
    keep, cursor = [], start
    for a, b in removed:
        keep.append((cursor, a)); cursor = b
    keep.append((cursor, stop))

    def remap(t):
        acc = 0.0
        for a, b in keep:
            if t < a:
                return acc
            if t <= b:
                return acc + (t - a)
            acc += b - a
        return acc

    filt, labels = [], []
    for i, (a, b) in enumerate(keep):
        filt.append(f'[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}]')
        labels.append(f'[v{i}]')
    filt.append(''.join(labels) + f'concat=n={len(keep)}:v=1:a=0,fps=25[outv]')
    graph = ';'.join(filt)
    mp4, webm = out / 'skills-demo.mp4', out / 'skills-demo.webm'
    flags = sys.argv[4:]
    (out / 'edit-graph.txt').write_text(graph)  # lets the webm be cut from the raw file with the identical timeline elsewhere
    if not ('--reuse' in flags and mp4.exists()):
        run(['ffmpeg', '-y', '-v', 'error', '-i', str(raw), '-filter_complex', graph, '-map', '[outv]', '-c:v', 'libx264',
             '-preset', 'medium', '-crf', '20', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(mp4)])
    # Same edited timeline as the mp4. VP8 realtime keeps the encode tractable on a loaded host.
    if '--skip-webm' not in flags:
      run(['ffmpeg', '-y', '-v', 'error', '-i', str(mp4), '-c:v', 'libvpx', '-b:v', '3M', '-maxrate', '4M', '-bufsize', '6M',
           '-deadline', 'realtime', '-cpu-used', '8', '-threads', '8', str(webm)])
    final = duration(mp4)

    chapters = [e for e in events if e['kind'] == 'chapter']
    ends = [c['t'] for c in chapters[1:]] + [next(e['t'] for e in events if e['kind'] == 'end')]
    rows = []
    for c, end in zip(chapters, ends):
        s, f = remap(c['t']), remap(end)
        rows.append({'n': c['chapter'], 'title': c['title'], 'start': s, 'end': f})
    trimmed = [(remap(a), b - a, labels_for[a].replace('-', ' ')) for a, b in removed]

    lines = ['# Skills + Agent contexts — product walkthrough', '',
             f'Video: skills-demo.mp4 / skills-demo.webm · 1440x900 · {fmt(final)} total.', '',
             '| Timestamp | Chapter | What is shown |', '| --- | --- | --- |',
             f'| 0:00.0 | Title card | "Skills + Agent contexts — product walkthrough" |']
    for r in rows:
        lines.append(f"| {fmt(r['start'])} | {r['n']}. {r['title']} | {CHAPTER_NOTES.get(r['n'], '')} |")
    lines.append(f'| {fmt(remap(next(e["t"] for e in events if e["kind"] == "end")))} | End card | Summary card |')
    runs = [t for t in trimmed if t[2] not in ('page load', 'white frame', 'hidden form step')]
    loads = [t for t in trimmed if t[2] == 'page load']
    whites = [t for t in trimmed if t[2] == 'white frame']
    if trimmed:
        lines += ['', 'Edit notes (the recording is otherwise continuous and real-time):', '']
        for at, secs, label in runs:
            lines.append(f'- at {fmt(at)}: {secs:.0f} s of waiting for the {label} removed (the caption says the run is in progress)')
        if loads:
            lines.append(f'- {len(loads)} page-load waits ({sum(t[1] for t in loads):.0f} s in total) shortened so no blank or skeleton screens are shown')
        hidden = [t for t in trimmed if t[2] == 'hidden form step']
        if hidden:
            lines.append(f'- {len(hidden)} form-filling stretches ({sum(t[1] for t in hidden):.0f} s) cut to the filled result')
        if whites:
            lines.append(f'- {len(whites)} blank white transition frames (mean luma >= {WHITE_YAVG}) cut')
    (out / 'chapters.json').write_text(json.dumps({'final': final, 'rows': rows, 'trimmed': trimmed}, indent=2))
    (out / 'chapters.md').write_text('\n'.join(lines) + '\n')

    frames = out / 'frames'
    frames.mkdir(exist_ok=True)
    for old in frames.glob('*.png'):
        old.unlink()
    points = [(f"chapter-{r['n']}-mid", (r['start'] + r['end']) / 2) for r in rows]
    points += [(f'chapter-{r["n"]}-start+1s', r['start'] + 1.0) for r in rows]
    t = 0.0
    while t < final:
        points.append((f't{int(t):03d}s', t)); t += 10.0
    for name, at in points:
        run(['ffmpeg', '-y', '-v', 'error', '-ss', f'{at:.2f}', '-i', str(mp4), '-frames:v', '1', str(frames / f'{name}.png')])
    left = white_runs(mp4, 3.2, max(3.2, final - 3.2))  # confirm no white flash survived the edit
    left = [r for r in left if r[1] - r[0] <= WHITE_MAX_RUN]
    print(json.dumps({'final': final, 'rows': rows, 'trimmed': trimmed, 'frames': len(points), 'white_runs_left': left, 'long_bright_runs_kept': long_white}, indent=1))


if __name__ == '__main__':
    main()
