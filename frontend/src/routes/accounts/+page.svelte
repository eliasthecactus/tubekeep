<script lang="ts">
	import { Plus, CircleCheck, Trash2, KeyRound, Loader, Upload, UserRound } from '@lucide/svelte';
	import { del, get, patch, post } from '#lib/api.ts';
	import { ago, datetime } from '#lib/format.ts';
	import { errorToast, on, toast } from '#lib/state.svelte.ts';
	import Modal from '#lib/components/Modal.svelte';
	import StatusBadge from '#lib/components/StatusBadge.svelte';

	let accounts = $state<any[]>([]);
	let proxies = $state<any[]>([]);
	let open = $state(false);
	let editing = $state<any>(null);
	let label = $state('');
	let cookies = $state('');
	let proxyId = $state<number | null>(null);
	let busy = $state(false);
	let testing = $state<number | null>(null);
	let confirmDel = $state<any>(null);
	let fileInput: HTMLInputElement | undefined = $state();

	async function load() {
		try {
			[accounts, proxies] = await Promise.all([get('/accounts'), get('/proxies')]);
		} catch (e) {
			errorToast(e);
		}
	}
	load();
	$effect(() => on('account', () => load()));

	function openAdd() {
		editing = null;
		label = '';
		cookies = '';
		proxyId = null;
		open = true;
	}
	function openEdit(a: any) {
		editing = a;
		label = a.label;
		cookies = '';
		proxyId = a.proxy_id;
		open = true;
	}
	async function readFile(e: Event) {
		const f = (e.target as HTMLInputElement).files?.[0];
		if (f) cookies = await f.text();
		if (fileInput) fileInput.value = '';
	}
	async function save() {
		busy = true;
		try {
			let test;
			if (editing) {
				const r = await patch(`/accounts/${editing.id}`, { label, proxy_id: proxyId, ...(cookies.trim() ? { cookies } : {}) });
				test = r.test;
			} else {
				const r = await post('/accounts', { label, cookies, proxy_id: proxyId });
				test = r.test;
			}
			if (test && !test.ok) toast(`Saved, but the test failed: ${test.error}`, 'error');
			else toast(test ? 'Saved, cookies work' : 'Saved', 'ok');
			open = false;
			load();
		} catch (e) {
			errorToast(e);
		} finally {
			busy = false;
		}
	}
	async function test(a: any) {
		testing = a.id;
		try {
			const r = await post(`/accounts/${a.id}/test`);
			toast(r.ok ? `${a.label}: signed in, cookies work` : `${a.label}: ${r.error}`, r.ok ? 'ok' : 'error');
			load();
		} catch (e) {
			errorToast(e);
		} finally {
			testing = null;
		}
	}
	async function remove() {
		try {
			await del(`/accounts/${confirmDel.id}`);
			confirmDel = null;
			load();
		} catch (e) {
			errorToast(e);
		}
	}
</script>

<div class="page-head">
	<div class="col" style="gap: 2px">
		<h1>YouTube accounts</h1>
		<p class="muted small">Cookies of a signed-in account let Tubekeep sync private playlists, Liked videos and Watch later.</p>
	</div>
	<span class="spacer"></span>
	<button class="primary" onclick={openAdd}><Plus size={15} /> Add account</button>
</div>

