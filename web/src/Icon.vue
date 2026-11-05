<script setup lang="ts">
defineProps<{ name: string; size?: number }>();
const paths: Record<string, string> = {
  sound: "M4 10v4m4-8v12m4-15v18m4-15v12m4-8v4",
  play: "m8 5 11 7-11 7V5Z",
  pause: "M8 5v14M16 5v14",
  download: "M12 3v12m-5-5 5 5 5-5M5 16v4h14v-4",
  arrow: "M5 12h14m-5-5 5 5-5 5",
  back: "M19 12H5m5-5-5 5 5 5",
  settings: "M4 7h16M4 17h16M8 4v6m8 4v6",
  check: "m5 12 4 4L19 6",
  trash: "M4 7h16M9 3h6l1 4M7 7l1 14h8l1-14M10 11v6m4-6v6",
  refresh: "M20 7v5h-5M4 17v-5h5M6 6a8 8 0 0 1 13 2M5 16a8 8 0 0 0 13 2",
  file: "M14 3H6v18h12V7l-4-4Zm0 0v5h4M9 12h6m-6 4h6",
  plus: "M12 5v14M5 12h14",
  headphones: "M4 13v-2a8 8 0 0 1 16 0v2M4 12H3v7h4v-7H4Zm16 0h1v7h-4v-7h3Z",
  upload: "M12 16V3m-5 5 5-5 5 5M4 16v5h16v-5",
  lock: "M6 10h12v11H6V10Zm3 0V6a3 3 0 0 1 6 0v4m-3 4v3",
  close: "m6 6 12 12M6 18 18 6",
  volume: "M4 9h4l5-4v14l-5-4H4V9Zm12 0a5 5 0 0 1 0 6m3-9a9 9 0 0 1 0 12",
  clock: "M12 8v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z",
};
</script>
<template>
  <svg
    :width="size || 20"
    :height="size || 20"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.7"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path :d="paths[name] || paths.sound" />
  </svg>
</template>
