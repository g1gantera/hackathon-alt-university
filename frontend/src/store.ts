import {translate, displayText} from './i18n/core.ts';
import { create } from 'zustand';
import type { Snapshot } from './types';
import { acceptSnapshot } from './realtime';
export const useDispatch = create<{
    snapshot: Snapshot | null;
    selected: string;
    connected: boolean;
    lastUpdate: number;
    setSnapshot: (s: Snapshot, fromSocket?: boolean) => void;
    select: (s: string) => void;
    setConnected: (s: boolean) => void;
}>(set => ({ snapshot: null, selected: 'T01', connected: false, lastUpdate: 0, setSnapshot: (s, fromSocket = false) => set(current => acceptSnapshot(current.snapshot, s, fromSocket) ? { snapshot: s, lastUpdate: fromSocket ? Date.now() : current.lastUpdate } : {}), select: s => set({ selected: s }), setConnected: s => set({ connected: s }) }));
export async function api<T = unknown>(path: string, method = 'GET', body?: unknown): Promise<T> {
    const result = await fetch('/api' + path, { method, cache: 'no-store', signal: AbortSignal.timeout(10000), headers: body ? { 'Content-Type': 'application/json' } : undefined, body: body ? JSON.stringify(body) : undefined });
    if (!result.ok) {
        const value = await result.json().catch(() => ({ detail: result.statusText }));
        throw new Error(displayText(typeof value.detail === 'string' ? value.detail : value.detail?.message || JSON.stringify(value.detail)));
    }
    return result.json();
}
export const clock = (seconds: number) => { const s = Math.max(0, Math.floor(seconds)); return `${String(8 + Math.floor(s / 3600)).padStart(2, '0')}:${String(Math.floor(s % 3600 / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`; };
export const minutes = (seconds: number) => translate("{0} мин", (seconds / 60).toFixed(1));
