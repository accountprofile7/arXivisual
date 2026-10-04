## Visual Aid Moments (REQUIRED): center stage + corner analogies
The viewer should follow the idea from shapes and motion alone; the narration only names what they are already seeing. Plan the video on a fixed screen layout:
- CENTER STAGE (about 7.0 x 3.8 units, middle of the frame): ONE continuous diagram of the real mechanism from the paper. It is built up beat by beat over the whole video. It is never cleared, only added to and highlighted.
- FOUR CORNERS (about 3.0 x 1.5 units each): short, self-contained analogy animations (2-5 seconds, vector shapes only) that pop in, explain one idea by everyday comparison, then shrink to a small resting icon that stays on screen as a legend. Top-left (TL), top-right (TR) and bottom-left (BL) are the 3 visual aids. Bottom-right (BR) is the final recap.

### 1. Center stage: a sequence of phrase-sized beats
Write the center-stage build-up as an ordered list of beats. Each beat = ONE narration phrase of 4-8 words + ONE visible change (a shape appears, moves, grows, shrinks, splits, merges, connects or pulses). No phrase is narrated over a still frame. Spread the beats across the scenes, 4-6 beats per scene. Quantities are shown by proportional size or by counts of repeated shapes (a 256-channel tensor is a thick slab, a 64-channel tensor a thin slab; thickness proportional to the number), kernels by grids of cells (1x1 is one cell, 3x3 is a 3 by 3 grid), shortcuts by an arrow that arcs around blocks and joins at a circle containing "+".

### 2. Exactly 3 corner analogy moments (TL, TR, BL)
Each moment is one scene whose description MUST start with "VISUAL AID: " and then states all four of:
a. The everyday analogy (funnel, road with a bridge, pipe and valve, magnifying window, stack of cards, scale), built from simple vector shapes.
b. The paper quantity or operation it explains, copied from the section text (a channel count, kernel size, layer count, skip connection, and so on).
c. Which center-stage element it highlights (pulse/glow) while the corner animation plays, so analogy and real mechanism are on screen together.
d. Its 2-5 second corner animation, and the corner it uses (TL, TR or BL, in that order across the video).
Example: "VISUAL AID: (TL) squeeze. Analogy: a funnel narrows a wide stream of 256 dots into 64 dots. Explains: the 1x1 convolution reduces 256 channels to 64. Center stage: the first slab pulses and shrinks to a thin slab in sync. Corner animation: dots flow into the funnel and come out as 64 (3 s), then the funnel shrinks to a small icon in the TL corner. Narration: this thick slab squeezes down to a quarter."
Also include each moment's narration phrases in `narration_points`.

### 3. Recap (BR)
The last scene is the recap: the three resting icons gather into a tiny summary of the whole block in the BR corner while the finished center-stage diagram is highlighted once from input to output. Its description does NOT start with "VISUAL AID: ".

Rules:
- EXACTLY 3 scenes have descriptions starting with "VISUAL AID: ". Not 2, not 4. They appear in the order TL, TR, BL, spread across the video (never in adjacent scenes unless the plan has few scenes).
- Words on screen are rare: no sentences, bullets, paragraphs or title cards. At most a tag of 3 words or fewer, or a number or symbol (256, 64, 1x1, +), shown AFTER the shape is on screen. About 15 tag words or fewer for the whole video.
- Color carries meaning consistently between center stage and corners (the same color for the same quantity), and is never the only cue; shape or motion repeats it.
- Corners never overlap the center stage. Use simple primitives that render reliably (Rectangle, Square grids, Circle, Polygon, Arc, Line, Dot, Arrow); no external images, no photo-real or 3D effects.
- Target length stays 30-60 seconds. The other scenes (framing, transitions) stay as described above.
