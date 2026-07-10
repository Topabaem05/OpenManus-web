import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Menu, Plus, Folder, Clock, Search, LayoutTemplate,
  Terminal, Code2, Globe, X, ChevronRight, Monitor, Square,
  Compass, Save, Check, ChevronUp, ChevronDown,
  FileCode2, PlayCircle, Loader2, Send, Paperclip, StopCircle, Sparkles,
  FileText
} from 'lucide-react';
import { useWebSocket, createSession, sendHttpMessage, sendPlanResponse, stopSession, fetchSessions } from './hooks/useWebSocket';
import type { TimelineItem, ServerEvent, ComputerView, CreateSessionResponse, BrowserState, Plan } from './types';

export default function App() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<TimelineItem[]>([]);
  const [activeComputerTab, setActiveComputerTab] = useState<ComputerView>('browser');
  const [inputText, setInputText] = useState('');
  const [mobileView, setMobileView] = useState<'task' | 'computer'>('task');
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const [overallProgress, setOverallProgress] = useState(0);
  const [isConnecting, setIsConnecting] = useState(false);
  const [currentFilePath, setCurrentFilePath] = useState('');
  const [currentCode, setCurrentCode] = useState('');
  const [terminalLines, setTerminalLines] = useState<string[]>([]);
  const [browserUrl, setBrowserUrl] = useState('');
  const [browserState, setBrowserState] = useState<BrowserState>({});
  const [toolsAvailable, setToolsAvailable] = useState<boolean | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [currentPlan, setCurrentPlan] = useState<Plan | null>(null);
  const [planAwaitingResponse, setPlanAwaitingResponse] = useState(false);
  const [createdFiles, setCreatedFiles] = useState<string[]>([]);
  const [persistedSessions, setPersistedSessions] = useState<Array<{session_id: string; status: string; last_message?: string; created_at?: string}>>([]);

  const { events, setEvents } = useWebSocket(sessionId);
  const timelineEndRef = useRef<HTMLDivElement>(null);
  const nextIdRef = useRef(1);
  const stepCountRef = useRef(0);
  const readyEventSeenRef = useRef(false);

  useEffect(() => {
    timelineEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [timeline]);

  useEffect(() => {
    fetchSessions().then(setPersistedSessions).catch(() => {});
  }, [sessionId]);

  useEffect(() => {
    if (events.length === 0) return;
    for (const event of events) {
      processEvent(event);
    }
    setEvents([]);
  }, [events]);

  const processEvent = useCallback((event: ServerEvent) => {
    switch (event.type) {
      case 'agent_ready': {
        if (readyEventSeenRef.current) break;
        readyEventSeenRef.current = true;
        const item: TimelineItem = {
          id: nextIdRef.current++,
          type: 'agent',
          title: 'Agent initialized',
          status: 'done',
          expanded: false,
          time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
          logs: [{ type: 'text', text: `Agent ready (model: ${event.model || 'unknown'})` }],
        };
        setTimeline(prev => [...prev, item]);
        break;
      }

      case 'step_start': {
        if (event.step && event.max_steps) {
          const progress = Math.min(10 + (event.step / event.max_steps) * 80, 90);
          setOverallProgress(Math.round(progress));
        }
        break;
      }

      case 'message_dispatched': {
        setTimeline(prev => {
          const lastActive = [...prev].reverse().find(i => i.type === 'agent' && i.status === 'active');
          if (!lastActive) return prev;
          return prev.map(item =>
            item.id === lastActive.id
              ? {
                  ...item,
                  logs: [
                    ...(item.logs || []),
                    { type: 'text' as const, text: `VM inbox updated (${event.message_length || 0} chars)` },
                  ],
                }
              : item
          );
        });
        break;
      }

      case 'message_received': {
        setTimeline(prev => {
          const lastActive = [...prev].reverse().find(i => i.type === 'agent' && i.status === 'active');
          if (!lastActive) return prev;
          return prev.map(item =>
            item.id === lastActive.id
              ? {
                  ...item,
                  logs: [
                    ...(item.logs || []),
                    { type: 'text' as const, text: `VM runner received message (${event.message_length || 0} chars)` },
                  ],
                }
              : item
          );
        });
        break;
      }

      case 'agent_capabilities': {
        const available = event.tools_available !== false && event.tool_calls_enabled !== false;
        setToolsAvailable(available);
        setTimeline(prev => {
          const lastAgent = [...prev].reverse().find(i => i.type === 'agent' && i.status === 'done');
          if (!lastAgent) return prev;
          return prev.map(item =>
            item.id === lastAgent.id
              ? {
                  ...item,
                  logs: [
                    ...(item.logs || []),
                    {
                      type: 'text' as const,
                      text: available
                        ? `Tools available: ${(event.tools || []).join(', ') || 'unknown'}`
                        : 'Tools are disabled for this model profile.',
                    },
                  ],
                }
              : item
          );
        });
        break;
      }

      case 'plan': {
        if (event.plan) {
          setCurrentPlan(event.plan as Plan);
          setPlanAwaitingResponse(true);
          setTimeline(prev => [...prev, {
            id: nextIdRef.current++,
            type: 'agent',
            title: 'Plan proposed',
            status: 'pending',
            expanded: true,
            time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
            plan: event.plan as Plan,
            logs: [{ type: 'text', text: 'Plan requires approval.' }],
          }]);
        }
        break;
      }

      case 'thought': {
        setIsRunning(true);
        setTimeline(prev => {
          const lastAgent = [...prev].reverse().find(i => i.type === 'agent' && i.status === 'active');
          if (lastAgent) {
            return prev.map(item =>
              item.id === lastAgent.id
                ? { ...item, logs: [...(item.logs || []), { type: 'typing_text' as const, text: event.content || '' }] }
                : item
            );
          }
          return prev;
        });
        break;
      }

      case 'tool_call': {
        setIsRunning(true);
        stepCountRef.current++;
        const toolName = event.tool || 'unknown';
        const log = buildToolCallLog(toolName, event);

        if (log.view) setActiveComputerTab(log.view);
        if (log.filePath) setCurrentFilePath(log.filePath);
        if (log.command) {
          setTerminalLines(prev => [...prev, `$ ${log.command}`]);
        }
        if (log.url) setBrowserUrl(log.url);

        setTimeline(prev => {
          const lastAgent = [...prev].reverse().find(i => i.type === 'agent' && i.status === 'active');
          if (lastAgent) {
            return prev.map(item =>
              item.id === lastAgent.id
                ? { ...item, logs: [...(item.logs || []), log.entry] }
                : item
            );
          }
          const newItem: TimelineItem = {
            id: nextIdRef.current++,
            type: 'agent',
            title: `Using ${toolName}`,
            status: 'active',
            expanded: true,
            time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
            logs: [log.entry],
          };
          return [...prev, newItem];
        });

        const progress = Math.min(10 + stepCountRef.current * 8, 90);
        setOverallProgress(progress);
        break;
      }

      case 'tool_result': {
        const toolName = event.tool || '';
        if (toolName === 'str_replace_editor') {
          if (event.output) setCurrentCode(extractCodeFromOutput(event.output));
        }
        if (event.output) {
          setTerminalLines(prev => [...prev, event.output || ''].slice(-50));
        }
        if (event.created_files && Array.isArray(event.created_files) && (event.created_files as string[]).length) {
          const paths = event.created_files as string[];
          setCreatedFiles(prev => [...new Set([...prev, ...paths])]);
          setTimeline(prev => {
            const last = [...prev].reverse().find(i => i.type === 'agent' && i.status === 'active');
            if (!last) return prev;
            const msg = { type: 'text' as const, text: `Created artifacts: ${paths.map((p: string) => p.split('/').pop() || p).join(', ')}` };
            return prev.map(item => item.id === last.id ? { ...item, logs: [...(item.logs || []), msg] } : item);
          });
          if (paths.some((p: string) => /\.(pptx|docx|pdf|html|png)$/i.test(p))) {
            setActiveComputerTab('preview');
          }
        }
        break;
      }

      case 'browser_state': {
        setActiveComputerTab('browser');
        setBrowserState(prev => ({
          url: event.url || prev.url || browserUrl,
          title: event.title,
          content: event.content,
          screenshot: event.screenshot,
        }));
        if (event.url) setBrowserUrl(event.url);
        if (event.error) {
          setTerminalLines(prev => [...prev, `Browser state error: ${event.error}`].slice(-50));
        }
        break;
      }

      case 'final_answer': {
        setIsRunning(false);
        setTimeline(prev => {
          const lastActive = [...prev].reverse().find(i => i.type === 'agent' && i.status === 'active');
          if (lastActive) {
            return prev.map(item =>
              item.id === lastActive.id
                ? {
                    ...item,
                    title: 'Task complete',
                    status: 'done' as const,
                    expanded: false,
                    logs: [...(item.logs || []), { type: 'text' as const, text: event.content || '' }],
                  }
                : item
            );
          }
          const newItem: TimelineItem = {
            id: nextIdRef.current++,
            type: 'agent',
            title: 'Task complete',
            status: 'done',
            expanded: false,
            time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
            logs: [{ type: 'text' as const, text: event.content || '' }],
          };
          return [...prev, newItem];
        });
        setOverallProgress(100);
        break;
      }

      case 'message_complete': {
        setIsRunning(false);
        setTimeline(prev => {
          const lastActive = [...prev].reverse().find(i => i.type === 'agent' && i.status === 'active');
          if (!lastActive) return prev;
          return prev.map(item =>
            item.id === lastActive.id
              ? {
                  ...item,
                  title: 'Task complete',
                  status: 'done' as const,
                  expanded: false,
                }
              : item
          );
        });
        setOverallProgress(100);
        break;
      }

      case 'error': {
        setIsRunning(false);
        setTimeline(prev => {
          const lastActive = [...prev].reverse().find(i => i.type === 'agent' && i.status === 'active');
          if (lastActive) {
            return prev.map(item =>
              item.id === lastActive.id
                ? {
                    ...item,
                    title: 'Error occurred',
                    status: 'done' as const,
                    expanded: true,
                    logs: [...(item.logs || []), { type: 'text' as const, text: `Error: ${event.message}` }],
                  }
                : item
            );
          }
          const newItem: TimelineItem = {
            id: nextIdRef.current++,
            type: 'agent',
            title: 'Error occurred',
            status: 'done',
            expanded: true,
            time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
            logs: [{ type: 'text' as const, text: `Error: ${event.message}` }],
          };
          return [...prev, newItem];
        });
        break;
      }
    }
  }, []);

  const addSessionReadyItem = (result: CreateSessionResponse) => {
    if (!result.llm_model) return;
    const vmHealth = result.vm_health;
    const healthText = vmHealth
      ? `VM ready (${vmHealth.python_version || 'python unknown'}, cwd: ${vmHealth.working_directory || '?'})`
      : 'VM health unavailable';
    readyEventSeenRef.current = true;
    setTimeline(prev => [...prev, {
      id: nextIdRef.current++,
      type: 'agent',
      title: 'Agent initialized',
      status: 'done',
      expanded: false,
      time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      logs: [
        {
          type: 'text',
          text: `Agent ready (model: ${result.llm_model})`,
        },
        {
          type: 'text',
          text: healthText,
        },
      ],
    }]);
  };

  const handleNewTask = async () => {
    setIsConnecting(true);
    try {
      const result = await createSession();
      const newSessionId = result.session_id;
      setSessionId(newSessionId);
      readyEventSeenRef.current = false;
      setTimeline([{
        id: nextIdRef.current++,
        type: 'user',
        content: 'New session created',
      }]);
      addSessionReadyItem(result);
      setOverallProgress(0);
      stepCountRef.current = 0;
      setTerminalLines([]);
      setCurrentCode('');
      setCurrentFilePath('');
      setBrowserUrl('');
      setBrowserState({});
      setCreatedFiles([]);
    } catch (e) {
      alert(`Failed to create session: ${e}`);
    } finally {
      setIsConnecting(false);
    }
  };

  const handlePlanResponse = async (action: 'approve' | 'reject' | 'edit', revision?: string) => {
    if (!sessionId) return;
    setPlanAwaitingResponse(false);
    setTimeline(prev => prev.map(item =>
      (item.type === 'agent' && item.plan && item.status === 'pending') ? { ...item, status: 'done' } : item
    ));
    try {
      await sendPlanResponse(sessionId, { action, revision });
    } catch (e) {
      setPlanAwaitingResponse(true);
      alert(`Failed to send plan response: ${e}`);
    }
  };

  const handleSend = async () => {
    if (!inputText.trim()) return;

    let currentSessionId = sessionId;
    if (!currentSessionId) {
      setIsConnecting(true);
      try {
        const result = await createSession();
        currentSessionId = result.session_id;
        setSessionId(currentSessionId);
        readyEventSeenRef.current = false;
        setTimeline([{
          id: nextIdRef.current++,
          type: 'user',
          content: 'New session created',
        }]);
        addSessionReadyItem(result);
        setOverallProgress(0);
        stepCountRef.current = 0;
        setTerminalLines([]);
        setCurrentCode('');
        setCurrentFilePath('');
        setBrowserUrl('');
        setBrowserState({});
        setCreatedFiles([]);
      } catch (e) {
        alert(`Failed to create session: ${e}`);
        setIsConnecting(false);
        return;
      }
      setIsConnecting(false);
    }

    const userMsg: TimelineItem = {
      id: nextIdRef.current++,
      type: 'user',
      content: inputText.trim(),
    };
    setTimeline(prev => [...prev, userMsg]);

    const agentItem: TimelineItem = {
      id: nextIdRef.current++,
      type: 'agent',
      title: 'Processing...',
      status: 'active',
      expanded: true,
      time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      logs: [],
    };
    setTimeline(prev => [...prev, agentItem]);
    setCurrentPlan(null);
    setPlanAwaitingResponse(false);
    setInputText('');

    if (!currentSessionId) return;

    try {
      await sendHttpMessage(currentSessionId, userMsg.content || '');
    } catch (e) {
      setTimeline(prev => [...prev, {
        id: nextIdRef.current++,
        type: 'agent',
        title: 'Send failed',
        status: 'done',
        logs: [{ type: 'text', text: `Failed to send message: ${e}` }],
      }]);
    }
  };

  const handleStop = async () => {
    if (!sessionId) return;
    try {
      await stopSession(sessionId);
      setIsRunning(false);
      setTimeline(prev => [...prev, {
        id: nextIdRef.current++,
        type: 'agent',
        title: 'Stopped by user',
        status: 'done',
        logs: [{ type: 'text', text: '⏹ Execution stopped.' }],
      }]);
    } catch (e) {
      alert(`Failed to stop: ${e}`);
    }
  };

  return (
    <div className="flex h-screen w-full bg-[#F7F7F8] font-[var(--font-sans)] text-[#171719] overflow-hidden">
      <Sidebar isSidebarOpen={isSidebarOpen} setIsSidebarOpen={setIsSidebarOpen} onNewTask={handleNewTask} isConnecting={isConnecting} sessions={persistedSessions} currentSessionId={sessionId} />
      <TaskPanel
        timeline={timeline}
        timelineEndRef={timelineEndRef}
        inputText={inputText}
        setInputText={setInputText}
        mobileView={mobileView}
        setMobileView={setMobileView}
        isSidebarOpen={isSidebarOpen}
        setIsSidebarOpen={setIsSidebarOpen}
        overallProgress={overallProgress}
        onSend={handleSend}
        onStop={handleStop}
        isConnecting={isConnecting}
        isRunning={isRunning}
        onPlanApprove={() => handlePlanResponse('approve')}
        onPlanReject={() => handlePlanResponse('reject', 'Plan declined by user.')}
      />
      <ComputerView
        activeTab={activeComputerTab}
        mobileView={mobileView}
        setMobileView={setMobileView}
        overallProgress={overallProgress}
        terminalLines={terminalLines}
        currentFilePath={currentFilePath}
        currentCode={currentCode}
        browserUrl={browserUrl}
        browserState={browserState}
        toolsAvailable={toolsAvailable}
        timeline={timeline}
        sessionId={sessionId}
        createdFiles={createdFiles}
      />
      {currentPlan && planAwaitingResponse && (
        <div className="fixed bottom-4 right-4 w-80 z-50 animate-modal-appear">
          <PlanCard
            plan={currentPlan}
            awaiting={planAwaitingResponse}
            onApprove={() => handlePlanResponse('approve')}
            onReject={() => handlePlanResponse('reject', 'Plan declined by user.')}
            onEdit={() => {
              const note = window.prompt('Edit plan - describe the change:');
              if (note !== null) handlePlanResponse('edit', note);
            }}
          />
        </div>
      )}
    </div>
  );
}

