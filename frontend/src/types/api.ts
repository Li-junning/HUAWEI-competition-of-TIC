export type TaskStatus = 'created' | 'running' | 'succeeded' | 'partial' | 'failed' | 'interrupted'
export type ClaimState = 'pending' | 'extracting' | 'retrieving' | 'judging' | 'done' | 'failed' | 'unchecked'
export type ClaimLabel = 'credible' | 'disputed' | 'incorrect' | 'evidence_insufficient' | 'not_applicable'
export type EvidenceRelation = 'supports' | 'refutes' | 'partially_supports' | 'irrelevant' | 'unknown'
export type ClaimType = 'person' | 'organization' | 'time' | 'location' | 'event' | 'statistic' | 'policy' | 'technology' | 'paper' | 'opinion' | string

export interface ServiceStatus { search_mode: string; judge_mode: string; ready: boolean; live: boolean; message: string }

export interface Coverage {
  verifiable_claims: number
  processed_verifiable_claims: number
  adjudicated_verifiable_claims: number
  processing_coverage: number | null
  verification_coverage: number | null
  extraction_coverage: number | null
}

export type LabelCounts = Record<ClaimLabel, number>

export interface TaskCreated {
  task_id: string
  status: 'created'
}

export interface TaskSummary {
  segmentation_method?: 'rules' | 'mimo' | 'rules_fallback'
  task_id: string
  status: TaskStatus
  created_at: string
  updated_at: string
  input_char_count: number
  claim_limit: number
  claims_extracted: number
  claims_processed: number
  claims_unchecked: number
  truncated: boolean
  failed_providers: string[]
  coverage: Coverage
  label_counts: LabelCounts
  score: number | null
  score_note: string | null
  error_code: string | null
}

export interface ClaimListItem {
  claim_id: string
  task_id: string
  source_text: string
  char_start: number
  char_end: number
  type: ClaimType
  normalized_claim: string
  manually_edited: boolean
  entities: string[]
  conditions: string[]
  queries: string[]
  label: ClaimLabel | null
  support_score: number | null
  reason: string | null
  state: ClaimState
  retry_count: number
  evidence_cluster_ids: string[]
}

export interface EvidenceItem {
  evidence_id: string
  url: string | null
  title: string | null
  publisher: string | null
  published_at: string | null
  retrieved_at: string | null
  excerpt: string | null
  relation: EvidenceRelation
  quality_reason: string | null
  is_reprint: boolean
}

export interface EvidenceCluster {
  cluster_id: string
  independence_reason: string
  items: EvidenceItem[]
}

export interface PaperCheck {
  input_title: string | null
  input_authors: string[]
  input_year: number | null
  input_venue: string | null
  input_doi: string | null
  candidate_title: string | null
  candidate_authors: string[]
  candidate_year: number | null
  candidate_venue: string | null
  candidate_doi: string | null
  field_matches: Record<string, boolean | null>
  existence_status: string
  claim_support_status: string
}

export interface ClaimDetail extends ClaimListItem {
  evidence_clusters: EvidenceCluster[]
  paper_check?: PaperCheck | null
}

export interface ClaimPage {
  items: ClaimListItem[]
  total: number
  offset: number
  limit: number
}

export interface ReviewEvent {
  event_id: string
  action: 'add' | 'split' | 'merge' | 'edit' | 'delete'
  reviewer: string
  created_at: string
  before_text: string
  after_text: string
  undone_at: string | null
  undone_by: string | null
}

export interface ApiErrorBody {
  error?: {
    code?: string
    message?: string
    request_id?: string
  }
}
