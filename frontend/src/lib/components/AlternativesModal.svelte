<script lang="ts">
	import { Search, ExternalLink, Plus, Replace, Loader, Music, Check, TriangleAlert } from '@lucide/svelte';
	import { untrack } from 'svelte';
	import { post } from '../api';
	import { duration } from '../format';
	import { errorToast, toast } from '../state.svelte';
	import type { Item } from '../types';
	import Modal from './Modal.svelte';

	type Cand = {
		video_id: string;
		title: string;
		artists: string[];
		album: string | null;
		duration: number | null;
		thumbnail: string | null;
		kind: string;
		url: string;
		score: number | null;
		in_playlist: boolean;
	};
	type Row = {
		id: number;
		item: Item | undefined;
		query: string;
		source: { title: string; artists: string[]; duration: number | null; known: boolean } | null;
		cands: Cand[];
		loading: boolean;
		error: string;
		done: string;
		busy: string;
	};

	let {
		open = $bindable(false),
		playlistId,
		itemIds,
		items = [],
		filesFollowPlaylist = false,
		onchanged
	}: {
		open?: boolean;
		playlistId: number;
		itemIds: number[];
		items?: Item[];
		filesFollowPlaylist?: boolean;
		onchanged?: () => void;
	} = $props();

	const MAX = 20;
	let rows = $state<Row[]>([]);
	let deleteOld = $state(false);

	$effect(() => {
		if (open) untrack(start); // only when it opens, not when the parent reloads its items
	});
	function start() {
		const ids = itemIds.slice(0, MAX);
		rows = ids.map((id) => ({ id, item: items.find((i) => i.id === id), query: '', source: null, cands: [], loading: true, error: '', done: '', busy: '' }));
		deleteOld = false;
		// two searches at a time, YouTube doesn't like bursts
		let next = 0;
		const worker = async () => {
			while (open && next < rows.length) await search(rows[next++]);
		};
		worker();
		worker();
	}

	async function search(row: Row, query?: string) {
		row.loading = true;
		row.error = '';
		try {
			const r = await post(`/playlists/${playlistId}/items/${row.id}/alternatives`, { query: query ?? null });
			row.source = r.source;
			row.query = r.query;
			row.cands = r.candidates;
			if (!r.query) row.error = "Tubekeep doesn't know what this video was. Type a search below.";
		} catch (e: any) {
			row.error = e.message;
		} finally {
			row.loading = false;
		}
	}

	async function apply(row: Row, c: Cand, replace: boolean) {
		row.busy = c.video_id + (replace ? ':r' : ':a');
		try {
			const r = await post(`/playlists/${playlistId}/items/${row.id}/alternative`, { candidate: c, replace, delete_file: deleteOld });
			row.done = `${replace ? 'Replaced with' : 'Added'} "${c.title}"${r.deleted_file ? ', old file deleted' : ''}`;
			toast(replace ? 'Replaced on YouTube' : 'Added to the YouTube playlist', 'ok');
			onchanged?.();
		} catch (e) {
			errorToast(e);
		} finally {
			row.busy = '';
		}
	}

	function label(row: Row) {
		const m = row.item?.meta;
		const title = row.source?.title || m?.title || row.item?.title || row.item?.video_id || '';
		const artists = row.source?.artists?.join(', ') || m?.artist || row.item?.channel || '';
		return { title, artists };
	}
	function scoreKind(s: number | null) {
		if (s == null) return '';
		return s >= 0.85 ? 'ok' : s >= 0.6 ? 'warn' : '';
	}
	function diff(a: number | null, b: number | null | undefined) {
		if (!a || !b) return '';
		const d = Math.round(a - b);
		return Math.abs(d) <= 2 ? '' : d > 0 ? ` (+${d}s)` : ` (${d}s)`;
	}
</script>

