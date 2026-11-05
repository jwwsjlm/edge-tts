<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { api, errorMessage, ApiError } from "./api";
import type { Channel, Job, Voice } from "./types";
import {
  cleanOptions,
  defaultOptions,
  isActive,
  statusLabels,
  dateLabel,
  visibleFields,
} from "./form";
import Controls from "./Controls.vue";
import Player from "./Player.vue";
import Icon from "./Icon.vue";

const text = ref("");
const channels = ref<Channel[]>([]);
const channelId = ref("");
const channel = computed(() =>
  channels.value.find((c) => c.id === channelId.value),
);
const values = ref<Record<string, string>>({});
const voices = ref<Voice[]>([]);
const voicesLoading = ref(false);
const voiceError = ref("");
const subtitles = ref(true);
const jobs = ref<Job[]>([]);
const selectedId = ref("");
const selectedJob = computed(
  () => jobs.value.find((job) => job.id === selectedId.value) || null,
);
const autoPlayId = ref("");
const retentionDays = ref(7);
const loading = ref(true);
const submitting = ref(false);
const error = ref("");
const notice = ref("");
const nextOffset = ref<number | null>(null);
const deletingId = ref("");
const savedOptions = new Map<string, Record<string, string>>();
const charCount = computed(() => Array.from(text.value).length);
const requiredMissing = computed(() =>
  channel.value
    ? visibleFields(channel.value, values.value).some(
        (f) => f.required && !values.value[f.name]?.trim(),
      )
    : true,
);
const canGenerate = computed(
  () =>
    channel.value &&
    !requiredMissing.value &&
    !submitting.value &&
    !voicesLoading.value &&
    !voiceError.value,
);
let timer: ReturnType<typeof setTimeout> | undefined;
let draftTimer: ReturnType<typeof setTimeout> | undefined;
let disposed = false;
let voiceRequest = 0;
const draftKey = "tts-studio-draft-v1";

