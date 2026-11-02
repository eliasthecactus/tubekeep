<script lang="ts">
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { RefreshCw, FileSearch, Pencil, Trash2, ExternalLink, Folder, Lock, Clock, ArrowLeft, PenLine, Eye } from '@lucide/svelte';
	import { del, get, patch, post } from '#lib/api.ts';
	import { ago, bytes, scheduleLabel, until } from '#lib/format.ts';
	import { errorToast, jobForPlaylist, on, toast } from '#lib/state.svelte.ts';
	import type { Playlist } from '#lib/types.ts';
	import ItemTable from '#lib/components/ItemTable.svelte';
	import FolderExtras from '#lib/components/FolderExtras.svelte';
	import JobCard from '#lib/components/JobCard.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import PlaylistForm from '#lib/components/PlaylistForm.svelte';
	import Segments from '#lib/components/Segments.svelte';
	import StatusBadge from '#lib/components/StatusBadge.svelte';
	import StorageAlert from '#lib/components/StorageAlert.svelte';

	const id = $derived(Number(page.params.id));
	let p = $state<Playlist | null>(null);
	let editOpen = $state(false);
	let delOpen = $state(false);
	let delFiles = $state(false);
	let form = $state<Record<string, any>>({});
	let accounts = $state<any[]>([]);
	let proxies = $state<any[]>([]);
	let busy = $state(false);
	let access = $state<{ writable: boolean; reason: string | null } | null>(null);
	let extras = $state<FolderExtras>();

	async function load() {
		try {
			p = await get(`/playlists/${id}`);
		} catch (e) {
			errorToast(e);
		}
	}
	async function loadAccess(refresh = false) {
		try {
			access = await get(`/playlists/${id}/access${refresh ? '?refresh=true' : ''}`);
		} catch {
			access = null;
		}
	}
	$effect(() => {
		void id;
		load();
		access = null;
		loadAccess();
	});
	let reloadTimer: ReturnType<typeof setTimeout>;
	function soon() {
		clearTimeout(reloadTimer);
		reloadTimer = setTimeout(load, 600);
	}
	$effect(() => on('item', (d) => d.playlist_id === id && soon()));
	$effect(() => on('playlist', (d) => d.id === id && load()));

	const job = $derived(jobForPlaylist(id));

	async function sync() {
		try {
			await post(`/playlists/${id}/sync`);
		} catch (e) {
			errorToast(e);
		}
	}
	async function verify() {
		try {
			await post(`/playlists/${id}/verify`);
			toast('Checking files on disk');
		} catch (e) {
			errorToast(e);
		}
	}
	async function openEdit() {
		[accounts, proxies] = await Promise.all([get('/accounts'), get('/proxies')]);
		form = { ...$state.snapshot(p), options: { ...p!.options }, move_files: true };
		editOpen = true;
	}
	async function save() {
		busy = true;
		try {
			const f = form;
			p = await patch(`/playlists/${id}`, {
				url: f.url,
				title: f.title,
				folder: f.folder || null,
				account_id: f.account_id,
				proxy_mode: f.proxy_mode,
				proxy_id: f.proxy_id,
				schedule: f.schedule,
				enabled: f.enabled,
				on_removed: f.on_removed,
				redownload_missing: f.redownload_missing,
				options: f.options,
				move_files: f.move_files && f.folder !== p?.folder
			});
			editOpen = false;
			toast('Saved', 'ok');
			loadAccess(true);
		} catch (e) {
			errorToast(e);
		} finally {
			busy = false;
		}
	}
	async function remove() {
		try {
			const r = await del(`/playlists/${id}?delete_files=${delFiles}`);
			toast(delFiles ? `Playlist removed, ${r.deleted_files} files deleted` : 'Playlist removed, files kept', 'ok');
			goto('/playlists');
		} catch (e) {
			errorToast(e);
		}
	}
</script>

<a href="/playlists" class="back small muted"><ArrowLeft size={14} /> Playlists</a>

