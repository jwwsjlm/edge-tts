<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { Channel, Voice } from "./types";
import { visibleFields } from "./form";
import Icon from "./Icon.vue";

const props = defineProps<{
  channel: Channel;
  values: Record<string, string>;
  voices: Voice[];
  loading: boolean;
}>();
const emit = defineEmits<{
  change: [name: string, value: string];
  error: [message: string];
}>();
const search = ref("");
const locale = ref("");
const fileName = ref("");
const fields = computed(() => visibleFields(props.channel, props.values));
const locales = computed(() =>
  [...new Set(props.voices.map((v) => v.locale))].sort(),
);
const filteredVoices = computed(() =>
  props.voices.filter(
    (v) =>
      (!locale.value || v.locale === locale.value) &&
      `${v.name} ${v.friendly_name}`
        .toLowerCase()
        .includes(search.value.toLowerCase()),
  ),
);
const currentVoice = computed(() =>
  props.voices.find((v) => v.name === props.values.voice),
);
const languageNames = new Intl.DisplayNames(["zh-CN"], { type: "language" });
function languageLabel(code: string): string {
  try {
    return code === "multilingual" ? "多语言" : languageNames.of(code) || code;
  } catch {
    return code;
  }
}
function voiceLabel(voice: Voice): string {
  const names: Record<string, string> = {
    Xiaoxiao: "晓晓",
    Xiaoyi: "晓伊",
    Yunjian: "云健",
    Yunxi: "云希",
    Yunxia: "云夏",
    Yunyang: "云扬",
    Xiaobei: "晓北",
    Xiaoni: "晓妮",
    Xiaomeng: "晓梦",
    Xiaoxuan: "晓萱",
  };
  const short =
    voice.name.split("-").at(-1)?.replace("Neural", "") || voice.name;
  const gender =
    voice.gender === "Female"
      ? "女声"
      : voice.gender === "Male"
        ? "男声"
        : "默认";
  return `${names[short] || short} · ${gender}`;
}
function value(event: Event): string {
  return (event.target as HTMLInputElement).value;
}
async function upload(event: Event, maxBytes: number): Promise<void> {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  if (!/\.(wav|mp3)$/i.test(file.name) || file.size > maxBytes || !file.size) {
    emit(
      "error",
      `请选择有效的 WAV / MP3 文件，大小不超过 ${(maxBytes / 1048576).toFixed(1)} MB。`,
    );
    input.value = "";
    return;
  }
  try {
    const data = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(new Error("无法读取文件"));
      reader.readAsDataURL(file);
    });
    const encoded = data.split(",")[1];
    const mime = file.name.toLowerCase().endsWith(".wav")
      ? "audio/wav"
      : "audio/mpeg";
    emit("change", "reference_audio", `data:${mime};base64,${encoded}`);
    fileName.value = file.name;
  } catch {
    emit("error", "无法读取参考音频，请重新选择文件。");
  }
}
watch(
  () => props.channel.id,
  () => {
    search.value = "";
    locale.value = "";
    fileName.value = "";
  },
);
watch(
  () => props.values.mimo_mode,
  () => {
    if (props.values.mimo_mode !== "clone") fileName.value = "";
  },
);
watch([search, locale], () => {
  if (
    filteredVoices.value.length &&
    !filteredVoices.value.some((v) => v.name === props.values.voice)
  )
    emit("change", "voice", filteredVoices.value[0].name);
});
</script>

<template>
  <div class="dynamic-controls">
    <div
      v-for="field in fields"
      :key="field.name"
      class="field"
      :class="{ 'range-field': field.type === 'range' }"
    >
      <div class="field-label">
        <label :for="`control-${field.name}`"
          >{{ field.label
          }}<span v-if="field.required" class="required"> *</span></label
        ><output v-if="field.type === 'range'" :for="`control-${field.name}`">{{
          values[field.name] || field.default
        }}</output>
      </div>
      <template v-if="field.type === 'voice'">
        <div v-if="channel.provider === 'edge'" class="voice-filters">
          <select v-model="locale" aria-label="筛选语言">
            <option value="">全部语言</option>
            <option v-for="code in locales" :key="code" :value="code">
              {{ languageLabel(code) }}
            </option>
          </select>
          <input
            v-model="search"
            aria-label="搜索音色"
            placeholder="搜索音色…"
            type="search"
          />
        </div>
        <select
          :id="`control-${field.name}`"
          :value="values.voice"
          :disabled="loading || !filteredVoices.length"
          @change="emit('change', 'voice', value($event))"
        >
          <option v-if="loading" value="">正在加载音色…</option>
          <option v-else-if="!filteredVoices.length" value="">
            没有匹配的音色
          </option>
          <option
            v-for="voice in filteredVoices"
            :key="voice.name"
            :value="voice.name"
          >
            {{ voiceLabel(voice) }} · {{ languageLabel(voice.locale) }}
          </option>
        </select>
        <p v-if="currentVoice" class="voice-detail">
          <Icon name="sound" :size="15" />{{ currentVoice.name }}
        </p>
      </template>
      <select
        v-else-if="field.type === 'select'"
        :id="`control-${field.name}`"
        :value="values[field.name] || field.default"
        @change="emit('change', field.name, value($event))"
      >
        <option
          v-for="option in field.options"
          :key="option.value"
          :value="option.value"
        >
          {{ option.label }}
        </option>
      </select>
      <template v-else-if="field.type === 'range'">
        <input
          :id="`control-${field.name}`"
          type="range"
          :min="field.min"
          :max="field.max"
          :step="field.step"
          :value="parseInt(values[field.name] || field.default)"
          @input="
            emit(
              'change',
              field.name,
              `${Number(value($event)) >= 0 ? '+' : ''}${value($event)}${field.suffix}`,
            )
          "
        />
        <div class="range-extents">
          <span>{{ field.min }}{{ field.suffix }}</span
          ><button
            type="button"
            @click="emit('change', field.name, field.default)"
          >
            恢复默认</button
          ><span>+{{ field.max }}{{ field.suffix }}</span>
        </div>
      </template>
      <textarea
        v-else-if="field.type === 'textarea'"
        :id="`control-${field.name}`"
        :value="values[field.name] || ''"
        :placeholder="field.placeholder"
        :required="field.required"
        :maxlength="field.max_length"
        rows="3"
        @input="emit('change', field.name, value($event))"
      ></textarea>
      <div v-else-if="field.type === 'audio'" class="upload-zone">
        <Icon name="upload" :size="22" /><span>{{
          fileName || "选择一段参考音频"
        }}</span
        ><small
          >WAV / MP3 · 最大
          {{ ((field.max_bytes || 7864320) / 1048576).toFixed(1) }} MB</small
        >
        <input
          :id="`control-${field.name}`"
          type="file"
          :accept="field.accept"
          :aria-label="field.label"
          @change="upload($event, field.max_bytes || 7864320)"
        />
      </div>
      <p v-if="field.hint" class="field-hint">{{ field.hint }}</p>
    </div>
  </div>
</template>
