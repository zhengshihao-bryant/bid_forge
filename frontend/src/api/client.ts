// API 客户端（axios 薄封装）
// dev 环境经 vite 代理 /api → http://127.0.0.1:8001
import axios from 'axios'

const http = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

// ═══════════════════════════════════════════════════════════════════
// 招标项目（M1 已可用）
// ═══════════════════════════════════════════════════════════════════
export interface TenderSummary {
  id: string
  name: string
  created_at: string
  extraction_status: string   // 未提取 / 提取中 / 已完成 / 失败
  extraction_progress: string
  requirement_count: number
  score_point_count: number
}

export interface TenderDocument {
  id: string
  file_name: string
  file_type: string
  total_pages: number
  char_count: number
  ocr_pages: number[]
  parse_error: string
  sections: SectionNode[]
}

export interface SectionNode {
  id: string
  title: string
  level: number
  order: number
  page_start: number | null
  page_end: number | null
  children: SectionNode[]
}

export interface TenderDetail extends TenderSummary {
  documents: TenderDocument[]
}

export interface RequirementItem {
  id: string
  type: string
  title: string
  original_text: string
  quantitative: { metric: string; op: string; value: string; unit: string }[]
  importance: string
  is_star: boolean
  source: {
    document: string
    page: number | null
    section_path: string
    block_id: string
    snippet: string
  } | null
  status: string
  response: string
  human_confirmed: boolean
}

export interface ScorePoint {
  id: string
  category: string
  item: string
  max_score: number | null
  criteria: string
  rule_id: string
  weight: number
  source_ref: string
}

export const tenderApi = {
  list: () => http.get<TenderSummary[]>('/tenders').then(r => r.data),
  get: (id: string) => http.get<TenderDetail>(`/tenders/${id}`).then(r => r.data),
  create: (files: File[], name: string) => {
    const form = new FormData()
    form.append('name', name)
    files.forEach(f => form.append('files', f))
    return http.post('/tenders', form).then(r => r.data)
  },
  extract: (id: string) =>
    http.post(`/tenders/${id}/extract`).then(r => r.data),
  requirements: (id: string, params: Record<string, string> = {}) =>
    http.get<RequirementItem[]>(`/tenders/${id}/requirements`, { params }).then(r => r.data),
  patchRequirement: (tenderId: string, reqId: string, patch: Record<string, string>) =>
    http.patch<RequirementItem>(`/tenders/${tenderId}/requirements/${reqId}`, patch).then(r => r.data),
  scorePoints: (id: string) =>
    http.get<ScorePoint[]>(`/tenders/${id}/score-points`).then(r => r.data),
}

// ═══════════════════════════════════════════════════════════════════
// M6 工作台聚合
// ═══════════════════════════════════════════════════════════════════
export interface StageState {
  key: string
  label: string
  status: 'pending' | 'in_progress' | 'done' | 'warning' | 'error'
  summary: string
}

export interface WorkbenchProject {
  id: string
  name: string
  created_at: string
  extraction_status: string
  requirement_count: number
  documents: { total: number; ok: number; ocr: number }
  matching: {
    status: string
    canonical_count: number
    match_count: number
    distribution: Record<string, number>
  }
  generation: {
    status: string
    job_id: string
    total_sections: number
    done_sections: number
  }
  quality: {
    report_id: string
    score: number
    status: string
    pending_issues: number
  }
  delivery: { finalized: boolean }
  stages: StageState[]
}

export interface KbStats {
  materials: number
  capabilities: number
  ready_materials: number
}

export interface WorkbenchIssue {
  id: string
  severity: string
  issue_type: string
  message: string
  section_id: string
  requirement_id: string
  status: string
  created_at: string
}

export const workbenchApi = {
  projects: () =>
    http.get<{ kb: KbStats; projects: WorkbenchProject[] }>('/workbench/projects').then(r => r.data),
  project: (id: string) =>
    http.get<WorkbenchProject & {
      kb: KbStats
      documents_detail: {
        id: string; file_name: string; file_type: string
        total_pages: number; char_count: number; ocr_pages: number[]
        parse_error: string; created_at: string
      }[]
      pending_issues: WorkbenchIssue[]
    }>(`/workbench/projects/${id}`).then(r => r.data),
}

