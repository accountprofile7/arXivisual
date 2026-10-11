"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";

type Props = {
  src: string;
  concept: string;
  /** False while the card is not the one being read; the clip pauses. */
  active?: boolean;
};

const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";

function subscribeReducedMotion(onChange: () => void) {
  const mq = window.matchMedia(REDUCED_MOTION_QUERY);
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
}

function useReducedMotion(): boolean {
  return useSyncExternalStore(
    subscribeReducedMotion,
    () => window.matchMedia(REDUCED_MOTION_QUERY).matches,
    () => false
  );
}

/**
 * Silent looping clip shown inline after the paragraph it illustrates.
 * Loads its source only near the viewport, plays only while at least half
 * visible, and always offers a pause/play toggle (WCAG 2.2.2). With
 * prefers-reduced-motion it never autoplays.
 */
export function VisualAid({ src, concept, active = true }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const reducedMotion = useReducedMotion();
  const [near, setNear] = useState(false);
  const [visible, setVisible] = useState(false);
  // null = follow the default (autoplay unless reduced motion); the toggle
  // overrides it for this clip.
  const [manual, setManual] = useState<boolean | null>(null);
  const [hadError, setHadError] = useState(false);
  const retried = useRef(false);

  const wantPlay = manual ?? !reducedMotion;
  const playing = wantPlay && visible && active;

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const nearObserver = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) setNear(true);
      },
      { rootMargin: "300px" }
    );
    const visibleObserver = new IntersectionObserver(
      ([entry]) => setVisible(entry.isIntersecting),
      { threshold: 0.5 }
    );
    nearObserver.observe(el);
    visibleObserver.observe(el);
    return () => {
      nearObserver.disconnect();
      visibleObserver.disconnect();
    };
  }, []);

  useEffect(() => {
    const v = videoRef.current;
    if (!v || !near) return;
    if (playing) {
      v.play().catch(() => {
        // Autoplay refused or interrupted by a pause(); the toggle still works.
      });
    } else {
      v.pause();
    }
  }, [playing, near]);

  if (!src) return null;

  return (
    <figure className="my-6">
      <div
        ref={containerRef}
        className="relative overflow-hidden rounded-xl border border-white/[0.06] bg-black/30"
      >
        {hadError ? (
          <div className="px-3 py-2 text-xs text-[#f27066]">
            Couldn&apos;t load this animation.
          </div>
        ) : (
          <video
            ref={videoRef}
            src={near ? src : undefined}
            className="block w-full"
            aria-label={`Animation: ${concept}`}
            muted
            loop
            playsInline
            preload="metadata"
            onError={() => {
              const v = videoRef.current;
              if (v && !retried.current) {
                retried.current = true;
                window.setTimeout(() => v.load(), 2000);
                return;
              }
              setHadError(true);
            }}
          />
        )}
        {!hadError && (
          <button
            type="button"
            onClick={() => setManual(!wantPlay)}
            aria-label={wantPlay ? `Pause animation: ${concept}` : `Play animation: ${concept}`}
            className="absolute bottom-3 right-3 grid h-9 w-9 place-items-center rounded-full border border-white/[0.12] bg-black/60 text-white/80 backdrop-blur-sm transition hover:text-white"
          >
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="currentColor" aria-hidden>
              {wantPlay ? <path d="M6 5h4v14H6zM14 5h4v14h-4z" /> : <path d="M8 5v14l11-7z" />}
            </svg>
          </button>
        )}
      </div>
      <figcaption className="mt-2 text-center text-xs text-white/30">{concept}</figcaption>
    </figure>
  );
}
