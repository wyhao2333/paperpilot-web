<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';
import { X, KeyRound, Users, ShieldCheck, LoaderCircle, Check, ExternalLink } from 'lucide-vue-next';
import { api, auth } from './api';

const emit = defineEmits<{ close: []; saved: [] }>();
const tab = ref('services'), loading = ref(true), saving = ref(false), error = ref(''), success = ref('');
const form = reactive({ ai: { provider: 'deepseek', base_url: 'https://api.deepseek.com', model: 'deepseek-flash', api_key: '', has_key: false }, ai_tasks: { translation: { provider: 'deepseek', model: 'deepseek-flash' }, chat: { provider: 'deepseek', model: 'deepseek-flash' } }, translation_concurrency: 6, baidu: { app_id: '', secret: '', has_secret: false }, youdao: { app_id: '', secret: '', has_secret: false }, microsoft: { api_key: '', region: 'eastasia', has_key: false }, glossary: '' });
type AITask = 'translation' | 'chat';
const taskLabels: Record<AITask, string> = { translation: '逐段翻译', chat: '文献问答与 AI 重排' };
const providerNames: Record<string, string> = { deepseek: 'DeepSeek', zhipu: '智谱 GLM', siliconflow: '硅基流动', qwen: '通义千问', moonshot: 'Moonshot', minimax: 'MiniMax', custom: '自定义兼容接口' };
const profiles = reactive<Record<string, any>>({}), activeProvider = ref('deepseek');
const mtService = ref('baidu'), testing = ref(false), testResult = ref(''), testRole = ref<AITask>('translation');
const removeAI = ref(false), removeMT = ref(false), accounts = ref<any[]>([]);
const member = reactive({ username: '', display_name: '', password: '' });
const presets: Record<string, { base_url: string; model: string }> = {
  deepseek: { base_url: 'https://api.deepseek.com', model: 'deepseek-flash' },
  zhipu: { base_url: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-4-flash' },
  qwen: { base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-plus' },
  siliconflow: { base_url: 'https://api.siliconflow.cn/v1', model: 'Qwen/Qwen3-8B' },
  moonshot: { base_url: 'https://api.moonshot.cn/v1', model: 'moonshot-v1-8k' },
  minimax: { base_url: 'https://api.minimax.io/v1', model: 'MiniMax-M2.5' },
  custom: { base_url: 'https://example.com/v1', model: '' },
};
const modelOptions: Record<string, string[]> = { deepseek: ['deepseek-flash'], zhipu: ['glm-4-flash', 'glm-4-flash-250414', 'glm-4.7-flash'], siliconflow: ['Qwen/Qwen3-8B'], qwen: ['qwen-plus', 'qwen-turbo'], moonshot: ['moonshot-v1-8k'], minimax: ['MiniMax-M2.5'] };
function taskHasKey(role: AITask) {
  const provider=form.ai_tasks[role].provider;
  const profile=provider===form.ai.provider ? {...form.ai,remove_key:removeAI.value} : profiles[provider];
  return Boolean(profile && !profile.remove_key && (profile.api_key || profile.has_key));
}
function chooseTaskProvider(role: AITask) {
  const selected=form.ai_tasks[role], profile=selected.provider===form.ai.provider ? form.ai : profiles[selected.provider];
  selected.model=profile?.model || presets[selected.provider]?.model || '';
  if(!profile) profiles[selected.provider]={provider:selected.provider,...presets[selected.provider],api_key:'',has_key:false};
  testResult.value='';
}
function preset() {
  const next=form.ai.provider;
  profiles[activeProvider.value]={...form.ai,provider:activeProvider.value,remove_key:removeAI.value};
  Object.assign(form.ai, profiles[next] || {provider:next,...presets[next],api_key:'',has_key:false});
  form.ai.provider=next;activeProvider.value=next;removeAI.value=Boolean(profiles[next]?.remove_key);testResult.value='';
}
function profilePayload(profile: any) { return {provider:profile.provider,base_url:profile.base_url,model:profile.model,api_key:profile.remove_key ? '' : profile.api_key || null}; }
async function testConnection(purpose: AITask) {
  testRole.value=purpose;testing.value=true;testResult.value='';error.value='';await save();if(error.value){testing.value=false;return;}
  try { const r=await api('/settings/test', {method:'POST',body:JSON.stringify({engine:'ai',purpose})});testResult.value=`${taskLabels[purpose]}（${form.ai_tasks[purpose].model}）：${r.text}`; }
  catch(err:any){error.value=err.message;}finally{testing.value=false;}
}
async function load() {
  try {
    const data = await api('/settings');
    Object.assign(form.ai, data.ai, { api_key: '' });
    Object.assign(form.ai_tasks, data.ai_tasks);
    form.translation_concurrency=data.translation_concurrency;
    Object.assign(profiles,Object.fromEntries(Object.entries(data.ai_profiles || {}).map(([key,value]:any)=>[key,{...value,api_key:''}])));
    activeProvider.value=form.ai.provider;
    Object.assign(form.baidu, data.baidu, { secret: '' });
    Object.assign(form.youdao, data.youdao, { secret: '' });
    Object.assign(form.microsoft, data.microsoft, { api_key: '' });
    form.glossary = data.glossary;
    if (auth.user?.role === 'admin') accounts.value = await api('/admin/users');
  } catch (err: any) { error.value = err.message; }
  finally { loading.value = false; }
}
async function save() {
  saving.value = true; error.value = ''; success.value = '';
  try {
    const data = await api('/settings', { method: 'PUT', body: JSON.stringify({
      ai: { provider: form.ai.provider, base_url: form.ai.base_url, model: form.ai.model, api_key: removeAI.value ? '' : form.ai.api_key || null },
      ai_profiles: Object.fromEntries(Object.entries(profiles).filter(([key])=>key!==form.ai.provider).map(([key,value])=>[key,profilePayload(value)])),
      ai_tasks: form.ai_tasks, translation_concurrency: form.translation_concurrency,
      baidu: { app_id: form.baidu.app_id, secret: removeMT.value ? '' : form.baidu.secret || null }, glossary: form.glossary,
      youdao: {app_id:form.youdao.app_id,secret:form.youdao.secret || null},
      microsoft: {api_key:form.microsoft.api_key || null,region:form.microsoft.region},
    }) });
    Object.assign(form.ai, data.ai, { api_key: '' }); Object.assign(form.baidu, data.baidu, { secret: '' });
    Object.assign(form.youdao,data.youdao,{secret:''});Object.assign(form.microsoft,data.microsoft,{api_key:''});
    Object.assign(profiles,Object.fromEntries(Object.entries(data.ai_profiles || {}).map(([key,value]:any)=>[key,{...value,api_key:''}])));
    removeAI.value = removeMT.value = false;
    success.value = '配置已保存。可以返回文献试译一个段落。'; emit('saved');
  } catch (err: any) { error.value = err.message; }
  finally { saving.value = false; }
}
async function addMember() {
  saving.value = true; error.value = ''; success.value = '';
  try {
    await api('/admin/users', { method: 'POST', body: JSON.stringify(member) });
    accounts.value = await api('/admin/users');
    member.username = member.display_name = member.password = '';
    success.value = '成员已创建，请将登录信息通过你信任的方式告知对方。';
  } catch (err: any) { error.value = err.message; }
  finally { saving.value = false; }
}
onMounted(load);
</script>

<template>
  <div class="modal-backdrop" @click.self="emit('close')">
    <section class="settings-modal" role="dialog" aria-modal="true" aria-labelledby="settings-title">
      <header class="modal-header"><div><span class="eyebrow">YOUR WORKSPACE</span><h2 id="settings-title">阅读服务设置</h2></div><button class="icon-button" aria-label="关闭设置" @click="emit('close')"><X :size="21" /></button></header>
      <div class="settings-tabs"><button :class="{ active: tab === 'services' }" @click="tab = 'services'"><KeyRound :size="15" />翻译与 AI</button><button v-if="auth.user?.role === 'admin'" :class="{ active: tab === 'members' }" @click="tab = 'members'"><Users :size="15" />成员管理</button></div>
      <div class="settings-body">
        <div v-if="loading" class="center-loading"><LoaderCircle class="spin" />正在读取设置</div>
        <template v-else-if="tab === 'services'">
          <div class="privacy-callout"><ShieldCheck :size="19" /><p>配置仅属于当前账号。密钥加密保存在本机服务中；点击翻译或提问时，相关文字会发送到所选服务。</p></div>
          <form id="service-form" @submit.prevent="save">
            <div class="section-label"><span class="step-circle">01</span><div><h3>为不同任务选择 AI</h3><p>翻译与问答独立选模型，同一服务商可复用已保存的密钥</p></div></div>
            <div v-for="(label, role) in taskLabels" :key="role" class="task-model-card">
              <div class="task-model-heading"><strong>{{ label }}</strong><span class="status-tag" :class="{configured:taskHasKey(role)}">{{ taskHasKey(role) ? '密钥已配置' : '请在下方配置密钥' }}</span></div>
              <div class="form-two"><label>{{ label }}服务商<select v-model="form.ai_tasks[role].provider" @change="chooseTaskProvider(role)"><option v-for="(name,id) in providerNames" :key="id" :value="id">{{ name }}</option></select></label><label>{{ label }}模型<input v-model="form.ai_tasks[role].model" :list="`models-${role}`" required placeholder="控制台中的模型 ID" /><datalist :id="`models-${role}`"><option v-for="model in modelOptions[form.ai_tasks[role].provider] || []" :key="model" :value="model" /></datalist></label></div>
              <p v-if="role==='translation'" class="field-hint">可选智谱 glm-4-flash 做翻译；问答可另外选择 DeepSeek 等模型。</p>
              <button type="button" class="secondary-button" :disabled="testing || saving || !taskHasKey(role)" @click="testConnection(role)"><LoaderCircle v-if="testing && testRole===role" :size="14" class="spin" />保存并测试{{ role==='translation' ? '翻译' : '问答' }}模型</button>
            </div>
            <label>翻译并发段数<input v-model.number="form.translation_concurrency" type="number" min="1" max="16" required /></label>
            <p class="field-hint">1～16 段，建议从 4～6 开始。实际并发受账号和模型限制；遇到 429 会等待重试，可调低并发。问答有独立处理通道；机器翻译按接口限制使用较低并发。</p>
            <p v-if="testResult" class="inline-success">{{ testResult }}</p>
            <div class="settings-divider" />
            <div class="section-label"><span class="step-circle">02</span><div><h3>服务商 API 连接</h3><p>在这里配置密钥和接口地址，再分配给上面的任务</p></div><span class="status-tag" :class="{ configured: form.ai.has_key }">{{ form.ai.has_key ? '已保存密钥' : '待配置' }}</span></div>
            <label>服务商<select v-model="form.ai.provider" @change="preset"><option value="deepseek">DeepSeek · V4.1 Flash</option><option value="zhipu">智谱 GLM · 支持免费 Flash</option><option value="siliconflow">硅基流动</option><option value="qwen">通义千问 Qwen</option><option value="moonshot">月之暗面 Moonshot</option><option value="minimax">MiniMax</option><option value="custom">自定义兼容接口</option></select></label>
            <label>Base URL<input v-model="form.ai.base_url" required type="url" placeholder="https://…/v1" /></label>
            <p v-if="form.ai.provider==='deepseek'" class="field-hint">官方当前用 deepseek-flash 调用 V4.1 Flash。可填写自己的完整模型 ID。</p>
            <p v-if="form.ai.provider==='zhipu'" class="field-hint">支持 glm-4-flash、glm-4-flash-250414、glm-4.7-flash 等。免费与并发权限以你的控制台为准，Flash 名称不代表相同并发额度。</p>
            <label>API Key<input v-model="form.ai.api_key" type="password" autocomplete="new-password" :placeholder="form.ai.has_key ? '已保存，留空则保留' : '填写你自己的 API Key'" /></label>
            <label v-if="form.ai.has_key" class="check-label"><input type="checkbox" v-model="removeAI" />移除已保存的 AI 密钥</label>
            <p class="field-hint">不同服务商分别保存地址与密钥。在上方任务中独立选择模型；同一服务商的地址和密钥可供两个任务共用。</p>
            <div class="settings-divider" />
            <div class="section-label"><span class="step-circle">03</span><div><h3>机器翻译</h3><p>阅读页可直接切换 6 种机器翻译服务</p></div></div>
            <div class="free-services"><strong>免 Key：MyMemory · Google GTX · Bing（实验）</strong><p>选择后可直接试译。MyMemory 有每日额度；Google 和 Bing 网页接口可能限流或要求验证，失败会显示所选服务的原因。</p></div>
            <label>配置官方 API<select v-model="mtService"><option value="baidu">百度通用翻译</option><option value="youdao">有道智云翻译</option><option value="microsoft">Microsoft Translator</option></select></label>
            <div v-if="mtService==='baidu'" class="form-two"><label>百度 APP ID<input v-model="form.baidu.app_id" placeholder="百度翻译应用 ID" /></label><label>百度密钥<input v-model="form.baidu.secret" type="password" autocomplete="new-password" :placeholder="form.baidu.has_secret ? '已保存，留空则保留' : '百度翻译应用密钥'" /></label></div>
            <div v-if="mtService==='youdao'" class="form-two"><label>有道应用 ID<input v-model="form.youdao.app_id" placeholder="有道智云 appKey" /></label><label>有道应用密钥<input v-model="form.youdao.secret" type="password" autocomplete="new-password" :placeholder="form.youdao.has_secret ? '已保存，留空则保留' : '有道应用密钥'" /></label></div>
            <div v-if="mtService==='microsoft'" class="form-two"><label>Microsoft Key<input v-model="form.microsoft.api_key" type="password" autocomplete="new-password" :placeholder="form.microsoft.has_key ? '已保存，留空则保留' : '资源的订阅密钥'" /></label><label>资源区域<input v-model="form.microsoft.region" placeholder="eastasia" /></label></div>
            <label v-if="form.baidu.has_secret" class="check-label"><input type="checkbox" v-model="removeMT" />移除已保存的百度翻译密钥</label>
            <div class="settings-divider" />
            <div class="section-label"><span class="step-circle">04</span><div><h3>术语偏好</h3><p>仅供 AI 翻译参考，每行一个词条</p></div></div>
            <textarea v-model="form.glossary" rows="4" maxlength="5000" placeholder="shoreline = 海岸线&#10;tidal flat = 潮滩" />
            <p class="field-hint">模型是否可用取决于服务商与账号权限。修改模型、原文或术语后，将使用新的译文缓存。</p>
          </form>
        </template>
        <template v-else>
          <p class="field-hint">成员的文献、译文、笔记和接口配置相互独立。当前版本由管理员添加账号。</p>
          <div class="account-list"><div v-for="account in accounts" :key="account.id"><span class="avatar">{{ account.display_name.slice(0, 1).toUpperCase() }}</span><div><strong>{{ account.display_name }}</strong><small>{{ account.username }}</small></div><span class="status-tag">{{ account.role === 'admin' ? '管理员' : '成员' }}</span></div></div>
          <h3 class="member-title">添加成员</h3>
          <form id="member-form" @submit.prevent="addMember"><div class="form-two"><label>用户名<input v-model="member.username" required pattern="[A-Za-z0-9_.\-]{2,40}" placeholder="英文字母或数字" autocomplete="off" /></label><label>显示名称<input v-model="member.display_name" maxlength="60" placeholder="如何称呼这位成员" /></label></div><label>初始密码<input v-model="member.password" required minlength="12" maxlength="256" type="password" autocomplete="new-password" placeholder="至少 12 个字符" /></label></form>
        </template>
        <p v-if="error" class="inline-error" role="alert">{{ error }}</p><p v-if="success" class="inline-success"><Check :size="16" />{{ success }}</p>
      </div>
      <footer class="modal-footer"><span>PaperPilot Web · 配置保存在你的服务器</span><button class="primary-button" :form="tab === 'services' ? 'service-form' : 'member-form'" :disabled="saving || loading" type="submit"><LoaderCircle v-if="saving" :size="16" class="spin" />{{ tab === 'services' ? '保存配置' : '创建成员' }}</button></footer>
    </section>
  </div>
</template>