// ═══════════════════════════════════════════════════════════════════
// 企业知识库（M2，M6-04 工作台使用）
// ═══════════════════════════════════════════════════════════════════
export interface KnowledgeMaterial {
  id: string
  category: string
  file_name: string
  file_type: string
  total_pages: number
  char_count: number
  ocr_pages: number[]
  parse_error: string
  process_status: string   // 未处理 / 处理中 / 已完成 / 失败
  process_progress: string
  chunk_count: number
  capability_count: number
  index_status: string
  created_at: string
}

export interface Capability {
  id: string                 // CAP-0001
  category: string
  name: string
  attributes: Record<string, unknown>
  description: string
  source_doc: string
  source_page: number | null
  created_at: string
}

export interface SearchHit {
  chunk_id: string
  material_id: string
  file_name: string
  category: string
  section_path: string
  page: number | null
  score: number
  content: string
}

export const knowledgeApi = {
  materials: (params: Record<string, string> = {}) =>
    http.get<KnowledgeMaterial[]>('/knowledge/materials', { params }).then(r => r.data),
  material: (id: string) =>
    http.get<KnowledgeMaterial & { sections: SectionNode[] }>(`/knowledge/materials/${id}`).then(r => r.data),
  upload: (files: File[], category: string) => {
    const form = new FormData()
    form.append('category', category)
    files.forEach(f => form.append('files', f))
    return http.post('/knowledge/materials', form).then(r => r.data)
  },
  process: (id: string) =>
    http.post(`/knowledge/materials/${id}/process`).then(r => r.data),
  capabilities: (params: Record<string, string> = {}) =>
    http.get<Capability[]>('/knowledge/capabilities', { params }).then(r => r.data),
  search: (q: string, top_k = 10) =>
    http.get<{ engine: string; hits: SearchHit[] }>('/knowledge/search',
      { params: { q, top_k } }).then(r => r.data),
}

// ═══════════════════════════════════════════════════════════════════
// 需求-能力匹配（M3，M6-03 工作台使用）
// ═══════════════════════════════════════════════════════════════════
export interface CanonicalRequirement {
  id: string                 // REQ-C-XXXX
  tender_id: string
  req_type: string
  title: string
  text: string
  constraints: { metric: string; op: string; value: string; unit: string }[]
  source_requirement_ids: string[]
  parent_requirement_id: string
  importance: string
  is_star: boolean
  is_scoring: boolean
  merge_method: string
  sources: unknown[]
  created_at: string
}

export interface MatchRecord {
  id: string                 // MAT-XXXX
  tender_id: string
  requirement_id: string
  status: 'FULL' | 'PARTIAL' | 'MISSING' | 'UNKNOWN'
  confidence: number
  reason: string
  method: string
  evidence_ids: string[]
  conflicts: unknown[]
  created_at: string
}

export interface EvidenceItem {
  evidence_id: string
  source_type: string
  source_id: string
  document: string
  category: string
  section_path: string
  page: number | null
  block_id: string
  content: string
  matched_text: string
  validation: string
  confidence: number
}

export interface TraceLink {
  requirement_id: string
  match_id: string
  evidence_id: string
  source_type: string
  source_id: string
  document: string
  section_path: string
  page: number | null
  block_id: string
  snippet: string
}

export const matchingApi = {
  status: (id: string) =>
    http.get<{ tender_id: string; status: string; progress: string;
      canonical_count: number; match_count: number }>(`/matching/tenders/${id}`).then(r => r.data),
  start: (id: string) =>
    http.post(`/matching/tenders/${id}/match`).then(r => r.data),
  canonicalRequirements: (id: string) =>
    http.get<{ tender_id: string; total: number; requirements: CanonicalRequirement[] }>(
      `/matching/tenders/${id}/requirements`).then(r => r.data),
  matches: (id: string, status = '') =>
    http.get<{ tender_id: string; total: number; counts: Record<string, number>;
      matches: MatchRecord[] }>(`/matching/tenders/${id}/matches`,
      { params: status ? { status } : {} }).then(r => r.data),
  matchDetail: (id: string, matchId: string) =>
    http.get<{ match: MatchRecord; evidences: EvidenceItem[]; trace: TraceLink[] }>(
      `/matching/tenders/${id}/matches/${matchId}`).then(r => r.data),
  responseTable: (id: string, format: 'json' | 'markdown' = 'json') =>
    http.get<Record<string, unknown>>(`/matching/tenders/${id}/response-table`,
      { params: { format } }).then(r => r.data),
}

