<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import { api, errorMessage, ApiError } from "./api";
import type { AdminChannel } from "./types";
import Icon from "./Icon.vue";

const secret = ref("");
const authenticated = ref(false);
const busy = ref(false);
const error = ref("");
const notice = ref("");
const channels = ref<AdminChannel[]>([]);
const editing = ref<string | null>(null);
const deleting = ref(false);
const retention = ref(7);
const form = reactive({
  name: "",
  provider: "edge" as "edge" | "mimo",
  enabled: true,
  api_key: "",
  base_url: "",
  proxy: "",
  default_voice: "zh-CN-XiaoxiaoNeural",
  default_mode: "preset" as "preset" | "design" | "clone",
});
const selected = computed(() =>
  channels.value.find((c) => c.id === editing.value),
);
const headers = () => ({ "X-API-Key": secret.value });
function reset(): void {
  editing.value = null;
  deleting.value = false;
  notice.value = "";
  error.value = "";
  Object.assign(form, {
    name: "",
    provider: "edge",
    enabled: true,
    api_key: "",
    base_url: "",
    proxy: "",
    default_voice: "zh-CN-XiaoxiaoNeural",
    default_mode: "preset",
  });
}
function edit(channel: AdminChannel): void {
  editing.value = channel.id;
  deleting.value = false;
  notice.value = "";
  error.value = "";
  Object.assign(form, {
    name: channel.name,
    provider: channel.provider,
    enabled: channel.enabled,
    api_key: "",
    base_url: channel.base_url || "",
    proxy: channel.proxy || "",
    default_voice: channel.default_voice || "",
    default_mode: channel.default_mode,
  });
}
function providerChanged(): void {
  form.api_key = "";
  form.proxy = "";
  form.default_mode = "preset";
  form.base_url =
    form.provider === "mimo" ? "https://api.xiaomimimo.com/v1" : "";
  form.default_voice =
    form.provider === "mimo" ? "mimo_default" : "zh-CN-XiaoxiaoNeural";
}
async function load(): Promise<void> {
  channels.value = (
    await api<{ channels: AdminChannel[] }>("/api/admin/channels", {
      headers: headers(),
    })
  ).channels;
  retention.value = (
    await api<{ retention_days: number }>("/api/admin/settings", {
      headers: headers(),
    })
  ).retention_days;
}
async function perform(action: () => Promise<void>): Promise<void> {
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    await action();
  } catch (e) {
    error.value = errorMessage(e);
    if (e instanceof ApiError && e.status === 401) authenticated.value = false;
  } finally {
    busy.value = false;
  }
}
async function login(): Promise<void> {
  await perform(async () => {
    await load();
    authenticated.value = true;
    if (channels.value[0]) edit(channels.value[0]);
  });
}
async function save(): Promise<void> {
  await perform(async () => {
    const payload = {
      ...form,
      api_key: form.api_key || null,
      base_url: form.base_url || null,
      proxy: form.proxy || null,
      default_voice: form.default_voice || null,
    };
    const result = await api<AdminChannel>(
      editing.value
        ? `/api/admin/channels/${editing.value}`
        : "/api/admin/channels",
      {
        method: editing.value ? "PUT" : "POST",
        headers: headers(),
        body: JSON.stringify(payload),
      },
    );
    await load();
    edit(result);
    notice.value = "渠道已保存，新任务立即使用此配置。";
  });
}
async function test(): Promise<void> {
  if (!editing.value) return;
  await perform(async () => {
    const result = await api<{ message: string }>(
      `/api/admin/channels/${editing.value}/test`,
      { method: "POST", headers: headers() },
    );
    notice.value = result.message;
  });
}
async function remove(): Promise<void> {
  if (!editing.value) return;
  await perform(async () => {
    await api(`/api/admin/channels/${editing.value}`, {
      method: "DELETE",
      headers: headers(),
    });
    await load();
    reset();
    notice.value = "渠道已删除，已提交的任务不受影响。";
  });
}
async function saveRetention(): Promise<void> {
  await perform(async () => {
    await api("/api/admin/settings", {
      method: "PUT",
      headers: headers(),
      body: JSON.stringify({ retention_days: retention.value }),
    });
    notice.value = "保留时间已更新，将用于接下来结束的任务。";
  });
}
function logout(): void {
  secret.value = "";
  authenticated.value = false;
  channels.value = [];
  form.api_key = "";
  error.value = "";
  notice.value = "";
}
</script>

