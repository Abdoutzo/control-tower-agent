"""Turn an evaluation run into an animated GIF of the agent graph.

For each question, a token travels through the graph exactly as the agent did:
START -> agent (model call) -> tools (the tools it asked for) -> agent -> ...
-> END, with the score of the answer.

    python scripts/animate_run.py eval/results/<run>             # writes <run>/agent_flow.gif
    python scripts/animate_run.py eval/results/<run> --only Q01,Q09
    python scripts/animate_run.py eval/results/<run> --width 800 --speed 1.6 --out docs/agent_flow.gif
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from control_tower.agent_tools import ACTION_TOOL_NAMES, ALL_TOOLS  # noqa: E402
from control_tower.evaluation import load_questions, score  # noqa: E402

W, H = 1200, 675
BG, INK, MUTED, LINE = "#ffffff", "#1d2433", "#6b7385", "#c9cfda"
AGENT, TOOLS, APPROVAL, ENDC, ACTIVE, OK, KO = "#7a4fb5", "#2e8b57", "#d99a1e", "#1f5fbf", "#e4572e", "#2e8b57", "#c0392b"

NODES = {  # name: (center x, center y, half width, half height, colour, title, subtitle)
    "start": (330, 70, 70, 24, ENDC, "START", ""),
    "agent": (330, 200, 120, 44, AGENT, "agent", "le modèle choisit"),
    "tools": (125, 400, 105, 44, TOOLS, "tools", "les outils calculent"),
    "approval": (340, 400, 88, 44, APPROVAL, "approval", "validation humaine"),
    "end": (540, 400, 62, 24, ENDC, "END", ""),
}
EDGES = {  # (from, to): list of points
    ("start", "agent"): [(330, 94), (330, 156)],
    ("agent", "tools"): [(250, 244), (165, 356)],
    ("tools", "agent"): [(105, 356), (105, 200), (210, 200)],
    ("agent", "approval"): [(330, 244), (330, 356)],
    ("approval", "tools"): [(252, 400), (232, 400)],
    ("agent", "end"): [(410, 244), (522, 376)],
}


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    names = (["DejaVuSans-Bold.ttf", "arialbd.ttf"] if bold else ["DejaVuSans.ttf", "arial.ttf"])
    for name in names:
        for folder in ("/usr/share/fonts/truetype/dejavu/", "C:/Windows/Fonts/", ""):
            try:
                return ImageFont.truetype(folder + name, size)
            except OSError:
                continue
    return ImageFont.load_default()


F_TITLE, F_NODE, F_SMALL, F_TEXT, F_BIG = font(22, True), font(18, True), font(13), font(15), font(30, True)
F_NODE_SMALL = font(13, True)
TOOL_NAMES = [t.name for t in ALL_TOOLS]


def rounds(record: dict) -> list[list[str]]:
    """Tools grouped by the model call that asked for them."""
    steps = record.get("steps") or [{"tool": t} for t in record.get("tool_calls", [])]
    if steps and all("call" in s for s in steps):
        out: dict[int, list[str]] = {}
        for s in steps:
            out.setdefault(s["call"], []).append(s["tool"])
        return list(out.values())
    # Older runs did not record the grouping: rebuild it from the number of model calls.
    groups: list[list[str]] = []
    for s in steps:
        if groups and groups[-1][-1] == s["tool"]:
            groups[-1].append(s["tool"])
        else:
            groups.append([s["tool"]])
    target = max(1, record.get("llm_calls", len(groups) + 1) - 1)
    while len(groups) > target:
        groups[0:2] = [groups[0] + groups[1]]
    return groups


def arrow(d: ImageDraw.ImageDraw, pts, colour, width):
    d.line(pts, fill=colour, width=width, joint="curve")
    (x1, y1), (x2, y2) = pts[-2], pts[-1]
    a = math.atan2(y2 - y1, x2 - x1)
    tip = [(x2, y2), (x2 - 12 * math.cos(a - 0.4), y2 - 12 * math.sin(a - 0.4)),
           (x2 - 12 * math.cos(a + 0.4), y2 - 12 * math.sin(a + 0.4))]
    d.polygon(tip, fill=colour)


def point_on(pts, t: float):
    seg = [math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    goal = t * sum(seg)
    for i, length in enumerate(seg):
        if goal <= length or i == len(seg) - 1:
            f = goal / length if length else 0
            return (pts[i][0] + f * (pts[i + 1][0] - pts[i][0]), pts[i][1] + f * (pts[i + 1][1] - pts[i][1]))
        goal -= length


def frame(q: dict, rec: dict, log: list[tuple[str, str]], active_node: str | None = None,
          active_edge: tuple | None = None, dot=None, tools_label: list[str] | None = None,
          counter: str = "", footer: str = "", verdict: tuple[str, str] | None = None, used: set | None = None) -> Image.Image:
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    d.text((30, 18), "Control Tower Agent : parcours de chaque question dans le graphe", font=F_TITLE, fill=INK)
    for e, pts in EDGES.items():
        on = e == active_edge
        arrow(d, pts, ACTIVE if on else LINE, 5 if on else 3)
    d.text((490, 262), "aucun outil :\nréponse finale", font=F_SMALL, fill=MUTED)
    d.text((14, 270), "retour\naprès outil", font=F_SMALL, fill=MUTED)
    for name, (x, y, hw, hh, colour, title, sub) in NODES.items():
        on = name == active_node
        box = [x - hw, y - hh, x + hw, y + hh]
        radius = hh if name in ("start", "end") else 10
        d.rounded_rectangle(box, radius=radius, fill=colour if on else "#f6f7fa", outline=colour, width=4 if on else 2)
        tc = "#ffffff" if on else colour
        tw = d.textlength(title, font=F_NODE)
        d.text((x - tw / 2, y - (16 if sub else 11)), title, font=F_NODE, fill=tc)
        if sub:
            sw = d.textlength(sub, font=F_SMALL)
            d.text((x - sw / 2, y + 8), sub, font=F_SMALL, fill="#ffffff" if on else MUTED)
    if dot:
        d.ellipse([dot[0] - 9, dot[1] - 9, dot[0] + 9, dot[1] + 9], fill=ACTIVE, outline="#ffffff", width=2)
    # The "tools" node holds every tool: show the whole toolbox, light up the ones running.
    running = {t: tools_label.count(t) for t in tools_label} if tools_label else {}
    d.text((20, 458), f"Boîte à outils du nœud tools ({len(TOOL_NAMES)} outils) : "
           "le modèle choisit lesquels lancer", font=F_SMALL, fill=MUTED)
    for i, name in enumerate(TOOL_NAMES):
        x, y = 20 + (i // 7) * 300, 480 + (i % 7) * 23
        action = name in ACTION_TOOL_NAMES
        if name in running:
            fill, outline, colour, f = TOOLS, TOOLS, "#ffffff", F_NODE_SMALL
        elif name in (used or set()):
            fill, outline, colour, f = "#e7f5ec", TOOLS, INK, F_SMALL
        else:
            fill, outline, colour, f = "#ffffff", APPROVAL if action else LINE, MUTED, F_SMALL
        d.rounded_rectangle([x, y, x + 285, y + 19], radius=5, fill=fill, outline=outline)
        label = name + (f"  ×{running[name]}" if running.get(name, 0) > 1 else "")
        label += "  (validation)" if action else ""
        d.text((x + 7, y + 2), label, font=f, fill=colour)
    if counter:
        d.text((462, 182), counter, font=F_TEXT, fill=AGENT)

    # right panel
    d.rounded_rectangle([640, 60, 1180, 632], radius=12, fill="#f6f7fa", outline=LINE)
    d.text((660, 75), f"{q['id']}  ·  {q['category']}", font=F_NODE, fill=ENDC)
    y = 105
    for line in textwrap.wrap(q["question"], 52)[:3]:
        d.text((660, y), line, font=F_TEXT, fill=INK)
        y += 21
    y += 10
    d.line([660, y, 1160, y], fill=LINE, width=1)
    y += 12
    for who, text in log[-12:]:
        colour = {"agent": AGENT, "tools": TOOLS, "end": ENDC, "start": ENDC, "approval": APPROVAL}.get(who, INK)
        d.text((660, y), who, font=F_SMALL, fill=colour)
        for k, line in enumerate(textwrap.wrap(text, 52)[:2]):
            d.text((740, y + k * 17), line, font=F_SMALL, fill=INK)
        y += 20 + 17 * (min(2, len(textwrap.wrap(text, 52))) - 1) + 6
    if verdict:
        label, colour = verdict
        d.rounded_rectangle([660, 565, 1160, 618], radius=10, fill=colour)
        tw = d.textlength(label, font=F_NODE)
        d.text((910 - tw / 2, 581), label, font=F_NODE, fill="#ffffff")
    if footer:
        d.text((30, 645), footer, font=F_SMALL, fill=MUTED)
    return im


def animate(q, rec, index, total, frames, durations):
    footer = f"question {index}/{total}  ·  données simulées  ·  {rec.get('_model', '')}"
    if not all("call" in s for s in rec.get("steps") or [{}]):
        footer += "  ·  regroupement par appel reconstruit"
    log: list[tuple[str, str]] = []
    used: set[str] = set()

    def hold(ms, **kw):
        frames.append(frame(q, rec, list(log), footer=footer, used=set(used), **kw))
        durations.append(ms)

    def travel(edge, **kw):
        for t in (0.0, 0.33, 0.66, 1.0):
            frames.append(frame(q, rec, list(log), active_edge=edge, dot=point_on(EDGES[edge], t), footer=footer,
                                used=set(used), **kw))
            durations.append(90)

    hold(900, active_node="start")
    travel(("start", "agent"))
    for n, group in enumerate(rounds(rec), 1):
        log.append(("agent", f"appel au modèle n°{n} : il demande {', '.join(group)}"))
        hold(1300, active_node="agent", counter=f"appel au modèle n°{n}")
        actions = [t for t in group if t in ACTION_TOOL_NAMES]
        if actions:  # deliverables wait for a human: the graph pauses in "approval"
            travel(("agent", "approval"), counter=f"appel au modèle n°{n}")
            log.append(("approval", f"pause : {', '.join(actions)} attend la validation humaine "
                                    "(approuvé pendant l'évaluation)"))
            hold(1900, active_node="approval", counter=f"appel au modèle n°{n}")
            travel(("approval", "tools"), counter=f"appel au modèle n°{n}")
        else:
            travel(("agent", "tools"), counter=f"appel au modèle n°{n}")
        log.append(("tools", f"{len(group)} outil(s) exécuté(s), résultats renvoyés au modèle"))
        hold(1700, active_node="tools", tools_label=group, counter=f"appel au modèle n°{n}")
        used.update(group)
        travel(("tools", "agent"))
    n_final = len(rounds(rec)) + 1
    log.append(("agent", f"appel au modèle n°{n_final} : plus d'outil demandé, il rédige la réponse"))
    hold(1300, active_node="agent", counter=f"appel au modèle n°{n_final}")
    travel(("agent", "end"))
    passed, pct = _verdict(q, rec)
    log.append(("end", f"réponse en {rec.get('seconds', 0):.0f} s, {rec.get('llm_calls', n_final)} appels au modèle"))
    verdict = (f"{'réussi' if passed else 'échoué'}  ·  score {pct:.0%}", OK if passed else KO)
    hold(2600, active_node="end", verdict=verdict)


def _verdict(q: dict, rec: dict) -> tuple[bool, float]:
    if rec.get("answer"):
        s = score(q, rec)
        return s["passed"], s["score"]
    return rec.get("_passed", False), rec.get("_score", 0.0)


def summary_frames(questions: list[dict], results: dict, title: str, per_page: int = 8) -> list[Image.Image]:
    """One table per page: each question, the tools of each round, calls, time, verdict."""
    pages = []
    for start in range(0, len(questions), per_page):
        im = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(im)
        d.text((30, 18), title, font=F_TITLE, fill=INK)
        d.text((30, 52), "Chaque crochet = un appel au modèle qui a demandé des outils ; ensuite il rédige la réponse.",
               font=F_SMALL, fill=MUTED)
        y = 85
        for q in questions[start:start + per_page]:
            rec = results[q["id"]]
            passed, pct = _verdict(q, rec)
            path = "  →  ".join("[" + ", ".join(f"{t} ×{g.count(t)}" if g.count(t) > 1 else t
                                                for t in dict.fromkeys(g)) + "]" for g in rounds(rec))
            d.rounded_rectangle([30, y, 1170, y + 64], radius=8, fill="#f6f7fa", outline=LINE)
            d.text((45, y + 8), q["id"], font=F_NODE, fill=ENDC)
            d.text((110, y + 10), textwrap.shorten(q["question"], 80, placeholder="..."), font=F_TEXT, fill=INK)
            d.text((110, y + 36), textwrap.shorten(path or "aucun outil", 110, placeholder="..."), font=F_SMALL,
                   fill=TOOLS)
            d.text((900, y + 36), f"{rec.get('llm_calls', 0)} appels · {rec.get('seconds', 0):.0f} s", font=F_SMALL,
                   fill=MUTED)
            d.rounded_rectangle([1040, y + 14, 1158, y + 48], radius=8, fill=OK if passed else KO)
            label = f"{pct:.0%}"
            d.text((1099 - d.textlength(label, font=F_NODE) / 2, y + 20), label, font=F_NODE, fill="#ffffff")
            y += 72
        pages.append(im)
    return pages


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--only", help="comma-separated question ids")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--width", type=int, help="resize to this width (smaller file, e.g. 800 for a README)")
    parser.add_argument("--speed", type=float, default=1.0, help="play faster (e.g. 1.5) to shorten the GIF")
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    rows = [json.loads(x) for x in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    results = {r["id"]: r for r in rows}
    questions = [q for q in load_questions() if q["id"] in results]
    if args.only:
        wanted = {x.strip() for x in args.only.split(",")}
        questions = [q for q in questions if q["id"] in wanted]

    frames: list[Image.Image] = []
    durations: list[int] = []
    model = f"{meta['provider']} / {meta['model']}"
    for page in summary_frames(questions, results, f"{len(questions)} questions évaluées : le parcours de chacune "
                                                   f"({meta['model'].split('/')[-1]})"):
        frames.append(page)
        durations.append(4000)
    for i, q in enumerate(questions, 1):
        rec = {**results[q["id"]], "_model": model}
        animate(q, rec, i, len(questions), frames, durations)
    n_pass = sum(_verdict(q, results[q["id"]])[0] for q in questions)
    for page in summary_frames(questions, results, f"Bilan : {n_pass}/{len(questions)} questions réussies"):
        frames.append(page)
        durations.append(7000)
    durations = [max(60, int(d / args.speed)) for d in durations]
    if args.width:
        size = (args.width, round(H * args.width / W))
        small = [f.resize(size, Image.LANCZOS) for f in frames]
        # One shared palette, no dithering: unchanged pixels stay identical between frames,
        # so the GIF only stores what moves (much smaller file).
        sample = small[::max(1, len(small) // 40)]
        montage = Image.new("RGB", (size[0], size[1] * (len(sample) + 1)), BG)
        for k, f in enumerate(sample):
            montage.paste(f, (0, k * size[1]))
        # Make sure every theme colour survives, even ones seen in few frames (approval, failed).
        swatch = ImageDraw.Draw(montage)
        for k, colour in enumerate((AGENT, TOOLS, APPROVAL, ENDC, ACTIVE, OK, KO, "#e7f5ec", INK, MUTED)):
            swatch.rectangle([k * 70, len(sample) * size[1], k * 70 + 69, (len(sample) + 1) * size[1]], fill=colour)
        palette = montage.quantize(colors=64, method=Image.Quantize.MEDIANCUT)
        frames = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in small]
    out = args.out or run_dir / "agent_flow.gif"
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=durations, loop=0, optimize=True)
    print(f"{len(frames)} frames, {sum(durations) / 1000:.0f} s -> {out}")


if __name__ == "__main__":
    main()