// ═══════════════════════════════════════════════════════════════════
// 标书生成（M4，M6-05 工作台使用）
// ═══════════════════════════════════════════════════════════════════
export interface GenerationSectionNode {
  id: string
  tender_id: string
  parent_id: string
  title: string
  level: number
  ord: number
  section_type: string
  source_refs: string[]
  requirement_types: string[]
  allowed_categories: string[]
  status: string           // 待生成/生成中/已完成/失败/跳过
  children?: GenerationSectionNode[]
}

export interface GenerationJob {
  id: string
  tender_id: string
  outline_id: string
  status: string           // 未生成/生成中/已完成/部分失败/失败
  progress: string
  section_states: Record<string, string>
  total_sections: number
  done_sections: number
  failed_sections: number
  error: string
  created_at: string
  updated_at: string
}

export interface SectionDraft {
  section_id: string
  tender_id: string
  generation_id: string
  title: string
  section_type: string
  paragraphs: { text: string; kind: string; evidence_ids: string[] }[]
  requirement_coverage: unknown[]
  evidence_refs: { evidence_id: string; source: string; snippet: string }[]
  warnings: string[]
  status: string           // 草稿/已编辑/已确认
  content_md: string
  generation_metadata: Record<string, unknown>
  version: number
  created_at: string
  updated_at: string
}

export const generationApi = {
  createOutline: (id: string) =>
    http.post<{ tender_id: string; outline_id: string; total_sections: number;
      mapped_requirements: number; unmapped_requirements: number;
      sections: GenerationSectionNode[] }>(`/generation/tenders/${id}/outline`).then(r => r.data),
  outline: (id: string) =>
    http.get<{ tender_id: string; sections: GenerationSectionNode[] }>(
      `/generation/tenders/${id}/outline`).then(r => r.data),
  startJob: (id: string) =>
    http.post<{ tender_id: string; job_id: string; status: string }>(
      `/generation/tenders/${id}/jobs`).then(r => r.data),
  job: (id: string, jobId: string) =>
    http.get<GenerationJob>(`/generation/tenders/${id}/jobs/${jobId}`).then(r => r.data),
  listJobs: (id: string) =>
    http.get<{ tender_id: string; jobs: GenerationJob[] }>(
      `/generation/tenders/${id}/jobs`).then(r => r.data),
  logs: (id: string, limit = 50) =>
    http.get<{ tender_id: string; logs: Record<string, unknown>[] }>(
      `/generation/tenders/${id}/logs`, { params: { limit } }).then(r => r.data),
  eventsUrl: (id: string, jobId: string) =>
    `/api/generation/tenders/${id}/jobs/${jobId}/events`,
  section: (id: string, sectionId: string) =>
    http.get<SectionDraft>(`/generation/tenders/${id}/sections/${sectionId}`).then(r => r.data),
  editSection: (id: string, sectionId: string, content_md: string) =>
    http.patch(`/generation/tenders/${id}/sections/${sectionId}`,
      { content_md }).then(r => r.data),
  regenerate: (id: string, sectionId: string) =>
    http.post<{ job_id: string; status: string }>(
      `/generation/tenders/${id}/sections/${sectionId}/regenerate`).then(r => r.data),
  responseTable: (id: string, format: 'json' | 'markdown' = 'markdown') =>
    http.get<{ tender_id: string; format: string; content?: string }>(
      `/generation/tenders/${id}/response-table`, { params: { format } }).then(r => r.data),
  documentUrl: (id: string, format = 'docx') =>
    `/api/generation/tenders/${id}/document?format=${format}`,
}

// ═══════════════════════════════════════════════════════════════════
// 质量检查（M5，M6-06/07 工作台使用）
// ═══════════════════════════════════════════════════════════════════
export interface DimensionScore {
  name: string
  score: number
  detail: string
}