<div class="list">
	{#each accounts as a (a.id)}
		<div class="acc card">
			<div class="avatar"><UserRound size={18} /></div>
			<div class="col grow" style="gap: 3px; min-width: 0">
				<div class="row"><b>{a.label}</b><StatusBadge status={a.status} /></div>
				<div class="small muted">
					Checked {ago(a.last_checked_at)} · cookies from {datetime(a.cookies_updated_at)} · {a.playlist_count} playlist{a.playlist_count === 1 ? '' : 's'}
					{#if a.proxy_id}· pinned to proxy #{a.proxy_id}{/if}
				</div>
				{#if a.last_error}<div class="small errtext ellipsis" title={a.last_error}>{a.last_error}</div>{/if}
			</div>
			<button class="sm" onclick={() => test(a)} disabled={testing === a.id}>{#if testing === a.id}<Loader size={14} class="spin" />{:else}<CircleCheck size={14} />{/if} Test</button>
			<button class="sm" onclick={() => openEdit(a)}><KeyRound size={14} /> Update cookies</button>
			<button class="sm ghost icon danger" onclick={() => (confirmDel = a)} title="Delete"><Trash2 size={14} /></button>
		</div>
	{:else}
		<div class="card empty">No accounts. Public playlists work without one.</div>
	{/each}
</div>

<section class="card pad howto">
	<h2>How to export cookies</h2>
	<ol>
		<li>Open a <b>private / incognito window</b> and sign in to YouTube there. A secondary Google account is safer, heavy downloading can get an account flagged.</li>
		<li>Install a cookies.txt exporter, for example <i>Get cookies.txt LOCALLY</i> (Chrome) or <i>cookies.txt</i> (Firefox), and allow it in private windows.</li>
		<li>On youtube.com export the cookies in <b>Netscape format</b>.</li>
		<li><b>Close the private window right away</b>, without logging out. YouTube rotates cookies of open sessions, which makes the export invalid.</li>
		<li>Paste the file here. Tubekeep keeps it encrypted and saves refreshed cookies after every run.</li>
	</ol>
	<p class="small muted">Tip: playlists set to <i>unlisted</i> work without an account.</p>
</section>

<Modal bind:open title={editing ? `Update ${editing.label}` : 'Add YouTube account'} width="600px">
	<div class="col" style="gap: 12px">
		<label class="field">Name<input bind:value={label} placeholder="e.g. Music account" /></label>
		<label class="field">
			<span class="row">cookies.txt <span class="spacer"></span><button class="sm" type="button" onclick={() => fileInput?.click()}><Upload size={14} /> Choose file</button></span>
			<textarea class="mono" rows="9" bind:value={cookies} placeholder={editing ? 'Leave empty to keep the current cookies' : '# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t…'}></textarea>
			<input bind:this={fileInput} type="file" accept=".txt,text/plain" hidden onchange={readFile} />
		</label>
		<label class="field">
			Pinned proxy
			<select bind:value={proxyId}>
				<option value={null}>No proxy (direct connection)</option>
				{#each proxies as p (p.id)}<option value={p.id}>{p.label} ({p.url})</option>{/each}
			</select>
			<span class="hint">A signed-in session should always use the same IP. All downloads with this account go through this proxy.</span>
		</label>
	</div>
	{#snippet footer()}
		<button onclick={() => (open = false)}>Cancel</button>
		<button class="primary" onclick={save} disabled={busy || (!editing && !cookies.trim())}>
			{#if busy}<Loader size={15} class="spin" /> Testing…{:else}Save and test{/if}
		</button>
	{/snippet}
</Modal>

<Modal open={!!confirmDel} title="Delete account?" width="420px" onclose={() => (confirmDel = null)}>
	<p>The cookies of <b>{confirmDel?.label}</b> are deleted. Playlists using it continue without an account.</p>
	{#snippet footer()}
		<button onclick={() => (confirmDel = null)}>Cancel</button>
		<button class="danger solid" onclick={remove}>Delete</button>
	{/snippet}
</Modal>

<style>
	.list {
		display: flex;
		flex-direction: column;
		gap: 10px;
		margin-bottom: 22px;
	}
	.acc {
		display: flex;
		align-items: center;
		gap: 12px;
		padding: 14px;
		flex-wrap: wrap;
	}
	.avatar {
		width: 38px;
		height: 38px;
		border-radius: 50%;
		background: var(--accent-soft);
		color: var(--accent);
		display: grid;
		place-items: center;
	}
	.errtext {
		color: var(--err);
	}
	.howto ol {
		margin: 10px 0;
		padding-left: 20px;
		display: flex;
		flex-direction: column;
		gap: 6px;
		color: var(--text-2);
	}
</style>
