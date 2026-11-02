<script lang="ts">
	import { Search, ExternalLink, Plus, Replace, Loader, Music, Check, TriangleAlert } from '@lucide/svelte';
	import { untrack } from 'svelte';
	import { post } from '../api';
	import { duration } from '../format';
	import { errorToast, toast } from '../state.svelte';
	import type { EditGroup, Item } from '../types';
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
		available: boolean;
	};
	type Link = { kind: 'song' | 'video' | 'album' | 'playlist' | 'artist'; title: string | null; count: number };
	type Row = {
		id: number;
		group: EditGroup;
		item: Item | undefined;
		query: string;
		source: { title: string; artists: string[]; duration: number | null; known: boolean } | null;
		cands: Cand[];
		link: Link | null;
		loading: boolean;
		error: string;
		done: string;
		busy: string;
	};

	let {
		open = $bindable(false),
		groups,
		skipped = [],
		items = [],
		onchanged
	}: {
		open?: boolean;
		/** the selected tracks of playlists that can be edited */
		groups: EditGroup[];
		/** selected tracks of read-only playlists, only mentioned */
		skipped?: EditGroup[];
		items?: Item[];
		onchanged?: () => void;
	} = $props();

	const total = $derived(groups.reduce((n, g) => n + g.item_ids.length, 0));
	const skippedTracks = $derived(skipped.reduce((n, g) => n + g.item_ids.length, 0));
	const allFollow = $derived(groups.length > 0 && groups.every((g) => g.files_follow));
	const someFollow = $derived(groups.some((g) => g.files_follow));

	const MAX = 20;
	let rows = $state<Row[]>([]);
	let deleteOld = $state(false);

	$effect(() => {
		if (open) untrack(start); // only when it opens, not when the parent reloads its items
	});
	function start() {
		const all = groups.flatMap((group) => group.item_ids.map((id) => ({ id, group })));
		rows = all.slice(0, MAX).map(({ id, group }) => ({ id, group, item: items.find((i) => i.id === id), query: '', source: null, cands: [], link: null, loading: true, error: '', done: '', busy: '' }));
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
			const r = await post(`/playlists/${row.group.playlist_id}/items/${row.id}/alternatives`, { query: query ?? null });
			row.source = r.source;
			row.query = r.query;
			row.cands = r.candidates;
			row.link = r.link ?? null;
			if (!r.query) row.error = "Tubekeep doesn't know what this video was. Type a search below.";
		} catch (e: any) {
			row.error = e.message;
			row.cands = [];
			row.link = null;
		} finally {
			row.loading = false;
		}
	}

	async function apply(row: Row, c: Cand, replace: boolean) {
		row.busy = c.video_id + (replace ? ':r' : ':a');
		try {
			const r = await post(`/playlists/${row.group.playlist_id}/items/${row.id}/alternative`, { candidate: c, replace, delete_file: deleteOld });
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
	const LINK_KIND = { song: 'Song', video: 'Video', album: 'Album', playlist: 'Playlist', artist: 'Songs of' };
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
	{#if allFollow}
		<p class="small muted">{groups.length === 1 ? 'This playlist deletes' : 'These playlists delete'} files of removed tracks, so replacing also deletes the old file.</p>
	{:else}
		<label class="check small"><input type="checkbox" bind:checked={deleteOld} /> When replacing, also delete the old local file</label>
		{#if someFollow}<p class="small muted">Playlists that delete files of removed tracks always delete the old file.</p>{/if}
	{/if}
	{#if total > MAX}<p class="small muted">Showing the first {MAX} of {total} tracks.</p>{/if}
	{#if skippedTracks}
		<p class="small muted">
			Left out {skippedTracks} track{skippedTracks === 1 ? '' : 's'} of playlists that can't be changed on YouTube:
			{skipped.map((g) => `${g.title} (${g.reason ?? 'read-only'})`).join(', ')}.
		</p>
	{/if}

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
						<span class="small faint ellipsis">{[l.artists, duration(row.source?.duration ?? row.item?.duration), groups.length > 1 ? row.group.title : ''].filter(Boolean).join(' · ')}</span>
					</div>
					{#if row.done}<span class="pill ok"><Check size={12} /> {row.done}</span>{/if}
				</div>
				{#if !row.done}
					<form class="q" onsubmit={(e) => { e.preventDefault(); search(row, row.query); }}>
						<input bind:value={row.query} placeholder="Search YouTube Music or paste a link…" />
						<button type="submit" class="sm" disabled={row.loading || !row.query.trim()}>
							{#if row.loading}<Loader size={14} class="spin" />{:else}<Search size={14} />{/if} Search
						</button>
					</form>
					{#if row.error}<div class="small errtext">{row.error}</div>{/if}
					{#if row.link}
						<div class="small muted ellipsis">{LINK_KIND[row.link.kind]} <b>{row.link.title || 'from the link'}</b>{row.link.kind === 'song' || row.link.kind === 'video' ? '' : ` · ${row.link.count} track${row.link.count === 1 ? '' : 's'}`}</div>
					{/if}
					{#if !row.loading && !row.error && !row.cands.length}<div class="small muted">{row.link ? 'Nothing in this link to pick.' : 'Nothing found. Try another search or paste a YouTube link.'}</div>{/if}
					<ul class:long={row.cands.length > 8}>
						{#each row.cands as c, i (c.video_id)}
							<li>
								<div class="thumb sm">{#if c.thumbnail}<img src={c.thumbnail} alt="" loading="lazy" />{:else}<Music size={14} />{/if}</div>
								<div class="grow col" style="min-width: 0">
									<span class="ellipsis">{c.title}</span>
									<span class="small faint ellipsis">{[c.artists.join(', '), c.album, c.kind === 'video' ? 'video' : ''].filter(Boolean).join(' · ')}</span>
								</div>
								<span class="small muted nowrap hide-sm">{duration(c.duration)}{diff(c.duration, row.source?.duration)}</span>
								{#if c.score != null}<span class="pill {scoreKind(c.score)}" title="How well it matches">{Math.round(c.score * 100)}%</span>{/if}
								{#if !c.available}<span class="pill err" title="YouTube says this video can't be played">unavailable</span>{/if}
								{#if c.in_playlist}<span class="pill info" title="This video is already in the playlist">in playlist</span>{/if}
								<a class="ghost icon sm btnlike" href={c.url} target="_blank" rel="noreferrer" title="Open on YouTube Music"><ExternalLink size={14} /></a>
								<button class="sm" disabled={!!row.busy || !c.available} onclick={() => apply(row, c, false)} title="Add to the playlist, keep the old entry">
									{#if row.busy === c.video_id + ':a'}<Loader size={14} class="spin" />{:else}<Plus size={14} />{/if} Add
								</button>
								<button class="sm" class:primary={i === 0 && c.available} disabled={!!row.busy || !c.available} onclick={() => apply(row, c, true)} title="Put it in place of the old entry and remove the old one">
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
	ul.long {
		max-height: 420px;
		overflow-y: auto;
		padding-right: 4px;
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
