<script lang="ts">
	import { ExternalLink, Image, Upload, Download, Sparkles, Save, RotateCcw, Loader } from '@lucide/svelte';
	import Modal from './Modal.svelte';
	import StatusBadge from './StatusBadge.svelte';
	import BulkEditor from './BulkEditor.svelte';
	import { api, get, post } from '../api';
	import { FIELD_LABELS, bytes, datetime, duration } from '../format';
	import { errorToast, toast, on } from '../state.svelte';

	let { itemId = $bindable(null), onchanged }: { itemId?: number | null; onchanged?: () => void } = $props();

	let open = $state(false);
	let item = $state<any>(null);
	let form = $state<Record<string, string>>({});
	let original = $state<Record<string, string>>({});
	let busy = $state(false);
	let coverV = $state(0);
	let showDesc = $state(false);
	let aiOpen = $state(false);
	let fileInput: HTMLInputElement | undefined = $state();

	const dirty = $derived(Object.keys(form).filter((k) => (form[k] ?? '') !== (original[k] ?? '')));

	async function load(id: number) {
		try {
			item = await get(`/items/${id}`);
			const m = item.meta ?? {};
			const f: Record<string, string> = {};
			for (const k of item.fields as string[]) f[k] = m[k] ?? '';
			form = f;
			original = { ...f };
			coverV++;
		} catch (e) {
			errorToast(e);
			open = false;
		}
	}

	$effect(() => {
		if (itemId != null) {
			open = true;
			load(itemId);
		}
	});
	$effect(() =>
		on('item', (d) => {
			if (item && d.id === item.id && d.status !== item.status) load(item.id);
		})
	);

	function close() {
		itemId = null;
		item = null;
	}

	async function save() {
		if (!dirty.length) return;
		busy = true;
		try {
			const fields = Object.fromEntries(dirty.map((k) => [k, form[k]]));
			const r = await post('/metadata/apply', { changes: [{ item_id: item.id, fields }] });
			if (!r.results[0].ok) throw new Error(r.results[0].error);
			toast('Tags saved', 'ok');
			await load(item.id);
			onchanged?.();
		} catch (e) {
			errorToast(e);
		} finally {
			busy = false;
		}
	}

	async function coverFromThumb() {
		busy = true;
		try {
			const r = await post('/items/cover-from-thumbnail', { item_ids: [item.id], square: true });
			if (!r.results[0].ok) throw new Error(r.results[0].error);
			coverV++;
			toast('Cover replaced', 'ok');
		} catch (e) {
			errorToast(e);
		} finally {
			busy = false;
		}
	}

	async function uploadCover(e: Event) {
		const f = (e.target as HTMLInputElement).files?.[0];
		if (!f) return;
		const fd = new FormData();
		fd.append('file', f);
		busy = true;
		try {
			await api('PUT', `/items/${item.id}/cover?square=true`, fd);
			coverV++;
			toast('Cover replaced', 'ok');
		} catch (err) {
			errorToast(err);
		} finally {
			busy = false;
			if (fileInput) fileInput.value = '';
		}
	}

	async function redownload() {
		try {
			await post('/items/download', { item_ids: [item.id], force: item.status === 'done' });
			toast('Download queued');
		} catch (e) {
			errorToast(e);
		}
	}
</script>

