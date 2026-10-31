<script lang="ts">
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { ArrowLeft, ArrowRight, ArrowLeftRight, RefreshCw, Trash2, ExternalLink, Search, Check, SkipForward, Undo2, Loader } from '@lucide/svelte';
	import { del, get, patch, post, qs } from '#lib/api.ts';
	import { ago, duration, until } from '#lib/format.ts';
	import { errorToast, live, on, toast } from '#lib/state.svelte.ts';
	import JobCard from '#lib/components/JobCard.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import ProviderBadge from '#lib/components/ProviderBadge.svelte';
	import SchedulePicker from '#lib/components/SchedulePicker.svelte';
	import StatusBadge from '#lib/components/StatusBadge.svelte';

	const id = $derived(Number(page.params.id));
	let t = $state<any>(null);
	let side = $state<'a' | 'b'>('a');
	let filter = $state('');
	let q = $state('');
	let items = $state<any[]>([]);
	let total = $state(0);
	let open = $state<number | null>(null);
	let searchQ = $state('');
	let searchRes = $state<any[] | null>(null);
	let searching = $state(false);
	let linkText = $state('');
	let delOpen = $state(false);

	const FILTERS = [
		{ key: '', label: 'All' },
		{ key: 'review', label: 'Needs review' },
		{ key: 'not_found', label: 'Not found' },
		{ key: 'added', label: 'Added' },
		{ key: 'matched', label: 'Already there' },
		{ key: 'synced', label: 'Came from the other side' },
		{ key: 'pending_add', label: 'Picked by you' },
		{ key: 'gone', label: 'Removed on target' },
		{ key: 'skipped', label: 'Skipped' },
		{ key: 'removed', label: 'Removed' }
	];
	const LABEL: Record<string, [string, string]> = {
		new: ['New', ''],
		matched: ['Already there', 'ok'],
		added: ['Added', 'ok'],
		synced: ['From other side', 'info'],
		review: ['Needs review', 'warn'],
		not_found: ['Not found', 'err'],
		pending_add: ['Added next run', 'info'],
		skipped: ['Skipped', ''],
		gone: ['Removed on target', ''],
		removed: ['Removed', '']
	};

	async function load() {
		try {
			t = await get(`/transfers/${id}`);
		} catch (e) {
			errorToast(e);
		}
	}
	async function loadItems() {
		try {
			const r = await get(`/transfers/${id}/items` + qs({ side, status: filter, q }));
			items = r.items;
			total = r.total;
		} catch (e) {
			errorToast(e);
		}
	}
	$effect(() => {
		void id;
		load();
	});
	$effect(() => {
		void [id, side, filter, q];
		loadItems();
	});
	$effect(() =>
		on('job', (j) => {
			if (j.transfer_id === id && ['done', 'failed', 'cancelled'].includes(j.status)) {
				load();
				loadItems();
			}
		})
	);
	const job = $derived(Object.values(live.jobs).find((j: any) => j.transfer_id === id) as any);
	const src = $derived(side === 'a' ? 'a' : 'b');
	const dst = $derived(side === 'a' ? 'b' : 'a');

	async function save(fields: Record<string, any>) {
		try {
			t = await patch(`/transfers/${id}`, fields);
			toast('Saved', 'ok');
		} catch (e) {
			errorToast(e);
		}
	}
	async function run() {
		await post(`/transfers/${id}/run`).catch(errorToast);
	}
	async function remove() {
		await del(`/transfers/${id}`).catch(errorToast);
		goto('/transfers');
	}
	function toggle(it: any) {
		open = open === it.id ? null : it.id;
		searchRes = null;
		linkText = '';
		searchQ = `${(it.meta.artists ?? []).slice(0, 1).join(' ')} ${it.meta.title}`.trim();
	}
	async function search(it: any) {
		searching = true;
		try {
			searchRes = await get(`/transfers/${id}/search` + qs({ side: it.side, q: searchQ }));
		} catch (e) {
			errorToast(e);
		} finally {
			searching = false;
		}
	}
	async function resolve(it: any, body: any) {
		try {
			await post(`/transfers/${id}/items/${it.id}/resolve`, body);
			open = null;
			loadItems();
			load();
		} catch (e) {
			errorToast(e);
		}
	}
	function artists(m: any) {
		return (m?.artists ?? []).join(', ');
	}
	function cnt(s: string, ...ks: string[]) {
		return ks.reduce((a, k) => a + (t?.counts?.[s]?.[k] || 0), 0);
	}
