import { get } from './api';

export type LiveItem = {
	item_id: number;
	title: string;
	pct: number;
	speed: number | null;
	eta: number | null;
	stage: string;
};
export type LiveJob = {
	id: number;
	type: string;
	title: string;
	playlist_id: number | null;
	transfer_id?: number | null;
	phase: string;
	status: string;
	stats: Record<string, number>;
	items: LiveItem[];
};

export const session = $state({
	ready: false,
	setupNeeded: false,
	user: null as null | { id: number; username: string },
	version: ''
});

export const live = $state({
	connected: false,
	jobs: {} as Record<number, LiveJob>
});

export const toasts = $state({ list: [] as { id: number; text: string; kind: string }[] });
let toastId = 0;
export function toast(text: string, kind: 'info' | 'ok' | 'error' = 'info') {
	const id = ++toastId;
	toasts.list.push({ id, text, kind });
	setTimeout(() => (toasts.list = toasts.list.filter((t) => t.id !== id)), kind === 'error' ? 7000 : 3500);
}
export function errorToast(e: unknown) {
	toast(e instanceof Error ? e.message : String(e), 'error');
}

type Handler = (data: any) => void;
const handlers: Record<string, Set<Handler>> = {};

/** Subscribe to a server event; returns an unsubscribe function (use in $effect). */
export function on(event: string, fn: Handler): () => void {
	(handlers[event] ??= new Set()).add(fn);
	return () => handlers[event]?.delete(fn);
}
function emit(event: string, data: any) {
	handlers[event]?.forEach((fn) => {
		try {
			fn(data);
		} catch (e) {
			console.error(e);
		}
	});
}

const FINAL = new Set(['done', 'failed', 'cancelled']);
let es: EventSource | null = null;

export async function loadLive() {
	try {
		const jobs = await get<LiveJob[]>('/jobs/live');
		const map: Record<number, LiveJob> = {};
		for (const j of jobs) map[j.id] = j;
		live.jobs = map;
	} catch {
		/* ignore */
	}
}

export function connectEvents() {
	if (es) return;
	es = new EventSource('/api/events');
	es.onopen = () => {
		live.connected = true;
		loadLive();
	};
	es.onerror = () => {
		live.connected = false;
	};
	es.addEventListener('job', (e) => {
		const d = JSON.parse((e as MessageEvent).data);
		if (FINAL.has(d.status) || FINAL.has(d.phase)) {
			delete live.jobs[d.id];
		} else {
			const prev = live.jobs[d.id];
			live.jobs[d.id] = { ...prev, ...d, items: d.items ?? prev?.items ?? [] };
		}
		emit('job', d);
	});
	es.addEventListener('progress', (e) => {
		const d = JSON.parse((e as MessageEvent).data);
		const j = live.jobs[d.job_id];
		if (j) {
			const i = j.items.findIndex((x) => x.item_id === d.item_id);
			if (i >= 0) j.items[i] = d;
			else j.items.push(d);
		}
		emit('progress', d);
	});
	for (const ev of ['item', 'log', 'playlist', 'system', 'account', 'proxy', 'transfer']) {
		es.addEventListener(ev, (e) => emit(ev, JSON.parse((e as MessageEvent).data)));
	}
}

export function disconnectEvents() {
	es?.close();
	es = null;
	live.connected = false;
}

/** Live progress of an item across all running jobs. */
export function itemProgress(itemId: number): LiveItem | undefined {
	for (const j of Object.values(live.jobs)) {
		const it = j.items?.find((x) => x.item_id === itemId);
		if (it) return it;
	}
	return undefined;
}

export function jobForPlaylist(pid: number): LiveJob | undefined {
	return Object.values(live.jobs).find((j) => j.playlist_id === pid);
}
