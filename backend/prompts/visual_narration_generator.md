## Show, Don't Label (REQUIRED): center stage + corner analogies
The plan contains scenes whose description starts with "VISUAL AID:". Each is a corner analogy moment: the narration names a concrete quantity or structure from the paper, the screen DRAWS it, and an analogy animation that grows out of a corner explains it while the center stage highlights the matching part. The picture carries the explanation; the narration only names what the viewer is already seeing. Implement every "VISUAL AID:" scene as geometry, synchronized with the voiceover phrases that mention it, inside their `with self.voiceover(text="...") as tracker:` blocks.

### Layout convention (Manim frame is about 14.2 wide x 8 tall)
- CENTER STAGE: a box of 7.0 x 3.8 centered at `ORIGIN` (x in [-3.5, 3.5], y in [-1.9, 1.9]). The real mechanism diagram lives here for the whole video. It is never cleared: only add to it and highlight it (`Indicate`, `Circumscribe`, or `.animate.set_stroke(YELLOW, width=6)` then back). Keep every center-stage mobject inside that box.
- FOUR CORNERS (resting spots): boxes of 3.0 x 1.5 flush with the four corners of the base safe area x in [-6, 6], y in [-3.5, 3.5] (corner box centers at x = +/-4.5, y = +/-2.75; use `UL`, `UR`, `DL`, `DR` signs). They span |x| in [3.0, 6.0], |y| in [2.0, 3.5], so resting icons never overlap the center stage (y gap 0.1) or each other. TL, TR and BL host the 3 analogy moments in the plan's order; BR hosts the final recap.
- ZOOM-UP INSETS: while an analogy plays it is shown large, in a 4.7 x 2.7 inset flush with the same corner (inset centers at x = +/-3.65, y = +/-2.15; insets span |x| in [1.3, 6.0], |y| in [0.8, 3.5]). An inset never covers another corner's resting spot, insets of different corners never overlap, and no inset covers the middle band |y| < 0.8 of the center stage. Only the ONE active inset may cover the edge of the center stage, and only while it plays. The center-stage part it highlights stays at least 0.3 clear of that inset, even while it pulses (`Indicate` grows it 1.2x), so analogy and mechanism are both visible: TL inset, keep it right of x = -1.0 or below y = 0.5; TR, left of x = 1.0 or below y = 0.5; BL, right of x = -1.0 or above y = -0.5. Add nothing new to the center stage inside the active inset while it plays. A pinned title must be gone before the first inset grows.
- Define the layout once as named constants and helpers at the top of `construct()` (`SAFE_X, SAFE_Y = 6.0, 3.5`, `CENTER_W, CENTER_H = 7.0, 3.8`, `CORNER_W, CORNER_H = 3.0, 1.5`, `INSET_W, INSET_H = 4.7, 2.7`, `ICON_W, ICON_H = 0.8, 0.5`, and the `corner_anchor(corner)`, `inset_anchor(corner)` and `icon_scale(mob)` helpers from the snippet below), then place everything through them so no magic coordinates drift.
- A corner analogy GROWS, PLAYS, then SETTLES. Grow: build it at inset size around `inset_anchor(corner)` on an opaque dark card (`RoundedRectangle(width=INSET_W, height=INSET_H, corner_radius=0.2, stroke_width=0, fill_color=BLACK, fill_opacity=1.0)`), give the finished analogy `set_z_index(1)` so the card never covers it (a part added to it later needs its own `set_z_index(1)`), then grow card and analogy out of the corner together with `FadeIn(card, analogy, target_position=corner_anchor(corner), scale=0.35)`. Play: 2-5 seconds of motion inside the inset at full size. Settle: in ONE `self.play`, the grow in reverse, `FadeOut(card, *temporary_parts, target_position=corner_anchor(corner), scale=0.35)`, while the part that stays shrinks to a small resting icon (about 0.8 x 0.5) with `part.animate.scale(icon_scale(part)).move_to(corner_anchor(corner))`; the icon stays on screen as a legend. `FadeIn` only parts not yet on screen; `FadeOut` or shrink only parts already on screen. At the end the three resting icons gather into a tiny summary in the BR corner.
- While a corner analogy plays, the matching center-stage element pulses or glows in the SAME `self.play(...)` or the same voiceover block, so analogy and mechanism are on screen together.

