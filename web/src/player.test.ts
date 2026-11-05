import { expect, it } from "vitest";
import { createApp, h, nextTick, ref } from "vue";
import Player from "./Player.vue";
import type { Job } from "./types";

it("preserves playback state when background polling refreshes the same job", async () => {
  const job = ref<Job>({
    id: "one",
    status: "completed",
    title: "Audio",
    created_at: 1,
    updated_at: 2,
    expires_at: 9999999999,
    channel_name: "Edge",
    provider: "edge",
    voice: null,
    response_format: "mp3",
    subtitles: false,
    preview: false,
    text_length: 10,
    total_segments: 1,
    completed_segments: 1,
    audio_bytes: 100,
    duration_ms: 2000,
    error: null,
  });
  const root = document.createElement("div");
  const app = createApp({ render: () => h(Player, { job: job.value }) });
  app.mount(root);
  const audio = root.querySelector("audio")!;
  audio.dispatchEvent(new Event("play"));
  await nextTick();
  expect(root.querySelector(".play-button")?.getAttribute("aria-label")).toBe(
    "暂停播放",
  );
  job.value = { ...job.value, updated_at: 3 };
  await nextTick();
  expect(root.querySelector(".play-button")?.getAttribute("aria-label")).toBe(
    "暂停播放",
  );
  app.unmount();
});
