import type { Channel, Control, Job } from "./types";

export function visibleFields(
  channel: Channel,
  values: Record<string, string>,
): Control[] {
  const mode = values.mimo_mode || "preset";
  return channel.fields.filter(
    (field) => !field.modes || field.modes.includes(mode),
  );
}

export function cleanOptions(
  channel: Channel,
  values: Record<string, string>,
): Record<string, string> {
  return Object.fromEntries(
    visibleFields(channel, values)
      .map((field) => [field.name, values[field.name] ?? field.default])
      .filter(([, value]) => value !== ""),
  );
}

export function defaultOptions(channel: Channel): Record<string, string> {
  const values = Object.fromEntries(
    channel.fields.map((field) => [field.name, field.default]),
  );
  return cleanOptions(channel, values);
}

export function isActive(job: Job): boolean {
  return ["queued", "running", "merging"].includes(job.status);
}

export const statusLabels: Record<Job["status"], string> = {
  queued: "排队中",
  running: "生成中",
  merging: "合并音频",
  completed: "已完成",
  failed: "生成失败",
  cancelled: "已取消",
  interrupted: "可继续",
};

export function clockTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const mins = Math.floor(seconds / 60);
  return `${mins}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
}

export function dateLabel(seconds: number): string {
  return new Date(seconds * 1000).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