### Where this block overrides the base spatial rules (narrated VISUAL_NARRATION videos only)
- The base "end every beat with FadeOut" rule is REPLACED: never FadeOut or clear the center stage between beats. Only a corner analogy's card and temporary parts leave when it settles to its resting icon.
- `buff` is at least 0.3 everywhere (`next_to`, `arrange`, tag placement); the 0.15/0.2 minimums do not apply.
- The "~7 labeled elements on screen" limit counts TAGS only (labeled elements). Unlabeled shapes (dots, grid cells, slabs, arrows) are exempt, but group them into `VGroup(...).arrange(..., buff=0.3)` rather than positioning each independently.
- Everything else in the base rules (safe area, LaTeX, no overlap, labels away from arrows) still applies.

### Pacing: a visible change every few words
- Split the narration into many short phrases of about 4-8 words, each in its own `with self.voiceover(text="...") as tracker:` block, and give EVERY phrase at least one animation (appear, move, grow, shrink, pulse, connect) with `run_time=tracker.duration` (or split into a few plays that add up to it). Never narrate over a still frame.
- Narration names what is moving ("this slab shrinks to a quarter") and never reads out on-screen text.
- A narration check rejects any phrase that starts with an animation command: never begin a phrase with "Display", "Show", "Fade", "Animate", "Create", "Draw", "Move" or "Write". Describe what the viewer sees instead ("The slab shrinks", "A grid slides across").
- Every phrase has at least 4 words.

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
- Prefer simple primitives that render reliably at 480p. Avoid `Prism`, `Cube`, 3D scenes, and anything needing LaTeX packages beyond basics. Smallest element is 0.25 units across (a `Dot` needs `radius` of at least 0.125).

Keep every spatial, safe-area and LaTeX rule above. The non-"VISUAL AID:" scenes stay as they are.

### Layout and contrast rules (REQUIRED)
- Opening: if a concept-name title is shown, it appears ALONE for about 1 second, then `FadeOut`s (or moves with `.animate.to_edge(UP).scale(0.6)`) BEFORE any diagram element appears. No diagram mobject may overlap the title's region; a pinned title keeps the whole diagram below y = 2.5, and must be gone before the first corner inset grows (insets reach the top edge).
- Tag contrast: every `Text` / `MathTex` tag uses a high-contrast color (`WHITE` or a bright palette color such as `YELLOW`, `TEAL`, `ORANGE`). Never use `GREY`, any `DARK_*` color, or opacity below 0.8 for text. Use `font_size` of at least 28 for `Text` at 480p, and place each tag with `next_to(shape, DOWN, buff=0.3)` (buff of 0.3 or more) so it never touches a shape.
- Final composition: the finished center-stage diagram fills a reasonable part of its box (about 60-100% of 7.0 x 3.8) and stays centered; the four corner icons sit at the edges. Never shrink the whole detailed diagram into a tiny cluster.
- Repeated or stacked blocks ("this block repeats N times"): draw a few simplified block icons at legible size (for example 3 `RoundedRectangle` icons plus a "..." tag) instead of scaling the full detailed diagram down.
- Do NOT `FadeOut` the center stage between scenes (that would break the build-up); only a corner analogy's card and temporary parts are removed when it settles to its resting icon.
- Labels follow their shape: when a shape is replaced or transformed (for example the 256 slab becoming the 64 slab), `Transform` / `ReplacementTransform` its label too (or `FadeOut` the old label in the same play). Never `Write` / `FadeIn` a second label at the spot where one already sits, so a tag is never drawn twice on top of itself.
- Tags never touch lines: a tag sits OUTSIDE every shape with at least 0.3 clearance, and never on or across a line (an inset's card is a background, so an analogy's tag may sit on it, fully inside the card).
- No frames (a static check enforces this): never draw a frame, boundary, outline or placeholder box around anything, in the center or in a corner. The one panel allowed is an inset's filled card with `stroke_width=0`. Group parts by spacing and color instead. Never construct a `SurroundingRectangle`, and never a `DashedVMobject` of a `Rectangle`, `Square`, `RoundedRectangle` or `ScreenRectangle`. Highlight only with `Circumscribe`, `Indicate` or `.animate.set_stroke(...)`. The diagram starts with the real input shape, not an outline of where it will go. If the plan asks for an outline, frame, dashed box or placeholder, leave it out.
- The final recap lives ONLY in the BR corner. In the last beat nothing new is added over the center stage except highlights (`Indicate`, `Circumscribe`, `set_stroke`): no row of stacked block icons across the top, nothing over arcs or arrows already drawn.

