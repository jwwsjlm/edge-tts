<script setup lang="ts">
import { computed, ref, watch, nextTick } from "vue";
import type { Job } from "./types";
import { clockTime, dateLabel, statusLabels, isActive } from "./form";
import Icon from "./Icon.vue";

const props = defineProps<{ job: Job | null; autoPlayId?: string }>();
const emit = defineEmits<{ action: [job: Job, action: "cancel" | "retry"] }>();
const audio = ref<HTMLAudioElement | null>(null);
const playing = ref(false);
const position = ref(0);
const duration = ref(0);
const volume = ref(1);
const error = ref("");
const progress = computed(() =>
  props.job?.total_segments
    ? (props.job.completed_segments / props.job.total_segments) * 100
    : 0,
);
const url = computed(() =>
  props.job ? `/api/jobs/${props.job.id}/audio` : "",
);
async function toggle(): Promise<void> {
  if (!audio.value) return;
  error.value = "";
  if (playing.value) audio.value.pause();
  else
    try {
      await audio.value.play();
    } catch {
      error.value = "播放未开始，请重试；也可以下载音频后播放。";
    }
}
function seek(event: Event): void {
  if (audio.value)
    audio.value.currentTime = Number((event.target as HTMLInputElement).value);
}
function adjustVolume(): void {
  if (audio.value) audio.value.volume = volume.value;
}
function metadataLoaded(): void {
  duration.value = Number.isFinite(audio.value?.duration)
    ? audio.value!.duration
    : (props.job?.duration_ms || 0) / 1000;
  adjustVolume();
}
watch([() => props.job?.id, () => props.job?.status], async () => {
  playing.value = false;
  position.value = 0;
  duration.value = (props.job?.duration_ms || 0) / 1000;
  error.value = "";
  if (props.job?.status === "completed" && props.autoPlayId === props.job.id) {
    await nextTick();
    try {
      await audio.value?.play();
    } catch {
      /* Browsers may require a second user gesture. */
    }
  }
});
</script>
<template>
  <section
    class="player-panel"
    aria-label="音频预览"
    :class="{ 'player-ready': job?.status === 'completed' }"
  >
    <template v-if="!job">
      <div class="player-empty">
        <span class="empty-icon"><Icon name="headphones" :size="25" /></span>
        <div>
          <h2>下一段好声音，从这里开始</h2>
          <p>生成完成后，可以在这里播放和下载。</p>
        </div>
      </div>
    </template>
    <template v-else-if="job.status !== 'completed'">
      <div class="player-state-head">
        <div>
          <span class="status" :class="job.status">{{
            statusLabels[job.status]
          }}</span>
          <h2>{{ job.title }}</h2>
        </div>
        <Icon name="sound" :size="28" />
      </div>
      <template v-if="isActive(job)">
        <progress :value="progress" max="100" aria-label="生成进度"></progress>
        <div class="progress-caption">
          <span>{{
            job.status === "merging"
              ? "片段已完成，正在整理音频…"
              : `${job.completed_segments} / ${job.total_segments} 段已完成`
          }}</span
          ><button class="text-button" @click="emit('action', job, 'cancel')">
            取消生成
          </button>
        </div>
        <p class="field-hint">可以关闭页面，回来后在当前浏览器继续查看。</p>
      </template>
      <div v-else class="failed-actions">
        <p role="status">
          {{ job.error || "已保存完成的片段，可以继续生成。" }}
        </p>
        <button
          class="button secondary compact"
          @click="emit('action', job, 'retry')"
        >
          <Icon name="refresh" :size="16" />继续生成
        </button>
      </div>
    </template>
    <template v-else>
      <audio
        ref="audio"
        :key="job.id"
        :src="url"
        preload="metadata"
        @play="playing = true"
        @pause="playing = false"
        @ended="playing = false"
        @timeupdate="position = audio?.currentTime || 0"
        @loadedmetadata="metadataLoaded"
        @error="error = '无法读取音频，文件可能已到期。请重试或重新生成。'"
      ></audio>
      <div class="recording-head">
        <div>
          <span class="result-label"
            ><span class="status-dot"></span
            >{{ job.preview ? "试听已就绪" : "声音已就绪" }}</span
          >
          <h2>{{ job.title }}</h2>
          <p>
            {{ job.channel_name }}<span>·</span
            >{{ job.response_format.toUpperCase() }}<span>·</span
            >{{ (job.audio_bytes / 1048576).toFixed(2) }} MB
          </p>
        </div>
        <a
          class="button secondary compact"
          :href="`${url}?attachment=true`"
          download
          ><Icon name="download" :size="17" />下载音频</a
        >
      </div>
      <div class="transport">
        <button
          class="play-button"
          :aria-label="playing ? '暂停播放' : '播放音频'"
          @click="toggle"
        >
          <Icon :name="playing ? 'pause' : 'play'" :size="24" /></button
        ><span class="time-label">{{ clockTime(position) }}</span
        ><input
          class="seek"
          type="range"
          aria-label="播放进度"
          min="0"
          :max="duration || 1"
          step="0.1"
          :value="position"
          @input="seek"
        /><span class="time-label">{{ clockTime(duration) }}</span
        ><label class="player-volume"
          ><Icon name="volume" :size="18" /><input
            v-model.number="volume"
            type="range"
            aria-label="播放音量"
            min="0"
            max="1"
            step="0.05"
            @input="adjustVolume"
        /></label>
      </div>
      <div class="result-footer">
        <span v-if="job.expires_at">{{ dateLabel(job.expires_at) }} 到期</span
        ><a
          v-if="job.subtitles"
          :href="`/api/jobs/${job.id}/subtitles`"
          download
          ><Icon name="download" :size="14" />下载 SRT 字幕</a
        >
      </div>
      <p v-if="error" class="error-text" role="alert">{{ error }}</p>
    </template>
  </section>
</template>
