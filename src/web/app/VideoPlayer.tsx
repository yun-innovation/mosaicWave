"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { assetFileUrl } from "./api";

type Props = {
  assetId: string;
  poster?: string;
  title: string;
};

function formatClock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const total = Math.floor(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  }
  return `${minutes}:${String(secs).padStart(2, "0")}`;
}

export function VideoPlayer({ assetId, poster, title }: Props) {
  const { t } = useTranslation("gallery");
  const videoRef = useRef<HTMLVideoElement>(null);
  const [src, setSrc] = useState(`/api/v1/assets/${assetId}/file`);
  const [playing, setPlaying] = useState(false);
  const [current, setCurrent] = useState(0);
  const [duration, setDuration] = useState(0);
  const [scrubbing, setScrubbing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const crossOrigin = src.startsWith("http") ? "use-credentials" : undefined;
  const known = Number.isFinite(duration) && duration > 0;

  useEffect(() => {
    setSrc(assetFileUrl(assetId));
    const el = videoRef.current;
    if (!el) return;
    el.pause();
    setPlaying(false);
    setCurrent(0);
    setDuration(0);
    setError(null);
  }, [assetId]);

  function syncDuration() {
    const el = videoRef.current;
    if (!el) return;
    if (Number.isFinite(el.duration) && el.duration > 0) {
      setDuration(el.duration);
    }
  }

  async function togglePlay() {
    const el = videoRef.current;
    if (!el) return;
    try {
      if (el.paused) {
        await el.play();
      } else {
        el.pause();
      }
    } catch {
      setError(t("video.cannotPlay"));
    }
  }

  function skip(delta: number) {
    const el = videoRef.current;
    if (!el || !known) return;
    const next = Math.min(duration, Math.max(0, el.currentTime + delta));
    el.currentTime = next;
    setCurrent(next);
  }

  function onScrub(value: number) {
    const el = videoRef.current;
    if (!el) return;
    el.currentTime = value;
    setCurrent(value);
  }

  return (
    <div className="video-player">
      <video
        ref={videoRef}
        src={src}
        poster={poster}
        title={title}
        preload="metadata"
        playsInline
        crossOrigin={crossOrigin}
        onClick={() => void togglePlay()}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        onTimeUpdate={() => {
          if (scrubbing) return;
          const el = videoRef.current;
          if (el) setCurrent(el.currentTime);
        }}
        onLoadedMetadata={syncDuration}
        onDurationChange={syncDuration}
        onError={() => setError(t("video.cannotPlay"))}
      />
      <div className="video-transport">
        <button type="button" onClick={() => void togglePlay()} aria-label={playing ? t("video.pause") : t("video.play")}>
          {playing ? t("video.pause") : t("video.play")}
        </button>
        <button type="button" onClick={() => skip(-10)} disabled={!known} aria-label={t("video.back")}>
          −10s
        </button>
        <input
          type="range"
          min={0}
          max={known ? duration : 0}
          step={0.1}
          value={known ? Math.min(current, duration) : 0}
          disabled={!known}
          aria-label={t("video.position")}
          aria-valuetext={formatClock(current)}
          onPointerDown={() => setScrubbing(true)}
          onPointerUp={() => setScrubbing(false)}
          onPointerCancel={() => setScrubbing(false)}
          onChange={(event) => onScrub(Number(event.target.value))}
        />
        <button type="button" onClick={() => skip(10)} disabled={!known} aria-label={t("video.forward")}>
          +10s
        </button>
        <span className="video-clock">
          {formatClock(current)} / {known ? formatClock(duration) : "—"}
        </span>
      </div>
      {error && <p className="video-error">{error}</p>}
    </div>
  );
}