### Few-shot snippet: layout helpers + one corner analogy that grows, plays and settles (inside `construct`)
```python
SAFE_X, SAFE_Y = 6.0, 3.5
CENTER_W, CENTER_H = 7.0, 3.8
CORNER_W, CORNER_H = 3.0, 1.5
INSET_W, INSET_H = 4.7, 2.7
ICON_W, ICON_H = 0.8, 0.5

def corner_anchor(corner):
    # Resting spot: center of a corner box flush with the safe area; corner is UL, UR, DL or DR
    x = (SAFE_X - CORNER_W / 2) * corner[0]
    y = (SAFE_Y - CORNER_H / 2) * corner[1]
    return np.array([x, y, 0])

def inset_anchor(corner):
    # Center of the zoomed-up inset, flush with the same corner
    x = (SAFE_X - INSET_W / 2) * corner[0]
    y = (SAFE_Y - INSET_H / 2) * corner[1]
    return np.array([x, y, 0])

def icon_scale(mob):
    # Shrinks mob to fit the resting icon; max() guards a zero width or height
    return min(ICON_W / max(mob.width, 0.01), ICON_H / max(mob.height, 0.01))

# Below center so its 1.2x Indicate pulse stays 0.3 clear of the UL inset
slab = Rectangle(width=2.4, height=1.6, color=BLUE, fill_opacity=0.6).move_to(DOWN * 0.5)
card = RoundedRectangle(width=INSET_W, height=INSET_H, corner_radius=0.2, stroke_width=0,
                        fill_color=BLACK, fill_opacity=1.0).move_to(inset_anchor(UL))
funnel = Polygon([-1.0, 0.8, 0], [1.0, 0.8, 0], [0.25, -0.8, 0], [-0.25, -0.8, 0], color=YELLOW)
dots = VGroup(*[Dot(radius=0.125, color=YELLOW) for _ in range(12)]).arrange_in_grid(3, 4, buff=0.25)
analogy = VGroup(dots, funnel).arrange(RIGHT, buff=0.4).move_to(inset_anchor(UL))
analogy.set_z_index(1)  # above the card, even while the card fades out on top of it
self.play(FadeIn(slab))
with self.voiceover(text="A wide stream reaches the funnel.") as tracker:
    self.play(FadeIn(card, analogy, target_position=corner_anchor(UL), scale=0.35),
              Indicate(slab, color=YELLOW), run_time=tracker.duration)
with self.voiceover(text="Every dot packs into the funnel.") as tracker:
    self.play(dots.animate.arrange_in_grid(3, 4, buff=0.05).move_to(funnel), run_time=tracker.duration)
with self.voiceover(text="The thick slab slims to a quarter.") as tracker:
    self.play(FadeOut(card, dots, target_position=corner_anchor(UL), scale=0.35),
              funnel.animate.scale(icon_scale(funnel)).move_to(corner_anchor(UL)),
              slab.animate.stretch_to_fit_width(0.6), run_time=tracker.duration)
```