<Modal bind:open title={item?.title ?? 'Track'} width="620px" side onclose={close}>
	{#if item}
		<div class="top">
			<div class="cover">
				{#if item.file_exists || item.thumbnail}
					<img src="/api/items/{item.id}/cover?v={coverV}" alt="" />
				{/if}
			</div>
			<div class="col grow" style="gap: 6px; min-width: 0">
				<div class="row wrap">
					<StatusBadge status={item.removed_at ? 'removed' : item.status} />
					{#if item.meta?.format}<span class="pill">{item.meta.format}</span>{/if}
					{#if item.file_size}<span class="pill">{bytes(item.file_size)}</span>{/if}
					{#if item.duration}<span class="pill">{duration(item.duration)}</span>{/if}
				</div>
				<div class="small muted ellipsis">{item.channel} · {item.playlist_title}</div>
				<a class="small row" style="gap: 4px; color: var(--accent)" href="https://www.youtube.com/watch?v={item.video_id}" target="_blank" rel="noreferrer">
					<ExternalLink size={13} /> Open on YouTube
				</a>
				{#if item.file_exists}
					<!-- svelte-ignore a11y_media_has_caption -->
					<audio controls preload="none" src="/api/items/{item.id}/audio"></audio>
				{/if}
			</div>
		</div>

		{#if item.error}
			<div class="err small">
				<b>{item.error_kind ?? 'error'}</b> after {item.attempts} attempt{item.attempts === 1 ? '' : 's'}:
				<pre>{item.error}</pre>
			</div>
		{/if}

		<div class="row wrap" style="margin: 12px 0">
			<button class="sm" onclick={redownload}><Download size={14} /> {item.status === 'done' ? 'Re-download' : 'Download now'}</button>
			{#if item.file_exists}
				<button class="sm" onclick={coverFromThumb} disabled={busy}><Image size={14} /> Cover from YouTube</button>
				<button class="sm" onclick={() => fileInput?.click()} disabled={busy}><Upload size={14} /> Upload cover</button>
				<input bind:this={fileInput} type="file" accept="image/*" hidden onchange={uploadCover} />
				<button class="sm" onclick={() => (aiOpen = true)}><Sparkles size={14} /> AI</button>
			{/if}
		</div>

		{#if item.file_exists}
			<div class="tags">
				{#each ['title', 'artist', 'album', 'album_artist'] as k (k)}
					<label class="field">
						<span>{FIELD_LABELS[k]} {#if item.meta_override?.[k] !== undefined}<span class="pill accent" title="Edited by you, kept on re-download">edited</span>{/if}</span>
						<input bind:value={form[k]} class:dirty={form[k] !== original[k]} />
					</label>
				{/each}
				<div class="grid4">
					{#each ['track', 'track_total', 'disc', 'year'] as k (k)}
						<label class="field">{FIELD_LABELS[k]}<input bind:value={form[k]} class:dirty={form[k] !== original[k]} /></label>
					{/each}
				</div>
				<label class="field">{FIELD_LABELS.genre}<input bind:value={form.genre} class:dirty={form.genre !== original.genre} /></label>
				<label class="field">{FIELD_LABELS.comment}<input bind:value={form.comment} class:dirty={form.comment !== original.comment} /></label>
				<label class="field">{FIELD_LABELS.lyrics}<textarea rows="4" bind:value={form.lyrics} class:dirty={form.lyrics !== original.lyrics}></textarea></label>
			</div>
		{:else}
			<p class="muted">The file is not on disk yet. Tags can be edited after the download.</p>
		{/if}

		<div class="meta small">
			<div><span class="faint">File</span> <code>{item.file_path ?? '—'}</code></div>
			<div><span class="faint">Video id</span> <code>{item.video_id}</code> · position {item.position}</div>
			{#if item.downloaded_at}<div><span class="faint">Downloaded</span> {datetime(item.downloaded_at)}</div>{/if}
			{#if item.yt_artist || item.yt_album}<div><span class="faint">YouTube Music</span> {item.yt_artist ?? ''} {item.yt_album ? `· ${item.yt_album}` : ''}</div>{/if}
			{#if item.description}
				<button class="ghost sm" style="padding: 0" onclick={() => (showDesc = !showDesc)}>{showDesc ? 'Hide' : 'Show'} YouTube description</button>
				{#if showDesc}<pre class="desc">{item.description}</pre>{/if}
			{/if}
		</div>
	{:else}
		<div class="empty"><Loader class="spin" /></div>
	{/if}

	{#snippet footer()}
		{#if item?.file_exists}
			<span class="faint small grow">{dirty.length ? `${dirty.length} unsaved change${dirty.length === 1 ? '' : 's'}` : 'Edited tags are kept when the file is downloaded again.'}</span>
			<button onclick={() => (form = { ...original })} disabled={!dirty.length}><RotateCcw size={15} /> Revert</button>
			<button class="primary" onclick={save} disabled={!dirty.length || busy}><Save size={15} /> Save tags</button>
		{/if}
	{/snippet}
</Modal>

{#if item}
	<BulkEditor bind:open={aiOpen} itemIds={[item.id]} mode="ai" onapplied={() => { load(item.id); onchanged?.(); }} />
{/if}

<style>
	.top {
		display: flex;
		gap: 14px;
		align-items: flex-start;
	}
	.cover {
		width: 120px;
		height: 120px;
		border-radius: 10px;
		overflow: hidden;
		background: var(--bg-3);
		flex-shrink: 0;
	}
	.cover img {
		width: 100%;
		height: 100%;
		object-fit: cover;
	}
	audio {
		width: 100%;
		height: 34px;
		margin-top: 4px;
	}
	.tags {
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.grid4 {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		gap: 10px;
	}
	.dirty {
		border-color: var(--warn) !important;
	}
	.err {
		margin-top: 12px;
		background: var(--err-soft);
		color: var(--err);
		padding: 8px 10px;
		border-radius: var(--radius-sm);
	}
	pre {
		white-space: pre-wrap;
		word-break: break-word;
		margin: 4px 0 0;
		font-family: var(--mono);
		font-size: 12px;
	}
	.meta {
		margin-top: 16px;
		display: flex;
		flex-direction: column;
		gap: 4px;
		color: var(--text-2);
		word-break: break-all;
	}
	.desc {
		max-height: 220px;
		overflow: auto;
		background: var(--bg-3);
		padding: 8px;
		border-radius: 6px;
		color: var(--text-2);
	}
	@media (max-width: 520px) {
		.grid4 {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
		.cover {
			width: 84px;
			height: 84px;
		}
	}
</style>