</script>

<a href="/transfers" class="back small muted"><ArrowLeft size={14} /> Transfers</a>

{#if t}
	<div class="page-head">
		<h1 class="grow ellipsis">{t.name}</h1>
		<button class="danger" onclick={() => (delOpen = true)}><Trash2 size={15} /></button>
		<button class="primary" onclick={run} disabled={!!job}><RefreshCw size={15} class={job ? 'spin' : ''} /> {job ? 'Running…' : 'Run now'}</button>
	</div>

	<div class="flow card">
		<div class="side">
			<ProviderBadge provider={t.a_provider} label={t.a_conn_label} />
			<b class="ellipsis">{t.a_playlist_name ?? t.a_playlist_id}</b>
			{#if t.a_url}<a class="small link" href={t.a_url} target="_blank" rel="noreferrer">open <ExternalLink size={11} /></a>{/if}
		</div>
		<span class="arrow">{#if t.two_way}<ArrowLeftRight size={22} />{:else}<ArrowRight size={22} />{/if}</span>
		<div class="side">
			<ProviderBadge provider={t.b_provider} label={t.b_conn_label} />
			<b class="ellipsis">{t.b_playlist_name ?? 'created on first run'}</b>
			{#if t.b_url}<a class="small link" href={t.b_url} target="_blank" rel="noreferrer">open <ExternalLink size={11} /></a>{/if}
		</div>
	</div>

	<div class="cols">
		<div class="card pad col">
			<div class="row wrap small">
				{#if t.last_status}<StatusBadge status={t.last_status} />{/if}
				<span class="muted">last run {ago(t.last_run_at)}</span>
				{#if t.keep_synced && t.enabled && t.next_run_at}<span class="muted">· next {until(t.next_run_at)}</span>{/if}
			</div>
			{#if t.last_error}<div class="small errbox">{t.last_error}</div>{/if}
			<div class="stats">
				<div><b>{cnt('a', 'added') + cnt('b', 'added')}</b><span>added</span></div>
				<div><b>{cnt('a', 'matched') + cnt('b', 'matched')}</b><span>already there</span></div>
				<div><b>{cnt('a', 'review') + cnt('b', 'review')}</b><span>to review</span></div>
				<div><b>{cnt('a', 'not_found') + cnt('b', 'not_found')}</b><span>not found</span></div>
			</div>
		</div>
		<div class="card pad col">
			<label class="check"><input type="checkbox" checked={t.keep_synced} onchange={(e) => save({ keep_synced: e.currentTarget.checked })} /> Keep in sync</label>
			{#if t.keep_synced}
				<div class="row"><SchedulePicker bind:value={t.schedule} /><button class="sm" onclick={() => save({ schedule: t.schedule })}>Save</button></div>
				<label class="check"><input type="checkbox" checked={t.two_way} onchange={(e) => save({ two_way: e.currentTarget.checked })} /> Both directions</label>
				<label class="check"><input type="checkbox" checked={t.sync_removals} onchange={(e) => save({ sync_removals: e.currentTarget.checked })} /> Sync removals</label>
				<label class="check"><input type="checkbox" checked={t.enabled} onchange={(e) => save({ enabled: e.currentTarget.checked })} /> Schedule active</label>
			{/if}
			<label class="check"><input type="checkbox" checked={t.add_uncertain} onchange={(e) => save({ add_uncertain: e.currentTarget.checked })} /> Add uncertain matches without review</label>
			{#if t.a_provider === 'youtube' || t.b_provider === 'youtube'}
				<label class="check"><input type="checkbox" checked={t.download} onchange={(e) => save({ download: e.currentTarget.checked })} /> Download the YouTube side with Tubekeep</label>
				{#if t.download_playlist_id}<a class="small link" href="/playlists/{t.download_playlist_id}">Open download playlist →</a>{/if}
			{/if}
		</div>
	</div>

	{#if job}<JobCard {job} />{/if}

	<div class="toolbar">
		<div class="tabs">
			<button class:active={side === 'a'} onclick={() => (side = 'a')}>Songs in <ProviderBadge provider={t.a_provider} size="sm" /></button>
			<button class:active={side === 'b'} onclick={() => (side = 'b')}>Songs in <ProviderBadge provider={t.b_provider} size="sm" /></button>
		</div>
		<div class="chips">
			{#each FILTERS as f (f.key)}
				<button class="chip" class:active={filter === f.key} onclick={() => (filter = f.key)}>{f.label}</button>
			{/each}
		</div>
		<div class="search"><Search size={15} /><input type="search" bind:value={q} placeholder="Search songs" /></div>
	</div>

	<div class="card">
		{#each items as it (it.id)}
			{@const st = LABEL[it.present ? it.status : 'removed'] ?? [it.status, '']}
			<div class="item" class:openrow={open === it.id}>
				<button class="itemhead" onclick={() => toggle(it)}>
					<span class="pos faint small">{it.position != null ? it.position + 1 : ''}</span>
					<span class="col grow" style="gap: 1px; min-width: 0">
						<span class="ellipsis"><b>{it.meta.title}</b></span>
						<span class="small faint ellipsis">{artists(it.meta)}{it.meta.album ? ` · ${it.meta.album}` : ''}{it.meta.duration ? ` · ${duration(it.meta.duration)}` : ''}</span>
					</span>
					<span class="match col hide-sm">
						{#if it.match_meta}
							<span class="ellipsis small">{it.match_meta.title}</span>
							<span class="ellipsis small faint">{artists(it.match_meta)}{it.score && it.score < 1 ? ` · ${Math.round(it.score * 100)}%` : ''}</span>
						{:else if it.candidates?.length}
							<span class="ellipsis small faint">best guess: {it.candidates[0].title} · {Math.round(it.candidates[0].score * 100)}%</span>
						{/if}
					</span>
					<span class="pill {st[1]}" title={it.note ?? ''}>{st[0]}</span>
				</button>
				{#if open === it.id}
					<div class="detail">
						{#if it.note}<p class="small muted">{it.note}</p>{/if}
						{#if it.candidates?.length}
							<div class="small faint" style="margin-bottom: 4px">Suggestions on {t[dst + '_provider_label']}</div>
							{#each it.candidates as c (c.id)}
								<div class="cand">
									<span class="col grow" style="min-width: 0"><span class="ellipsis">{c.title}</span><span class="small faint ellipsis">{artists(c)}{c.album ? ` · ${c.album}` : ''}{c.duration ? ` · ${duration(c.duration)}` : ''}</span></span>
									<span class="small faint">{Math.round(c.score * 100)}%</span>
									{#if c.url}<a href={c.url} target="_blank" rel="noreferrer" class="small link"><ExternalLink size={12} /></a>{/if}
									<button class="sm" onclick={() => resolve(it, { track: c })}><Check size={14} /> Use</button>
								</div>
							{/each}
						{/if}
						<div class="row" style="margin-top: 8px">
							<input bind:value={searchQ} placeholder="Search on {t[dst + '_provider_label']}" onkeydown={(e) => e.key === 'Enter' && search(it)} />
							<button onclick={() => search(it)} disabled={searching}>{#if searching}<Loader size={14} class="spin" />{:else}<Search size={14} />{/if} Search</button>
						</div>
						{#if searchRes}
							{#each searchRes as c (c.id)}
								<div class="cand">
									<span class="col grow" style="min-width: 0"><span class="ellipsis">{c.title}</span><span class="small faint ellipsis">{artists(c)}{c.album ? ` · ${c.album}` : ''}{c.duration ? ` · ${duration(c.duration)}` : ''}</span></span>
									{#if c.url}<a href={c.url} target="_blank" rel="noreferrer" class="small link"><ExternalLink size={12} /></a>{/if}
									<button class="sm" onclick={() => resolve(it, { track: c })}><Check size={14} /> Use</button>
								</div>
							{:else}
								<div class="small faint pad">No results</div>
							{/each}
						{/if}
						<div class="row" style="margin-top: 8px">
							<input class="mono" bind:value={linkText} placeholder="…or paste a link to the song on {t[dst + '_provider_label']}" />
							<button onclick={() => resolve(it, { url: linkText })} disabled={!linkText.trim()}>Use link</button>
						</div>
						<div class="row" style="margin-top: 8px">
							{#if it.status === 'skipped'}
								<button class="sm" onclick={() => resolve(it, { skip: false })}><Undo2 size={14} /> Don't skip</button>
							{:else}
								<button class="sm" onclick={() => resolve(it, { skip: true })}><SkipForward size={14} /> Skip this song</button>
							{/if}
							<span class="spacer"></span>
							<span class="hint">Picked songs are added on the next run.</span>
						</div>
					</div>
				{/if}
			</div>
		{:else}
			<div class="empty">{t.last_run_at ? 'No songs match this filter.' : 'Not run yet.'}</div>
		{/each}
	</div>
	{#if total > items.length}<p class="small faint" style="margin-top: 8px">Showing {items.length} of {total}. Use the search to narrow down.</p>{/if}

	<Modal bind:open={delOpen} title="Delete transfer?" width="440px">
		<p>The transfer and its matching history are deleted. The playlists on both services stay as they are.</p>
		{#snippet footer()}
			<button onclick={() => (delOpen = false)}>Cancel</button>
			<button class="danger solid" onclick={remove}>Delete</button>
		{/snippet}
	</Modal>
{/if}

<style>
	.back {
		display: inline-flex;
		align-items: center;
		gap: 4px;
		margin-bottom: 10px;
	}
	.flow {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
		gap: 18px;
		align-items: center;
		padding: 16px;
		margin-bottom: 14px;
	}
	.side {
		display: flex;
		flex-direction: column;
		gap: 4px;
		min-width: 0;
	}
	.arrow {
		color: var(--text-3);
	}
	.link {
		color: var(--accent);
		display: inline-flex;
		align-items: center;
		gap: 3px;
	}
	.cols {
		display: grid;
		grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
		gap: 14px;
		margin-bottom: 14px;
	}
	.stats {
		display: grid;
		grid-template-columns: repeat(4, 1fr);
		gap: 8px;
		margin-top: 6px;
	}
	.stats div {
		display: flex;
		flex-direction: column;
	}
	.stats b {
		font-size: 20px;
		font-weight: 680;
	}
	.stats span {
		font-size: 12px;
		color: var(--text-2);
	}
	.errbox {
		color: var(--err);
		background: var(--err-soft);
		padding: 8px 10px;
		border-radius: 8px;
		white-space: pre-wrap;
	}
	.toolbar {
		display: flex;
		flex-direction: column;
		gap: 10px;
		margin: 18px 0 12px;
	}
	.tabs {
		display: flex;
		gap: 4px;
		border-bottom: 1px solid var(--line);
	}
	.tabs button {
		border: none;
		background: none;
		border-radius: 0;
		border-bottom: 2px solid transparent;
		margin-bottom: -1px;
		color: var(--text-2);
	}
	.tabs button.active {
		color: var(--text);
		border-bottom-color: var(--accent);
	}
	.chips {
		display: flex;
		gap: 6px;
		flex-wrap: wrap;
	}
	.chip {
		height: 28px;
		border-radius: 99px;
		font-size: 12.5px;
		color: var(--text-2);
	}
	.chip.active {
		background: var(--text);
		color: var(--bg);
		border-color: var(--text);
	}
	.search {
		position: relative;
		display: flex;
		align-items: center;
	}
	.search :global(svg) {
		position: absolute;
		left: 10px;
		color: var(--text-3);
	}
	.search input {
		padding-left: 32px;
	}
	.item {
		border-bottom: 1px solid var(--line);
	}
	.item:last-child {
		border-bottom: none;
	}
	.itemhead {
		all: unset;
		box-sizing: border-box;
		display: flex;
		align-items: center;
		gap: 12px;
		width: 100%;
		padding: 9px 14px;
		cursor: pointer;
	}
	.itemhead:hover,
	.openrow .itemhead {
		background: var(--bg-hover);
	}
	.pos {
		width: 26px;
		text-align: right;
		flex-shrink: 0;
	}
	.match {
		width: 34%;
		min-width: 0;
		gap: 1px;
	}
	.detail {
		padding: 10px 14px 14px 52px;
		background: var(--bg);
	}
	.cand {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 6px 0;
		border-bottom: 1px dashed var(--line);
	}
	@media (max-width: 760px) {
		.cols {
			grid-template-columns: 1fr;
		}
		.hide-sm {
			display: none;
		}
		.detail {
			padding-left: 14px;
		}
		.flow {
			grid-template-columns: 1fr;
		}
	}
</style>
