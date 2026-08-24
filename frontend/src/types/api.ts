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
  status: string;
  paragraphs: ParagraphItem[];
  chapter_summary: string;
  auto_fixed_count: number;
}

export interface TaskStatusResponse {
  task_id: string;
  book_id: string;
  status: 'idle' | 'running' | 'paused' | 'completed' | 'failed' | 'stopped';
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
  confidence: number;
  notes?: string;
  first_chapter?: string | null;
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
  chapter_id: string;
  reviewed_at: string;
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
    auto_apply?: boolean;
    invalid_reason?: string;
  }>;
  glossary_delta: Array<{
    source: string;
    target: string;
    category?: string;
    note?: string;
    confidence?: number;
  }>;
  memory_delta: Array<{
    key?: string;
    value?: string;
    category?: string;
    note?: string;
    confidence?: number;
  }>;
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
  book_id?: string;
  timestamp: string;
}

export interface QueueItem {
  id: string;
  book_id: string;
  book_name: string;
  source_type: string;
  options: PipelineStartRequest;
  status: 'pending' | 'running' | 'paused' | 'completed' | 'failed' | 'cancelled';
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
  retry_count: number;
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


