export interface BookSummary {
  id: string;
  name: string;
  source_type: string;
  total_chapters: number;
  translated_chapters: number;
  total_paragraphs: number;
  translated_paragraphs: number;
  progress_percentage: number;
  status: 'pending' | 'translating' | 'reviewing' | 'completed' | 'paused' | 'error';
  has_output_epub: boolean;
  epub_download_url?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ChapterSummary {
  id: string;
  index: number;
  title: string;
  total_paragraphs: number;
  translated_paragraphs: number;
  status: 'pending' | 'translated' | 'reviewed';
  auto_fixed_count: number;
}

export interface ParagraphItem {
  id: string;
  index: number;
  chapter_id: string;
  source: string;
  translated: string;
  status: 'pending' | 'translated' | 'fallback_recovered' | 'review_fixed' | 'manually_edited';
  provider?: string | null;
  fallback_from?: string | null;
  fallback_reason?: string | null;
  duration_ms?: number | null;
  metadata?: Record<string, any>;
}

export interface ChapterDetail {
  id: string;
  index: number;
  title: string;
  total_paragraphs: number;
  translated_paragraphs: number;
  status: 'pending' | 'translated' | 'fallback_recovered' | 'review_fixed' | 'manually_edited';
  paragraphs: ParagraphItem[];
  chapter_summary: string;
  auto_fixed_count: number;
}

export interface TaskStatusResponse {
  task_id: string;
  book_id: string;
  status: 'idle' | 'running' | 'paused' | 'completed' | 'failed' | 'stopped';
  phase?: 'queued' | 'initializing' | 'translating' | 'reviewing' | 'finalizing' | 'idle';
  reviewer_states?: Partial<Record<'primary' | 'secondary', 'standby' | 'pending' | 'reviewing' | 'retry_wait' | 'retrying' | 'completed' | 'failed' | 'cancelled'>>;
  reviewer_details?: Partial<Record<'primary' | 'secondary', ReviewerExecutionDetail>>;
  overall_progress: number;
  current_chapter: string;
  current_chapter_index: number;
  total_chapters: number;
  current_batch: number;
  total_batches: number;
  recovered_paragraphs: number;
  message: string;
  error_detail?: string | null;
  started_at?: string | null;
  updated_at?: string | null;
}

export interface ReviewerExecutionDetail {
  status?: 'standby' | 'pending' | 'reviewing' | 'retry_wait' | 'retrying' | 'completed' | 'failed' | 'cancelled';
  backend?: string;
  attempt?: number;
  candidate_index?: number;
  candidate_total?: number;
  chunk_index?: number;
  total_chunks?: number;
  split_depth?: number;
  split_path?: string;
  timeout_seconds?: number;
  error?: string;
  retry_reason?: string;
  retry_index?: number;
  retry_total?: number;
  retry_delay_seconds?: number;
  retry_resume_at?: string;
  http_status?: number;
}

export interface PipelineStartRequest {
  book_id: string;
  apply?: boolean;
  autonomous?: boolean;
  finalize?: boolean;
  layout?: 'horizontal' | 'preserve';
  primary_translator?: string;
  fallback_translators?: string[];
  reviewer?: string;
  translation_policy?: string;
  max_cycles?: number;
}

export interface PromptItem {
  id: string;
  filename: string;
  path: string;
  name: string;
  type: 'translation' | 'review';
  content: string;
}

export interface GlossaryItem {
  source: string;
  target: string;
  category: string;
  status?: string;
  confidence: number;
  note?: string;
  term_id?: string | null;
  source_normalized?: string | null;
  canonical_term_id?: string | null;
  first_seen_chunk?: string | null;
  last_seen_chunk?: string | null;
  occurrences?: number;
  chapter_count?: number;
  sample_ids?: string[];
  evidence?: Array<Record<string, unknown>>;
  provenance?: string[];
  created_at?: string | null;
  updated_at?: string | null;
  retired_reason?: string | null;
}

export interface GlossaryResponse {
  book_id: string;
  terms: GlossaryItem[];
  conflicts: any[];
  updated_at?: string | null;
}

export interface BookMemoryResponse {
  book_id: string;
  characters: Array<{
    name: string;
    alias?: string[];
    role?: string;
    traits?: string[];
    summary?: string;
    first_seen?: string;
  }>;
  world_settings: Array<{
    term: string;
    explanation?: string;
    category?: string;
  }>;
  timeline?: Array<{
    chapter_id?: string;
    event?: string;
    impact?: string;
  }>;
  chapter_states?: Array<{
    chapter_id: string;
    chapter_name?: string;
    summary?: string;
    character_states?: Record<string, any>;
    active_conflicts?: string[];
  }>;
}

export interface ChapterReviewReport {
  schema_version?: string;
  chapter_id: string;
  reviewed_at: string | null;
  checked_paragraphs: number;
  reported_issues: number;
  applied_fixes: number;
  fixes: Array<{
    id: string;
    category?: string;
    severity?: string;
    confidence?: number;
    reason?: string;
    replacement?: string;
    operation?: 'replace' | 'clear';
    approved_translation?: string | null;
    auto_apply?: boolean;
    consensus?: boolean | null;
    reporters?: string[];
    invalid_reason?: string;
    applied?: boolean;
    not_applied_reason?: string | null;
  }>;
  context_findings?: Array<{
    id: string;
    category?: string;
    severity?: string;
    confidence?: number;
    reason?: string;
    evidence_ids?: string[];
    consensus?: boolean | null;
    reporters?: string[];
  }>;
  glossary_delta: {
    add: Array<{
      source: string;
      target: string;
      category?: string;
      note?: string;
      confidence?: number;
    }>;
    update: Array<{
      source: string;
      target: string;
      category?: string;
      note?: string;
      confidence?: number;
    }>;
    conflicts: Array<Record<string, unknown>>;
  };
  memory_delta: {
    add: Array<Record<string, unknown>>;
    update: Array<Record<string, unknown>>;
    conflicts: Array<Record<string, unknown>>;
  };
  chapter_state?: {
    chapter_id?: string;
    title?: string;
    status?: string;
    updated_at?: string;
    summary?: string;
    important_changes?: string[];
    active_entities?: string[];
    location?: string;
  };
  dual_review?: {
    enabled?: boolean;
    primary_fixes_count?: number;
    secondary_fixes_count?: number;
    consensus_fixes_count?: number;
    merged_fixes_count?: number;
  };
  review_diagnostics?: {
    chunking?: {
      mode?: 'source_chars' | 'paragraph_count';
      min_chars?: number | null;
      max_chars?: number | null;
      chunk_count?: number;
      context_before?: number;
      context_after?: number;
    };
    backtrack?: {
      enabled?: boolean;
      candidate_count?: number;
      rechecks?: Array<Record<string, unknown>>;
    };
  };
  migration_warning?: string | null;
}

export interface PreflightProviderResult {
  provider: string;
  type: string;
  role: string;
  status: 'ok' | 'failed' | 'warning';
  latency_ms: number;
  model: string;
  message: string;
}

export interface PreflightResponse {
  all_passed: boolean;
  results: PreflightProviderResult[];
}

export interface SystemConfig {
  paths?: {
    output_root?: string;
    translation_policy?: string;
  };
  roles?: {
    primary_translator?: string;
    fallback_translator?: string;
    secondary_fallback_translator?: string;
    fallback_translators?: string[];
    reviewer?: string;
    secondary_reviewer?: string;
    dual_review?: boolean;
    fallback_reviewers?: string[];
  };
  providers?: Record<string, {
    type: string;
    model?: string;
    base_url?: string;
    api_key?: string;
    api_key_ref?: string;
    api_key_configured?: boolean;
    api_key_preview?: string | null;
    temperature?: number;
    context_tokens?: number;
    timeout?: number;
    binary?: string;
    agy?: string;
    effort?: string;
    agent?: string;
  }>;
  pipeline?: {
    max_cycles?: number;
    max_chapter_batches?: number;
    primary_batch_max_chars?: number;
    max_provider_split_depth?: number;
    translation_max_tokens?: number;
    health_check_timeout?: number;
    layout?: string;
    apply?: boolean;
    autonomous?: boolean;
  };
  queue?: {
    source_root?: string;
    translated_root?: string;
  };
}

export interface StreamEvent {
  event: string;
  data: any;
  book_id?: string | null;
  timestamp: string;
  event_id: string;
}

export interface QueueItem {
  id: string;
  book_id: string;
  book_name: string;
  source_type: string;
  options: PipelineStartRequest;
  status: 'pending' | 'recovery_pending' | 'running' | 'pausing' | 'paused' | 'cancelling' | 'completed' | 'failed' | 'cancelled';
  phase?: 'queued' | 'initializing' | 'translating' | 'reviewing' | 'finalizing' | 'idle';
  reviewer_states?: Partial<Record<'primary' | 'secondary', 'standby' | 'pending' | 'reviewing' | 'retry_wait' | 'retrying' | 'completed' | 'failed' | 'cancelled'>>;
  reviewer_details?: Partial<Record<'primary' | 'secondary', ReviewerExecutionDetail>>;
  order_index: number;
  priority: number;
  overall_progress: number;
  current_chapter: string;
  current_chapter_index: number;
  total_chapters: number;
  message: string;
  error_detail?: string | null;
  enqueued_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  updated_at?: string | null;
  retry_count: number;
  checkpoint: Record<string, unknown>;
  process_id?: string | null;
  recovery_reason?: string | null;
}

export interface QueueStatusResponse {
  is_paused: boolean;
  concurrency: number;
  total_items: number;
  running_count: number;
  pending_count: number;
  completed_count: number;
  failed_count: number;
  items: QueueItem[];
}

export interface EnqueueRequest {
  book_ids: string[];
  options?: PipelineStartRequest;
  insert_front?: boolean;
}
