<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue';
import { ArrowLeft, ArrowRight, Check, CheckCheck, ChevronDown, CircleAlert, Download, FileText, FolderOpen, KeyRound, Languages, Library, LoaderCircle, LogOut, Menu, MessageSquare, MoreHorizontal, PanelRightClose, PanelRightOpen, Pencil, Plane, Plus, Search, Settings, ShieldCheck, Sparkles, StickyNote, Trash2, X } from 'lucide-vue-next';
import { api, auth, uploadPdf, type Detail, type DocumentItem, type Paragraph } from './api';
import SettingsDialog from './SettingsDialog.vue';
import PdfViewer from './PdfViewer.vue';
import SourceText from './SourceText.vue';

const booting = ref(true), localAdmin = ref(false), authBusy = ref(false), authError = ref('');
const credentials = reactive({ username: '', password: '', display_name: '' });
const documents = ref<DocumentItem[]>([]), detail = ref<Detail | null>(null), selectedId = ref('');
const usage = ref({ characters: 0, calls: 0, limit: 0, max_upload_mb: 50, max_pages: 200 });
const dismissedJobErrors = ref<string[]>([]);
const settings = ref<any>(null), showSettings = ref(false), mobileLibrary = ref(false);
const librarySearch = ref(''), textSearch = ref(''), showTextSearch = ref(false), showMenu = ref(false);
const readerPane = ref<HTMLElement>(), fileInput = ref<HTMLInputElement>(), loadingDoc = ref(false);
const chatPane = ref<HTMLElement>();
const engineNames:Record<string,string>={ai:'AI 学术翻译',google:'Google GTX · 免 Key',bing:'Bing · 实验',mymemory:'MyMemory · 免 Key',baidu:'百度通用翻译',youdao:'有道智云翻译',microsoft:'Microsoft Translator'};
const engine = ref(engineNames[localStorage.getItem('pp-engine') || ''] ? localStorage.getItem('pp-engine')! : 'mymemory');
const mode = ref('bilingual'), fontSize = ref(Number(localStorage.getItem('pp-font-size')) || 17);
const sidePanel = ref<'pdf' | 'chat' | 'notes' | 'outline' | null>(null), pdfPage = ref(1), selectedParagraph = ref('');
const confirmReflow = ref<'local'|'ai'|'restore'|null>(null);
const currentParagraph = ref(''), progressSaved = ref(false), autoTranslate = ref(false);
const uploading = ref(false), uploadPercent = ref(0), dragOver = ref(false), submitting = ref(false);
const notes = ref(''), noteStatus = ref(''), notesDirty = ref(false), notesOwner = ref('');
const question = ref(''), questionParagraph = ref<string | null>(null), chatBusy = ref(false);
const questionComposing = ref(false);
const confirmFull = ref(false), editor = ref<{ kind: 'title' | 'paragraph'; id: string; text: string } | null>(null);
const savingEdit = ref(false), toast = ref(''), toastError = ref(false);
let toastTimer: ReturnType<typeof setTimeout>, progressTimer: ReturnType<typeof setTimeout>, notesTimer: ReturnType<typeof setTimeout>, autoTimer: ReturnType<typeof setTimeout>;
let pollTimer: ReturnType<typeof setInterval>, refreshing = false, requestVersion = 0, hydratingNotes = false, restoring = false;

const filteredDocuments = computed(() => documents.value.filter(x => x.title.toLowerCase().includes(librarySearch.value.toLowerCase())));
const paragraphs = computed(() => detail.value?.paragraphs || []);
const currentParagraphIds = computed(() => new Set(paragraphs.value.map(p => p.id)));
const filteredParagraphs = computed(() => {
  const search = textSearch.value.trim().toLowerCase();
  return search ? paragraphs.value.filter(p => p.text.toLowerCase().includes(search) || p.translation?.text.toLowerCase().includes(search)) : paragraphs.value;
});
const activeJobs = computed(() => detail.value?.jobs.filter(x => ['queued', 'running'].includes(x.status)) || []);
const translationJob = computed(() => activeJobs.value.find(x => x.kind === 'translate'));
const lastTranslationJob = computed(() => detail.value?.jobs.find(x => x.kind === 'translate'));
function translatable(p:Paragraph){return !['equation','figure','table','authors'].includes(p.kind);}
const textParagraphs = computed(()=>paragraphs.value.filter(translatable));
const outline = computed(()=>paragraphs.value.filter(p=>['title','heading','figure','table'].includes(p.kind)));
const layoutJob = computed(()=>activeJobs.value.find(j=>['parse','ai_reflow'].includes(j.kind)));
const lastLayoutJob = computed(()=>detail.value?.jobs.find(j=>['parse','ai_reflow'].includes(j.kind)));
const translatedCount = computed(() => textParagraphs.value.filter(x => x.translation?.status === 'done').length);
const remainingCount = computed(() => textParagraphs.value.length - translatedCount.value);
function taskAI(role: 'translation' | 'chat') {
  const selected=settings.value?.ai_tasks?.[role], legacy=settings.value?.ai;
  if(!selected) return legacy;
  const profile=settings.value?.ai_profiles?.[selected.provider] || (legacy?.provider===selected.provider ? legacy : {});
  return {...profile,...selected};
}
const translationAI = computed(() => taskAI('translation'));
const chatAI = computed(() => taskAI('chat'));
const hasAI = computed(() => Boolean(translationAI.value?.has_key && chatAI.value?.has_key));
const configured = computed(() => {
  if(engine.value==='ai')return Boolean(translationAI.value?.has_key);
  if(['google','bing','mymemory'].includes(engine.value))return true;
  const s=settings.value?.[engine.value];return engine.value==='microsoft' ? Boolean(s?.has_key) : Boolean(s?.app_id && s?.has_secret);
});
const currentIndex = computed(() => Math.max(0, paragraphs.value.findIndex(p => p.id === currentParagraph.value)));
const selectedLocations = computed(() => paragraphs.value.find(p => p.id === selectedParagraph.value)?.locations);
const chatRunning = computed(() => activeJobs.value.some(x => x.kind === 'chat'));