<template>
  <main class="admin shell">
    <div class="page-heading">
      <div>
        <h1>渠道管理</h1>
        <p>连接语音服务，让工作台拥有更多声音。</p>
      </div>
      <button
        v-if="authenticated"
        class="button secondary compact"
        @click="logout"
      >
        <Icon name="lock" :size="16" />退出管理</button
      ><a v-else href="/" class="text-button"
        ><Icon name="back" :size="17" />返回工作台</a
      >
    </div>
    <div v-if="error" class="message error" role="alert">
      <Icon name="close" :size="18" /><span>{{ error }}</span>
    </div>
    <div v-if="notice" class="message notice" role="status">
      <Icon name="check" :size="18" /><span>{{ notice }}</span>
    </div>
    <section v-if="!authenticated" class="login-panel panel">
      <span class="login-icon"><Icon name="lock" :size="28" /></span>
      <h2>进入管理员工作区</h2>
      <p>使用服务端配置中的管理员密钥登录。</p>
      <form @submit.prevent="login">
        <div class="field">
          <label for="admin-secret">管理员密钥</label
          ><input
            id="admin-secret"
            v-model="secret"
            type="password"
            autocomplete="current-password"
            placeholder="输入 config.yaml 中的 api_key"
            required
          />
        </div>
        <button class="button primary" :disabled="busy || !secret.trim()">
          {{ busy ? "正在验证…" : "进入管理" }}<Icon name="arrow" :size="18" />
        </button>
      </form>
      <p class="field-hint">密钥仅保留在此页面内存中，刷新后需要重新输入。</p>
    </section>
    <div v-else class="admin-grid">
      <aside class="channel-list panel">
        <div class="panel-heading">
          <h2>
            已添加渠道<span class="count">{{ channels.length }}</span>
          </h2>
          <button
            class="icon-button"
            aria-label="新增渠道"
            :disabled="busy"
            @click="reset"
          >
            <Icon name="plus" />
          </button>
        </div>
        <button
          v-for="item in channels"
          :key="item.id"
          class="channel-item"
          :class="{ selected: editing === item.id }"
          :disabled="busy"
          @click="edit(item)"
        >
          <span class="provider-icon" :class="item.provider">{{
            item.provider === "edge" ? "E" : "M"
          }}</span
          ><span
            ><strong>{{ item.name }}</strong
            ><small>{{
              item.provider === "edge" ? "Microsoft Edge" : "Xiaomi MiMo"
            }}</small></span
          ><span class="channel-state" :class="{ enabled: item.enabled }">{{
            item.enabled ? "已启用" : "已停用"
          }}</span></button
        ><button class="add-channel" :disabled="busy" @click="reset">
          <Icon name="plus" :size="18" />添加语音渠道
        </button>
      </aside>
      <section class="channel-editor panel">
        <div class="panel-heading">
          <h2>{{ editing ? "编辑渠道" : "添加渠道" }}</h2>
          <span class="section-note">仅管理员可见</span>
        </div>
        <form @submit.prevent="save">
          <div class="admin-form-grid">
            <div class="field">
              <label for="channel-name">渠道名称</label
              ><input
                id="channel-name"
                v-model="form.name"
                maxlength="80"
                placeholder="例如：小米 MiMo 主渠道"
                required
              />
            </div>
            <div class="field">
              <label for="provider">渠道类型</label
              ><select
                id="provider"
                v-model="form.provider"
                :disabled="!!editing"
                @change="providerChanged"
              >
                <option value="edge">Microsoft Edge</option>
                <option value="mimo">小米 MiMo</option>
              </select>
            </div>
          </div>
          <template v-if="form.provider === 'mimo'"
            ><div class="field">
              <label for="base-url">API 地址</label
              ><input
                id="base-url"
                v-model="form.base_url"
                type="url"
                placeholder="https://api.xiaomimimo.com/v1"
              />
              <p class="field-hint">
                填写 API 基础地址，服务会自动追加语音接口路径。
              </p>
            </div>
            <div class="field">
              <label for="provider-key"
                >API 密钥<span v-if="selected?.has_api_key" class="configured"
                  ><Icon name="check" :size="13" />已配置</span
                ></label
              ><input
                id="provider-key"
                v-model="form.api_key"
                type="password"
                autocomplete="off"
                :placeholder="
                  selected?.has_api_key
                    ? '留空保留原密钥；输入新值可替换'
                    : '输入 MiMo API Key'
                "
              />
              <p class="field-hint">密钥保存在服务端，不会下发给普通用户。</p>
            </div></template
          >
          <div class="admin-form-grid">
            <div class="field">
              <label for="default-voice">默认音色</label
              ><input
                id="default-voice"
                v-model="form.default_voice"
                :placeholder="
                  form.provider === 'edge'
                    ? 'zh-CN-XiaoxiaoNeural'
                    : 'mimo_default'
                "
              />
            </div>
            <div v-if="form.provider === 'mimo'" class="field">
              <label for="default-mode">默认模式</label
              ><select id="default-mode" v-model="form.default_mode">
                <option value="preset">预置音色</option>
                <option value="design">设计音色</option>
                <option value="clone">克隆音色</option>
              </select>
            </div>
          </div>
          <div class="field">
            <label for="proxy">代理地址<span class="optional">可选</span></label
            ><input
              id="proxy"
              v-model="form.proxy"
              type="url"
              placeholder="http://127.0.0.1:7890"
            />
          </div>
          <label class="checkbox-row"
            ><input v-model="form.enabled" type="checkbox" /><span
              >启用此渠道<small>启用后，普通用户可在工作台选择。</small></span
            ></label
          >
          <div class="admin-form-actions">
            <button
              class="button primary compact"
              :disabled="busy || !form.name.trim()"
            >
              <Icon name="check" :size="18" />{{
                busy ? "处理中…" : "保存渠道"
              }}</button
            ><button
              v-if="editing"
              class="button secondary compact"
              type="button"
              :disabled="busy"
              @click="test"
            >
              <Icon name="refresh" :size="17" />测试已保存配置</button
            ><button
              v-if="editing && !deleting"
              class="text-button danger"
              type="button"
              :disabled="busy"
              @click="deleting = true"
            >
              删除渠道
            </button>
          </div>
          <p v-if="editing && form.provider === 'mimo'" class="field-hint">
            连接测试会合成一段简短语音，使用此渠道的上游额度。
          </p>
          <div v-if="deleting" class="delete-confirm">
            <p>
              删除后，新任务将无法再选择此渠道。已提交的任务继续使用原配置。
            </p>
            <button
              type="button"
              class="button danger-button compact"
              :disabled="busy"
              @click="remove"
            >
              确认删除</button
            ><button
              type="button"
              class="text-button"
              @click="deleting = false"
            >
              取消
            </button>
          </div>
        </form>
      </section>
      <section class="retention-panel panel">
        <div>
          <h2>结果保留时间</h2>
          <p>任务结束后，自动清理到期文本、参考音频和下载文件。</p>
        </div>
        <form @submit.prevent="saveRetention">
          <label for="retention-days" class="sr-only">保留天数</label
          ><input
            id="retention-days"
            v-model.number="retention"
            type="number"
            min="1"
            max="3650"
            required
          /><span>天</span
          ><button class="button secondary compact" :disabled="busy">
            保存
          </button>
        </form>
      </section>
    </div>
  </main>
</template>