export interface QualityReport {
  id: string
  tender_id: string
  document_version: string
  score: number
  dimensions: DimensionScore[]
  counts: Record<string, number>
  issue_counts: Record<string, number>
  summary: string
  status: string
  reviewer: string
  review_time: string
  created_at: string
}

export interface QualityIssue {
  id: string
  report_id: string
  tender_id: string
  document_version: string
  section_id: string
  requirement_id: string
  issue_type: string
  severity: 'CRITICAL' | 'ERROR' | 'WARNING' | 'INFO'
  status: string           // 待处理/已确认/已忽略/已修复
  message: string
  source_refs: string[]
  suggestion: string
  autofixable: boolean
  created_at: string
}

export const qualityApi = {
  check: (id: string, include_llm = false) =>
    http.post<{ tender_id: string; report: QualityReport; issues: QualityIssue[] }>(
      `/quality/tenders/${id}/check`, null,
      { params: { include_llm } }).then(r => r.data),
  reports: (id: string) =>
    http.get<{ tender_id: string; reports: QualityReport[] }>(
      `/quality/tenders/${id}/reports`).then(r => r.data),
  issues: (id: string, status = '') =>
    http.get<{ tender_id: string; status_filter: string; issues: QualityIssue[] }>(
      `/quality/tenders/${id}/issues`,
      { params: status ? { status } : {} }).then(r => r.data),
  patchIssue: (issueId: string, patch: { status: string; reviewer?: string; note?: string }) =>
    http.patch(`/quality/issues/${issueId}`, patch).then(r => r.data),
  autofix: (issueId: string) =>
    http.post(`/quality/issues/${issueId}/autofix`).then(r => r.data),
  finalize: (id: string, reviewer: string, force = false) =>
    http.post<{ report_id: string; tender_id: string; status: string; score: number;
      reviewer: string; review_time: string;
      artifacts: { final_md: string; final_docx: string; report_json: string } }>(
      `/quality/tenders/${id}/finalize`, { reviewer, force }).then(r => r.data),
  finalUrl: (id: string, format: 'json' | 'markdown' | 'docx' = 'docx') =>
    `/api/quality/tenders/${id}/final?format=${format}`,
  finalContent: (id: string, format: 'json' | 'markdown' = 'markdown') =>
    http.get<Record<string, unknown>>(`/quality/tenders/${id}/final`,
      { params: { format } }).then(r => r.data),
}

// ═══════════════════════════════════════════════════════════════════
// SSE：生成任务进度流（fetch + ReadableStream 解析 text/event-stream）
// ═══════════════════════════════════════════════════════════════════
export interface JobEvent {
  event: 'log' | 'snapshot' | 'done'
  data: Record<string, unknown>
}

/**
 * 消费 SSE 流。onEvent 逐条回调，流关闭（done/异常）时 resolve。
 * 返回 AbortController，调用方可随时取消。
 */
export function streamJobEvents(
  url: string,
  onEvent: (e: JobEvent) => void,
): AbortController {
  const controller = new AbortController()
  ;(async () => {
    try {
      const resp = await fetch(url, { signal: controller.signal })
      if (!resp.ok || !resp.body) {
        onEvent({ event: 'done', data: { error: `HTTP ${resp.status}` } })
        return
      }
      const reader = resp.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buffer = ''
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        // SSE 帧以空行分隔；每帧可能是 event: xxx\ndata: yyy
        let idx: number
        while ((idx = buffer.indexOf('\n\n')) >= 0) {
          const frame = buffer.slice(0, idx)
          buffer = buffer.slice(idx + 2)
          const event = frame.startsWith('event: ')
            ? frame.slice(7, frame.indexOf('\n'))
            : 'log'
          const dataLine = frame.split('\n').find(l => l.startsWith('data: '))
          if (!dataLine) continue
          try {
            onEvent({ event: event as JobEvent['event'], data: JSON.parse(dataLine.slice(6)) })
          } catch { /* 非 JSON 帧忽略 */ }
        }
      }
    } catch {
      onEvent({ event: 'done', data: { error: 'stream closed' } })
    }
  })()
  return controller
}

export default http
