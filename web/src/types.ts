export interface TimelineItem {
  id: number;
  type: 'user' | 'agent';
  content?: string;
  title?: string;
  status?: 'pending' | 'active' | 'done';
  expanded?: boolean;
  time?: string;
  logs?: LogEntry[];
  artifact?: Artifact;
  plan?: Plan;
}

export interface LogEntry {
  type: 'action' | 'typing_text' | 'text';
  icon?: string;
  text?: string;
  target?: string;
}

export interface PlanStep {
  id: string;
  title: string;
  status: 'pending' | 'active' | 'done';
}

export interface Plan {
  revision: string;
  steps: PlanStep[];
}

export interface PlanEvent {
  type: 'plan';
  plan: Plan;
}

export interface PlanResponseEvent {
  type: 'plan_response';
  action: 'approve' | 'reject' | 'edit';
  revision?: string;
}

export interface ArtifactItem {
  path: string;
  name: string;
  size?: number;
  content?: string;
}

export interface Artifact {
  title: string;
  desc: string;
  icon: string;
}

export interface VmHealth {
  status: string;
  error?: string;
  python_version?: string;
  working_directory?: string;
  runner_path_exists?: boolean;
  tmp_writable?: boolean;
  browser_ready?: boolean;
}

export interface BrowserState {
  url?: string;
  title?: string;
  content?: string;
  screenshot?: string;
}

export interface ServerEvent {
  type: string;
  content?: string;
  tool?: string;
  input?: Record<string, unknown>;
  created_files?: string[];
  message_type?: string;
  plan?: Plan;
  output?: string;
  error?: string;
  step?: number;
  max_steps?: number;
  message?: string;
  file_path?: string;
  command?: string;
  action?: string;
  url?: string;
  query?: string;
  total_steps?: number;
  code?: number;
  model?: string;
  llm_config?: string;
  status?: string;
  message_length?: number;
  title?: string;
  screenshot?: string;
  tools_available?: boolean;
  tool_calls_enabled?: boolean;
  tools?: string[];
}

export interface CreateSessionResponse {
  session_id: string;
  status: string;
  vm_name: string;
  llm_config?: string;
  llm_model?: string;
  vm_health?: VmHealth;
}

export interface PlanResponseBody {
  action: 'approve' | 'reject' | 'edit';
  revision?: string;
}

export type ComputerView = 'browser' | 'editor' | 'terminal' | 'preview';