function change(name: string, value: string): void {
  if (!channel.value) return;
  values.value = cleanOptions(channel.value, {
    ...defaultOptions(channel.value),
    ...values.value,
    [name]: value,
  });
}
async function loadVoices(): Promise<void> {
  if (!channel.value) return;
  const id = channel.value.id;
  const request = ++voiceRequest;
  voicesLoading.value = true;
  voiceError.value = "";
  try {
    const result = await api<{ voices: Voice[] }>(
      `/api/channels/${encodeURIComponent(id)}/voices`,
    );
    if (request !== voiceRequest) return;
    voices.value = result.voices;
    if (
      values.value.voice &&
      !voices.value.some((v) => v.name === values.value.voice) &&
      voices.value[0]
    )
      values.value.voice = voices.value[0].name;
  } catch (e) {
    if (request === voiceRequest) voiceError.value = errorMessage(e);
  } finally {
    if (request === voiceRequest) voicesLoading.value = false;
  }
}
watch(channelId, async (id, old) => {
  if (old) savedOptions.set(old, { ...values.value });
  const current = channels.value.find((c) => c.id === id);
  if (!current) return;
  values.value = cleanOptions(current, {
    ...defaultOptions(current),
    ...(savedOptions.get(id) || {}),
  });
  voices.value = [];
  await loadVoices();
});
async function refreshJobs(more = false): Promise<void> {
  const offset = more ? nextOffset.value : 0;
  if (offset === null) return;
  const result = await api<{ jobs: Job[]; next_offset: number | null }>(
    `/api/jobs?offset=${offset}`,
  );
  if (more)
    jobs.value = [
      ...jobs.value,
      ...result.jobs.filter((j) => !jobs.value.some((old) => old.id === j.id)),
    ];
  else {
    const updated = new Map(result.jobs.map((j) => [j.id, j]));
    jobs.value = [
      ...result.jobs,
      ...jobs.value.filter(
        (j) =>
          result.jobs.length === 50 &&
          (j.expires_at === null || j.expires_at * 1000 > Date.now()) &&
          !updated.has(j.id) &&
          j.created_at < (result.jobs.at(-1)?.created_at || Infinity),
      ),
    ];
  }
  if (more || jobs.value.length <= 50) nextOffset.value = result.next_offset;
  else if (nextOffset.value !== null) nextOffset.value = jobs.value.length;
  if (!selectedId.value || !jobs.value.some((j) => j.id === selectedId.value))
    selectedId.value = jobs.value.find(isActive)?.id || jobs.value[0]?.id || "";
}
async function poll(): Promise<void> {
  try {
    await refreshJobs();
    for (const job of jobs.value.filter(
      (j) => isActive(j) && !jobs.value.slice(0, 50).includes(j),
    )) {
      const updated = await api<Job>(`/api/jobs/${job.id}`);
      jobs.value = jobs.value.map((j) => (j.id === updated.id ? updated : j));
    }
  } catch (e) {
    notice.value = errorMessage(e);
    if (e instanceof ApiError && e.status === 401) return;
  }
  if (!disposed)
    timer = setTimeout(
      poll,
      document.hidden ? 10000 : jobs.value.some(isActive) ? 1500 : 10000,
    );
}
async function initialize(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    const session = await api<{ retention_days: number }>("/api/session");
    retentionDays.value = session.retention_days;
    channels.value = (
      await api<{ channels: Channel[] }>("/api/channels")
    ).channels;
    let preferred = "";
    try {
      const saved = JSON.parse(localStorage.getItem(draftKey) || "{}");
      text.value = typeof saved.text === "string" ? saved.text : "";
      preferred = saved.channelId;
      if (saved.options && preferred)
        savedOptions.set(preferred, saved.options);
    } catch {
      /* A disabled or full browser store must not prevent synthesis. */
    }
    channelId.value = channels.value.some((c) => c.id === preferred)
      ? preferred
      : channels.value[0]?.id || "";
    await refreshJobs();
    if (timer) clearTimeout(timer);
    timer = setTimeout(poll, 1500);
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    loading.value = false;
  }
}
async function generate(preview = false): Promise<void> {
  if (!channel.value || !canGenerate.value || (!text.value.trim() && !preview))
    return;
  submitting.value = true;
  error.value = "";
  notice.value = "";
  try {
    const job = await api<Job>("/api/jobs", {
      method: "POST",
      body: JSON.stringify({
        channel_id: channel.value.id,
        text: text.value.trim()
          ? text.value
          : "你好，欢迎来到语音工作台。让每一段文字，都拥有自己的声音。",
        options: cleanOptions(channel.value, values.value),
        subtitles: channel.value.supports_subtitles && subtitles.value,
        preview,
      }),
    });
    jobs.value.unshift(job);
    selectedId.value = job.id;
    if (preview) autoPlayId.value = job.id;
    notice.value = preview
      ? "试听任务已提交，完成后可播放。"
      : "任务已提交。关闭页面也会继续生成。";
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    submitting.value = false;
  }
}
async function jobAction(
  job: Job,
  action: "cancel" | "retry" | "delete",
): Promise<void> {
  try {
    error.value = "";
    if (action === "delete") {
      await api(`/api/jobs/${job.id}`, { method: "DELETE" });
      jobs.value = jobs.value.filter((j) => j.id !== job.id);
      deletingId.value = "";
      if (selectedId.value === job.id)
        selectedId.value = jobs.value[0]?.id || "";
    } else {
      const updated = await api<Job>(`/api/jobs/${job.id}/${action}`, {
        method: "POST",
      });
      jobs.value = jobs.value.map((j) => (j.id === job.id ? updated : j));
    }
  } catch (e) {
    error.value = errorMessage(e);
  }
}
watch(
  [text, channelId, values],
  () => {
    if (draftTimer) clearTimeout(draftTimer);
    draftTimer = setTimeout(() => {
      try {
        const options = Object.fromEntries(
          Object.entries(values.value).filter(
            ([name]) => name !== "reference_audio",
          ),
        );
        localStorage.setItem(
          draftKey,
          JSON.stringify({
            text: text.value,
            channelId: channelId.value,
            options,
          }),
        );
      } catch {
        /* Drafts are optional when browser storage is unavailable. */
      }
    }, 300);
  },
  { deep: true },
);
function keyboard(event: KeyboardEvent): void {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    event.preventDefault();
    void generate();
  }
}
onMounted(() => {
  void initialize();
  window.addEventListener("keydown", keyboard);
});
onUnmounted(() => {
  disposed = true;
  clearTimeout(timer);
  clearTimeout(draftTimer);
  window.removeEventListener("keydown", keyboard);
});
</script>

