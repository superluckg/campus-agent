export interface Conversation {
  conversation_id: string;
  title: string;
  status: string;
  created_at: string;
  updated_at: string;
  last_user_message_at: string | null;
  title_status: "pending" | "generated" | "custom";
  is_busy: boolean;
}

export interface Citation {
  citation_index: number;
  title: string;
  filename?: string;
  page_number?: number;
  page_numbers?: number[];
  section_path?: string;
  source_url?: string;
  quoted_excerpt?: string;
}

export interface Message {
  message_id: string;
  request_id?: string | null;
  trace_id?: string | null;
  role: "user" | "assistant";
  content: string;
  status: string;
  agent_names: string[];
  display_metadata: Record<string, unknown>;
  citations: Citation[];
  created_at: string;
}

export interface Trace {
  trace_id: string;
  request_id: string;
  conversation_id: string;
  status: string;
  primary_intent?: string;
  selected_agents: string[];
  selected_skills: Array<Record<string, unknown>>;
  duration_ms?: number;
  started_at: string;
  query?: string | null;
  route?: string | null;
  finished_at?: string | null;
  request_source?: string;
  error_code?: string | null;
  user_id?: string;
  tool_call_count?: number;
  retrieval_rounds?: number;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  total_tokens?: number | null;
}
