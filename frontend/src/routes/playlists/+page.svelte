<script lang="ts">
	import { Plus, RefreshCw, FileSearch, UserRound, Lock, Loader, Folder, Clock, Download, HardDrive } from '@lucide/svelte';
	import { get, post } from '#lib/api.ts';
	import { ago, bytes, scheduleLabel, until } from '#lib/format.ts';
	import { errorToast, jobForPlaylist, on, toast } from '#lib/state.svelte.ts';
	import type { Playlist } from '#lib/types.ts';
	import Modal from '#lib/components/Modal.svelte';
	import PlaylistForm from '#lib/components/PlaylistForm.svelte';
	import Segments from '#lib/components/Segments.svelte';
	import StatusBadge from '#lib/components/StatusBadge.svelte';

	let lists = $state<Playlist[]>([]);
	let loaded = $state(false);
	let accounts = $state<any[]>([]);
	let proxies = $state<any[]>([]);
	let addOpen = $state(false);
	let importOpen = $state(false);
	let busy = $state(false);
	let form = $state<Record<string, any>>({});
	let importAcc = $state<number | null>(null);
	let importList = $state<any[] | null>(null);
	let importSel = $state<string[]>([]);
	let importBusy = $state(false);

	async function load() {
		try {
			lists = await get('/playlists');
			loaded = true;
		} catch (e) {
			errorToast(e);
		}
	}
	load();
	$effect(() => on('playlist', () => load()));
	$effect(() => on('job', (j) => ['done', 'failed', 'cancelled'].includes(j.status) && load()));

	async function openAdd() {
		const [acc, px, s] = await Promise.all([get('/accounts'), get('/proxies'), get('/settings')]);
		accounts = acc;
		proxies = px;
		form = {
			urlsText: '',
			folder: '',
			account_id: s.default_account_id ?? null,
			schedule: s.default_schedule,
			proxy_mode: 'default',
			proxy_id: null,
			on_removed: 'keep',
			redownload_missing: true,
			enabled: true,
			sync_now: true,
			options: {}
		};
		addOpen = true;
	}

	async function add() {
		const urls = (form.urlsText as string).split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith('#'));
		if (!urls.length) return toast('Paste at least one URL', 'error');
		busy = true;
		try {
			const { urlsText, ...rest } = form;
			const r = await post('/playlists', { ...rest, urls, folder: urls.length === 1 && form.folder ? form.folder : null });
			toast(`Added ${r.created.length} playlist${r.created.length === 1 ? '' : 's'}${r.skipped.length ? `, ${r.skipped.length} already existed` : ''}`, 'ok');
			addOpen = false;
			load();
		} catch (e) {
			errorToast(e);
		} finally {
			busy = false;
		}
	}

	async function openImport() {
		accounts = await get('/accounts');
		importAcc = accounts[0]?.id ?? null;
		importList = null;
		importSel = [];
		importOpen = true;
	}
	async function fetchAccountLists() {
		if (!importAcc) return;
		importBusy = true;
		importList = null;
		try {
			importList = await get(`/accounts/${importAcc}/playlists`);
		} catch (e) {
			errorToast(e);
		} finally {
			importBusy = false;
		}
	}
	async function importSelected() {
		busy = true;
		try {
			const r = await post('/playlists', { urls: importSel, account_id: importAcc, sync_now: true });
			toast(`Added ${r.created.length} playlists`, 'ok');
			importOpen = false;
			load();
		} catch (e) {
			errorToast(e);
		} finally {
			busy = false;
		}
	}

	async function sync(p: Playlist) {
		try {
			await post(`/playlists/${p.id}/sync`);
		} catch (e) {
			errorToast(e);
		}
	}
	async function syncAll() {
		try {
			const r = await post('/playlists/sync-all');
			toast(`Started ${r.jobs.length} syncs`);
		} catch (e) {
			errorToast(e);
		}
	}
	async function verifyAll() {
		try {
			await post('/verify-all');
			toast('Checking all files on disk');
		} catch (e) {
			errorToast(e);
		}
	}

	function total(c: Record<string, number>) {
		return ['done', 'pending', 'queued', 'downloading', 'failed', 'missing', 'unavailable'].reduce((a, k) => a + (c[k] || 0), 0);
	}
</script>

<div class="page-head">
	<h1>Playlists</h1>
	<span class="pill">{lists.length}</span>
	<span class="spacer"></span>
	<button onclick={verifyAll} title="Check that every downloaded file still exists"><FileSearch size={15} /> Verify files</button>
	<button onclick={syncAll}><RefreshCw size={15} /> Sync all</button>
	<button onclick={openImport}><UserRound size={15} /> From account</button>
	<button class="primary" onclick={openAdd}><Plus size={15} /> Add playlist</button>
</div>

