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
