import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createApp, nextTick, type App } from "vue";
import Workspace from "./Workspace.vue";
import type { Job } from "./types";

let app: App | undefined;
let root: HTMLDivElement;
const job: Job = {
  id: "job1",
  status: "completed",
  title: "已完成的语音",
  created_at: 1700000000,
  updated_at: 1700000100,
  expires_at: 1900000000,
  channel_name: "Edge",
  provider: "edge",
  voice: "zh-CN-XiaoxiaoNeural",
  response_format: "mp3",
  subtitles: true,
  preview: false,
  text_length: 20,
  total_segments: 1,
  completed_segments: 1,
  audio_bytes: 1024,
  duration_ms: 2000,
  error: null,
};
const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
  if (url === "/api/session") return Response.json({ retention_days: 7 });
  if (url === "/api/channels")
    return Response.json({
      channels: [
        {
          id: "edge",
          name: "Edge",
          provider: "edge",
          supports_subtitles: true,
          fields: [
            {
              name: "voice",
              label: "音色",
              type: "voice",
              default: "zh-CN-XiaoxiaoNeural",
            },
            {
              name: "response_format",
              label: "格式",
              type: "select",
              default: "mp3",
              options: [{ value: "mp3", label: "MP3" }],
            },
          ],
        },
      ],
    });
  if (url.endsWith("/voices"))
    return Response.json({
      voices: [
        {
          name: "zh-CN-XiaoxiaoNeural",
          friendly_name: "晓晓",
          gender: "Female",
          locale: "zh-CN",
          language: "zh",
        },
      ],
    });
  if (url === "/api/jobs" && init?.method === "POST")
    return Response.json(
      { ...job, id: "new-job", status: "queued", completed_segments: 0 },
      { status: 202 },
    );
  return Response.json({ jobs: [job], next_offset: null });
});
async function settle(): Promise<void> {
  for (let i = 0; i < 15; i++) {
    await Promise.resolve();
    await nextTick();
  }
}
beforeEach(() => {
  localStorage.clear();
  fetcher.mockClear();
  vi.stubGlobal("fetch", fetcher);
  root = document.createElement("div");
  document.body.appendChild(root);
});
afterEach(() => {
  app?.unmount();
  root.remove();
  vi.unstubAllGlobals();
});

describe("workspace user flow", () => {
  it("restores server jobs and exposes audio and subtitle downloads", async () => {
    app = createApp(Workspace);
    app.mount(root);
    await settle();
    expect(root.textContent).toContain("已完成的语音");
    expect(root.querySelector("audio")?.getAttribute("src")).toBe(
      "/api/jobs/job1/audio",
    );
    expect(
      root.querySelector('a[href="/api/jobs/job1/audio?attachment=true"]'),
    ).not.toBeNull();
    expect(
      root.querySelector('a[href="/api/jobs/job1/subtitles"]'),
    ).not.toBeNull();
  });
  it("submits the visible settings without any administrative credential", async () => {
    app = createApp(Workspace);
    app.mount(root);
    await settle();
    const textarea = root.querySelector<HTMLTextAreaElement>("#script-text")!;
    textarea.value = "开始生成语音。";
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
    await nextTick();
    const button = [...root.querySelectorAll<HTMLButtonElement>("button")].find(
      (b) => b.textContent?.includes("生成语音"),
    )!;
    expect(button.disabled).toBe(false);
    button.click();
    await settle();
    const call = fetcher.mock.calls.find(
      ([, init]) => init?.method === "POST",
    )!;
    expect(JSON.parse(String(call[1]?.body))).toMatchObject({
      text: "开始生成语音。",
      channel_id: "edge",
      options: { voice: "zh-CN-XiaoxiaoNeural" },
    });
    expect(new Headers(call[1]?.headers).get("X-API-Key")).toBeNull();
    expect(root.textContent).toContain("任务已提交");
  });
});