function buildToolCallLog(toolName: string, event: ServerEvent) {
  const input = event.input || {};
  const asString = (value: unknown): string => typeof value === 'string' ? value : '';
  let icon = 'search';
  let text = '';
  let view: ComputerView | undefined;
  let filePath = '';
  let command = '';
  let url = '';

  if (toolName === 'browser_use') {
    icon = 'search';
    const action = event.action || asString(input.action);
    url = event.url || asString(input.url);
    text = `Browser: ${action}${url ? ` → ${url}` : ''}`;
    view = 'browser';
  } else if (toolName === 'str_replace_editor') {
    icon = 'code';
    const cmd = event.command || asString(input.command);
    filePath = event.file_path || asString(input.path);
    text = `Editor: ${cmd} ${filePath}`;
    view = 'editor';
  } else if (toolName === 'python_execute') {
    icon = 'terminal';
    command = 'python3 -c "..."';
    text = `Python execution`;
    view = 'terminal';
  } else if (toolName === 'bash') {
    icon = 'terminal';
    command = event.command || asString(input.command);
    text = `Terminal: ${command}`;
    view = 'terminal';
  } else if (toolName === 'web_search') {
    icon = 'search';
    const q = event.query || asString(input.query);
    text = `Search: ${q}`;
    view = 'browser';
  } else if (toolName === 'document_generation') {
    icon = 'document';
    const fmt = asString(input.format);
    text = `Document: ${fmt || 'markdown'}`;
    view = 'preview';
  } else if (toolName === 'wide_research') {
    icon = 'search';
    const subAgents = (input as Record<string, unknown>).sub_agents;
    text = `Wide Research: ${subAgents} parallel sub-agents dispatched`;
  } else if (toolName === 'terminate') {
    icon = 'play';
    text = 'Task terminated';
  } else {
    icon = 'search';
    text = `${toolName}`;
  }

  return {
    icon,
    text,
    view,
    filePath,
    command,
    url,
    entry: { type: 'action' as const, icon, text, target: view },
  };
}

