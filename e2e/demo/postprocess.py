#!/usr/bin/env python3
"""Edit the raw walkthrough recording into the deliverables (runs ffmpeg/ffprobe only).

Usage: postprocess.py <raw.webm> <events.json> <out_dir>
- Trims the blank lead-in before the title card.
- Shortens each long wait (cut-start/cut-end) to a short lead and tail; captions say the wait is trimmed.
- Cuts hidden form steps, page loads and every stretch in which a loading placeholder (skeleton, busy or
  "Loading" region) was on screen (ph-start/ph-end, reported by the page itself).
- Renders the captions in a band below the 1440x810 app frame (ASS subtitles), so they never cover the UI,
  and reports any caption held for less than MIN_CAPTION seconds of the edited video.
- Writes skills-demo.mp4 (H.264, yuv420p, faststart) and skills-demo.webm (VP8) from the same timeline,
  chapters.md with timestamps on the edited timeline, frames/ (chapter mid-points + every 10 s) and
  contact/ (one frame per second, tiled) for the frame audit.
"""
import json
import subprocess
import sys
from pathlib import Path

LEAD, TAIL = 1.0, 1.5  # seconds kept at each end of a trimmed wait
BAND = 90  # caption band height below the app frame
MIN_CAPTION = 4.0  # seconds every caption should stay on screen in the edited video
PH_MAX = 12.0  # a placeholder visible longer than this is reported, not cut (it would be real content)
WHITE_YAVG = 233  # signalstats mean luma at or above this is a blank white page-transition frame
WHITE_MAX_RUN = 1.5  # transition flashes last well under 1.5 s; longer bright runs would be real page content and are kept

