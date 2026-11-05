export interface Control {
  name: string;
  label: string;
  type: "select" | "range" | "textarea" | "audio" | "voice";
  default: string;
  options?: { value: string; label: string }[];
  modes?: string[];
  required?: boolean;
  min?: number;
  max?: number;
  step?: number;
  suffix?: string;
  placeholder?: string;
  hint?: string;
  max_bytes?: number;
  max_length?: number;
  accept?: string;
}
export interface Channel {
  id: string;
  name: string;
  provider: "edge" | "mimo";
  description: string;
  supports_subtitles: boolean;
  fields: Control[];
}
export interface Voice {
  name: string;
  friendly_name: string;
  locale: string;
  language: string;
  gender: string;
}
export interface Job {
  id: string;
  status:
    | "queued"
    | "running"
    | "merging"
    | "completed"
    | "failed"
    | "cancelled"
    | "interrupted";
  title: string;
  created_at: number;
  updated_at: number;
  expires_at: number | null;
  channel_name: string;
  provider: string;
  voice: string | null;
  response_format: "mp3" | "wav";
  subtitles: boolean;
  preview: boolean;
  text_length: number;
  total_segments: number;
  completed_segments: number;
  audio_bytes: number;
  duration_ms: number;
  error: string | null;
}
export interface AdminChannel {
  id: string;
  name: string;
  provider: "edge" | "mimo";
  enabled: boolean;
  has_api_key: boolean;
  base_url: string | null;
  proxy: string | null;
  default_voice: string | null;
  default_mode: "preset" | "design" | "clone";
}