<template>
  <main class="workspace shell">
    <div class="page-heading">
      <div>
        <h1>文本转语音</h1>
        <p>选一种声音，让你的文字自然开口。</p>
      </div>
      <span class="free-label"
        ><span class="status-dot"></span>免费使用，无需登录</span
      >
    </div>
    <div v-if="error" class="message error" role="alert">
      <Icon name="close" :size="18" /><span>{{ error }}</span
      ><button class="text-button" @click="initialize">重新加载</button>
    </div>
    <div v-if="notice" class="message notice" role="status">
      <Icon name="check" :size="17" /><span>{{ notice }}</span
      ><button class="icon-button" aria-label="关闭提示" @click="notice = ''">
        <Icon name="close" :size="16" />
      </button>
    </div>
    <div class="studio-grid" :aria-busy="loading">
      <section class="editor-panel panel">
        <div class="panel-heading">
          <h2><Icon name="file" :size="18" />输入文本</h2>
          <button
            class="text-button"
            @click="
              text =
                '清晨，推开窗，风带着青草的气息走进房间。\n\n有些美好，不必匆忙。给自己一点时间，也给生活一点想象。\n\n从这一刻开始，让每一段文字，都拥有自己的声音。'
            "
          >
            试试示例
          </button>
        </div>
        <label for="script-text" class="sr-only">需要转换为语音的文本</label>
        <textarea
          id="script-text"
          v-model="text"
          class="script-editor"
          placeholder="在这里输入或粘贴你的文字…&#10;&#10;一段旁白、一篇文章，或是一个想被听见的故事。"
          spellcheck="false"
        ></textarea>
        <div class="editor-toolbar">
          <span
            ><strong>{{ charCount.toLocaleString() }}</strong> 字<span
              class="subtle-separator"
              >/</span
            >长文自动分段</span
          ><button
            class="text-button muted"
            :disabled="!text"
            @click="text = ''"
          >
            <Icon name="trash" :size="15" />清空
          </button>
        </div>
        <div class="editor-note">
          <Icon name="lock" :size="15" /><span
            >草稿保存在当前浏览器。生成任务仅对当前浏览器可见。</span
          >
        </div>
      </section>

      <aside class="settings-panel panel" aria-label="语音设置">
        <div class="panel-heading">
          <h2><Icon name="settings" :size="18" />声音设置</h2>
          <button
            v-if="channel"
            class="text-button muted"
            @click="values = defaultOptions(channel)"
          >
            重置
          </button>
        </div>
        <div class="settings-content">
          <div class="field">
            <label for="channel-choice">语音渠道</label
            ><select
              id="channel-choice"
              v-model="channelId"
              :disabled="loading || !channels.length"
            >
              <option v-if="!channels.length" value="">
                {{ loading ? "正在加载渠道…" : "暂无可用渠道" }}
              </option>
              <option v-for="item in channels" :key="item.id" :value="item.id">
                {{ item.name }}
              </option>
            </select>
            <p v-if="channel" class="channel-description">
              <span class="provider-symbol">{{
                channel.provider === "edge" ? "E" : "M"
              }}</span
              >{{ channel.description }}
            </p>
          </div>
          <div v-if="!loading && !channels.length" class="empty-channel">
            <p>还没有启用的语音渠道。</p>
            <a href="/admin">前往渠道管理<Icon name="arrow" :size="16" /></a>
          </div>
          <Controls
            v-if="channel"
            :channel="channel"
            :values="values"
            :voices="voices"
            :loading="voicesLoading"
            @change="change"
            @error="error = $event"
          />
          <div v-if="voiceError" class="inline-error" role="alert">
            {{ voiceError
            }}<button class="text-button" @click="loadVoices">
              重新加载音色
            </button>
          </div>
          <label v-if="channel?.supports_subtitles" class="checkbox-row"
            ><input v-model="subtitles" type="checkbox" /><span
              >同时生成字幕<small>完成后可下载 SRT 文件</small></span
            ></label
          >
        </div>
      </aside>
      <div class="generate-actions">
        <button
          class="button primary"
          :disabled="!canGenerate || !text.trim()"
          @click="generate(false)"
        >
          <Icon :name="submitting ? 'clock' : 'sound'" />{{
            submitting ? "正在提交…" : "生成语音"
          }}<Icon name="arrow" :size="18" /></button
        ><button
          class="button secondary"
          :disabled="!canGenerate"
          @click="generate(true)"
        >
          <Icon name="headphones" :size="18" />先试听一下
        </button>
        <p>
          试听前 120 字<span>·</span><kbd>Ctrl</kbd> + <kbd>Enter</kbd> 生成全文
        </p>
      </div>

      <Player
        class="player-area"
        :job="selectedJob"
        :auto-play-id="autoPlayId"
        @action="jobAction"
      />

      <section class="history-area" aria-label="生成记录">
        <div class="history-heading">
          <h2>
            生成记录<span v-if="jobs.length">{{ jobs.length }}</span>
          </h2>
          <span
            ><Icon name="clock" :size="15" />默认保留
            {{ retentionDays }} 天</span
          >
        </div>
        <div v-if="!jobs.length" class="history-empty">
          <Icon name="file" :size="28" />
          <div>
            <h3>{{ loading ? "正在加载记录…" : "还没有生成记录" }}</h3>
            <p>完成第一段语音后，随时回来继续播放或下载。</p>
          </div>
        </div>
        <div v-else class="job-list">
          <article
            v-for="job in jobs"
            :key="job.id"
            class="job-row"
            :class="{ selected: job.id === selectedId }"
          >
            <button
              class="job-select"
              :aria-label="`查看任务：${job.title}`"
              :aria-pressed="job.id === selectedId"
              @click="selectedId = job.id"
            >
              <span class="job-icon"
                ><Icon
                  :name="
                    job.status === 'completed'
                      ? 'play'
                      : isActive(job)
                        ? 'clock'
                        : 'file'
                  "
                  :size="18" /></span
              ><span class="job-copy"
                ><strong
                  >{{ job.preview ? "试听 · " : "" }}{{ job.title }}</strong
                ><span
                  >{{ job.channel_name }}<span class="dot">·</span
                  >{{ job.text_length }} 字<span class="dot">·</span
                  >{{ dateLabel(job.created_at) }}</span
                ></span
              >
            </button>
            <div class="job-status">
              <span class="status" :class="job.status">{{
                statusLabels[job.status]
              }}</span
              ><small v-if="isActive(job)"
                >{{ job.completed_segments }}/{{ job.total_segments }} 段</small
              >
            </div>
            <template v-if="deletingId === job.id"
              ><button
                class="text-button danger"
                @click="jobAction(job, 'delete')"
              >
                确认删除</button
              ><button
                class="icon-button"
                aria-label="取消删除"
                @click="deletingId = ''"
              >
                <Icon name="close" :size="16" /></button
            ></template>
            <button
              v-else
              class="icon-button delete-job"
              :aria-label="`删除任务：${job.title}`"
              @click="deletingId = job.id"
            >
              <Icon name="trash" :size="17" />
            </button>
          </article>
        </div>
        <button
          v-if="nextOffset !== null"
          class="text-button load-more"
          @click="refreshJobs(true).catch((e) => (error = errorMessage(e)))"
        >
          加载更多记录
        </button>
      </section>
    </div>
  </main>
</template>