CHAPTER_NOTES = {
    1: 'Library of five skills; refund-policy opened in the editor (files, references); v2 policy pasted (item price only, never shipping); the live check flags a fake api_key line, it is removed, the draft autosaves.',
    2: 'Publish v2, compare v1 and v2, move the skill production label v1 to v2 (the dialog shows no skill-level tests; the caption says contexts stay on v1 until they test and accept v2), label history.',
    3: 'support-agent context: AGENTS.md, linked skills (four follow production, escalation-tone pinned to v1); refund-policy v2 available but not accepted.',
    4: 'refund-policy dependents (support-agent follows, returns-desk pinned); update preview refund-policy v1 to v2; accept into a draft; review the linked skills and publish context v2.',
    5: 'Whole-context answer check (candidate v2 vs reference v1, expected "$25"): v2 passes, v1 fails; application verification on the demo application: "v2 passed 3 of 3 cases.", Ready for release review; per-case grades show v1 failing answer and business tool on the damaged-mug refund.',
    6: 'Observe trace of a refund that included shipping turned into a reviewed regression case (refund-policy, Issue a refund, answer must contain "$24"); reproduced on the application: "Repair verified: v2 passed 1 of 1 case; the recorded failure reproduced on v1."',
    7: 'Release packet from the verified run, carried into the request; the evidence row shows the run\'s own outcomes (repair verified, per-case lines); reviewer chosen by name; "Signed in as Priya Shah" card; reviewer sees the same outcomes, approves, releases; release receipt.',
    8: 'Context v2 lock pins refund-policy v2 (digest highlighted); skill Use tab, Application > cURL command copied (public API origin, no keys); terminal frame: the copied command run during the take returns refund-policy v2 with the same digest.',
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


def ass_time(t):
    cs = int(round(t * 100))
    return f'{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}'


def ass_text(text):
    return text.replace('\\', '\\\\').replace('{', '(').replace('}', ')').replace('\n', ' ')


def write_captions(path, captions, width, height):
    """Captions in the band below the app frame: a purple chapter chip, then one line of text."""
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,DejaVu Sans,25,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,2,40,40,{(BAND - 25) // 2 + 2},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    rows = []
    for c in captions:
        chip = c['chip'].upper()
        text = (r'{\b1\fs17\c&HFF9BA9&}' + ass_text(chip) + r'{\b0\fs25\c&HFFFFFF&}    ' if chip else '') + ass_text(c['text'])
        rows.append(f"Dialogue: 0,{ass_time(c['start'])},{ass_time(c['end'])},Cap,,0,0,0,,{text}")
    Path(path).write_text(head + '\n'.join(rows) + '\n')


def main():
    raw, events_path, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    doc = json.loads(events_path.read_text())
    events = doc['events']
    vw, vh = doc.get('viewport', {}).get('width', 1440), doc.get('viewport', {}).get('height', 810)
    total = duration(raw)
    start = max(0.0, next(e['t'] for e in events if e['kind'] == 'title') - 0.2)
    stop = min(total, next((e['t'] for e in events if e['kind'] == 'stop'), total) + 0.3)
    removed, labels_for = [], {}

    def pairs(kind):
        opened = None
        for e in events:
            if e['kind'] == f'{kind}-start' and opened is None:
                opened = e
            elif e['kind'] == f'{kind}-end' and opened is not None:
                yield opened, e
                opened = None
        if opened is not None:
            yield opened, {'t': stop}

    for a, b in pairs('cut'):  # long waits: keep a short lead and tail; the caption says the run is in progress
        x, y = a['t'] + LEAD, b['t'] - TAIL
        if y - x > 1.0:
            removed.append((x, y)); labels_for[x] = a.get('label', 'run')
    for a, b in pairs('quick'):  # navigation between shots: jump-cut, keeping a short beat at each end
        x, y = a['t'] + 0.3, b['t'] - 0.3
        if y - x > 0.3:
            removed.append((x, y)); labels_for[x] = 'jump cut'
    for a, b in pairs('hide'):  # hidden stretches (form filling, consent ticks): cut exactly
        if b['t'] - a['t'] > 0.1:
            removed.append((a['t'], b['t'])); labels_for[a['t']] = 'hidden form step'
    for a, b in pairs('load'):  # page loads: drop skeleton/blank frames, keep a short beat either side
        x, y = a['t'] + 0.25, b['t'] - 0.45
        if y - x > 0.3:
            removed.append((x, y)); labels_for[x] = 'page load'
    long_placeholders = []
    for a, b in pairs('ph'):  # loading placeholders the page reported, wherever they appeared
        x, y = a['t'] - 0.08, b['t'] + 0.12
        if y - x > PH_MAX:
            long_placeholders.append((round(a['t'], 2), round(b['t'] - a['t'], 2), a.get('label', '')))
            continue
        removed.append((x, y)); labels_for[x] = 'placeholder'
    # Blank white frames at page transitions: cut every run between the title and end cards.
    title_end = start + 3.2
    end_card = next(e['t'] for e in events if e['kind'] == 'end')
    long_white = []
    for a, b in white_runs(raw, title_end, end_card):
        if b - a > WHITE_MAX_RUN:  # a sustained bright frame is page content (mostly-white screens), not a flash
            long_white.append((round(a, 2), round(b - a, 2)))
            continue
        removed.append((a, b)); labels_for.setdefault(a, 'white frame')
    removed.sort()
    merged = []
    for a, b in removed:  # overlapping cuts merge so the trim list stays monotonic
        if merged and a <= merged[-1][1]:
            pa, pb = merged[-1]; merged[-1] = (pa, max(pb, b))
        else:
            merged.append((a, b))
            labels_for.setdefault(a, 'cut')
    removed = merged
    keep, cursor = [], start
    for a, b in removed:
        if a > cursor:
            keep.append((cursor, a))
        cursor = max(cursor, b)
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

    caps = [e for e in events if e['kind'] == 'caption']
    boundaries = [e['t'] for e in events if e['kind'] in ('end', 'stop')]
    captions = []
    for i, c in enumerate(caps):
        nxt = caps[i + 1]['t'] if i + 1 < len(caps) else min(boundaries or [stop])
        s0, s1 = remap(c['t']), remap(nxt)
        captions.append({'chip': c.get('chip', ''), 'text': c['label'], 'start': s0, 'end': s1, 'held': round(s1 - s0, 2)})
    short = [(fmt(c['start']), c['held'], c['text']) for c in captions if c['held'] < MIN_CAPTION]
    write_captions(out / 'captions.ass', captions, vw, vh + BAND)

    filt, labels = [], []
    for i, (a, b) in enumerate(keep):
        filt.append(f'[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}]')
        labels.append(f'[v{i}]')
    filt.append(''.join(labels) + f'concat=n={len(keep)}:v=1:a=0,fps=25,pad={vw}:{vh + BAND}:0:0:color=0x111118,'
                f"subtitles={out / 'captions.ass'}[outv]")
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
    ends = [c['t'] for c in chapters[1:]] + [end_card]
    rows = []
    for c, end in zip(chapters, ends):
        rows.append({'n': c['chapter'], 'title': c['title'], 'start': remap(c['t']), 'end': remap(end)})
    trimmed = [(remap(a), b - a, labels_for.get(a, 'cut').replace('-', ' ')) for a, b in removed]

    lines = ['# Skills + Agent contexts — product walkthrough', '',
             f'Video: skills-demo.mp4 / skills-demo.webm · {vw}x{vh + BAND} (app frame {vw}x{vh}, caption band {BAND} px) · {fmt(final)} total.', '',
             '| Timestamp | Chapter | What is shown |', '| --- | --- | --- |',
             '| 0:00.0 | Title card | "Skills + Agent contexts"; disclosure: demo workspace with sample data, model responses simulated, demo application is a test fixture |']
    for r in rows:
        lines.append(f"| {fmt(r['start'])} | {r['n']}. {r['title']} | {CHAPTER_NOTES.get(r['n'], '')} |")
    lines.append(f'| {fmt(remap(end_card))} | End card | Summary card |')
    groups = {}
    for at, secs, label in trimmed:
        key = label if label in ('page load', 'white frame', 'hidden form step', 'placeholder', 'cut', 'jump cut') else 'run'
        groups.setdefault(key, []).append((at, secs, label))
    lines += ['', 'Edit notes (the recording is otherwise continuous and real-time):', '']
    for at, secs, label in groups.get('run', []):
        lines.append(f'- at {fmt(at)}: {secs:.0f} s of waiting for the {label} removed (lead and tail kept)')
    names = {'page load': 'page-load waits', 'placeholder': 'stretches with a loading placeholder (skeleton, busy or "Loading" region) on screen',
             'hidden form step': 'form-filling and consent-tick stretches cut to the filled result', 'white frame': 'blank white transition frames (mean luma >= 233)',
             'jump cut': 'navigation stretches (menus, tabs, list rows) jump-cut to a short beat at each end', 'cut': 'other merged cuts'}
    for key in ('page load', 'placeholder', 'hidden form step', 'jump cut', 'white frame', 'cut'):
        if groups.get(key):
            lines.append(f'- {len(groups[key])} {names[key]} ({sum(t[1] for t in groups[key]):.0f} s in total) removed')
    lines += ['', f'Captions: {len(captions)}, each one line, shown in the band below the app frame; '
              f'shortest hold {min(c["held"] for c in captions):.1f} s.' if captions else '']
    (out / 'chapters.json').write_text(json.dumps({'final': final, 'rows': rows, 'trimmed': trimmed, 'captions': captions}, indent=2))
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
    # Frame audit: every second of the edited video, tiled 4x3 per sheet with timestamps burned in.
    contact = out / 'contact'
    contact.mkdir(exist_ok=True)
    for old in contact.glob('*.jpg'):
        old.unlink()
    run(['ffmpeg', '-y', '-v', 'error', '-i', str(mp4), '-vf',
         "fps=1,drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:text='%{pts\\:hms}':x=10:y=10:fontsize=34:fontcolor=yellow:box=1:boxcolor=black@0.7,scale=720:-1,tile=4x3",
         '-q:v', '4', str(contact / 'sheet-%02d.jpg')])
    left = white_runs(mp4, 3.2, max(3.2, final - 3.2))  # confirm no white flash survived the edit
    left = [r for r in left if r[1] - r[0] <= WHITE_MAX_RUN]
    print(json.dumps({'final': final, 'rows': rows, 'trimmed_count': len(trimmed), 'short_captions': short, 'long_placeholders_kept': long_placeholders,
                      'white_runs_left': left, 'long_bright_runs_kept': long_white}, indent=1))


if __name__ == '__main__':
    main()
