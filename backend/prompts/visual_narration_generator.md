## Show, Don't Label (REQUIRED)
The plan contains scenes whose description starts with "VISUAL AID:". Each is a "visual aid moment": the narration names a concrete quantity or structure from the paper and the screen must DRAW it. Implement every "VISUAL AID:" scene as geometry, synchronized with the voiceover line that mentions it, inside that line's `with self.voiceover(text="...") as tracker:` block.

Show-don't-label rules:
- Numbers and dimensions are conveyed by proportional shapes, not by writing them. A 256-channel tensor is a thick slab (width proportional to 256), a 64-channel tensor a thin slab (proportional to 64); a compression is the thick slab visibly transforming into the thin one (`Transform` / `ReplacementTransform` / `.animate.stretch_to_fit_width`). Pick one scale factor, apply it to every slab, and keep the widest slab inside the safe area.
- Kernels and filters are grids of cells: a 1x1 kernel is a single Square, a 3x3 kernel a 3 by 3 grid built with `VGroup(*[Square(side_length=0.35) for _ in range(9)]).arrange_in_grid(3, 3, buff=0)`.
- Channels, depth or layers are stacked rectangles or slabs; counts are that many repeated shapes.
- A shortcut or data path is an `Arrow` or `CurvedArrow` that arcs AROUND the blocks it skips and ends at a `Circle` containing a "+" (`Text("+")`) where it joins the main path.
- `MathTex` / `Text` is allowed only as a brief tag (3 words or fewer, or a number) placed with `next_to(shape, DOWN, buff=0.3)` AFTER the shape is already on screen. Never use text as a substitute for the shape, and never type out the sentence the narration says.
- Time the drawing to the line: the shape's `self.play(..., run_time=tracker.duration)` happens inside the voiceover block that names the quantity, so the shape appears as the quantity is spoken.
- Prefer simple primitives that render reliably at 480p: `Rectangle`, `Square`, `RoundedRectangle`, `Arrow`, `CurvedArrow`, `Circle`, `VGroup`. Avoid `Prism`, `Cube`, 3D scenes, and anything needing LaTeX packages beyond basics.
- Keep every spatial, safe-area and LaTeX rule above. The non-"VISUAL AID:" scenes stay as they are.

Layout and contrast rules for "VISUAL AID:" scenes (REQUIRED):
- Opening: the title appears ALONE. Then either `FadeOut` the title or move it with `.animate.to_edge(UP).scale(0.6)` BEFORE any diagram element appears. No diagram mobject may overlap the title's region; when a title stays pinned at the top, keep the whole diagram below y = 2.5.
- Tag contrast: every `Text` / `MathTex` tag uses a high-contrast color (`WHITE` or a bright palette color such as `YELLOW`, `TEAL`, `ORANGE`). Never use `GREY`, any `DARK_*` color, or opacity below 0.8 for text. Use `font_size` of at least 28 for `Text` at 480p, and place each tag with `next_to(shape, DOWN, buff=0.2)` (buff of 0.15 or more) so it never touches a shape.
- Final composition: every scene's last frame must fill a reasonable part of the safe area: the main group's width between 40% and 85% of the frame width, centered vertically in the safe area. Never shrink the whole detailed diagram into a tiny cluster at the top.
- Repeated or stacked blocks ("this block repeats N times"): draw a few simplified block icons at legible size (for example 3 `RoundedRectangle` icons plus a "..." tag) instead of scaling the full detailed diagram down.
- `FadeOut` all previous content before building a new composition, so old and new shapes never overlap.
