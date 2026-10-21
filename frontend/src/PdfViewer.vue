<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { ChevronLeft, ChevronRight, Download, LoaderCircle, Maximize2, Minimize2, Minus, Plus, ScanLine } from 'lucide-vue-next';
import type { PDFDocumentProxy, PDFDocumentLoadingTask, RenderTask } from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import type { Location } from './api';

const props = defineProps<{ documentId: string; page: number; locations?: Location[] }>();
const emit = defineEmits<{ page: [value: number] }>();
const viewer = ref<HTMLElement>();
const container = ref<HTMLElement>();
const canvas = ref<HTMLCanvasElement>();
const expandButton = ref<HTMLButtonElement>();
const loading = ref(true), error = ref(''), pageCount = ref(1), width = ref(400);
const expanded = ref(false), fitWidth = ref(true), zoom = ref(1), naturalWidth = ref(612), naturalHeight = ref(792);
const scale = computed(() => fitWidth.value ? Math.max(150, width.value - 32) / naturalWidth.value : zoom.value);
const percent = computed(() => Math.round(scale.value * 100));
const displayWidth = computed(() => naturalWidth.value * scale.value);
const displayHeight = computed(() => naturalHeight.value * scale.value);
let pdf: PDFDocumentProxy | null = null;
let loadingTask: PDFDocumentLoadingTask | null = null;
let renderTask: RenderTask | null = null;
let observer: ResizeObserver | null = null;
let version = 0, paintVersion = 0;
const highlight = computed(() => props.locations?.find(x => x.page === props.page));
const highlightStyle = computed(() => {
  const loc = highlight.value;
  if (!loc) return {};
  return { left: `${loc.bbox[0] / loc.width * 100}%`, top: `${loc.bbox[1] / loc.height * 100}%`, width: `${(loc.bbox[2] - loc.bbox[0]) / loc.width * 100}%`, height: `${(loc.bbox[3] - loc.bbox[1]) / loc.height * 100}%` };
});

function changeZoom(delta: number) {
  zoom.value = Math.min(4, Math.max(.25, Math.round((scale.value + delta) * 100) / 100));
  fitWidth.value = false;
}

async function toggleExpanded() {
  expanded.value = !expanded.value;
  await nextTick();
  expandButton.value?.focus();
}

