## Show, Don't Label (REQUIRED): center stage + corner analogies
The plan contains scenes whose description starts with "VISUAL AID:". Each is a corner analogy moment: the narration names a concrete quantity or structure from the paper, the screen DRAWS it, and a small analogy animation in a corner explains it while the center stage highlights the matching part. The picture carries the explanation; the narration only names what the viewer is already seeing. Implement every "VISUAL AID:" scene as geometry, synchronized with the voiceover phrases that mention it, inside their `with self.voiceover(text="...") as tracker:` blocks.

### Layout convention (Manim frame is about 14.2 wide x 8 tall)
- CENTER STAGE: a box of about 7.5 x 5.0 centered at `ORIGIN`. The real mechanism diagram lives here for the whole video. It is never cleared: only add to it and highlight it (`Indicate`, `Circumscribe`, or `.animate.set_stroke(YELLOW, width=6)` then back). Keep every center-stage mobject inside x in [-3.75, 3.75], y in [-2.5, 2.5].
- FOUR CORNERS: boxes of about 3.2 x 2.0 anchored to the four corners of the safe area (`UL`, `UR`, `DL`, `DR`) with `buff` of at least 0.2 from the frame edge. Corners never overlap the center stage or each other. TL, TR and BL host the 3 analogy moments in the plan's order; BR hosts the final recap.
- Define the layout once as named constants and helpers at the top of `construct()` (for example `CENTER_W, CENTER_H = 7.5, 5.0`, `CORNER_W, CORNER_H = 3.2, 2.0`, and a `corner_anchor(direction)` helper), then place everything through them so no magic coordinates drift.
- A corner analogy pops in (`FadeIn(..., scale=0.5)` / `GrowFromCenter`), plays for 2-5 seconds, then shrinks to a small resting icon (about 0.8 x 0.5) in the same corner, which stays on screen as a legend. At the end the three resting icons gather into a tiny summary in the BR corner.
- While a corner analogy plays, the matching center-stage element pulses or glows in the SAME `self.play(...)` or the same voiceover block, so analogy and mechanism are on screen together.

### Pacing: a visible change every few words
- Split the narration into many short phrases of about 4-8 words, each in its own `with self.voiceover(text="...") as tracker:` block, and give EVERY phrase at least one animation (appear, move, grow, shrink, pulse, connect) with `run_time=tracker.duration` (or split into a few plays that add up to it). Never narrate over a still frame.
- Narration names what is moving ("this slab shrinks to a quarter") and never reads out on-screen text.

### Text budget (a static check enforces this)
- No sentences, bullets, `Paragraph`, `BulletedList` or `Title` cards. An optional concept name may flash for about 1 second at the start, as a `Text` of 3 words or fewer, then leaves.
- Each `Text` / `MarkupText` is 3 words or fewer; prefer numbers and symbols ("256", "64", "1x1", "+"). Whole video: 15 tag words or fewer in total (each `MathTex` and each purely numeric or symbolic tag counts as 1). Over budget means the code is sent back with the offending strings listed, so use shapes and motion instead.

### Vector vocabulary (Manim primitives only; no external images, no SVG or image files)
- Quantities: proportional sizes (slab thickness, bar length), a count of dots, grid sizes. Pick one scale factor and apply it to every slab; keep the widest slab inside its box.
- Operations: motion (shrink, expand, slide, merge, split), arrows for flow, a `Circle` with "+" or "x" for combining.
- Analogies: funnels (`Polygon`), roads and bridges (`Line`, `Arc`), pipes and valves, magnifying windows (`Square` + `Circle` handle sliding over a grid), stacks of cards, scales. Build them from `Polygon`, `Arc`, `Line`, `Dot`, `Circle`, `Rectangle`, `VGroup`.
- Kernels and filters are grids of cells: 1x1 is a single `Square`, 3x3 is `VGroup(*[Square(side_length=0.35) for _ in range(9)]).arrange_in_grid(3, 3, buff=0)`.
- A shortcut or data path is an `Arrow` or `CurvedArrow` that arcs AROUND the blocks it skips and ends at a `Circle` containing a "+" where it joins the main path.
- Color means the same thing everywhere (one color per quantity, in the center and in the corners) and is never the only cue: repeat it with shape or motion.
- Prefer simple primitives that render reliably at 480p. Avoid `Prism`, `Cube`, 3D scenes, and anything needing LaTeX packages beyond basics. Smallest element is about 0.25 units.

Keep every spatial, safe-area and LaTeX rule above. The non-"VISUAL AID:" scenes stay as they are.

### Layout and contrast rules (REQUIRED)
- Opening: if a concept-name title is shown, it appears ALONE for about 1 second, then `FadeOut`s (or moves with `.animate.to_edge(UP).scale(0.6)`) BEFORE any diagram element appears. No diagram mobject may overlap the title's region; a pinned title keeps the whole diagram below y = 2.5.
- Tag contrast: every `Text` / `MathTex` tag uses a high-contrast color (`WHITE` or a bright palette color such as `YELLOW`, `TEAL`, `ORANGE`). Never use `GREY`, any `DARK_*` color, or opacity below 0.8 for text. Use `font_size` of at least 28 for `Text` at 480p, and place each tag with `next_to(shape, DOWN, buff=0.2)` (buff of 0.15 or more) so it never touches a shape.
- Final composition: the finished center-stage diagram fills a reasonable part of its box (about 60-100% of 7.5 x 5.0) and stays centered; the four corner icons sit at the edges. Never shrink the whole detailed diagram into a tiny cluster.
- Repeated or stacked blocks ("this block repeats N times"): draw a few simplified block icons at legible size (for example 3 `RoundedRectangle` icons plus a "..." tag) instead of scaling the full detailed diagram down.
- Do NOT `FadeOut` the center stage between scenes (that would break the build-up); only a corner analogy's temporary parts are removed when it shrinks to its resting icon.

### Few-shot snippet: corner helper + phrase-paced voiceover pair (inside `construct`)
```python
CORNER_W, CORNER_H, EDGE_BUFF = 3.2, 2.0, 0.25

def corner_anchor(corner):
    # Center of a corner box kept EDGE_BUFF inside the frame; corner is UL, UR, DL or DR
    x = (config.frame_width / 2 - CORNER_W / 2 - EDGE_BUFF) * corner[0]
    y = (config.frame_height / 2 - CORNER_H / 2 - EDGE_BUFF) * corner[1]
    return np.array([x, y, 0])

slab = Rectangle(width=2.4, height=1.6, color=BLUE, fill_opacity=0.6).move_to(ORIGIN)
dots = VGroup(*[Dot(radius=0.06, color=YELLOW) for _ in range(12)]).arrange_in_grid(3, 4, buff=0.12)
dots.move_to(corner_anchor(UL))
self.play(FadeIn(slab))
with self.voiceover(text="A wide stream enters the funnel.") as tracker:
    self.play(FadeIn(dots, scale=0.5), Indicate(slab, color=YELLOW), run_time=tracker.duration)
with self.voiceover(text="It squeezes down to a quarter.") as tracker:
    self.play(dots.animate.scale(0.4).move_to(corner_anchor(UL)),
              slab.animate.stretch_to_fit_width(0.6), run_time=tracker.duration)
```
