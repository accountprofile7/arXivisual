## Visual Aid Moments (REQUIRED)
This video must contain EXACTLY 3 "visual aid moments", spread across the video (one early, one in the middle, one late; never two in adjacent scenes). A visual aid moment is a beat where the narration names a concrete quantity or structure taken from the section text and the screen DRAWS it instead of writing it.

For each of the 3 moments, decide:
1. The term or quantity, copied from the section text (a channel count, kernel size, layer count, dimension, number of heads, a skip connection, and so on).
2. Its geometric depiction, where size, count or position encodes the value:
   - a size or width: proportional sizing (a 256-channel tensor is a thick slab, a 64-channel tensor a thin slab, thickness proportional to the number)
   - a kernel or filter: a grid of cells (1x1 is a single cell, 3x3 is a 3 by 3 grid)
   - channels or depth: stacked slabs or layers
   - a data path or shortcut: an arrow that arcs around the blocks and joins at a circle containing "+"
   - a count: that many repeated shapes, or a proportional bar
3. The narration line that says it (12-24 words, plain language, naming the quantity).

Encode each moment inside the scene description of the scene that shows it. That scene's description MUST start with "VISUAL AID: " and then give (a), (b) and (c) explicitly, for example:
"VISUAL AID: 1x1 convolution reduces 256 channels to 64. Draw a thick slab (depth proportional to 256) that visibly compresses into a thin slab (proportional to 64); show a 1x1 kernel as a single cell next to a 3x3 kernel grid. Narration: a one by one convolution squeezes 256 channels down to just 64."
Also include each moment's narration line in `narration_points`.

Rules for visual aid moments:
- Exactly 3 scenes have descriptions starting with "VISUAL AID: ". Not 2, not 4.
- The shape appears first and carries the meaning. Text is allowed only as a short tag (3 words or fewer, or a number) AFTER the shape is on screen, never as a substitute for it.
- Use simple primitives that render reliably: Rectangle, Square grids, Arrow, Circle with a "+". No photo-real or 3D-perspective effects.
- The other scenes (framing, transitions, takeaway) stay as described above.
