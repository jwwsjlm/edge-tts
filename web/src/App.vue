<script setup lang="ts">
import Icon from "./Icon.vue";
import Workspace from "./Workspace.vue";
import Admin from "./Admin.vue";
const admin = window.location.pathname === "/admin";
</script>
<template>
  <header class="site-header">
    <a class="brand" href="/" aria-label="TTS Studio 首页"
      ><span class="brand-mark"><Icon name="sound" :size="24" /></span
      ><span>TTS <strong>Studio</strong></span
      ><span class="brand-divider"></span
      ><span class="brand-caption">让文字被听见</span></a
    >
    <nav aria-label="主导航">
      <a href="/" :class="{ active: !admin }">语音工作台</a
      ><a href="/admin" :class="{ active: admin }"
        ><Icon name="settings" :size="17" />渠道管理</a
      >
    </nav>
  </header>
  <Admin v-if="admin" /><Workspace v-else />
  <footer class="site-footer">
    <span>TTS Studio <span class="footer-dot">·</span> Edge & MiMo</span
    ><span>自由创作，每一种声音都有可能。</span>
  </footer>
</template>
