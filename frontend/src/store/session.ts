/**
 * Session store. Owner: person 1.
 *
 * Stub: holds the event log for the current stream and nothing else. Person 1
 * grows this into the real state (messages, citations, artifacts, model
 * status, tool timeline) as the panels land.
 */
import { create } from 'zustand';
import type { Event } from '../types/events';

interface SessionState {
  sessionId: string | null;
  events: Event[];
  push: (ev: Event) => void;
  reset: () => void;
}

export const useSession = create<SessionState>((set) => ({
  sessionId: null,
  events: [],
  push: (ev) =>
    set((s) => ({
      events: [...s.events, ev],
      sessionId: ev.type === 'session.start' ? ev.session_id : s.sessionId,
    })),
  reset: () => set({ sessionId: null, events: [] }),
}));