function onKeydown(event: KeyboardEvent) {
  if (!expanded.value) return;
  if (event.key === 'Escape') {
    event.preventDefault(); event.stopPropagation();
    void toggleExpanded();
  } else if (event.key === 'Tab') {
    const elements = [...(viewer.value?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], [tabindex="0"]') || [])];
    const first = elements[0], last = elements[elements.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  }
}

async function load() {
  const current = ++version;
  loading.value = true; error.value = '';
  renderTask?.cancel();
  await loadingTask?.destroy();
  pdf = null;
  try {
    const engine = await import('pdfjs-dist');
    engine.GlobalWorkerOptions.workerSrc = workerUrl;
    const ownTask = engine.getDocument({ url: `/api/documents/${props.documentId}/pdf`, withCredentials: true, cMapUrl: '/vendor/pdfjs/cmaps/', cMapPacked: true, standardFontDataUrl: '/vendor/pdfjs/standard_fonts/', wasmUrl: '/vendor/pdfjs/wasm/' });
    loadingTask = ownTask;
    const loaded = await ownTask.promise;
    if (current !== version) { await ownTask.destroy(); return; }
    pdf = loaded; pageCount.value = loaded.numPages;
    await paint();
  } catch (err) { if (current === version) { error.value = '原页加载失败，可下载 PDF 核对。'; loading.value = false; } }
}

async function paint() {
  if (!pdf || !canvas.value) return;
  const current = ++paintVersion;
  const previous = renderTask;
  previous?.cancel();
  loading.value = true; error.value = '';
  try {
    if (previous) await previous.promise.catch(() => {});
    if (current !== paintVersion) return;
    const page = await pdf.getPage(Math.min(Math.max(props.page, 1), pdf.numPages));
    if (current !== paintVersion) return;
    const natural = page.getViewport({ scale: 1 });
    naturalWidth.value = natural.width; naturalHeight.value = natural.height;
    // Re-render PDF vectors at the requested scale; cap the backing buffer on
    // memory-limited laptops while retaining the full scrollable page size.
    const ratio = Math.min(window.devicePixelRatio || 1, 2, Math.sqrt(16_000_000 / (displayWidth.value * displayHeight.value)));
    const viewport = page.getViewport({ scale: scale.value * ratio });
    const element = canvas.value;
    element.width = viewport.width; element.height = viewport.height;
    renderTask = page.render({ canvas: element, viewport });
    await renderTask.promise;
    if (current === paintVersion) loading.value = false;
  } catch (err: any) { if (err?.name !== 'RenderingCancelledException' && current === paintVersion) { error.value = '此页渲染失败，请下载原文查看。'; loading.value = false; } }
}
watch(() => props.documentId, load);
watch(() => props.page, paint);
watch([width, zoom, fitWidth], paint);
onMounted(async () => {
  await nextTick();
  observer = new ResizeObserver(entries => { const value = Math.round(entries[0].contentRect.width); if (Math.abs(value - width.value) > 2) width.value = value; });
  if (container.value) observer.observe(container.value);
  await load();
});
onBeforeUnmount(() => { version++; paintVersion++; observer?.disconnect(); renderTask?.cancel(); void loadingTask?.destroy(); });
</script>

<template>
  <div class="pdf-viewer" :class="{ 'pdf-expanded': expanded }" ref="viewer" :role="expanded ? 'dialog' : undefined" :aria-modal="expanded ? true : undefined" :aria-label="expanded ? 'PDF 放大预览' : 'PDF 预览'" @keydown="onKeydown">
    <div class="pdf-navigation">
      <button class="icon-button" aria-label="上一页" :disabled="page <= 1" @click="emit('page', page - 1)"><ChevronLeft :size="17" /></button>
      <span>第 {{ page }} / {{ pageCount }} 页</span>
      <button class="icon-button" aria-label="下一页" :disabled="page >= pageCount" @click="emit('page', page + 1)"><ChevronRight :size="17" /></button>
      <a :href="`/api/documents/${documentId}/pdf`" download class="icon-button" aria-label="下载原 PDF"><Download :size="16" /></a>
      <button ref="expandButton" class="pdf-expand-button" :aria-label="expanded ? '退出 PDF 放大预览' : '放大查看 PDF'" :title="expanded ? '退出放大（Esc）' : '放大查看 PDF'" @click="toggleExpanded"><Minimize2 v-if="expanded" :size="15" /><Maximize2 v-else :size="15" /><span>{{ expanded ? '退出放大' : '放大查看' }}</span></button>
    </div>
    <div class="pdf-zoom-toolbar" role="group" aria-label="PDF 缩放">
      <button class="icon-button" aria-label="缩小 PDF" :disabled="scale <= .25" @click="changeZoom(-.25)"><Minus :size="15" /></button>
      <output aria-label="PDF 缩放比例" aria-live="polite">{{ percent }}%</output>
      <button class="icon-button" aria-label="放大 PDF" :disabled="scale >= 4" @click="changeZoom(.25)"><Plus :size="15" /></button>
      <button class="pdf-fit-button" :aria-pressed="fitWidth" @click="fitWidth = true"><ScanLine :size="14" />适应宽度</button>
      <small v-if="expanded">Esc 退出</small>
    </div>
    <div class="pdf-scroll" ref="container" tabindex="0" aria-label="PDF 页面滚动区域">
      <div v-if="error" class="inline-error">{{ error }}</div>
      <div v-if="loading" class="pdf-loading pdf-render-status" role="status"><LoaderCircle class="spin" :size="17" /> 正在渲染原页</div>
      <div class="pdf-page-stage"><div class="pdf-canvas-wrap" :style="{ width: `${displayWidth}px`, height: `${displayHeight}px` }"><canvas ref="canvas" aria-label="PDF 原始页面" /><div v-if="highlight" class="pdf-highlight" :style="highlightStyle" /></div></div>
      <p class="pdf-hint">放大后可左右、上下滚动。高亮对应所选段落，复杂版式请核对原页。</p>
    </div>
  </div>
</template>
