import { describe, expect, it } from "vitest";
import { cleanOptions, defaultOptions, clockTime } from "./form";
import type { Channel } from "./types";

const channel: Channel = {
  id: "mimo",
  name: "MiMo",
  provider: "mimo",
  description: "",
  supports_subtitles: false,
  fields: [
    { name: "mimo_mode", type: "select", label: "模式", default: "preset" },
    {
      name: "voice",
      type: "voice",
      label: "音色",
      default: "mimo_default",
      modes: ["preset"],
    },
    {
      name: "reference_audio",
      type: "audio",
      label: "参考音频",
      default: "",
      modes: ["clone"],
      required: true,
    },
    {
      name: "voice_description",
      type: "textarea",
      label: "描述",
      default: "",
      modes: ["design"],
      required: true,
    },
    { name: "instructions", type: "textarea", label: "风格", default: "" },
  ],
};

describe("provider-aware form serialization", () => {
  it("does not submit stale preset voices, edge controls or secrets with clone requests", () => {
    expect(
      cleanOptions(channel, {
        mimo_mode: "clone",
        voice: "mimo_default",
        rate: "+20%",
        api_key: "secret",
        reference_audio: "data:audio/wav;base64,abc",
      }),
    ).toEqual({
      mimo_mode: "clone",
      reference_audio: "data:audio/wav;base64,abc",
    });
  });
  it("restores only applicable defaults", () => {
    expect(defaultOptions(channel)).toEqual({
      mimo_mode: "preset",
      voice: "mimo_default",
    });
    expect(
      cleanOptions(channel, {
        mimo_mode: "design",
        voice_description: "温柔女声",
        reference_audio: "previous file",
      }),
    ).toEqual({ mimo_mode: "design", voice_description: "温柔女声" });
  });
  it("handles unavailable media durations", () => {
    expect(clockTime(Infinity)).toBe("0:00");
    expect(clockTime(65)).toBe("1:05");
  });
});