{#if p}
	<div class="page-head">
		<div class="col grow" style="gap: 4px; min-width: 0">
			<h1 class="ellipsis">{p.title ?? 'Not synced yet'}</h1>
			<div class="row wrap small muted">
				<a href={p.url} target="_blank" rel="noreferrer" class="row" style="gap: 4px"><ExternalLink size={13} /> YouTube</a>
				{#if p.folder}<span class="row mono" style="gap: 4px"><Folder size={13} /> {p.folder}</span>{/if}
				{#if p.account_label}<span class="row" style="gap: 4px"><Lock size={13} /> {p.account_label}</span>{/if}
				{#if access}
					<button class="link row" style="gap: 4px" onclick={() => loadAccess(true)} title={access.writable ? 'The linked account can edit this playlist on YouTube. Click to check again.' : `${access.reason ?? 'Read-only'}. Click to check again.`}>
						{#if access.writable}<PenLine size={13} /> Editable on YouTube{:else}<Eye size={13} /> Read-only{/if}
					</button>
				{/if}
				<span class="row" style="gap: 4px"><Clock size={13} /> {scheduleLabel(p.schedule)}{p.enabled && p.next_run_at ? `, next ${until(p.next_run_at)}` : p.schedule ? ' (paused)' : ''}</span>
			</div>
		</div>
		<button onclick={verify} title="Check files on disk, link moved files"><FileSearch size={15} /> Verify files</button>
		<button onclick={openEdit}><Pencil size={15} /> Edit</button>
		<button class="danger" onclick={() => (delOpen = true)}><Trash2 size={15} /></button>
		<button class="primary" onclick={sync} disabled={!!job}><RefreshCw size={15} class={job ? 'spin' : ''} /> {job ? 'Syncing…' : 'Sync now'}</button>
	</div>

	{#if p.storage_offline_at}<StorageAlert playlists={[p]} onchanged={load} />{/if}

	<div class="summary card pad">
		<Segments counts={p.counts} height={8} />
		<div class="row wrap small" style="gap: 16px; margin-top: 10px">
			<span><b>{p.counts.done}</b> downloaded</span>
			<span><b>{(p.counts.pending || 0) + (p.counts.queued || 0) + (p.counts.downloading || 0)}</b> to do</span>
			<span class:err={p.counts.failed}><b>{p.counts.failed}</b> failed</span>
			<span class:warn={p.counts.missing}><b>{p.counts.missing}</b> missing on disk</span>
			<span><b>{p.counts.unavailable}</b> unavailable</span>
			<span><b>{p.counts.removed}</b> removed from playlist</span>
			<span class="spacer"></span>
			<span class="muted">{bytes(p.bytes)}</span>
			<span class="muted">last sync {ago(p.last_sync_at)}</span>
			{#if p.last_sync_status}<StatusBadge status={p.last_sync_status} />{/if}
		</div>
		{#if p.last_error && !p.storage_offline_at}<div class="lasterr small">{p.last_error}</div>{/if}
	</div>

	{#if job}<JobCard {job} />{/if}

	<ItemTable playlistId={id} counts={p.counts} writable={!!access?.writable} onchanged={() => { load(); extras?.reload(); }} />
	<FolderExtras playlistId={id} bind:this={extras} />

	<Modal bind:open={editOpen} title="Edit playlist" width="640px">
		<PlaylistForm bind:v={form} mode="edit" {accounts} {proxies} originalFolder={p.folder ?? ''} />
		{#snippet footer()}
			<button onclick={() => (editOpen = false)}>Cancel</button>
			<button class="primary" onclick={save} disabled={busy}>Save</button>
		{/snippet}
	</Modal>

	<Modal bind:open={delOpen} title="Remove playlist?" width="460px">
		<p>The playlist and its track list are removed from Tubekeep.</p>
		<label class="check" style="margin-top: 12px"><input type="checkbox" bind:checked={delFiles} /> Also delete the {p.counts.done} downloaded files</label>
		{#snippet footer()}
			<button onclick={() => (delOpen = false)}>Cancel</button>
			<button class="danger solid" onclick={remove}>Remove{delFiles ? ' and delete files' : ''}</button>
		{/snippet}
	</Modal>
{/if}

<style>
	.link {
		all: unset;
		cursor: pointer;
	}
	.link:hover {
		color: var(--text);
	}
	.back {
		display: inline-flex;
		align-items: center;
		gap: 4px;
		margin-bottom: 10px;
	}
	.summary {
		margin-bottom: 16px;
	}
	.err {
		color: var(--err);
	}
	.warn {
		color: var(--warn);
	}
	.lasterr {
		margin-top: 10px;
		color: var(--err);
		background: var(--err-soft);
		padding: 8px 10px;
		border-radius: 8px;
		white-space: pre-wrap;
	}
</style>