function notify(message: string, error = false) { toast.value = message; toastError.value = error; clearTimeout(toastTimer); toastTimer = setTimeout(() => toast.value = '', error ? 8000 : 4500); }
function fail(error: any) { notify(error?.message || '操作失败，请重试。', true); }
function bytes(value: number) { return value >= 1024 * 1024 ? `${(value / 1024 / 1024).toFixed(1)} MB` : `${Math.ceil(value / 1024)} KB`; }
function statusLabel(value: string) { return ({ queued: '等待解析', parsing: '正在解析', ready: '可以阅读', needs_ocr: '需要 OCR', error: '解析失败' } as Record<string, string>)[value] || value; }
async function refreshSettings() { settings.value = await api('/settings'); usage.value = await api('/usage'); }
async function loadLibrary() { documents.value = await api('/documents'); }
async function enterWorkspace() {
  await Promise.all([loadLibrary(), refreshSettings()]);
  const previous = localStorage.getItem(`pp-last-document-${auth.user?.id}`);
  if (previous && documents.value.some(x => x.id === previous)) await openDocument(previous);
}
async function signIn() {
  authBusy.value = true; authError.value = '';
  try {
    const result = await api('/login', { method: 'POST', body: JSON.stringify(credentials) });
    auth.user = result.user; auth.csrf = result.csrf; credentials.password = '';
    await enterWorkspace();
  } catch (error: any) { authError.value = error.message; }
  finally { authBusy.value = false; }
}
async function logout() {
  try { await flushNotes(); await api('/logout', { method: 'POST' }); auth.user = null; auth.csrf = ''; detail.value = null; selectedId.value = ''; documents.value = []; sidePanel.value = null; autoTranslate.value = false; }
  catch (error) { fail(error); }
}
async function openDocument(id: string) {
  try {
    await flushNotes(); clearTimeout(progressTimer); clearTimeout(autoTimer);
    selectedId.value = id; loadingDoc.value = true; detail.value = null; textSearch.value = ''; mobileLibrary.value = false;
    questionParagraph.value = null; selectedParagraph.value = ''; progressSaved.value = false;
    const version = ++requestVersion;
    const data = await api<Detail>(`/documents/${id}?engine=${engine.value}`);
    if (version !== requestVersion) return;
    detail.value = data; hydratingNotes = true; notes.value = data.notes.text; notesOwner.value = id; notesDirty.value = false; noteStatus.value = '';
    pdfPage.value = data.progress?.page || 1; currentParagraph.value = data.progress?.paragraph_id || data.paragraphs[0]?.id || '';
    selectedParagraph.value = currentParagraph.value;
    localStorage.setItem(`pp-last-document-${auth.user?.id}`, id);
    hydratingNotes = false; loadingDoc.value = false;
    if (!booting.value) await restoreReadingPosition();
    updateLibraryFromDetail();
  } catch (error) { fail(error); }
  finally { loadingDoc.value = false; }
}
async function restoreReadingPosition() {
  restoring = true;
  await nextTick();
  const paragraphId = detail.value?.progress?.paragraph_id;
  if (paragraphId) document.getElementById(`p-${paragraphId}`)?.scrollIntoView({ block: 'start' });
  else readerPane.value?.scrollTo(0, 0);
  setTimeout(() => { restoring = false; onScroll(); }, 300);
}
function updateLibraryFromDetail() { if (detail.value) documents.value = documents.value.map(x => x.id === detail.value!.document.id ? { ...x, ...detail.value!.document, paragraph_count: paragraphs.value.length } : x); }
async function refreshDocument() {
  if (!selectedId.value || refreshing || loadingDoc.value) return;
  const id = selectedId.value, version = requestVersion;
  refreshing = true;
  try {
    const hadJobs = activeJobs.value.length > 0;
    const result = await api<Detail>(`/documents/${id}?engine=${engine.value}`);
    if (version !== requestVersion || id !== selectedId.value) return;
    detail.value = result; updateLibraryFromDetail();
    if(!result.paragraphs.some(p=>p.id===currentParagraph.value)) {currentParagraph.value=result.progress?.paragraph_id || result.paragraphs[0]?.id || '';selectedParagraph.value=currentParagraph.value;}
    if (hadJobs && activeJobs.value.length === 0) { await refreshSettings(); scheduleAuto(); }
  } catch (error) { fail(error); }
  finally { refreshing = false; }
}
async function handleUpload(file?: File) {
  if (!file || uploading.value) return;
  if (file.size > usage.value.max_upload_mb * 1024 * 1024) { notify(`单篇 PDF 上限为 ${usage.value.max_upload_mb}MB。`, true); return; }
  uploading.value = true; uploadPercent.value = 0;
  try {
    const result = await uploadPdf(file, p => uploadPercent.value = p);
    await loadLibrary(); await openDocument(result.document.id);
    notify(result.duplicate ? '这篇文献已在你的文献库中，已打开。' : 'PDF 已上传，正在后台重建段落。');
  } catch (error) { fail(error); }
  finally { uploading.value = false; if (fileInput.value) fileInput.value.value = ''; }
}
function onFile(event: Event) { void handleUpload((event.target as HTMLInputElement).files?.[0]); }
function drop(event: DragEvent) { dragOver.value = false; void handleUpload(event.dataTransfer?.files[0]); }
async function openSample() {
  uploading.value = true;
  try { const result = await api('/sample', { method: 'POST' }); await loadLibrary(); await openDocument(result.document.id); notify('已打开合成测试文献。它用于检查解析与阅读界面，不代表真实研究。'); }
  catch (error) { fail(error); } finally { uploading.value = false; }
}
function requireService() { if (configured.value) return true; showSettings.value = true; notify('先保存所选引擎的服务配置，再试译一个段落。'); return false; }
async function translate(ids: string[] | null, force = false, silent = false) {
  if (!detail.value || submitting.value || translationJob.value || layoutJob.value) return;
  if (!configured.value) { if (!silent) requireService(); return; }
  submitting.value = true;
  try {
    await api(`/documents/${selectedId.value}/translate`, { method: 'POST', body: JSON.stringify({ engine: engine.value, paragraph_ids: ids, force }) });
    confirmFull.value = false; await refreshDocument();
  } catch (error) { if (!silent) fail(error); }
  finally { submitting.value = false; }
}
function fullTranslation() { if (!requireService()) return; if (!remainingCount.value) { notify('当前引擎的所有段落均已缓存，可以逐段选择重译。'); return; } confirmFull.value = true; }
async function cancelTranslation() { if (translationJob.value) { try { await api(`/jobs/${translationJob.value.id}/cancel`, { method: 'POST' }); await refreshDocument(); } catch (error) { fail(error); } } }
function scheduleAuto() {
  clearTimeout(autoTimer);
  if (!autoTranslate.value || !configured.value || translationJob.value || textSearch.value || detail.value?.document.status !== 'ready') return;
    autoTimer = setTimeout(() => { const next = paragraphs.value.slice(currentIndex.value, currentIndex.value + 3).filter(p => translatable(p) && !p.translation).map(p => p.id); if (next.length) void translate(next, false, true); }, 900);
}
function toggleAuto() { if (autoTranslate.value && !requireService()) autoTranslate.value = false; scheduleAuto(); }
async function changeEngine() {
  localStorage.setItem('pp-engine', engine.value);
  if (!selectedId.value) return;
  const id = selectedId.value, version = ++requestVersion;
  try { const result = await api<Detail>(`/documents/${id}?engine=${engine.value}`); if (version === requestVersion && id === selectedId.value) { detail.value = result; updateLibraryFromDetail(); } scheduleAuto(); }
  catch (error) { fail(error); }
}
function onScroll() {
  if (restoring || !readerPane.value || !detail.value || textSearch.value) return;
  const top = readerPane.value.getBoundingClientRect().top + 80;
  const items = [...readerPane.value.querySelectorAll<HTMLElement>('.paragraph')];
  const visible = items.find(x => x.getBoundingClientRect().bottom > top);
  if (!visible) return;
  const id = visible.dataset.id!, paragraph = paragraphs.value.find(x => x.id === id);
  if (!paragraph) return;
  currentParagraph.value = id; progressSaved.value = false;
  if (sidePanel.value !== 'pdf') { pdfPage.value = paragraph.page; selectedParagraph.value = id; }
  clearTimeout(progressTimer);
  const documentId = selectedId.value;
  progressTimer = setTimeout(async () => {
    try { await api(`/documents/${documentId}/progress`, { method: 'PUT', body: JSON.stringify({ paragraph_id: id, page: paragraph.page }) }); if (selectedId.value === documentId) progressSaved.value = true; }
    catch (error) { fail(error); }
  }, 900);
  scheduleAuto();
}
async function jump(id: string) { textSearch.value = ''; await nextTick(); document.getElementById(`p-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
function original(paragraph?: Paragraph) { if (paragraph) { pdfPage.value = paragraph.page; selectedParagraph.value = paragraph.id; currentParagraph.value = paragraph.id; } sidePanel.value = 'pdf'; }
function pdfPageChanged(page: number) { pdfPage.value = page; const p = paragraphs.value.find(x => x.page === page); if (p) { selectedParagraph.value = p.id; void jump(p.id); } }
function ask(paragraph: Paragraph) { questionParagraph.value = paragraph.id; sidePanel.value = 'chat'; if (!question.value) question.value = '请解释这一段的主要意思和关键术语。'; }
async function sendQuestion() {
  if (!question.value.trim() || chatBusy.value || chatRunning.value) return;
  if (!chatAI.value?.has_key) { showSettings.value = true; notify('请先配置问答与重排使用的 AI 服务。'); return; }
  chatBusy.value = true;
  try { await api(`/documents/${selectedId.value}/chat`, { method: 'POST', body: JSON.stringify({ question: question.value, paragraph_id: questionParagraph.value }) }); question.value = ''; await refreshDocument(); }
  catch (error) { fail(error); } finally { chatBusy.value = false; }
}
function onQuestionKeydown(event: KeyboardEvent) {
  if(event.key !== 'Enter' || event.shiftKey || event.isComposing || event.keyCode === 229 || questionComposing.value) return;
  event.preventDefault();
  if(!event.repeat) void sendQuestion();
}
function retryQuestion(messageId: string) {
  const messages = detail.value?.messages || [], index = messages.findIndex(m => m.id === messageId);
  const previous = messages[index - 1];
  if (previous?.role === 'user') { question.value = previous.text; questionParagraph.value = null; void sendQuestion(); }
}
function jobErrorText(error?: string | null) {
  return usage.value.limit <= 0 && /(?:今日|每日)发送字符上限/.test(error || '')
    ? '此前任务被旧版字数限制中止；当前已取消本站字数限制，可以重新执行。' : error;
}
watch(notes, () => { if (hydratingNotes || !notesOwner.value) return; notesDirty.value = true; noteStatus.value = '正在保存…'; clearTimeout(notesTimer); notesTimer = setTimeout(() => void flushNotes(), 700); });
watch(() => {
  const messages = detail.value?.messages || [], last = messages[messages.length - 1];
  return JSON.stringify([selectedId.value, sidePanel.value, messages.length, last?.id, last?.status, last?.text]);
}, async () => {
  if (sidePanel.value !== 'chat') return;
  await nextTick();
  if (chatPane.value) chatPane.value.scrollTop = chatPane.value.scrollHeight;
});
watch(sidePanel, async () => {
  const anchor = currentParagraph.value;
  restoring = true;
  await nextTick();
  if (anchor) document.getElementById(`p-${anchor}`)?.scrollIntoView({ block: 'start' });
  setTimeout(() => restoring = false, 300);
});
async function flushNotes() {
  clearTimeout(notesTimer);
  if (!notesDirty.value || !notesOwner.value) return;
  const id = notesOwner.value, text = notes.value;
  try { await api(`/documents/${id}/notes`, { method: 'PUT', body: JSON.stringify({ text }) }); if (notesOwner.value === id && notes.value === text) { notesDirty.value = false; noteStatus.value = '已保存'; } }
  catch (error) { noteStatus.value = '保存失败，请重试'; fail(error); }
}
async function saveEdit() {
  if (!editor.value || savingEdit.value) return;
  savingEdit.value = true;
  try { const item = editor.value; await api(item.kind === 'title' ? `/documents/${selectedId.value}` : `/documents/${selectedId.value}/paragraphs/${item.id}`, { method: 'PATCH', body: JSON.stringify({ text: item.text }) }); editor.value = null; await refreshDocument(); notify('已保存修改。原文变更后的段落需要重新翻译。'); }
  catch (error) { fail(error); } finally { savingEdit.value = false; }
}
async function deleteDocument() {
  showMenu.value = false;
  if (!window.confirm('删除这篇文献及其译文、笔记和问答记录？')) return;
  try { await api(`/documents/${selectedId.value}`, { method: 'DELETE' }); detail.value = null; selectedId.value = ''; notesOwner.value = ''; notesDirty.value = false; localStorage.removeItem(`pp-last-document-${auth.user?.id}`); await loadLibrary(); notify('文献已删除。'); }
  catch (error) { fail(error); }
}
async function retryParse() { try { await api(`/documents/${selectedId.value}/reparse`, { method: 'POST' }); await refreshDocument(); } catch (error) { fail(error); } }
async function reflowDocument(){
  const action=confirmReflow.value;if(!action)return;
  submitting.value=true;
  try{await flushNotes();await api(`/documents/${selectedId.value}/${action==='ai'?'ai-reflow':action==='restore'?'restore-layout':'reparse'}`,{method:'POST'});confirmReflow.value=null;showMenu.value=false;await refreshDocument();notify(action==='restore'?'已恢复上个阅读版本。':'重排已开始，原阅读版本已保留，可在更多操作中恢复。');}
  catch(error){fail(error);}finally{submitting.value=false;}
}
function requestAIReflow(){if(!chatAI.value?.has_key){showSettings.value=true;notify('请先配置问答与重排的 AI 接口。');return;}confirmReflow.value='ai';}
async function jumpOutline(p:Paragraph){currentParagraph.value=p.id;selectedParagraph.value=p.id;if(window.innerWidth<=760)sidePanel.value=null;await jump(p.id);}
function changeFont(delta: number) { fontSize.value = Math.min(23, Math.max(14, fontSize.value + delta)); localStorage.setItem('pp-font-size', String(fontSize.value)); }
async function home() { await flushNotes(); detail.value = null; selectedId.value = ''; notesOwner.value = ''; sidePanel.value = null; mobileLibrary.value = false; }
onMounted(async () => {
  try { const result = await api('/session'); auth.user = result.user; auth.csrf = result.csrf || '';localAdmin.value=Boolean(result.local_admin); if (auth.user) await enterWorkspace();else if(result.needs_admin)authError.value='管理员尚未设置，请在服务器本机初始化管理员。'; }
  catch (error: any) { authError.value = '无法连接本机服务。' + error.message; }
  finally { booting.value = false; }
  if (detail.value) await restoreReadingPosition();
  pollTimer = setInterval(() => { if (detail.value && (activeJobs.value.length || ['queued', 'parsing'].includes(detail.value.document.status))) void refreshDocument(); }, 1400);
});
onBeforeUnmount(() => { clearInterval(pollTimer); clearTimeout(progressTimer); clearTimeout(notesTimer); clearTimeout(autoTimer); clearTimeout(toastTimer); });
</script>

<template>
  <div v-if="booting" class="boot-screen"><Plane :size="32" /><LoaderCircle class="spin" :size="20" /><p>正在打开阅读空间</p></div>
  <div v-else-if="!auth.user" class="auth-layout">
    <section class="auth-story"><a class="brand" href="/"><span class="brand-mark"><Plane :size="22" /></span><span>PaperPilot<small>WEB READER</small></span></a><div class="auth-story-main"><span class="eyebrow">A QUIETER WAY TO READ</span><h1>逐段对照，<br />让文献更易读。</h1><p>原文与理解之间，少一次来回切换。<br />为专注的科研阅读，留一个安静的空间。</p><div class="reading-preview"><div class="preview-meta"><span class="tiny-dot" /> 阅读方式示意 <span>EN → 中文</span></div><p class="preview-english">A good question is the beginning of understanding.</p><div class="preview-chinese">一个好问题，是理解的开始。</div><div class="preview-line" /><div class="preview-line short" /></div></div><footer><ShieldCheck :size="15" /> 自主托管 · 文献保存在你自己的服务中</footer></section>
    <section class="auth-form-panel"><div class="auth-form"><span class="eyebrow">INVITED READERS</span><h2>进入阅读空间</h2><p>仅接受管理员创建的账号。</p><form @submit.prevent="signIn"><label>用户名<input v-model="credentials.username" required minlength="2" maxlength="40" autocomplete="username" /></label><label>密码<input v-model="credentials.password" required maxlength="256" type="password" autocomplete="current-password" /></label><p v-if="authError" class="inline-error" role="alert">{{ authError }}</p><button class="primary-button auth-submit" :disabled="authBusy"><LoaderCircle v-if="authBusy" class="spin" :size="17" />进入阅读空间<ArrowRight :size="17" /></button></form><p class="auth-footnote">不开放注册。账号由管理员在后台添加；服务器本机可直接进入管理员空间。</p><span class="version-label">PAPERPILOT WEB / 0.2</span></div></section>
  </div>
  <div v-else class="app-shell">
    <input ref="fileInput" class="visually-hidden" type="file" accept=".pdf,application/pdf" aria-label="上传 PDF 文献" @change="onFile" />
    <div v-if="mobileLibrary" class="sidebar-scrim" @click="mobileLibrary = false" />
    <aside class="sidebar" :class="{ 'mobile-open': mobileLibrary }">
      <button class="brand brand-button" @click="home"><span class="brand-mark"><Plane :size="21" /></span><span>PaperPilot<small>WEB READER</small></span></button>
      <div class="workspace-tag"><span class="tiny-dot" />{{ auth.user.display_name }} 的阅读空间<ShieldCheck :size="12" /></div>
      <button class="primary-button upload-sidebar" :disabled="uploading" @click="fileInput?.click()"><LoaderCircle v-if="uploading" :size="16" class="spin" /><Plus v-else :size="18" />{{ uploading ? `上传中 ${uploadPercent}%` : '导入 PDF 文献' }}</button>
      <div class="library-heading"><span><Library :size="14" />我的文献</span><small>{{ documents.length }}</small></div>
      <div class="search-input library-search"><Search :size="14" /><input v-model="librarySearch" placeholder="搜索文献" aria-label="搜索文献库" /></div>
      <nav class="document-list" aria-label="文献列表"><button v-for="item in filteredDocuments" :key="item.id" class="document-item" :class="{ selected: selectedId === item.id }" @click="openDocument(item.id)"><span class="document-icon"><FileText :size="19" /></span><span class="document-info"><strong>{{ item.title.replace(/\.pdf$/i, '') }}</strong><small><span class="doc-dot" :class="item.status" />{{ item.status === 'ready' ? `${item.page_count} 页 · ${bytes(item.size)}` : statusLabel(item.status) }}</small></span></button><div v-if="!filteredDocuments.length" class="library-empty"><FolderOpen :size="25" /><p>{{ librarySearch ? '没有找到匹配文献' : '你的下一篇论文，从这里开始。' }}</p></div></nav>
      <div class="sidebar-bottom"><button class="sidebar-settings" @click="showSettings = true"><Settings :size="17" />服务与成员设置<span v-if="!hasAI" class="unconfigured-dot" /></button><div class="user-row"><span class="avatar">{{ auth.user.display_name.slice(0, 1).toUpperCase() }}</span><span><strong>{{ auth.user.display_name }}</strong><small>{{ auth.user.role === 'admin' ? '管理员' : '课题组成员' }}</small></span><button v-if="!localAdmin" class="icon-button" aria-label="退出登录" title="退出登录" @click="logout"><LogOut :size="16" /></button></div></div>
    </aside>
    <main class="main-workspace">
      <header v-if="!detail && !loadingDoc" class="workspace-header"><div><button class="icon-button mobile-only" aria-label="打开文献库" @click="mobileLibrary = true"><Menu :size="20" /></button><span>我的阅读空间</span><span class="breadcrumb-separator">/</span><strong>文献库</strong></div><span class="local-badge"><span class="tiny-dot" /> 私有文献库</span></header>
      <div v-if="loadingDoc" class="center-loading full-height"><LoaderCircle class="spin" :size="25" />正在打开文献</div>
      <section v-else-if="!detail" class="welcome-page" @dragover.prevent="dragOver = true" @dragleave.prevent="dragOver = false" @drop.prevent="drop">
        <div class="welcome-intro"><span class="eyebrow"><span /> PAPER BY PAPER, IDEA BY IDEA</span><h1>把下一篇论文，<br />读得更明白<span class="accent-period">。</span></h1><p>英文原文，中文理解。逐段对照，自在切换。</p></div>
        <div class="welcome-stats"><div><Library :size="17" /><strong>{{ documents.length }}</strong><span>篇已导入文献</span></div><div><Languages :size="18" /><strong>7</strong><span>种可选翻译方式</span></div><div><Sparkles :size="17" /><strong>{{ usage.characters.toLocaleString() }}</strong><span>今日发送字符 · {{ usage.limit > 0 ? `上限 ${usage.limit.toLocaleString()}` : '不限额' }}</span></div></div>
        <button class="upload-zone" :class="{ dragging: dragOver }" :disabled="uploading" @click="fileInput?.click()"><span class="upload-illustration"><FileText :size="32" /><span><Plus :size="16" /></span></span><h2>{{ uploading ? `正在上传 ${uploadPercent}%` : '拖入一篇 PDF，开始阅读' }}</h2><p>或点击这里选择文件</p><span class="file-limit">PDF · 单篇最多 {{ usage.max_upload_mb }} MB / {{ usage.max_pages }} 页</span></button>
        <div class="sample-row"><span>想先看看阅读效果？</span><button :disabled="uploading" @click="openSample">打开合成示例文献<ArrowRight :size="14" /></button></div>
        <div class="feature-row"><div><span>01</span><h3>原文译文，逐段相随</h3><p>保留段落对应关系，重点内容随时核对原页。</p></div><div><span>02</span><h3>从泛读到精读</h3><p>按段翻译或翻译全文，选择机翻与 AI 服务。</p></div><div><span>03</span><h3>在自己的空间里阅读</h3><p>文献、笔记和进度，保存在你自己的服务中。</p></div></div>
        <footer class="welcome-footer"><ShieldCheck :size="14" /><span>本机直接使用管理员空间；免 Key 机翻可直接试译，AI 需配置接口。</span><button @click="showSettings = true">配置服务<ArrowRight :size="13" /></button></footer>
      </section>
      <template v-else>
        <header class="reader-header"><button class="icon-button mobile-only" aria-label="打开文献库" @click="mobileLibrary = true"><Menu :size="20" /></button><button class="icon-button desktop-only" aria-label="返回文献库" @click="home"><ArrowLeft :size="18" /></button><div class="reader-title"><span class="eyebrow">PAPER READER <span> / {{ detail.document.page_count }} PAGES</span></span><h1 :title="detail.document.title">{{ detail.document.title.replace(/\.pdf$/i, '') }}</h1></div><div class="reader-header-actions"><button class="icon-button" :class="{ pressed: sidePanel === 'pdf' }" aria-label="查看 PDF 原页" title="查看 PDF 原页" @click="sidePanel = sidePanel === 'pdf' ? null : 'pdf'"><PanelRightOpen :size="19" /></button><button class="icon-button" :class="{ pressed: sidePanel === 'chat' }" aria-label="文献问答" title="文献问答" @click="sidePanel = sidePanel === 'chat' ? null : 'chat'"><MessageSquare :size="18" /></button><button class="icon-button" :class="{ pressed: sidePanel === 'notes' }" aria-label="阅读笔记" title="阅读笔记" @click="sidePanel = sidePanel === 'notes' ? null : 'notes'"><StickyNote :size="18" /></button><div class="menu-anchor"><button class="icon-button" aria-label="更多文献操作" @click="showMenu = !showMenu"><MoreHorizontal :size="20" /></button><div v-if="showMenu" class="action-menu"><button @click="editor = { kind: 'title', id: selectedId, text: detail.document.title }; showMenu = false"><Pencil :size="14" />修改标题</button><a :href="`/api/documents/${selectedId}/export?engine=${engine}`" download @click="showMenu = false"><Download :size="14" />导出对照 Markdown</a><a :href="`/api/documents/${selectedId}/pdf`" download @click="showMenu = false"><FileText :size="14" />下载原 PDF</a><button :disabled="activeJobs.length>0" @click="confirmReflow='local';showMenu=false"><FileText :size="14" />更新基础重排</button><button v-if="detail.has_previous_layout" :disabled="activeJobs.length>0" @click="confirmReflow='restore';showMenu=false"><ArrowLeft :size="14" />恢复上个重排版本</button><button @click="sidePanel='outline';showMenu=false"><Library :size="14" />目录与图表</button><button class="danger-text" @click="deleteDocument"><Trash2 :size="14" />删除文献</button></div></div></div></header>
        <div class="reader-toolbar"><div class="reading-modes"><button :class="{ active: mode === 'bilingual' }" @click="mode = 'bilingual'"><Languages :size="14" />对照</button><button :class="{ active: mode === 'english' }" @click="mode = 'english'">原文</button><button :class="{ active: mode === 'chinese' }" @click="mode = 'chinese'">译文</button></div><div class="engine-control"><span class="tiny-dot" :class="{ inactive: !configured }" /><select v-model="engine" aria-label="翻译引擎" @change="changeEngine"><option v-for="(name,key) in engineNames" :key="key" :value="key">{{ name }}</option></select><ChevronDown :size="12" /></div><label class="check-label auto-toggle"><input type="checkbox" v-model="autoTranslate" @change="toggleAuto" />边读边译</label><span class="toolbar-spacer" /><div class="font-control"><button aria-label="缩小字号" @click="changeFont(-1)">A−</button><button aria-label="放大字号" @click="changeFont(1)">A+</button></div><button class="icon-button" aria-label="搜索文献内容" @click="showTextSearch = !showTextSearch"><Search :size="16" /></button><button class="secondary-button reflow-action" :disabled="Boolean(layoutJob) || activeJobs.length>0 || detail.document.status!=='ready'" @click="requestAIReflow"><Sparkles :size="14" /><span>AI 重排</span></button><button class="primary-button translate-all" :disabled="Boolean(translationJob) || submitting || detail.document.status !== 'ready'" @click="fullTranslation"><Sparkles :size="14" />翻译全文</button></div>
        <div v-if="showTextSearch" class="reader-search"><Search :size="15" /><input v-model="textSearch" placeholder="搜索原文或当前引擎的译文" aria-label="搜索原文或译文" /><span>{{ filteredParagraphs.length }} 段</span><button class="icon-button" aria-label="关闭内容搜索" @click="showTextSearch = false; textSearch = ''"><X :size="15" /></button></div>
        <div v-if="translationJob" class="job-banner"><LoaderCircle :size="15" class="spin" /><span>{{ translationJob.cancel_requested ? '正在停止，当前请求完成后生效' : '正在翻译，关闭网页后任务仍会继续' }}<small>{{ translationJob.completed }} 完成 / {{ translationJob.failed }} 失败 / 共 {{ translationJob.total }} 段</small></span><div class="job-progress"><i :style="{ width: `${(translationJob.completed + translationJob.failed) / Math.max(translationJob.total, 1) * 100}%` }" /></div><button :disabled="Boolean(translationJob.cancel_requested)" @click="cancelTranslation">停止</button></div>
        <div v-else-if="lastTranslationJob && ['failed', 'partial'].includes(lastTranslationJob.status) && !dismissedJobErrors.includes(lastTranslationJob.id)" class="job-banner error-job"><CircleAlert :size="16" /><span>{{ jobErrorText(lastTranslationJob.error) || '部分段落翻译失败。' }}<small>已完成译文已保存，重新启动会跳过有效缓存。</small></span><button @click="fullTranslation">继续翻译</button><button aria-label="关闭翻译错误提示" @click="dismissedJobErrors.push(lastTranslationJob.id)"><X :size="14" /></button></div>
        <div v-if="layoutJob?.kind==='ai_reflow'" class="job-banner"><LoaderCircle :size="15" class="spin" /><span>AI 正在调整段落边界，保留每一行原文<small>{{ layoutJob.completed }} / {{ layoutJob.total }} 批；通过完整性检查后替换阅读版；已校验批次会保存，服务忙时可稍后继续</small></span></div><div v-else-if="lastLayoutJob?.status==='failed' && !dismissedJobErrors.includes(lastLayoutJob.id)" class="job-banner error-job"><CircleAlert :size="15" /><span>{{ jobErrorText(lastLayoutJob.error) }}</span><button @click="confirmReflow=lastLayoutJob.kind==='ai_reflow'?'ai':'local'">重新重排</button><button aria-label="关闭重排错误提示" @click="dismissedJobErrors.push(lastLayoutJob.id)"><X :size="14" /></button></div>
        <div class="reading-layout" :class="{ 'with-panel': sidePanel }">
          <div ref="readerPane" class="reader-scroll" @scroll.passive="onScroll">
            <div v-if="['queued', 'parsing'].includes(detail.document.status)" class="parser-state"><span class="parser-icon"><FileText :size="32" /><LoaderCircle :size="20" class="spin" /></span><span class="eyebrow">PREPARING YOUR PAPER</span><h2>正在重建阅读段落</h2><p>提取文字、判断栏位，并保留原页对应位置。<br />任务在后台执行，可以稍后再回来。</p></div>
            <div v-else-if="detail.document.status === 'error'" class="parser-state"><CircleAlert :size="36" /><h2>这篇 PDF 暂时未能解析</h2><p>{{ detail.document.error }}</p><div class="button-row"><button class="secondary-button" @click="original()">查看原 PDF</button><button class="primary-button" @click="retryParse">重试解析</button></div></div>
            <div v-else-if="detail.document.status === 'needs_ocr'" class="parser-state"><FileText :size="36" /><h2>这篇文献需要文字识别</h2><p>未检测到足够的可提取文字。<br />请先对扫描件执行 OCR，再上传带文字层的 PDF。</p><button class="primary-button" @click="original()">查看原 PDF</button></div>
            <div v-else class="reading-paper" :style="{ '--reading-font-size': `${fontSize}px` }">
              <div class="paper-heading"><span class="eyebrow">{{ mode === 'bilingual' ? 'BILINGUAL READING' : mode === 'english' ? 'ORIGINAL TEXT' : 'CHINESE TRANSLATION' }}</span><span>{{ paragraphs.length }} 段 · {{ translatedCount }} 段已译</span></div>
              <details v-if="detail.document.warnings.length" class="parsing-notice"><summary><CircleAlert :size="13" />关于段落重排与原页核对<ChevronDown :size="12" /></summary><ul><li v-for="warning in detail.document.warnings" :key="warning">{{ warning }}</li></ul></details>
              <div v-if="detail.document.parser_version<2" class="layout-upgrade"><span>这篇文献使用旧版解析。更新后可查看公式、图表和改进后的双栏段落。</span><button @click="confirmReflow='local'">更新重排</button></div><p v-if="!configured" class="config-notice"><KeyRound :size="14" /><span>当前翻译引擎尚未配置，你仍可阅读原文。</span><button @click="showSettings = true">去配置<ArrowRight :size="12" /></button></p>
              <article v-for="p in filteredParagraphs" :key="p.id" :id="`p-${p.id}`" :data-id="p.id" class="paragraph" :class="[p.kind, { 'is-current': currentParagraph === p.id }]">
                <div class="paragraph-meta"><span class="paragraph-number">P{{ p.ordinal + 1 }}</span><button @click="original(p)">第 {{ p.page }} 页</button><small v-if="p.edited">原文已修正</small><div class="paragraph-actions"><button title="核对原页" aria-label="核对本段原页" @click="original(p)"><FileText :size="13" /></button><button v-if="translatable(p)" title="修正提取文本" aria-label="修正本段原文" @click="editor = { kind: 'paragraph', id: p.id, text: p.text }"><Pencil :size="13" /></button><button title="向 AI 提问" aria-label="对本段提问" @click="ask(p)"><MessageSquare :size="13" /></button></div></div>
                <details v-if="p.kind==='authors'" class="authors-details"><summary>作者与机构</summary><img :src="`/api/documents/${selectedId}/assets/${p.content.asset}`" alt="作者与机构原稿" loading="lazy" /></details><button v-else-if="p.content.asset" class="source-block" :class="p.kind" @click="original(p)" :aria-label="`${p.text}，查看原页`"><img :src="`/api/documents/${selectedId}/assets/${p.content.asset}`" :alt="p.text" :width="Math.round(p.content.width || 100)" :height="Math.round(p.content.height || 100)" loading="lazy" decoding="async" :style="p.kind==='equation'?{width:`${(p.content.width || 100)/(p.content.size || 10)}em`}:{}" /><small>{{ p.kind==='equation'?'原稿公式':p.kind==='table'?'原稿表格':p.text }} · 点击核对原页</small></button><div v-else-if="mode !== 'chinese'" class="original-text" lang="en"><SourceText :paragraph="p" :document-id="selectedId" /></div>
                <div v-if="mode !== 'english' && translatable(p)" class="translation-block" :class="{ 'has-translation': p.translation?.status === 'done', 'translation-error': p.translation?.status === 'error' }">
                  <template v-if="p.translation?.status === 'done'"><p class="translated-text" lang="zh-CN"><SourceText :paragraph="p" :document-id="selectedId" :text="p.translation.text" /></p><div class="translation-provenance"><CheckCheck :size="11" /><span>{{ p.translation.provider }}</span><button :disabled="Boolean(translationJob) || submitting" @click="translate([p.id], true)">重译</button></div></template>
                  <template v-else-if="p.translation?.status === 'running'"><LoaderCircle :size="14" class="spin" /><span>正在翻译这一段…</span></template>
                  <template v-else-if="p.translation?.status === 'error'"><CircleAlert :size="14" /><span>{{ p.translation.error }}</span><button :disabled="Boolean(translationJob) || submitting" @click="translate([p.id])">重试</button></template>
                  <template v-else><span class="translation-placeholder"><Languages :size="14" />{{ mode === 'chinese' ? '这一段还没有译文' : '中文译文' }}</span><button :disabled="Boolean(translationJob) || submitting" @click="configured ? translate([p.id]) : requireService()"><Sparkles :size="12" />翻译此段</button></template>
                </div>
              </article>
              <div v-if="!filteredParagraphs.length" class="search-empty">没有找到匹配段落。</div>
              <div class="paper-end"><span />本篇文献阅读完毕<span /></div>
            </div>
          </div>
          <aside v-if="sidePanel" class="reading-side-panel"><header><div class="panel-tabs"><button :class="{active:sidePanel==='outline'}" @click="sidePanel='outline'"><Library :size="14" />目录与图表</button><button :class="{ active: sidePanel === 'pdf' }" @click="sidePanel = 'pdf'"><FileText :size="14" />原页</button><button :class="{ active: sidePanel === 'chat' }" @click="sidePanel = 'chat'"><MessageSquare :size="14" />问答</button><button :class="{ active: sidePanel === 'notes' }" @click="sidePanel = 'notes'"><StickyNote :size="14" />笔记</button></div><button class="icon-button" aria-label="关闭侧栏" @click="sidePanel = null"><PanelRightClose :size="17" /></button></header>
            <PdfViewer v-if="sidePanel === 'pdf'" :key="selectedId" :document-id="selectedId" :page="pdfPage" :locations="selectedLocations" @page="pdfPageChanged" />
            <nav v-else-if="sidePanel==='outline'" class="outline-panel" aria-label="文献目录与图表"><button v-for="p in outline" :key="p.id" :class="p.kind" @click="jumpOutline(p)"><span>{{ p.text.replace(/PPMATH\d{6}/g,'[公式]') }}</span><small>第 {{ p.page }} 页</small></button><p v-if="!outline.length">未检测到标题或图表，可在原页中查看。</p></nav><div v-else-if="sidePanel === 'notes'" class="notes-panel"><div class="notes-heading"><span class="eyebrow">YOUR READING NOTES</span><span>{{ noteStatus }}</span></div><textarea v-model="notes" maxlength="50000" placeholder="记下你的问题、想法和下一步…&#10;&#10;笔记会自动保存到这篇文献。" aria-label="文献阅读笔记" /><div class="notes-footer"><span>{{ notes.length.toLocaleString() }} / 50,000 字符</span><button v-if="notesDirty" @click="flushNotes">立即保存</button></div></div>
            <div v-else class="chat-panel"><div ref="chatPane" class="chat-messages"><div v-if="!detail.messages.length" class="chat-welcome"><span><Sparkles :size="24" /></span><h3>带着问题读文献</h3><p>选择一段原文提问，或询问这篇文献的内容。回答会列出所参考的原文段落。</p><small>当前采用文字摘录检索，模型看不到公式和图表图片，相关细节需核对原页。</small></div><div v-for="message in detail.messages" :key="message.id" class="chat-message" :class="message.role"><span class="message-role">{{ message.role === 'user' ? '你' : 'PaperPilot' }}</span><p v-if="message.status === 'running'" class="chat-thinking"><LoaderCircle class="spin" :size="14" />正在结合原文思考…</p><p v-else :class="{ 'danger-text': message.status === 'error' }">{{ message.status==='error' ? jobErrorText(message.text) : message.text }}</p><button v-if="message.status==='error'" class="text-button" :disabled="chatBusy || chatRunning" @click="retryQuestion(message.id)">重新提问</button><details v-if="message.evidence.length" class="evidence-list"><summary>{{ message.evidence.some(source => !currentParagraphIds.has(source.id)) ? '旧版原文（编号已变化，请重新提问）' : '参考原文' }} · {{ message.evidence.length }} 段</summary><small v-if="message.evidence.some(source => !currentParagraphIds.has(source.id))">此回答基于重排前的阅读版，段落编号已变化；请重新提问核对出处。</small><button v-for="source in message.evidence" :key="source.id" :disabled="!currentParagraphIds.has(source.id)" @click="jump(source.id)">P{{ source.ordinal + 1 }} · p.{{ source.page }}</button></details></div></div><form class="chat-form" @submit.prevent="sendQuestion"><p class="chat-model-label">问答模型：{{ chatAI?.provider }} · {{ chatAI?.model }}</p><div v-if="questionParagraph" class="selected-quote">针对 P{{ (paragraphs.find(p => p.id === questionParagraph)?.ordinal || 0) + 1 }} 提问<button type="button" aria-label="取消选段" @click="questionParagraph = null"><X :size="12" /></button></div><textarea v-model="question" rows="3" maxlength="2500" placeholder="这项研究的方法是什么？" aria-label="向文献提问" @keydown="onQuestionKeydown" @compositionstart="questionComposing = true" @compositionend="questionComposing = false" /><div><small>Enter 发送 · Shift+Enter 换行</small><button class="primary-button" aria-label="发送问题" :disabled="chatBusy || chatRunning || !question.trim()"><LoaderCircle v-if="chatBusy || chatRunning" :size="14" class="spin" /><ArrowRight v-else :size="16" /></button></div></form></div>
          </aside>
        </div>
        <footer class="reading-status"><span><span class="tiny-dot" />{{ paragraphs.length ? `第 ${currentIndex + 1} / ${paragraphs.length} 段` : statusLabel(detail.document.status) }}<i />PDF 第 {{ pdfPage }} 页</span><span><Check v-if="progressSaved" :size="12" />{{ progressSaved ? '阅读位置已保存' : '阅读进度自动保存' }}</span></footer>
      </template>
    </main>
  </div>
  <SettingsDialog v-if="showSettings" @close="showSettings = false" @saved="refreshSettings" />
  <div v-if="confirmFull" class="modal-backdrop" @click.self="confirmFull = false"><section class="small-modal" role="dialog" aria-modal="true" aria-labelledby="translate-title"><span class="modal-symbol"><Languages :size="25" /></span><h2 id="translate-title">开始翻译这篇文献？</h2><p>将待译段落发送到<strong>{{ engine === 'ai' ? translationAI?.provider + ' · ' + translationAI?.model : engineNames[engine] }}</strong>。已有的有效缓存会跳过。<template v-if="engine==='ai'">并发 {{ settings?.translation_concurrency || 6 }} 段。</template></p><div class="translation-summary"><div><strong>{{ remainingCount }}</strong><span>段待翻译</span></div><div><strong>{{ textParagraphs.filter(p => p.translation?.status !== 'done').reduce((sum, p) => sum + p.text.length, 0).toLocaleString() }}</strong><span>原文字符，不含 AI 上下文</span></div></div><p class="field-hint">接口可能产生费用，以服务商账单为准。你可以随时停止后续段落。</p><div class="button-row"><button class="secondary-button" @click="confirmFull = false">稍后再说</button><button class="primary-button" :disabled="submitting" @click="translate(null)"><LoaderCircle v-if="submitting" class="spin" :size="15" />开始翻译</button></div></section></div>
  <div v-if="confirmReflow" class="modal-backdrop" @click.self="confirmReflow=null"><section class="small-modal" role="dialog" aria-modal="true" aria-labelledby="reflow-title"><span class="modal-symbol"><Sparkles :size="25" /></span><h2 id="reflow-title">{{ confirmReflow==='ai'?'用 AI 调整阅读段落':confirmReflow==='restore'?'恢复上个重排版本':'更新基础重排' }}</h2><p v-if="confirmReflow==='ai'">将提取出的文字与公式占位符发送到问答与重排模型：<strong>{{ chatAI?.provider }} · {{ chatAI?.model }}</strong>。AI 仅决定哪些原文行属于同一段，不改写内容，图像与完整 PDF 不发送。已校验的批次会保存，失败后重试可接着处理。本站默认不设字数限额；实际用量与费用以服务商为准。</p><p v-else-if="confirmReflow==='local'">在本机重新识别双栏、段落、公式与图表，不调用外部 API。</p><p v-else>恢复上个阅读版本及对应译文。</p><p class="field-hint">重排前的阅读版与译文会保存为可恢复版本，笔记保持保留。新段落需要重新翻译。</p><div class="button-row"><button class="secondary-button" @click="confirmReflow=null">取消</button><button class="primary-button" :disabled="submitting" @click="reflowDocument"><LoaderCircle v-if="submitting" class="spin" :size="15" />{{ confirmReflow==='restore'?'恢复版本':'开始重排' }}</button></div></section></div>
  <div v-if="editor" class="modal-backdrop" @click.self="editor = null"><section class="small-modal edit-modal" role="dialog" aria-modal="true" aria-labelledby="edit-title"><header class="modal-header"><h2 id="edit-title">{{ editor.kind === 'title' ? '修改文献标题' : '修正提取的段落' }}</h2><button class="icon-button" aria-label="关闭编辑" @click="editor = null"><X :size="18" /></button></header><textarea v-model="editor.text" :rows="editor.kind === 'title' ? 3 : 12" :maxlength="editor.kind === 'title' ? 200 : 50000" aria-label="编辑内容" /><p class="field-hint">{{ editor.kind === 'paragraph' ? '原 PDF 保留。修正后的段落及相关 AI 上下文会使用新的译文缓存。' : '标题会显示在你的文献库中。' }}</p><div class="button-row"><button class="secondary-button" @click="editor = null">取消</button><button class="primary-button" :disabled="savingEdit || !editor.text.trim()" @click="saveEdit">保存修改</button></div></section></div>
  <div v-if="toast" class="toast" :class="{ error: toastError }" role="status"><CircleAlert v-if="toastError" :size="18" /><Check v-else :size="18" /><span>{{ toast }}</span><button aria-label="关闭提示" @click="toast = ''"><X :size="15" /></button></div>
</template>
