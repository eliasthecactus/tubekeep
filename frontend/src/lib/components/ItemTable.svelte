<script lang="ts">
	import { Search, Wand2, Sparkles, Image, Download, RotateCcw, EyeOff, Eye, Trash2, X, Music, Loader, ListX, Replace, TriangleAlert, Eraser } from '@lucide/svelte';
	import { SvelteSet } from 'svelte/reactivity';
	import { get, post, qs } from '../api';
	import { ago, bytes, duration } from '../format';
	import { errorToast, itemProgress, on, toast } from '../state.svelte';
	import type { EditGroup, Item } from '../types';
	import StatusBadge from './StatusBadge.svelte';
	import BulkEditor from './BulkEditor.svelte';
	import ItemEditor from './ItemEditor.svelte';
	import Modal from './Modal.svelte';
	import AlternativesModal from './AlternativesModal.svelte';

	let {
		playlistId = null,
		counts = null,
		playlists = [],
		writable = false,
		onchanged
	}: {
		playlistId?: number | null;
		counts?: Record<string, number> | null;
		playlists?: { id: number; title: string | null; url: string }[];
		/** the playlist's account can edit the YouTube playlist */
		writable?: boolean;
		onchanged?: () => void;
	} = $props();

	const FILTERS: { key: string; label: string; status?: string; removed?: string }[] = [
		{ key: 'all', label: 'All' },
		{ key: 'done', label: 'Downloaded', status: 'done' },
		{ key: 'todo', label: 'Not done', status: 'pending,queued,downloading' },
		{ key: 'failed', label: 'Failed', status: 'failed' },
		{ key: 'missing', label: 'Missing', status: 'missing' },
		{ key: 'unavailable', label: 'Unavailable', status: 'unavailable' },
		{ key: 'ignored', label: 'Ignored', status: 'ignored' },
		{ key: 'removed', label: 'Removed from playlist', removed: 'only' }
	];
	function countFor(key: string): number | null {
		if (!counts) return null;
		if (key === 'all') return Object.entries(counts).filter(([k]) => k !== 'removed' && k !== 'bytes').reduce((a, [, v]) => a + v, 0);
		if (key === 'todo') return (counts.pending || 0) + (counts.queued || 0) + (counts.downloading || 0);
		return counts[key] ?? null;
	}

	let filter = $state('all');
	let q = $state('');
	let qDebounced = $state('');
	let sort = $state('position');
	let pl = $state<number | ''>('');
	let items = $state<Item[]>([]);
	let total = $state(0);
	let loading = $state(false);
	const selected = new SvelteSet<number>();
	let bulkOpen = $state(false);
	let bulkMode = $state<'ops' | 'ai'>('ops');
	let editId = $state<number | null>(null);
	let confirmDelete = $state(false);
	let confirmRemove = $state(false);
	let removeFiles = $state(false);
	let removing = $state(false);
	let altOpen = $state(false);
	// selected tracks grouped by playlist, with edit access, fetched when a YouTube action is opened
	let editGroups = $state<EditGroup[]>([]);
	let editBusy = $state(false);
	const editable = $derived(editGroups.filter((g) => g.writable));
	const readOnly = $derived(editGroups.filter((g) => !g.writable));
	const removable = $derived(editable.filter((g) => !g.syncing));
	const notRemovable = $derived([...readOnly, ...editable.filter((g) => g.syncing)]);
	const removeCount = $derived(removable.reduce((n, g) => n + g.item_ids.length, 0));
	const removeAllFollow = $derived(removable.length > 0 && removable.every((g) => g.files_follow));
	const removeSomeFollow = $derived(removable.some((g) => g.files_follow));
	let coverV = $state(Date.now());
	const LIMIT = 200;

	let t: ReturnType<typeof setTimeout>;
	$effect(() => {
		const v = q;
		clearTimeout(t);
		t = setTimeout(() => (qDebounced = v), 250);
	});

	function params(offset = 0) {
		const f = FILTERS.find((x) => x.key === filter)!;
		return {
			status: f.status,
			removed: f.removed, // removed tracks only show under "Removed from playlist"
			q: qDebounced,
			sort,
			offset,
			limit: LIMIT,
			playlist_id: playlistId ?? (pl || undefined)
		};
	}

	async function load(append = false) {
		loading = true;
		try {
			const p = params(append ? items.length : 0);
			const path = playlistId ? `/playlists/${playlistId}/items` : '/items';
			const r = await get<{ total: number; items: Item[] }>(path + qs({ ...p, playlist_id: playlistId ? undefined : p.playlist_id }));
			items = append ? [...items, ...r.items] : r.items;
			total = r.total;
			if (!append) selected.clear();
		} catch (e) {
			errorToast(e);
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		// reload when filters change
		void [filter, qDebounced, sort, pl, playlistId];
		load();
	});

	$effect(() =>
		on('item', (d) => {
			const i = items.findIndex((x) => x.id === d.id);
			if (i >= 0) {
				items[i] = { ...items[i], ...d, file_name: d.file_path ? d.file_path.split('/').pop() : null };
				if (d.status === 'done') coverV = Date.now();
			}
		})
	);

	export function reload() {
		load();
	}

	const allOnPageSelected = $derived(items.length > 0 && items.every((i) => selected.has(i.id)));
	function togglePage() {
		if (allOnPageSelected) items.forEach((i) => selected.delete(i.id));
		else items.forEach((i) => selected.add(i.id));
	}
	async function selectAllMatching() {
		const p = params();
		const ids = await get<number[]>('/items/ids' + qs({ playlist_id: p.playlist_id, status: p.status, q: p.q, removed: p.removed }));
		ids.forEach((id) => selected.add(id));
	}

	let lastClicked: number | null = null;
	function toggle(e: MouseEvent, id: number) {
		if (e.shiftKey && lastClicked != null) {
			const a = items.findIndex((x) => x.id === lastClicked);
			const b = items.findIndex((x) => x.id === id);
			const [s, t2] = a < b ? [a, b] : [b, a];
			const on = !selected.has(id);
			for (let k = s; k <= t2; k++) on ? selected.add(items[k].id) : selected.delete(items[k].id);
		} else if (selected.has(id)) selected.delete(id);
		else selected.add(id);
		lastClicked = id;
	}

	const ids = $derived([...selected]);

	async function action(name: string) {
		try {
			await post('/items/action', { item_ids: ids, action: name });
			toast('Done', 'ok');
			load();
			onchanged?.();
		} catch (e) {
			errorToast(e);
		}
	}
	// tracks no longer in their YouTube playlist can be forgotten (the button shows when such a track is selected)
	const removedSelected = $derived(filter === 'removed' || items.some((i) => i.removed_at && selected.has(i.id)));
	let confirmForget = $state(false);
	let forgetFiles = $state(false);
	async function forget() {
		confirmForget = false;
		try {
			const r = await post('/items/action', { item_ids: ids, action: forgetFiles ? 'forget_delete' : 'forget' });
			toast(`Forgot ${r.forgotten} track${r.forgotten === 1 ? '' : 's'}` + (r.deleted_files ? `, deleted ${r.deleted_files} file${r.deleted_files === 1 ? '' : 's'}` : ''), 'ok');
			selected.clear();
			load();
			onchanged?.();
		} catch (e) {
			errorToast(e);
		}
	}
	async function download(force: boolean) {
		try {
			const r = await post('/items/download', { item_ids: ids, force });
			toast(`Queued ${ids.length} track${ids.length === 1 ? '' : 's'} in ${r.jobs.length} job${r.jobs.length === 1 ? '' : 's'}`);
		} catch (e) {
			errorToast(e);
		}
	}
	let coverBusy = $state(false);
	async function covers() {
		coverBusy = true;
		try {
			const r = await post('/items/cover-from-thumbnail', { item_ids: ids, square: true });
			const bad = r.results.filter((x: any) => !x.ok);
			toast(`Covers updated: ${r.results.length - bad.length}${bad.length ? `, failed: ${bad.length}` : ''}`, bad.length ? 'error' : 'ok');
			coverV = Date.now();
		} catch (e) {
			errorToast(e);
		} finally {
			coverBusy = false;
		}
	}
	async function removeFromYouTube() {
		removing = true;
		try {
			const r = await post('/items/remove', { item_ids: removable.flatMap((g) => g.item_ids), delete_files: removeFiles });
			const parts = [`Removed ${r.removed} from YouTube`];
			if (r.playlists > 1) parts[0] += ` in ${r.playlists} playlists`;
			if (r.deleted_files) parts.push(`${r.deleted_files} files deleted`);
			const skipped = r.skipped.reduce((n: number, x: any) => n + x.tracks, 0);
			if (skipped) parts.push(`${skipped} skipped (${r.skipped.map((x: any) => `${x.title}: ${x.reason}`).join('; ')})`);
			toast(parts.join(', '), skipped ? 'error' : 'ok');
			confirmRemove = false;
			load();
			onchanged?.();
		} catch (e) {
			errorToast(e);
		} finally {
			removing = false;
		}
	}
	/** groups the selection by playlist; false when nothing of it can be changed on YouTube */
	async function loadEditGroups(): Promise<boolean> {
		editBusy = true;
		try {
			editGroups = await post<EditGroup[]>('/items/edit-targets', { item_ids: ids });
		} catch (e) {
			errorToast(e);
			return false;
		} finally {
			editBusy = false;
		}
		if (editable.length) return true;
		const why = [...new Set(readOnly.map((g) => g.reason).filter(Boolean))];
		toast(`None of the selected tracks can be changed on YouTube${why.length ? `: ${why.join('; ')}` : ''}`, 'error');
		return false;
	}
	async function openRemove() {
		if (!(await loadEditGroups())) return;
		if (!removable.length) {
			toast('Wait until the sync of these playlists is finished', 'error');
			return;
		}
		removeFiles = removeAllFollow;
		confirmRemove = true;
	}
	async function openAlternatives() {
		if (await loadEditGroups()) altOpen = true;
	}
	function openBulk(mode: 'ops' | 'ai') {
		bulkMode = mode;
		bulkOpen = true;
	}

	function display(it: Item) {
		const m = it.meta;
		return {
			title: m?.title || it.title || it.video_id,
			sub: [m?.artist || it.channel, m?.album].filter(Boolean).join(' · ')
		};
	}
