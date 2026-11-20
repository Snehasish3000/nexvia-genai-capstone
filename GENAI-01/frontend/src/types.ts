export type Role = 'admin' | 'finance' | 'engineering' | 'guest';
export type RetrievalMode = 'dense' | 'hybrid' | 'hybrid_rerank';
export type FeedbackRating = 'up' | 'down';

export interface Session {
  accessToken: string;
  role: Role;
  username: string;
  expiresIn: number;
}

export interface Citation {
  chunk_id: string;
  source: string;
  page: number | null;
  quote: string;
  score: number;
}

export interface TokenUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  estimated_cost_usd: number;
}

export interface ChatResponse {
  answer: string;
  citations: Citation[];
  confidence: number;
  abstained: boolean;
  retrieval_mode: RetrievalMode;
  latency_ms: number;
  usage: TokenUsage;
  cache_hit: boolean;
  request_id: string;
}

export interface AdminStats {
  documents: number;
  chunks: number;
  feedback_count: number;
  total_queries: number;
  avg_latency_ms: number;
  total_estimated_cost_usd: number;
  cache_entries: number;
  generated_at: string;
}

export interface IngestResponse {
  document_id: string;
  source: string;
  chunks_created: number;
  allowed_roles: Role[];
  ingested_at: string;
}

export interface ApiErrorShape {
  detail?: string | Array<{ msg?: string }>;
  message?: string;
}
