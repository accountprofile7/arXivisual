from manim import *


class SilentLoopExample(Scene):
    """Short silent looping clip: ends in the same composition it starts with."""

    def construct(self):
        # Opening composition: a row of three small boxes
        boxes = VGroup(*[Square(side_length=1.0, color=BLUE) for _ in range(3)])
        boxes.arrange(RIGHT, buff=0.8)
        self.add(boxes)
        self.wait(0.4)

        # Beat 1: a dot travels through the boxes
        dot = Dot(color=YELLOW).move_to(boxes[0])
        self.play(FadeIn(dot), run_time=0.5)
        for box in boxes[1:]:
            self.play(dot.animate.move_to(box), box.animate.set_fill(BLUE, opacity=0.5), run_time=0.8)
        self.wait(0.3)

        # Beat 2: a short label appears
        label = Text("Output", font_size=28).next_to(boxes[2], DOWN, buff=0.4)
        self.play(FadeIn(label), run_time=0.5)
        self.wait(0.4)

        # Beat 3: reverse everything back to the opening composition so it loops
        self.play(
            FadeOut(label),
            FadeOut(dot),
            *[box.animate.set_fill(BLUE, opacity=0) for box in boxes[1:]],
            run_time=0.8,
        )
        self.wait(0.3)