function extractCodeFromOutput(output: string): string {
  const lines = output.split('\n');
  const codeLines = lines.filter(l => /^\s*\d+\s/.test(l)).map(l => l.replace(/^\s*\d+\s/, ''));
  return codeLines.join('\n');
}

function PlanCard({ plan, awaiting, onApprove, onReject, onEdit }: { plan: Plan; awaiting: boolean; onApprove: () => void; onReject: () => void; onEdit: () => void }) {
  return (
    <div className="rounded-xl border border-[rgba(0,0,0,0.06)] bg-[#FFFFFF] p-4 shadow-lg animate-modal-appear">
      <div className="flex items-center justify-between mb-2">
        <h4 className="font-bold text-[14px] text-[#171719]">Plan review</h4>
        <span className="text-[11px] text-[#9CA0A8] font-mono">{plan.revision}</span>
      </div>
      <ol className="list-decimal list-inside space-y-1 mb-3">
        {plan.steps.map(step => <li key={step.id} className={`text-[13px] ${step.status === 'done' ? 'text-[#9CA0A8] line-through' : step.status === 'active' ? 'text-[#5B5BD6] font-semibold' : 'text-[#6B6E76]'}`}>{step.title}</li>)}
      </ol>
      {awaiting ? (
        <div className="flex gap-2">
          <button onClick={onApprove} className="px-3 py-1.5 bg-[#171719] text-[#FFFFFF] text-[12px] font-semibold rounded hover:opacity-85">Approve</button>
          <button onClick={onReject} className="px-3 py-1.5 border border-[rgba(0,0,0,0.06)] text-[12px] font-semibold rounded hover:bg-[#E6E7EA]">Decline</button>
          <button onClick={onEdit} className="px-3 py-1.5 border border-[rgba(0,0,0,0.06)] text-[12px] font-semibold rounded hover:bg-[#E6E7EA] text-[#6B6E76]">Edit</button>
        </div>
      ) : <div className="text-[12px] text-[#9CA0A8]">Plan handled.</div>}
    </div>
  );
}

