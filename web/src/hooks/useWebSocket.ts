import { useEffect, useRef, useState, useCallback } from 'react';
import type { CreateSessionResponse, ServerEvent } from '../types';

const API_BASE = import.meta.env.VITE_API_BASE || '';

export function useWebSocket(sessionId: string | null) {
  const [connected, setConnected] = useState(false);
  const [events, setEvents] = useState<ServerEvent[]>([]);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttemptRef = useRef(0);

  const RECONNECT_BASE_DELAY = 3000;
  const RECONNECT_MAX_DELAY = 30000;
  const RECONNECT_MAX_ATTEMPTS = 10;

  const connect = useCallback(() => {
    if (!sessionId) return;

    if (reconnectAttemptRef.current >= RECONNECT_MAX_ATTEMPTS) {
      setConnected(false);
      return;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}${API_BASE}/ws/${sessionId}`;

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      reconnectAttemptRef.current = 0;
    };
    ws.onclose = () => {
      setConnected(false);
      const attempt = reconnectAttemptRef.current;
      const delay = Math.min(
        RECONNECT_BASE_DELAY * Math.pow(2, attempt),
        RECONNECT_MAX_DELAY
      );
      reconnectAttemptRef.current += 1;
      reconnectTimerRef.current = setTimeout(connect, delay);
    };
    ws.onerror = () => setConnected(false);

    ws.onmessage = (e) => {
      try {
        const event: ServerEvent = JSON.parse(e.data);
        setEvents((prev) => [...prev, event]);
      } catch {
      }
    };
  }, [sessionId]);

  useEffect(() => {
    if (sessionId) {
      connect();
    }
    return () => {
      wsRef.current?.close();
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
    };
  }, [sessionId, connect]);

  return { connected, events, setEvents };
}

export async function createSession(sessionId?: string): Promise<CreateSessionResponse> {
  const res = await fetch(`${API_BASE}/api/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId }),
  });
  if (!res.ok) throw new Error(`Failed to create session: ${res.statusText}`);
  return res.json();
}

export async function sendHttpMessage(sessionId: string, message: string) {
  const res = await fetch(`${API_BASE}/api/sessions/${sessionId}/message`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
  });
  if (!res.ok) throw new Error(`Failed to send message: ${res.statusText}`);
  return res.json();
}

export interface PlanResponseBody {
  action: 'approve' | 'reject' | 'edit';
  revision?: string;
}

export async function sendPlanResponse(sessionId: string, body: PlanResponseBody) {
  const res = await fetch(`${API_BASE}/api/sessions/${sessionId}/message`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message_type: 'plan_response', ...body }),
  });
  if (!res.ok) throw new Error(`Failed to send plan response: ${res.statusText}`);
  return res.json();
}

export async function deleteSession(sessionId: string) {
  const res = await fetch(`${API_BASE}/api/sessions/${sessionId}`, {
    method: 'DELETE',
  });
  return res.ok;
}

export async function stopSession(sessionId: string) {
  const res = await fetch(`${API_BASE}/api/sessions/${sessionId}/stop`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error(`Failed to stop session: ${res.statusText}`);
  return res.json();
}

export interface PersistedSession {
  session_id: string;
  status: string;
  last_message?: string;
  created_at?: string;
}

export async function fetchSessions(): Promise<PersistedSession[]> {
  const res = await fetch(`${API_BASE}/api/sessions`);
  if (!res.ok) return [];
  const data = await res.json();
  return data.sessions || [];
}
