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

export default http