function Sidebar({ isSidebarOpen, setIsSidebarOpen, onNewTask, isConnecting, sessions, currentSessionId }: {
  isSidebarOpen: boolean;
  setIsSidebarOpen: (v: boolean) => void;
  onNewTask: () => void;
  isConnecting: boolean;
  sessions: Array<{session_id: string; status: string; last_message?: string; created_at?: string}>;
  currentSessionId: string | null;
}) {
  return (
    <div className={`${isSidebarOpen ? 'w-64' : 'w-20'} transition-all duration-300 flex-shrink-0 border-r border-[rgba(0,0,0,0.06)] bg-[#F7F7F8] flex flex-col h-full hidden md:flex z-10`}>
      <div className="h-14 flex items-center px-4 border-b border-[rgba(0,0,0,0.06)]">
        <div className="w-7 h-7 bg-[#171719] rounded-md flex items-center justify-center text-[#FFFFFF] font-bold text-sm cursor-pointer flex-shrink-0" onClick={() => setIsSidebarOpen(!isSidebarOpen)}>M</div>
        {isSidebarOpen && <span className="ml-3 font-bold text-[#171719] text-[15px] tracking-tight">Web-Manus</span>}
      </div>
      <div className="flex-1 overflow-y-auto py-4 px-3 space-y-1">
        <button
          className="w-full flex items-center px-3 py-2 bg-[#171719] text-[#FFFFFF] rounded-lg hover:opacity-85 transition-colors mb-6 shadow-sm disabled:opacity-50 font-medium"
          onClick={onNewTask}
          disabled={isConnecting}
        >
          {isConnecting ? <Loader2 size={16} className="animate-spin" /> : <Plus size={16} />}
          {isSidebarOpen && <span className="ml-2 text-[13px] font-medium">{isConnecting ? 'Creating...' : '새 작업 (New Task)'}</span>}
        </button>
        {isSidebarOpen && sessions.length > 0 && (
          <div className="space-y-0.5 mb-4">
            <div className="px-3 text-[11px] font-bold text-[#9CA0A8] uppercase tracking-wider mb-2 mt-4">Recent Sessions</div>
            {sessions.slice(0, 10).map(s => (
              <div
                key={s.session_id}
                className={`w-full flex items-center px-3 py-2 rounded-lg transition-colors cursor-pointer ${currentSessionId === s.session_id ? 'bg-[#E6E7EA] text-[#171719]' : 'text-[#6B6E76] hover:bg-[#FFFFFF] hover:text-[#171719]'}`}
                title={s.last_message || s.session_id}
              >
                <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 mr-2 ${s.status === 'ready' ? 'bg-[#171719]' : s.status === 'error' ? 'bg-[#D94C4C]' : 'bg-[#5B5BD6]'}`} />
                <span className="text-[12px] truncate">{s.last_message || s.session_id.slice(0, 16)}</span>
              </div>
            ))}
          </div>
        )}
        <div className="space-y-0.5">
          {isSidebarOpen && <div className="px-3 text-[11px] font-bold text-[#9CA0A8] uppercase tracking-wider mb-2 mt-4">Workspace</div>}
          <NavItem icon={<Folder size={16} />} label="프로젝트 (Projects)" isOpen={isSidebarOpen} />
          <NavItem icon={<Clock size={16} />} label="최근 항목 (Recent)" isOpen={isSidebarOpen} />
          <NavItem icon={<LayoutTemplate size={16} />} label="예약됨 (Scheduled)" isOpen={isSidebarOpen} />
        </div>
      </div>
    </div>
  );
}

function NavItem({ icon, label, isOpen, active }: { icon: React.ReactNode; label: string; isOpen: boolean; active?: boolean }) {
  return (
    <button className={`w-full flex items-center px-3 py-2 rounded-lg transition-colors ${active ? 'bg-[#E6E7EA] text-[#171719] font-semibold' : 'text-[#6B6E76] hover:bg-[#FFFFFF] hover:text-[#171719]'}`}>
      <span className="flex-shrink-0">{icon}</span>
      {isOpen && <span className="ml-3 text-[13px]">{label}</span>}
    </button>
  );
}

const AVAILABLE_SKILLS = [
  { name: 'research', desc: 'Deep web research on a topic with sources' },
  { name: 'code', desc: 'Write, debug, or review code' },
  { name: 'write', desc: 'Create documents, reports, or content' },
  { name: 'slides', desc: 'Generate PowerPoint / slide deck from an outline' },
  { name: 'pdf', desc: 'Generate a formatted PDF document or report' },
  { name: 'data', desc: 'Load, analyze, and visualize datasets' },
  { name: 'analyze', desc: 'Analyze data and create visualizations' },
  { name: 'translate', desc: 'Translate text between languages' },
  { name: 'summarize', desc: 'Summarize long text or documents' },
  { name: 'wide-research', desc: 'Parallel multi-agent research (100+ agents)' },
];

function TaskPanel({ timeline, timelineEndRef, inputText, setInputText, mobileView, setMobileView, isSidebarOpen, setIsSidebarOpen, overallProgress, onSend, onStop, isConnecting, isRunning, onPlanApprove, onPlanReject }: {
  timeline: TimelineItem[];
  timelineEndRef: React.RefObject<HTMLDivElement | null>;
  inputText: string;
  setInputText: (v: string) => void;
  mobileView: 'task' | 'computer';
  setMobileView: (v: 'task' | 'computer') => void;
  isSidebarOpen: boolean;
  setIsSidebarOpen: (v: boolean) => void;
  overallProgress: number;
  onSend: () => void;
  onStop: () => void;
  isConnecting: boolean;
  isRunning: boolean;
  onPlanApprove: () => void;
  onPlanReject: () => void;
}) {
  return (
    <div className={`flex flex-col h-full border-r border-[rgba(0,0,0,0.06)] bg-[#F7F7F8] ${mobileView === 'task' ? 'flex-1' : 'hidden md:flex w-[420px] lg:w-[480px] flex-shrink-0 z-10'}`}>
      <div className="h-14 flex items-center justify-between px-5 border-b border-[rgba(0,0,0,0.06)] bg-[#F7F7F8]/90 backdrop-blur-md z-10 sticky top-0">
        <div className="flex items-center">
          <div className="md:hidden mr-3" onClick={() => setIsSidebarOpen(!isSidebarOpen)}>
            <Menu size={20} className="text-[#9CA0A8]" />
          </div>
          <h2 className="font-bold text-[15px] text-[#171719] truncate">
            {timeline.length > 0 ? 'Active Session' : 'Web-Manus'}
          </h2>
        </div>
        <button className="md:hidden text-[#5B5BD6] text-sm font-medium flex items-center bg-[#F2F3F5] px-3 py-1.5 rounded-full" onClick={() => setMobileView('computer')}>
          컴퓨터 보기 <ChevronRight size={16} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-5 space-y-6 bg-[#F7F7F8] scroll-smooth relative">
        {timeline.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-[#9CA0A8]">
            <Compass size={48} className="mb-4 text-[#6B6E76]" />
            <p className="text-[15px] font-medium text-[#9CA0A8]">Click "New Task" to start</p>
            <p className="text-[13px] mt-1">A microsandbox VM will be created for your session</p>
          </div>
        )}
        {timeline.map((item, index) => {
          const isLast = index === timeline.length - 1;
          if (item.type === 'user') {
            return (
              <div key={item.id} className="flex items-start mb-8 mt-2 animate-in fade-in slide-in-from-bottom-2 duration-500">
                <div className="w-7 h-7 rounded-full bg-[#171719]/20 flex items-center justify-center text-[#5B5BD6] font-bold mr-3 flex-shrink-0 text-xs">U</div>
                <div className="text-[#171719] text-[15px] font-medium leading-relaxed bg-[#E6E7EA] px-4 py-3 rounded-2xl rounded-tl-none text-left">
                  {item.content}
                </div>
              </div>
            );
          }
          return <AgentTimelineItem key={item.id} item={item} isLast={isLast} onApprovePlan={onPlanApprove} onRejectPlan={onPlanReject} />;
        })}
        {overallProgress > 0 && overallProgress < 100 && (
          <div className="flex items-center ml-10 text-[#6B6E76] text-[13px] pb-4">
            <Loader2 size={14} className="mr-2 animate-loading-ring text-[#5B5BD6]" /> 에이전트가 생각하고 있습니다...
          </div>
        )}
        <div ref={timelineEndRef} className="h-2" />
      </div>

      <div className="p-4 bg-[#F7F7F8] border-t border-[rgba(0,0,0,0.06)] relative">
        {inputText.startsWith('/') && !inputText.includes(' ') && (
          <div className="absolute bottom-full left-4 mb-1 w-72 bg-[#FFFFFF] border border-[rgba(0,0,0,0.06)] rounded-xl shadow-xl overflow-hidden z-50 animate-dropdown">
            {AVAILABLE_SKILLS.filter(s => s.name.startsWith(inputText.slice(1))).map((skill, idx) => (
              <div
                key={skill.name}
                onClick={() => setInputText('/' + skill.name + ' ')}
                className={`flex items-center px-3 py-2.5 cursor-pointer hover:bg-[#E6E7EA] transition-colors ${idx === 0 ? 'bg-[#E6E7EA]' : ''}`}
              >
                <Sparkles size={14} className="text-[#5B5BD6] mr-2.5 flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="text-[13px] font-semibold text-[#171719]">/{skill.name}</div>
                  <div className="text-[11px] text-[#6B6E76] truncate">{skill.desc}</div>
                </div>
              </div>
            ))}
            {AVAILABLE_SKILLS.filter(s => s.name.startsWith(inputText.slice(1))).length === 0 && (
              <div className="px-3 py-2.5 text-[13px] text-[#9CA0A8]">No matching skills</div>
            )}
          </div>
        )}
        <div className="relative flex items-end bg-[#FFFFFF] border border-[rgba(0,0,0,0.06)] rounded-2xl focus-within:ring-2 focus-within:ring-[#5B5BD6] focus-within:border-[#5B5BD6] transition-all p-1">
          <button className="p-2.5 text-[#6B6E76] hover:text-[#171719] rounded-lg transition-colors"><Paperclip size={18} /></button>
          <textarea
            rows={1}
            className="flex-1 max-h-32 p-2.5 bg-transparent text-[#171719] placeholder:text-[#9CA0A8] focus:outline-none resize-none text-[14px]"
            placeholder="무엇이든 물어보세요... (type / for skills)"
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); onSend(); } }}
          />
          {isRunning ? (
            <button
              className="p-2 mb-1 mr-1 rounded-lg transition-colors bg-[#D94C4C] text-[#171719] shadow-md hover:bg-[#B83A3A]"
              onClick={onStop}
              title="정지"
            >
              <StopCircle size={16} />
            </button>
          ) : (
            <button
              className={`p-2 mb-1 mr-1 rounded-lg transition-colors ${inputText.trim() && !isConnecting ? 'bg-[#171719] text-[#FFFFFF] shadow-md hover:opacity-85' : 'bg-[#E6E7EA] text-[#9CA0A8]'}`}
              onClick={onSend}
              disabled={!inputText.trim() || isConnecting}
            >
              <Send size={16} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function AgentTimelineItem({ item, isLast, onApprovePlan, onRejectPlan }: { item: TimelineItem; isLast: boolean; onApprovePlan?: () => void; onRejectPlan?: () => void }) {
  const [isExpanded, setIsExpanded] = useState(item.expanded);

  useEffect(() => {
    setIsExpanded(item.expanded);
  }, [item.expanded]);

  return (
    <div className="relative pl-0 animate-in fade-in slide-in-from-bottom-2 duration-300">
      {!isLast && <div className="absolute left-[13px] top-8 bottom-[-24px] w-[2px] bg-[#E6E7EA]"></div>}
      <div className="flex items-start">
        <div className="relative z-10 w-7 h-7 rounded-full bg-[#FFFFFF] flex items-center justify-center flex-shrink-0 mt-0.5 shadow-sm border border-[rgba(0,0,0,0.06)]">
          {item.status === 'done' && <Check size={14} strokeWidth={3} className="text-[#9CA0A8]" />}
          {item.status === 'active' && <div className="w-3 h-3 rounded-full bg-[#5B5BD6] animate-status-pulse" />}
          {item.status === 'pending' && <div className="w-2 h-2 rounded-full bg-gray-300" />}
        </div>
        <div className="ml-3 flex-1 pb-2">
          <div className="flex items-center cursor-pointer select-none group" onClick={() => setIsExpanded(!isExpanded)}>
            <h3 className={`font-bold text-[14px] transition-colors ${item.status === 'pending' ? 'text-[#9CA0A8]' : 'text-[#171719] group-hover:text-[#5B5BD6]'}`}>{item.title}</h3>
            {item.time && <span className="ml-auto text-[11px] text-[#9CA0A8] font-mono">{item.time}</span>}
          </div>
          <div className={`overflow-hidden transition-all duration-500 ease-in-out ${isExpanded ? 'max-h-[800px] opacity-100 mt-3' : 'max-h-0 opacity-0'}`}>
            <div className="space-y-3 pb-3">
              {item.plan && (
                <div className="bg-[#FFFFFF] border border-[rgba(0,0,0,0.06)] rounded-xl p-3 space-y-2 animate-in fade-in duration-300">
                  <div className="text-[11px] font-bold text-[#6B6E76] uppercase tracking-wider mb-1">Plan</div>
                  {item.plan.steps.map((step, si) => (
                    <div key={step.id} className="flex items-start text-[13px]">
                      <div className="flex-shrink-0 w-4 h-4 mr-2 mt-0.5">
                        {step.status === 'done' && <div className="w-4 h-4 rounded-full bg-[#171719] flex items-center justify-center"><Check size={10} strokeWidth={3} className="text-[#FFFFFF]" /></div>}
                        {step.status === 'active' && <div className="w-4 h-4 rounded-full bg-[#5B5BD6] animate-status-pulse" />}
                        {step.status === 'pending' && <div className="w-4 h-4 rounded-full border-2 border-[#2D3035]" />}
                      </div>
                      <span className={step.status === 'done' ? 'text-[#9CA0A8] line-through' : step.status === 'active' ? 'text-[#171719] font-medium' : 'text-[#6B6E76]'}>{si + 1}. {step.title}</span>
                    </div>
                  ))}
                  {item.status === 'pending' && (
                    <div className="flex gap-2 mt-3 pt-2 border-t border-[rgba(0,0,0,0.06)]">
                      <button onClick={(e) => { e.stopPropagation(); onApprovePlan?.(); }} className="px-3 py-1.5 bg-[#171719] text-[#FFFFFF] text-[12px] font-semibold rounded hover:opacity-85">Approve</button>
                      <button onClick={(e) => { e.stopPropagation(); onRejectPlan?.(); }} className="px-3 py-1.5 border border-[rgba(0,0,0,0.06)] text-[12px] font-semibold rounded hover:bg-[#E6E7EA] text-[#6B6E76]">Decline</button>
                    </div>
                  )}
                </div>
              )}
              {item.logs?.map((log, idx) => {
                if (log.type === 'action') {
                  return (
                    <div
                      key={idx}
                      className="flex items-center w-fit px-3 py-1.5 bg-[#E6E7EA] hover:bg-[#F2F3F5] cursor-pointer hover-physics rounded-[var(--radius-md)] text-[13px] font-semibold text-[#171719] active:scale-95 animate-in fade-in zoom-in-95 duration-300"
                    >
                      {log.icon === 'search' && <Search size={14} className="mr-2 text-[#9CA0A8]" />}
                      {log.icon === 'code' && <Code2 size={14} className="mr-2 text-[#9CA0A8]" />}
                      {log.icon === 'terminal' && <Terminal size={14} className="mr-2 text-[#9CA0A8]" />}
                      {log.icon === 'play' && <PlayCircle size={14} className="mr-2 text-[#9CA0A8]" />}
                      {log.icon === 'save' && <Save size={14} className="mr-2 text-[#9CA0A8]" />}
                      {log.icon === 'document' && <FileText size={14} className="mr-2 text-[#9CA0A8]" />}
                      {log.text}
                    </div>
                  );
                } else if (log.type === 'typing_text') {
                  return <TypewriterText key={idx} text={log.text || ''} />;
                } else {
                  return (
                    <div key={idx} className="bg-[#FFFFFF] border border-[rgba(0,0,0,0.06)] rounded-xl p-3.5 text-[14px] text-[#6B6E76] leading-relaxed shadow-sm animate-in fade-in duration-500 whitespace-pre-line">
                      {log.text}
                    </div>
                  );
                }
              })}
              {item.artifact && (
                <div className="mt-4 p-3 border border-[rgba(0,0,0,0.06)] rounded-xl flex items-center justify-between bg-[#FFFFFF] shadow-sm hover:border-[#5B5BD6] hover:shadow-md transition-all cursor-pointer animate-artifact-stagger stagger-1 hover-physics group">
                  <div className="flex items-center">
                    <div className="w-10 h-10 bg-[#E6E7EA] rounded-lg flex items-center justify-center mr-3 text-xl group-hover:scale-105 transition-transform">{item.artifact.icon}</div>
                    <div>
                      <h4 className="font-bold text-[#171719] text-[14px]">{item.artifact.title}</h4>
                      <p className="text-[12px] text-[#6B6E76]">{item.artifact.desc}</p>
                    </div>
                  </div>
                  <button className="px-4 py-1.5 bg-[#E6E7EA] hover:bg-[#F2F3F5] text-[#171719] text-[12px] font-bold rounded-md transition-colors">보기</button>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function TypewriterText({ text }: { text: string }) {
  const [displayedText, setDisplayedText] = useState('');

  useEffect(() => {
    let index = 0;
    const interval = setInterval(() => {
      setDisplayedText(text.slice(0, index + 1));
      index++;
      if (index === text.length) clearInterval(interval);
    }, 30);
    return () => clearInterval(interval);
  }, [text]);

  return (
    <div className="bg-[#FFFFFF] border border-[rgba(0,0,0,0.06)] rounded-xl p-3.5 text-[14px] text-[#171719] leading-relaxed shadow-sm relative animate-in fade-in">
      <span className="whitespace-pre-line">{displayedText}</span>
      <span className="inline-block w-1.5 h-4 bg-[#5B5BD6] ml-1 mb-[-2px] animate-pulse"></span>
    </div>
  );
}

function ComputerView({ activeTab, mobileView, setMobileView, overallProgress, terminalLines, currentFilePath, currentCode, browserUrl, browserState, toolsAvailable, timeline, sessionId, createdFiles }: {
  activeTab: ComputerView;
  mobileView: 'task' | 'computer';
  setMobileView: (v: 'task' | 'computer') => void;
  overallProgress: number;
  terminalLines: string[];
  currentFilePath: string;
  currentCode: string;
  browserUrl: string;
  browserState: BrowserState;
  toolsAvailable: boolean | null;
  timeline: TimelineItem[];
  sessionId: string | null;
  createdFiles: string[];
}) {
  const statusType = overallProgress >= 100 ? 'success' : 'active';
  const activeTask = [...timeline].reverse().find(i => i.type === 'agent');
  const isIdle = timeline.length === 0 || overallProgress === 0;

  const toolIcons: Record<ComputerView, React.ReactNode> = {
    browser: <Globe size={12} className="text-[#6B6E76]" />,
    editor: <Code2 size={12} className="text-[#6B6E76]" />,
    terminal: <Terminal size={12} className="text-[#6B6E76]" />,
    preview: <Globe size={12} className="text-[#6B6E76]" />,
  };

  const toolNames: Record<ComputerView, string> = {
    browser: '브라우저',
    editor: '에디터',
    terminal: '터미널',
    preview: '프리뷰',
  };

  return (
    <div className={`flex flex-col h-full bg-[#F7F7F8] text-[#171719] font-[var(--font-sans)] z-20 ${mobileView === 'computer' ? 'flex-1' : 'hidden md:flex flex-1'} transition-all`}>
      <div className="flex items-center justify-between px-4 py-2.5 bg-[#F7F7F8] border-b border-[rgba(0,0,0,0.06)]">
        <div className="flex items-center">
          <button className="md:hidden p-1 mr-2 text-[#6B6E76] hover:text-[#171719]" onClick={() => setMobileView('task')}>
            <ChevronRight size={20} className="rotate-180" />
          </button>
          <h2 className="font-bold text-[16px] text-[#171719] flex items-center">Manus의 컴퓨터</h2>
        </div>
        <div className="flex items-center space-x-3 text-[#9CA0A8]">
          <Monitor size={18} className="cursor-pointer hover:text-[#171719] transition-colors" />
          <Square size={16} className="cursor-pointer hover:text-[#171719] transition-colors" />
          <X size={20} className="cursor-pointer hover:text-[#171719] transition-colors" />
        </div>
      </div>

      <div className="flex items-center px-4 py-2 text-[12px] text-[#6B6E76] bg-[#F7F7F8] border-b border-[rgba(0,0,0,0.06)] z-10 transition-all">
        <div className="flex items-center bg-[#E6E7EA] rounded px-1.5 py-0.5 mr-2">{toolIcons[activeTab]}</div>
        {isIdle ? (
          <span className="font-medium text-[#9CA0A8]">대기 중 — 작업을 입력하세요</span>
        ) : (
          <span className="font-medium text-[#6B6E76]">Manus 님은 {toolNames[activeTab]}을(를) 사용 중입니다</span>
        )}
      </div>

      <div className="flex-1 overflow-hidden px-4 pb-2 pt-4 relative flex flex-col bg-[#F7F7F8]">
        <div className="flex-1 border border-[rgba(0,0,0,0.06)] rounded-xl flex flex-col overflow-hidden relative bg-[#FFFFFF] shadow-sm">
          <div className="flex-1 overflow-hidden relative flex">
            {isIdle ? (
              <div className="flex flex-col items-center justify-center w-full h-full bg-[#F7F7F8] text-[#9CA0A8]">
                <Compass size={64} className="mb-4 text-[#6B6E76]" />
                <h3 className="text-lg font-bold text-[#9CA0A8]">Ready</h3>
                <p className="text-sm text-[#9CA0A8] mt-2">Type a message and press Send to start</p>
              </div>
            ) : toolsAvailable === false ? (
              <ToolBlockedView />
            ) : (
              <>
                {activeTab === 'browser' && <BrowserView url={browserUrl} state={browserState} />}
                {activeTab === 'editor' && <EditorView filePath={currentFilePath} code={currentCode} />}
                {activeTab === 'terminal' && <TerminalView lines={terminalLines} />}
                {activeTab === 'preview' && <PreviewView sessionId={sessionId} createdFiles={createdFiles} />}
              </>
            )}
          </div>

          <div className="bg-[#FFFFFF] border-t border-[rgba(0,0,0,0.06)] px-4 py-3 flex items-center space-x-4">
            <div className="flex items-center space-x-3 text-[#9CA0A8]">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polygon points="19 20 9 12 19 4 19 20"></polygon><line x1="5" y1="19" x2="5" y2="5"></line></svg>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polygon points="5 4 15 12 5 20 5 4"></polygon><line x1="19" y1="5" x2="19" y2="19"></line></svg>
            </div>
            <div className="flex-1 h-[5px] bg-[#E6E7EA] rounded-full flex items-center relative overflow-hidden">
              {isIdle ? null : (
                <div className={`h-full ${statusType === 'success' ? 'bg-[#1F9D63]' : 'bg-[#5B5BD6]'} rounded-full transition-all duration-500 ease-out`} style={{ width: `${overallProgress}%` }}></div>
              )}
            </div>
            <div className="flex items-center text-[11px] font-bold text-[#9CA0A8] w-16 justify-end">
              {isIdle ? (
                <span className="text-[#9CA0A8]">대기</span>
              ) : (
                <>
                  <div className={`w-2 h-2 ${statusType === 'success' ? 'bg-[#1F9D63]' : 'bg-[#5B5BD6] animate-status-pulse'} rounded-full mr-1.5`}></div> 라이브
                </>
              )}
            </div>
          </div>
        </div>
      </div>

      <div className="px-4 pb-4 pt-2 bg-[#F7F7F8]">
        <div className="border border-[rgba(0,0,0,0.06)] rounded-xl p-3 flex items-center justify-between bg-[#FFFFFF] shadow-sm">
          <div className="flex items-center">
            {isIdle ? (
              <>
                <Check size={18} className="text-[#9CA0A8] mr-2.5" />
                <div>
                  <div className="font-bold text-[13px] text-[#9CA0A8]">Ready</div>
                </div>
              </>
            ) : statusType === 'success' ? (
              <>
                <Check size={18} className="text-[#1F9D63] mr-2.5" />
                <div>
                  <div className="font-bold text-[13px] text-[#171719]">{activeTask?.title || 'Complete'}</div>
                </div>
              </>
            ) : (
              <>
                <Loader2 size={18} className="text-[#5B5BD6] animate-loading-ring mr-2.5" />
                <div>
                  <div className="font-bold text-[13px] text-[#171719]">{activeTask?.title || 'Processing...'}</div>
                </div>
              </>
            )}
          </div>
          <div className="flex items-center text-[12px] font-medium text-[#9CA0A8] font-mono">
            {overallProgress}% <ChevronUp size={16} className="ml-2 text-[#6B6E76]" />
          </div>
        </div>
      </div>
    </div>
  );
}

function ToolBlockedView() {
  return (
    <div className="flex flex-col items-center justify-center w-full h-full bg-[#F7F7F8] text-center px-8">
      <Terminal size={48} className="text-[#6B6E76] mb-4" />
      <h3 className="text-lg font-bold text-[#6B6E76]">도구 실행 불가</h3>
      <p className="text-sm text-[#9CA0A8] mt-2 max-w-md">
        현재 모델 프로필은 브라우저, 터미널, 파일 편집 도구 호출을 사용할 수 없습니다.
      </p>
    </div>
  );
}

function BrowserView({ url, state }: { url: string; state: BrowserState }) {
  const displayUrl = state.url || url || 'about:blank';
  const screenshotSrc = state.screenshot
    ? `data:image/jpeg;base64,${state.screenshot}`
    : '';

  return (
    <div className="flex flex-col w-full h-full bg-[#F7F7F8] text-[#171719]">
      <div className="flex items-center px-3 py-2 bg-[#FFFFFF] border-b border-[rgba(0,0,0,0.06)]">
        <div className="flex items-center space-x-2 text-[#9CA0A8] mr-3">
          <ChevronRight size={14} className="rotate-180" />
          <ChevronRight size={14} />
        </div>
        <div className="flex-1 bg-[#E6E7EA] rounded-md px-3 py-1.5 text-[13px] text-[#9CA0A8] border border-[rgba(0,0,0,0.06)]">
          {displayUrl}
        </div>
      </div>
      <div className="flex-1 min-h-0 bg-white relative">
        {screenshotSrc ? (
          <img
            src={screenshotSrc}
            alt={state.title || displayUrl}
            className="w-full h-full object-contain bg-white animate-thumbnail-crossfade animate-image-scale-in"
          />
        ) : (
          <div className="flex h-full flex-col items-center justify-center p-8 text-center">
            <Globe size={48} className="text-[#171719] mb-4 animate-bounce" />
            <h3 className="text-lg font-bold text-[#6B6E76]">웹 페이지 분석 중</h3>
            <p className="text-sm text-[#9CA0A8] mt-2">{displayUrl !== 'about:blank' ? `Loading: ${displayUrl}` : 'Waiting for browser action...'}</p>
          </div>
        )}
        {state.content && (
          <div className="absolute bottom-4 left-4 right-4 max-h-32 overflow-auto rounded-md border border-[rgba(0,0,0,0.06)] bg-[#FFFFFF]/95 p-3 text-left text-[12px] text-[#6B6E76] shadow-sm">
            {state.content}
          </div>
        )}
      </div>
    </div>
  );
}

function EditorView({ filePath, code }: { filePath: string; code: string }) {
  const displayPath = filePath || 'client / src / pages / Home.tsx';
  const displayCode = code || `// Code will appear here as the agent edits files...
// The agent is working in the microsandbox VM...`;

  return (
    <div className="flex w-full h-full">
      <div className="w-48 bg-[#FFFFFF] border-r border-[rgba(0,0,0,0.06)] h-full flex flex-col font-mono text-[12px] text-[#6B6E76]">
        <div className="p-2 font-bold text-[#6B6E76] tracking-wide border-b border-[rgba(0,0,0,0.06)] text-[11px]">EXPLORER</div>
        <div className="p-2 space-y-1">
          <div className="flex items-center space-x-1 cursor-pointer hover:bg-[#E6E7EA] px-1 py-0.5 rounded">
            <ChevronDown size={14} /> <span>project</span>
          </div>
          <div className="pl-4 space-y-1 border-l border-[rgba(0,0,0,0.06)] ml-2">
            <div className="flex items-center space-x-1 cursor-pointer hover:bg-[#E6E7EA] px-1 py-0.5 rounded">
              <FileCode2 size={13} /> <span className="truncate">{displayPath.split('/').pop()}</span>
            </div>
          </div>
        </div>
      </div>
      <div className="flex-1 bg-[#F7F7F8] p-4 font-mono text-[13px] leading-[1.6] overflow-y-auto text-[#6B6E76]">
        <pre className="text-[#6B6E76] whitespace-pre-wrap">{displayCode}</pre>
      </div>
    </div>
  );
}

function TerminalView({ lines }: { lines: string[] }) {
  return (
    <div className="p-4 font-mono text-[13px] leading-[1.6] text-[#171719] bg-[#1e1e1e] h-full w-full overflow-y-auto">
      <div><span className="text-[#2FB573] font-semibold">manus@vm:~/project$</span> agent running</div>
      {lines.map((line, i) => (
        <div key={i} className="mt-1 text-[#6B6E76] whitespace-pre-wrap">{line}</div>
      ))}
      {lines.length === 0 && <div className="mt-4 text-[#9CA0A8] animate-pulse">Waiting for commands...</div>}
    </div>
  );
}

function PreviewView({ sessionId, createdFiles }: { sessionId: string | null; createdFiles: string[] }) {
  const [files, setFiles] = useState<{name: string; path: string; size?: number}[]>([]);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<string>('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!sessionId) return;
    setLoading(true);
    fetch(`/api/sessions/${sessionId}/files/list?path=/workspace`)
      .then(r => r.ok ? r.json() : {files: []})
      .then(data => setFiles(data.files || []))
      .catch(() => setFiles([]))
      .finally(() => setLoading(false));
  }, [sessionId, createdFiles.length]);

  const loadFile = async (path: string) => {
    setSelectedFile(path);
    setFileContent('');
    if (!sessionId) return;
    try {
      const res = await fetch(`/api/sessions/${sessionId}/files?path=${encodeURIComponent(path)}`);
      if (res.ok) setFileContent(await res.text());
    } catch {}
  };

  return (
    <div className="flex w-full h-full bg-[#F7F7F8] text-[#171719]">
      <div className="w-48 bg-[#FFFFFF] border-r border-[rgba(0,0,0,0.06)] h-full flex flex-col font-mono text-[12px]">
        <div className="p-2 font-bold text-[#6B6E76] tracking-wide border-b border-[rgba(0,0,0,0.06)] text-[11px]">ARTIFACTS</div>
        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          {loading && <div className="text-[#9CA0A8] text-[11px]">Loading...</div>}
          {!loading && files.length === 0 && createdFiles.length === 0 && (
            <div className="text-[#9CA0A8] text-[11px] px-1">No artifacts yet</div>
          )}
          {createdFiles.map(f => {
            const name = f.split('/').pop() || f;
            return (
              <div key={f} onClick={() => loadFile(f)} className={`flex items-center cursor-pointer px-1 py-0.5 rounded hover:bg-[#E6E7EA] ${selectedFile === f ? 'bg-[#E6E7EA] text-[#5B5BD6]' : 'text-[#6B6E76]'}`}>
                <FileCode2 size={13} className="mr-1.5 flex-shrink-0" />
                <span className="truncate">{name}</span>
              </div>
            );
          })}
          {files.filter(f => !createdFiles.includes(f.path)).map(f => (
            <div key={f.path} onClick={() => loadFile(f.path)} className={`flex items-center cursor-pointer px-1 py-0.5 rounded hover:bg-[#E6E7EA] ${selectedFile === f.path ? 'bg-[#E6E7EA] text-[#5B5BD6]' : 'text-[#6B6E76]'}`}>
              <FileCode2 size={13} className="mr-1.5 flex-shrink-0" />
              <span className="truncate">{f.name}</span>
            </div>
          ))}
        </div>
      </div>
      <div className="flex-1 bg-[#F7F7F8] p-4 font-mono text-[13px] overflow-y-auto">
        {selectedFile ? (
          <>
            <div className="text-[#9CA0A8] text-[11px] mb-2 border-b border-[rgba(0,0,0,0.06)] pb-1">{selectedFile}</div>
            <pre className="text-[#6B6E76] whitespace-pre-wrap">{fileContent || 'Loading...'}</pre>
          </>
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-[#9CA0A8]">
            <Globe size={48} className="mb-4 text-[#6B6E76]" />
            <p className="text-sm">Select an artifact to preview</p>
          </div>
        )}
      </div>
    </div>
  );
}