<div class="grid">
	{#each lists as p (p.id)}
		{@const job = jobForPlaylist(p.id)}
		{@const c = p.counts}
		{@const t = total(c)}
		<a class="pl card" href="/playlists/{p.id}">
			<div class="row">
				<h2 class="ellipsis grow" title={p.title ?? p.url}>{p.title ?? 'Not synced yet'}</h2>
				{#if p.storage_offline_at}<span class="pill warn" title="The download folder is missing (drive not mounted?). Syncs are paused."><HardDrive size={11} /> folder missing</span>{/if}
				{#if p.account_id}<span class="pill {p.account_status === 'expired' ? 'err' : ''}" title="Uses account {p.account_label}"><Lock size={11} /> {p.account_label}</span>{/if}
			</div>
			<div class="small faint ellipsis mono">{p.url.replace(/^https?:\/\/(www\.)?/, '')}</div>
			<div class="prog">
				<Segments counts={c} />
				<div class="row small muted">
					<span><b>{c.done}</b> / {t} downloaded</span>
					{#if c.failed}<span class="err">· {c.failed} failed</span>{/if}
					{#if c.missing}<span class="warn">· {c.missing} missing</span>{/if}
					<span class="spacer"></span>
					<span>{bytes(c.bytes)}</span>
				</div>
			</div>
			<div class="foot small">
				{#if job}
					<span class="pill info"><Loader size={11} class="spin" /> {job.status === 'queued' ? 'waiting' : job.phase}</span>
					{#if job.stats?.to_download}<span class="muted">{job.stats.downloaded ?? 0}/{job.stats.to_download}</span>{/if}
				{:else}
					{#if p.last_sync_status}<StatusBadge status={p.last_sync_status} title={p.last_error ?? ''} />{/if}
					<span class="muted">synced {ago(p.last_sync_at)}</span>
				{/if}
				<span class="spacer"></span>
				<span class="faint row" style="gap: 4px" title={scheduleLabel(p.schedule)}><Clock size={12} /> {p.enabled && p.next_run_at ? until(p.next_run_at) : p.schedule ? 'paused' : 'manual'}</span>
				<button class="sm icon" onclick={(e) => { e.preventDefault(); sync(p); }} disabled={!!job} title="Sync now"><RefreshCw size={14} /></button>
			</div>
			{#if p.folder}<div class="small faint ellipsis row" style="gap: 5px"><Folder size={12} /> <span class="mono ellipsis">{p.folder}</span></div>{/if}
		</a>
	{:else}
		{#if loaded}
			<div class="card empty" style="grid-column: 1/-1">
				<Download size={28} />
				<p style="margin: 10px 0">No playlists yet. Add YouTube playlist URLs or import them from an account.</p>
				<button class="primary" onclick={openAdd}><Plus size={15} /> Add playlist</button>
			</div>
		{/if}
	{/each}
</div>

<Modal bind:open={addOpen} title="Add playlists" width="640px">
	<PlaylistForm bind:v={form} {accounts} {proxies} />
	{#snippet footer()}
		<button onclick={() => (addOpen = false)}>Cancel</button>
		<button class="primary" onclick={add} disabled={busy}>Add</button>
	{/snippet}
</Modal>

<Modal bind:open={importOpen} title="Import playlists from an account" width="560px">
	{#if !accounts.length}
		<p>Add a YouTube account first (cookies), then its playlists show up here. <a href="/accounts" style="color: var(--accent)">Go to accounts</a></p>
	{:else}
		<div class="row">
			<select bind:value={importAcc}>
				{#each accounts as a (a.id)}<option value={a.id}>{a.label}</option>{/each}
			</select>
			<button onclick={fetchAccountLists} disabled={importBusy}>{#if importBusy}<Loader size={15} class="spin" />{/if} Load playlists</button>
		</div>
		{#if importList}
			<div class="ilist">
				{#each importList as p (p.url)}
					<label class="check irow" class:dim={p.added}>
						<input type="checkbox" disabled={p.added} checked={importSel.includes(p.url)} onchange={(e) => (importSel = e.currentTarget.checked ? [...importSel, p.url] : importSel.filter((x) => x !== p.url))} />
						<span class="grow ellipsis">{p.title}</span>
						{#if p.count}<span class="faint small">{p.count}</span>{/if}
						{#if p.added}<span class="pill">added</span>{/if}
					</label>
				{/each}
			</div>
		{/if}
	{/if}
	{#snippet footer()}
		<button onclick={() => (importOpen = false)}>Cancel</button>
		<button class="primary" onclick={importSelected} disabled={!importSel.length || busy}>Add {importSel.length || ''} selected</button>
	{/snippet}
</Modal>

<style>
	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(330px, 1fr));
		gap: 14px;
	}
	.pl {
		display: flex;
		flex-direction: column;
		gap: 10px;
		padding: 16px;
		transition: border-color 0.12s, transform 0.12s;
	}
	.pl:hover {
		border-color: var(--line-2);
	}
	.prog {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.foot {
		display: flex;
		align-items: center;
		gap: 8px;
	}
	.err {
		color: var(--err);
	}
	.warn {
		color: var(--warn);
	}
	.empty :global(svg) {
		color: var(--text-3);
	}
	.ilist {
		margin-top: 12px;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		max-height: 380px;
		overflow: auto;
	}
	.irow {
		display: flex;
		padding: 8px 10px;
		border-bottom: 1px solid var(--line);
	}
	.irow.dim {
		opacity: 0.55;
	}
</style>