</script>

<div class="toolbar">
	<div class="chips">
		{#each FILTERS as f (f.key)}
			{@const c = countFor(f.key === 'removed' ? 'removed' : f.key)}
			<button class="chip" class:active={filter === f.key} onclick={() => (filter = f.key)}>
				{f.label}
				{#if c !== null}<span class="cnt">{c}</span>{/if}
			</button>
		{/each}
	</div>
	<div class="row wrap">
		<div class="search grow">
			<Search size={15} />
			<input type="search" bind:value={q} placeholder="Search title, artist, album, file, video id…" />
		</div>
		{#if !playlistId && playlists.length}
			<select bind:value={pl} style="width: 200px">
				<option value="">All playlists</option>
				{#each playlists as p (p.id)}<option value={p.id}>{p.title ?? p.url}</option>{/each}
			</select>
		{/if}
		<select bind:value={sort} style="width: 160px">
			<option value="position">Playlist order</option>
			<option value="title">Title</option>
			<option value="added">Recently added</option>
			<option value="downloaded">Recently downloaded</option>
			<option value="status">Status</option>
		</select>
	</div>
</div>

{#if selected.size}
	<div class="bulk card">
		<b>{selected.size} selected</b>
		{#if selected.size < total && allOnPageSelected}
			<button class="ghost sm" onclick={selectAllMatching}>Select all {total}</button>
		{/if}
		<span class="spacer"></span>
		<button class="sm" onclick={() => openBulk('ops')}><Wand2 size={14} /> Edit tags</button>
		<button class="sm" onclick={() => openBulk('ai')}><Sparkles size={14} /> AI tags</button>
		<button class="sm" onclick={covers} disabled={coverBusy}>{#if coverBusy}<Loader size={14} class="spin" />{:else}<Image size={14} />{/if} Covers</button>
		<button class="sm" onclick={() => download(false)} title="Download tracks that are not done"><Download size={14} /> Download</button>
		<button class="sm" onclick={() => download(true)} title="Download again and overwrite"><RotateCcw size={14} /> Re-download</button>
		<button class="sm" onclick={() => action('reset')} title="Reset failed/unavailable/missing to pending">Retry later</button>
		<button class="sm" onclick={() => action('ignore')}><EyeOff size={14} /> Ignore</button>
		<button class="sm" onclick={() => action('unignore')}><Eye size={14} /> Unignore</button>
		<button class="sm danger" onclick={() => (confirmDelete = true)}><Trash2 size={14} /> Delete files</button>
		{#if removedSelected}
			<button class="sm" onclick={() => { forgetFiles = false; confirmForget = true; }} title="Remove tracks that are no longer in their YouTube playlist from Tubekeep"><Eraser size={14} /> Forget</button>
		{/if}
		{#if !playlistId || writable}
			<button class="sm" onclick={openAlternatives} disabled={editBusy} title="Search YouTube Music for a working version"><Replace size={14} /> Find alternatives</button>
			<button class="sm danger" onclick={openRemove} disabled={editBusy} title="Remove from the playlist on YouTube"><ListX size={14} /> Remove from YouTube</button>
		{/if}
		<button class="ghost icon sm" onclick={() => selected.clear()} title="Clear selection"><X size={15} /></button>
	</div>
{/if}

<div class="card table-wrap">
	<table class="tbl">
		<thead>
			<tr>
				<th style="width: 36px"><input type="checkbox" checked={allOnPageSelected} onchange={togglePage} /></th>
				<th style="width: 52px"></th>
				<th>Track</th>
				{#if !playlistId}<th class="hide-sm">Playlist</th>{/if}
				<th>Status</th>
				<th class="hide-sm" style="text-align: right">Length</th>
				<th class="hide-sm" style="text-align: right">Size</th>
			</tr>
		</thead>
		<tbody>
			{#each items as it (it.id)}
				{@const d = display(it)}
				{@const prog = itemProgress(it.id)}
				<tr class:sel={selected.has(it.id)} class:removed={!!it.removed_at}>
					<td><input type="checkbox" checked={selected.has(it.id)} onclick={(e) => toggle(e, it.id)} /></td>
					<td>
						<button class="thumb" onclick={() => (editId = it.id)} aria-label="Edit">
							{#if it.status === 'done' && it.meta?.has_cover}
								<img loading="lazy" src="/api/items/{it.id}/cover?v={coverV}" alt="" />
							{:else if it.thumbnail}
								<img loading="lazy" src={it.thumbnail} alt="" class="yt" />
							{:else}
								<Music size={16} />
							{/if}
						</button>
					</td>
					<td class="track">
						<button class="link" onclick={() => (editId = it.id)}>
							<span class="t ellipsis" title={d.title}>{d.title}</span>
							<span class="s ellipsis faint small">{d.sub}</span>
						</button>
					</td>
					{#if !playlistId}<td class="hide-sm small muted ellipsis" style="max-width: 180px">{it.playlist_title}</td>{/if}
					<td class="st">
						{#if prog && (it.status === 'downloading' || it.status === 'queued')}
							<div class="prog">
								<div class="pbar"><span style:width="{prog.pct || 0}%"></span></div>
								<span class="small faint">{prog.stage === 'downloading' ? `${Math.round(prog.pct)}%` : prog.stage}</span>
							</div>
						{:else}
							<span title={it.error ?? ''}><StatusBadge status={it.removed_at && it.status !== 'done' ? 'removed' : it.status} /></span>
							{#if it.removed_at && it.status === 'done'}<span class="pill warn" title="No longer in the YouTube playlist">removed</span>{/if}
							{#if it.status === 'done' && it.downloaded_at}<span class="faint small hide-sm"> {ago(it.downloaded_at)}</span>{/if}
							{#if it.error && it.status !== 'done'}<div class="small errtext ellipsis" title={it.error}>{it.error.split('\n').pop()}</div>{/if}
						{/if}
					</td>
					<td class="hide-sm small muted" style="text-align: right">{duration(it.duration)}</td>
					<td class="hide-sm small muted" style="text-align: right">{it.file_size ? bytes(it.file_size) : ''}</td>
				</tr>
			{:else}
				<tr><td colspan="7" class="empty">{loading ? 'Loading…' : 'No tracks match.'}</td></tr>
			{/each}
		</tbody>
	</table>
	{#if items.length < total}
		<div class="more">
			<button onclick={() => load(true)} disabled={loading}>Load more ({total - items.length} left)</button>
		</div>
	{/if}
</div>

<BulkEditor bind:open={bulkOpen} itemIds={ids} mode={bulkMode} onapplied={() => { load(); coverV = Date.now(); }} />
<ItemEditor bind:itemId={editId} onchanged={() => load()} />

<AlternativesModal bind:open={altOpen} groups={editable} skipped={readOnly} {items} onchanged={() => { load(); onchanged?.(); }} />

<Modal bind:open={confirmRemove} title="Remove {removeCount} track{removeCount === 1 ? '' : 's'} from YouTube?" width="480px">
	<div class="warnbox">
		<TriangleAlert size={16} />
		<span>This removes the track{removeCount === 1 ? '' : 's'} from the <b>real playlist{removable.length === 1 ? '' : 's'} on YouTube</b>, not only from Tubekeep. Tubekeep can't undo it.</span>
	</div>
	{#if removable.length > 1 || notRemovable.length}
		<ul class="groups small">
			{#each removable as g (g.playlist_id)}
				<li><span class="ellipsis">{g.title}</span><span class="muted">{g.item_ids.length} track{g.item_ids.length === 1 ? '' : 's'}</span></li>
			{/each}
			{#each notRemovable as g (g.playlist_id)}
				<li class="skip" title={g.syncing ? 'Wait until the sync of this playlist is finished' : (g.reason ?? 'Read-only')}>
					<span class="ellipsis">{g.title}</span>
					<span class="muted">{g.item_ids.length} skipped, {g.syncing && g.writable ? 'syncing' : 'read-only'}</span>
				</li>
			{/each}
		</ul>
	{/if}
	{#if removeAllFollow}
		<p class="small muted" style="margin-top: 12px">{removable.length === 1 ? 'This playlist deletes' : 'These playlists delete'} files of removed tracks, so downloaded files are deleted too.</p>
	{:else}
		<label class="check" style="margin-top: 12px"><input type="checkbox" bind:checked={removeFiles} /> Also delete the downloaded files</label>
		{#if removeSomeFollow}<p class="small muted">Playlists that delete files of removed tracks always delete them.</p>{/if}
	{/if}
	{#snippet footer()}
		<button onclick={() => (confirmRemove = false)}>Cancel</button>
		<button class="danger solid" onclick={removeFromYouTube} disabled={removing}>{#if removing}<Loader size={14} class="spin" />{/if} Remove from YouTube</button>
	{/snippet}
</Modal>

<Modal bind:open={confirmDelete} title="Delete {selected.size} files?" width="440px">
	<p>The audio files are deleted from disk and the tracks are marked as ignored, so the next sync does not download them again.</p>
	{#snippet footer()}
		<button onclick={() => (confirmDelete = false)}>Cancel</button>
		<button class="danger solid" onclick={() => { confirmDelete = false; action('delete_file'); }}>Delete files</button>
	{/snippet}
</Modal>

<Modal bind:open={confirmForget} title="Forget removed tracks?" width="460px">
	<p>Selected tracks that are no longer in their YouTube playlist disappear from Tubekeep. Tracks still in a playlist are left alone. If one is added to the playlist again, it shows up as a new track.</p>
	<label class="check small"><input type="checkbox" bind:checked={forgetFiles} /> Also delete their files (files another playlist still uses are kept)</label>
	{#snippet footer()}
		<button onclick={() => (confirmForget = false)}>Cancel</button>
		<button class={forgetFiles ? 'danger solid' : 'primary'} onclick={forget}>{forgetFiles ? 'Forget and delete files' : 'Forget'}</button>
	{/snippet}
</Modal>

<style>
	.warnbox {
		display: flex;
		gap: 8px;
		align-items: flex-start;
		background: var(--warn-soft);
		border-radius: 8px;
		padding: 10px 12px;
	}
	.warnbox :global(svg) {
		color: var(--warn);
		flex-shrink: 0;
		margin-top: 2px;
	}
	.groups {
		list-style: none;
		margin: 12px 0 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 4px;
		max-height: 200px;
		overflow-y: auto;
	}
	.groups li {
		display: flex;
		justify-content: space-between;
		gap: 12px;
		min-width: 0;
	}
	.groups li.skip {
		color: var(--text-3);
	}
	.toolbar {
		display: flex;
		flex-direction: column;
		gap: 10px;
		margin-bottom: 12px;
	}
	.chips {
		display: flex;
		gap: 6px;
		flex-wrap: wrap;
	}
	.chip {
		height: 30px;
		border-radius: 999px;
		padding: 0 12px;
		font-size: 13px;
		color: var(--text-2);
	}
	.chip.active {
		background: var(--text);
		color: var(--bg);
		border-color: var(--text);
	}
	.cnt {
		font-size: 11.5px;
		opacity: 0.7;
		font-variant-numeric: tabular-nums;
	}
	.search {
		position: relative;
		display: flex;
		align-items: center;
		min-width: 200px;
	}
	.search :global(svg) {
		position: absolute;
		left: 10px;
		color: var(--text-3);
	}
	.search input {
		padding-left: 32px;
	}
	.bulk {
		position: sticky;
		top: 8px;
		z-index: 5;
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 6px;
		padding: 8px 10px;
		margin-bottom: 10px;
		border-color: color-mix(in srgb, var(--accent) 40%, var(--line));
	}
	tr.sel td {
		background: color-mix(in srgb, var(--accent-soft) 60%, transparent);
	}
	tr.removed .t {
		text-decoration: line-through;
		text-decoration-color: var(--text-3);
	}
	.thumb {
		width: 40px;
		height: 40px;
		padding: 0;
		border-radius: 6px;
		overflow: hidden;
		border: none;
		background: var(--bg-3);
		color: var(--text-3);
	}
	.thumb img {
		width: 100%;
		height: 100%;
		object-fit: cover;
	}
	.thumb img.yt {
		opacity: 0.55;
	}
	.track {
		max-width: 0;
		width: 50%;
	}
	.link {
		all: unset;
		cursor: pointer;
		display: flex;
		flex-direction: column;
		min-width: 0;
		max-width: 100%;
	}
	.link:hover .t {
		color: var(--accent);
	}
	.t {
		font-weight: 560;
	}
	.st {
		min-width: 130px;
		max-width: 260px;
	}
	.errtext {
		color: var(--err);
		max-width: 260px;
	}
	.prog {
		display: flex;
		flex-direction: column;
		gap: 3px;
	}
	.pbar {
		height: 5px;
		border-radius: 9px;
		background: var(--bg-3);
		overflow: hidden;
		width: 120px;
	}
	.pbar span {
		display: block;
		height: 100%;
		background: var(--info);
		transition: width 0.4s;
	}
	.more {
		padding: 12px;
		display: flex;
		justify-content: center;
		border-top: 1px solid var(--line);
	}
	@media (max-width: 720px) {
		.hide-sm {
			display: none;
		}
	}
</style>