<Modal bind:open title="Find alternatives" width="760px">
	<div class="warnbox small">
		<TriangleAlert size={15} />
		<span><b>Add</b> and <b>Replace</b> change the real playlist on YouTube, not only Tubekeep. Replace puts the new video where the old one was and removes the old one from YouTube.</span>
	</div>
	{#if filesFollowPlaylist}
		<p class="small muted">This playlist deletes files of removed tracks, so replacing also deletes the old file.</p>
	{:else}
		<label class="check small"><input type="checkbox" bind:checked={deleteOld} /> When replacing, also delete the old local file</label>
	{/if}
	{#if itemIds.length > MAX}<p class="small muted">Showing the first {MAX} of {itemIds.length} selected tracks.</p>{/if}

	<div class="rows">
		{#each rows as row (row.id)}
			{@const l = label(row)}
			<section class="card">
				<div class="src">
					<div class="thumb">
						{#if row.item?.thumbnail}<img src={row.item.thumbnail} alt="" loading="lazy" />{:else}<Music size={16} />{/if}
					</div>
					<div class="grow col" style="min-width: 0">
						<b class="ellipsis">{l.title || 'Unknown video'}</b>
						<span class="small faint ellipsis">{[l.artists, duration(row.source?.duration ?? row.item?.duration)].filter(Boolean).join(' · ')}</span>
					</div>
					{#if row.done}<span class="pill ok"><Check size={12} /> {row.done}</span>{/if}
				</div>
				{#if !row.done}
					<form class="q" onsubmit={(e) => { e.preventDefault(); search(row, row.query); }}>
						<input bind:value={row.query} placeholder="Search YouTube Music…" />
						<button type="submit" class="sm" disabled={row.loading || !row.query.trim()}>
							{#if row.loading}<Loader size={14} class="spin" />{:else}<Search size={14} />{/if} Search
						</button>
					</form>
					{#if row.error}<div class="small errtext">{row.error}</div>{/if}
					{#if !row.loading && !row.error && !row.cands.length}<div class="small muted">Nothing found. Try another search.</div>{/if}
					<ul>
						{#each row.cands as c, i (c.video_id)}
							<li>
								<div class="thumb sm">{#if c.thumbnail}<img src={c.thumbnail} alt="" loading="lazy" />{:else}<Music size={14} />{/if}</div>
								<div class="grow col" style="min-width: 0">
									<span class="ellipsis">{c.title}</span>
									<span class="small faint ellipsis">{[c.artists.join(', '), c.album, c.kind === 'video' ? 'video' : ''].filter(Boolean).join(' · ')}</span>
								</div>
								<span class="small muted nowrap hide-sm">{duration(c.duration)}{diff(c.duration, row.source?.duration)}</span>
								{#if c.score != null}<span class="pill {scoreKind(c.score)}" title="How well it matches">{Math.round(c.score * 100)}%</span>{/if}
								{#if c.in_playlist}<span class="pill info" title="This video is already in the playlist">in playlist</span>{/if}
								<a class="ghost icon sm btnlike" href={c.url} target="_blank" rel="noreferrer" title="Open on YouTube Music"><ExternalLink size={14} /></a>
								<button class="sm" disabled={!!row.busy} onclick={() => apply(row, c, false)} title="Add to the playlist, keep the old entry">
									{#if row.busy === c.video_id + ':a'}<Loader size={14} class="spin" />{:else}<Plus size={14} />{/if} Add
								</button>
								<button class="sm" class:primary={i === 0} disabled={!!row.busy} onclick={() => apply(row, c, true)} title="Put it in place of the old entry and remove the old one">
									{#if row.busy === c.video_id + ':r'}<Loader size={14} class="spin" />{:else}<Replace size={14} />{/if} Replace
								</button>
							</li>
						{/each}
					</ul>
				{/if}
			</section>
		{/each}
	</div>

	{#snippet footer()}
		<button onclick={() => (open = false)}>Close</button>
	{/snippet}
</Modal>

<style>
	.warnbox {
		display: flex;
		gap: 8px;
		align-items: flex-start;
		background: var(--warn-soft);
		color: var(--text);
		border-radius: 8px;
		padding: 8px 10px;
		margin-bottom: 10px;
	}
	.warnbox :global(svg) {
		color: var(--warn);
		flex-shrink: 0;
		margin-top: 2px;
	}
	.rows {
		display: flex;
		flex-direction: column;
		gap: 10px;
		margin-top: 12px;
	}
	section {
		padding: 10px 12px;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.src,
	li {
		display: flex;
		align-items: center;
		gap: 10px;
		min-width: 0;
	}
	.q {
		display: flex;
		gap: 6px;
	}
	ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	li {
		padding-top: 6px;
		border-top: 1px solid var(--line);
	}
	.thumb {
		width: 40px;
		height: 40px;
		border-radius: 6px;
		overflow: hidden;
		background: var(--bg-3);
		color: var(--text-3);
		display: grid;
		place-items: center;
		flex-shrink: 0;
	}
	.thumb.sm {
		width: 34px;
		height: 34px;
	}
	.thumb img {
		width: 100%;
		height: 100%;
		object-fit: cover;
	}
	.btnlike {
		display: inline-grid;
		place-items: center;
		width: 28px;
		height: 28px;
		border-radius: 6px;
		color: var(--text-2);
	}
	.btnlike:hover {
		background: var(--bg-hover);
	}
	.nowrap {
		white-space: nowrap;
	}
	.errtext {
		color: var(--err);
	}
	@media (max-width: 720px) {
		.hide-sm {
			display: none;
		}
		li {
			flex-wrap: wrap;
		}
	}
</style>
