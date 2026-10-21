import { reactive } from 'vue';

export interface User { id: string; username: string; display_name: string; role: string }
export interface DocumentItem { id: string; title: string; filename: string; status: string; page_count: number; paragraph_count?: number; size: number; warnings: string[]; error: string | null; parser_version: number }
export interface Translation { status: string; text: string; error: string | null; provider: string }
export interface Location { page: number; bbox: number[]; width: number; height: number }
export interface Paragraph { id: string; ordinal: number; page: number; kind: string; text: string; edited: boolean; locations: Location[]; translation: Translation | null; content: {asset?:string;parts?:any[];width?:number;height?:number;size?:number} }
export interface Job { id: string; kind: string; status: string; total: number; completed: number; failed: number; error: string | null; cancel_requested: number }
export interface ChatMessage { id: string; role: string; text: string; status: string; evidence: { id: string; ordinal: number; page: number }[] }
export interface Detail { document: DocumentItem; paragraphs: Paragraph[]; jobs: Job[]; progress: { paragraph_id: string | null; page: number } | null; notes: { text: string }; messages: ChatMessage[]; has_previous_layout:boolean }

export const auth = reactive<{ user: User | null; csrf: string }>({ user: null, csrf: '' });

export async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch('/api' + path, {
    ...options, credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-PaperPilot': '1', 'X-CSRF-Token': auth.csrf, ...options.headers },
  });
  const data = await response.json().catch(() => ({ detail: `请求失败（HTTP ${response.status}）` }));
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请求失败，请重试。');
  return data as T;
}

export function uploadPdf(file: File, progress: (percent: number) => void): Promise<{ document: DocumentItem; duplicate: boolean }> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open('POST', '/api/documents');
    request.setRequestHeader('X-PaperPilot', '1');
    request.setRequestHeader('X-CSRF-Token', auth.csrf);
    request.upload.onprogress = event => { if (event.lengthComputable) progress(Math.round(event.loaded / event.total * 100)); };
    request.onerror = () => reject(new Error('上传连接中断，请检查服务是否运行。'));
    request.onload = () => {
      let data;
      try { data = JSON.parse(request.responseText); } catch { reject(new Error(`上传失败（HTTP ${request.status}）`)); return; }
      if (request.status >= 200 && request.status < 300) resolve(data);
      else reject(new Error(data.detail || '上传失败。'));
    };
    const form = new FormData(); form.append('file', file); request.send(form);
  });
}
