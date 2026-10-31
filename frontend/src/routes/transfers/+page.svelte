<script lang="ts">
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { Plus, ArrowRight, ArrowLeftRight, RefreshCw, Loader, Trash2, CircleCheck, ExternalLink } from '@lucide/svelte';
	import { del, get, post, put } from '#lib/api.ts';
	import { ago, until } from '#lib/format.ts';
	import { errorToast, live, on, toast } from '#lib/state.svelte.ts';
	import Modal from '#lib/components/Modal.svelte';
	import ProviderBadge from '#lib/components/ProviderBadge.svelte';
	import PlaylistPicker from '#lib/components/PlaylistPicker.svelte';
	import SchedulePicker from '#lib/components/SchedulePicker.svelte';
	import StatusBadge from '#lib/components/StatusBadge.svelte';

	let conns = $state<any[]>([]);
	let transfers = $state<any[]>([]);
	let info = $state<any>(null);
	let accounts = $state<any[]>([]);

	async function load() {
		try {
			[conns, transfers, info, accounts] = await Promise.all([get('/connections'), get('/transfers'), get('/providers'), get('/accounts')]);
		} catch (e) {
			errorToast(e);
		}
	}
	load();
	$effect(() => on('transfer', () => load()));
	$effect(() => on('job', (j) => j.type === 'transfer' && ['done', 'failed', 'cancelled'].includes(j.status) && load()));
	$effect(() => {
		const err = page.url.searchParams.get('error');
		const ok = page.url.searchParams.get('connected');
		if (err) toast(err, 'error');
		if (ok) toast(`${ok} connected`, 'ok');
		if (err || ok) goto('/transfers', { replaceState: true });
	});

	// ---------------------------------------------------------------- connect flows
	let connectOpen = $state(false);
	let connectProvider = $state<string>('youtube');
	let ytAccount = $state<number | null>(null);
	let busy = $state(false);
	let tidalFlow = $state<{ flow_id: string; url: string; code: string } | null>(null);
	let setup = $state<any>({ spotify_client_id: '', public_url: '', apple_team_id: '', apple_key_id: '', apple_private_key: '' });

	async function openConnect(p: string) {
		connectProvider = p;
		ytAccount = accounts[0]?.id ?? null;
		tidalFlow = null;
		const s = await get('/settings');
		setup = { spotify_client_id: s.spotify_client_id, public_url: s.public_url, apple_team_id: s.apple_team_id, apple_key_id: s.apple_key_id, apple_private_key: '', apple_key_set: s.apple_private_key_set };
		connectOpen = true;
	}
	async function saveSetup(keys: string[]) {
		const body: any = {};
		for (const k of keys) if (k !== 'apple_private_key' || setup.apple_private_key) body[k] = setup[k];
		await put('/settings', body);
		info = await get('/providers');
	}
	async function connectYouTube() {
		busy = true;
		try {
			await post('/connections/youtube', { account_id: ytAccount });
			toast('YouTube Music connected', 'ok');
			connectOpen = false;
			load();
		} catch (e) {
			errorToast(e);
		} finally {
			busy = false;
		}
	}
	async function connectSpotify() {
		busy = true;
		try {
			await saveSetup(['spotify_client_id', 'public_url']);
			const r = await get('/connections/spotify/authorize');
			if (location.hostname === 'localhost') {
				toast('Spotify only accepts 127.0.0.1 as local address. Open Tubekeep via http://127.0.0.1:' + location.port, 'error');
				busy = false;
				return;
			}
			location.href = r.url;
		} catch (e) {
			errorToast(e);
			busy = false;
		}
	}
	function loadMusicKit(): Promise<any> {
		return new Promise((resolve, reject) => {
			const w = window as any;
			if (w.MusicKit) return resolve(w.MusicKit);
			document.addEventListener('musickitloaded', () => resolve(w.MusicKit), { once: true });
			const s = document.createElement('script');
			s.src = 'https://js-cdn.music.apple.com/musickit/v3/musickit.js';
			s.async = true;
			s.onerror = () => reject(new Error('Could not load MusicKit JS from Apple'));
			document.head.appendChild(s);
		});
	}
	async function connectApple() {
		busy = true;
		try {
			await saveSetup(['apple_team_id', 'apple_key_id', 'apple_private_key']);
			const { token } = await get('/connections/apple/developer-token');
			const MK = await loadMusicKit();
			const mk = await MK.configure({ developerToken: token, app: { name: 'Tubekeep', build: '1.0' } });
			const userToken = await mk.authorize();
			if (!userToken) throw new Error('Apple Music login was cancelled');
			await post('/connections/apple', { music_user_token: userToken, storefront: mk.storefrontId || mk.storefrontCountryCode });
			toast('Apple Music connected', 'ok');
			connectOpen = false;
			load();
		} catch (e) {
			errorToast(e);
		} finally {
			busy = false;
		}
	}
	async function connectTidal() {
		busy = true;
		try {
			tidalFlow = await post('/connections/tidal/start', {});
			window.open(tidalFlow!.url, '_blank', 'noopener');
			const fid = tidalFlow!.flow_id;
			for (let i = 0; i < 200 && connectOpen && tidalFlow?.flow_id === fid; i++) {
				await new Promise((r) => setTimeout(r, 3000));
				const s = await get(`/connections/tidal/status/${fid}`);
				if (s.status === 'done') {
					toast(`TIDAL connected as ${s.name}`, 'ok');
					connectOpen = false;
					load();
					break;
				}
				if (s.status === 'error') throw new Error(s.error);
			}
		} catch (e) {
			errorToast(e);
		} finally {
			busy = false;
		}
	}
	async function testConn(c: any) {
		const r = await post(`/connections/${c.id}/test`).catch((e) => ({ ok: false, error: e.message }));
		toast(r.ok ? `${c.label}: OK (${r.name})` : `${c.label}: ${r.error}`, r.ok ? 'ok' : 'error');
		load();
	}
	async function removeConn(c: any) {
		if (!confirm(`Remove the connection ${c.label}? Transfers using it are deleted too.`)) return;
		await del(`/connections/${c.id}`).catch(errorToast);
		load();
	}

	// ---------------------------------------------------------------- new transfer
	let wizOpen = $state(false);
	let step = $state(1);
	let w = $state<any>({});
	function openWizard() {
		if (conns.length < 1) return toast('Connect at least one service first', 'error');
		w = {
			a_conn_id: conns[0]?.id ?? null,
			src: { id: null, name: null },
			b_conn_id: conns.find((c) => c.id !== conns[0]?.id)?.id ?? conns[0]?.id ?? null,
			dst: { id: null, name: null },
			keep_synced: true,
			two_way: false,
			sync_removals: false,
			add_uncertain: false,
			download: false,
			schedule: '0 */6 * * *',
			name: ''
		};
		step = 1;
		wizOpen = true;
	}
	const connOf = (id: number) => conns.find((c) => c.id === id);
	const involvesYouTube = $derived(connOf(w.a_conn_id)?.provider === 'youtube' || connOf(w.b_conn_id)?.provider === 'youtube');
	const targetNoRemove = $derived(connOf(w.b_conn_id)?.can_remove === false || (w.two_way && connOf(w.a_conn_id)?.can_remove === false));
	async function createTransfer() {
		busy = true;
		try {
			const t = await post('/transfers', {
				name: w.name || undefined,
				a_conn_id: w.a_conn_id,
				a_playlist_id: w.src.id,
				a_playlist_name: w.src.name,
				b_conn_id: w.b_conn_id,
				b_playlist_id: w.dst.id,
				b_playlist_name: w.dst.id ? w.dst.name : w.dst.name || w.src.name,
				keep_synced: w.keep_synced,
				two_way: w.keep_synced && w.two_way,
				sync_removals: w.keep_synced && w.sync_removals,
				add_uncertain: w.add_uncertain,
				download: w.download,
				schedule: w.schedule,
				run_now: true
			});
			wizOpen = false;
			toast('Transfer started', 'ok');
			goto(`/transfers/${t.id}`);
		} catch (e) {
			errorToast(e);
		} finally {
			busy = false;
		}
	}
	async function runNow(t: any) {
		await post(`/transfers/${t.id}/run`).catch(errorToast);
	}
	const jobFor = (id: number) => Object.values(live.jobs).find((j: any) => j.transfer_id === id) as any;
	function n(c: Record<string, number>, ...ks: string[]) {
		return ks.reduce((a, k) => a + (c?.[k] || 0), 0);
	}
</script>

<div class="page-head">
	<div class="col" style="gap: 2px">
		<h1>Transfers</h1>
		<p class="muted small">Copy playlists between music services and keep them in sync, one way or both ways.</p>
	</div>
	<span class="spacer"></span>
	<button class="primary" onclick={openWizard}><Plus size={15} /> New transfer</button>
</div>

<section style="margin-bottom: 24px">
	<div class="section-title"><h2>Connected services</h2></div>
	<div class="conns">
		{#each conns as c (c.id)}
			<div class="conn card">
				<ProviderBadge provider={c.provider} label={c.label} />
				<div class="row small">
					<StatusBadge status={c.status} title={c.last_error ?? ''} />
					<span class="faint">{c.provider_label}{c.can_remove ? '' : ' · add only'}</span>
				</div>
				<div class="row">
					<button class="sm" onclick={() => testConn(c)}><CircleCheck size={14} /> Test</button>
					{#if c.status === 'expired'}<button class="sm" onclick={() => openConnect(c.provider)}>Reconnect</button>{/if}
					<span class="spacer"></span>
					<button class="sm icon ghost danger" onclick={() => removeConn(c)} title="Remove"><Trash2 size={14} /></button>
				</div>
			</div>
		{/each}
		<div class="conn add card">
			<b>Connect a service</b>
			<div class="addgrid">
				{#each ['youtube', 'spotify', 'apple', 'tidal'] as p (p)}
					<button onclick={() => openConnect(p)}><ProviderBadge provider={p} size="sm" /></button>
				{/each}
			</div>
		</div>
	</div>
</section>

<section>
	<div class="section-title"><h2>Transfers</h2><span class="pill">{transfers.length}</span></div>
	<div class="tlist">
		{#each transfers as t (t.id)}
			{@const job = jobFor(t.id)}
			<a class="tr card" href="/transfers/{t.id}">
				<div class="flow">
					<div class="side">
						<ProviderBadge provider={t.a_provider} size="sm" />
						<b class="ellipsis">{t.a_playlist_name ?? t.a_playlist_id}</b>
					</div>
					<span class="arrow">{#if t.two_way}<ArrowLeftRight size={18} />{:else}<ArrowRight size={18} />{/if}</span>
					<div class="side">
						<ProviderBadge provider={t.b_provider} size="sm" />
						<b class="ellipsis">{t.b_playlist_name ?? 'new playlist'}</b>
					</div>
				</div>
				<div class="row wrap small muted">
					<span>{n(t.counts.a, 'added', 'matched', 'synced')} / {n(t.counts.a, 'added', 'matched', 'synced', 'review', 'not_found', 'new', 'pending_add', 'gone', 'skipped')} on both sides</span>
					{#if n(t.counts.a, 'review') + n(t.counts.b, 'review')}<span class="pill warn">{n(t.counts.a, 'review') + n(t.counts.b, 'review')} to review</span>{/if}
					{#if n(t.counts.a, 'not_found') + n(t.counts.b, 'not_found')}<span class="pill">{n(t.counts.a, 'not_found') + n(t.counts.b, 'not_found')} not found</span>{/if}
					<span class="spacer"></span>
					{#if job}
						<span class="pill info"><Loader size={11} class="spin" /> {job.phase}</span>
					{:else}
						{#if t.last_status}<StatusBadge status={t.last_status} title={t.last_error ?? ''} />{/if}
						<span>ran {ago(t.last_run_at)}</span>
						{#if t.keep_synced}<span class="faint">· next {t.enabled ? until(t.next_run_at) : 'paused'}</span>{:else}<span class="faint">· one-time copy</span>{/if}
					{/if}
					<button class="sm icon" onclick={(e) => { e.preventDefault(); runNow(t); }} disabled={!!job} title="Run now"><RefreshCw size={14} /></button>
				</div>
				{#if t.last_error && !job}<div class="small errtext ellipsis">{t.last_error}</div>{/if}
			</a>
		{:else}
			<div class="card empty">No transfers yet. Connect two services (or one service twice) and start a transfer.</div>
		{/each}
	</div>
</section>

<!-- ------------------------------------------------------------ connect dialog -->
<Modal bind:open={connectOpen} title="Connect {({ youtube: 'YouTube Music', spotify: 'Spotify', apple: 'Apple Music', tidal: 'TIDAL' } as any)[connectProvider]}" width="600px">
	{#if connectProvider === 'youtube'}
		<p class="muted">YouTube Music uses the cookies of one of your YouTube accounts (the same ones used for private playlist downloads).</p>
		{#if accounts.length}
			<label class="field" style="margin-top: 12px">
				Account
				<select bind:value={ytAccount}>{#each accounts as a (a.id)}<option value={a.id}>{a.label}</option>{/each}</select>
			</label>
		{:else}
			<p style="margin-top: 12px">No account yet. <a href="/accounts" style="color: var(--accent)">Add one under Accounts</a> first.</p>
		{/if}
	{:else if connectProvider === 'spotify'}
		<ol class="steps">
			<li>Open <a href="https://developer.spotify.com/dashboard" target="_blank" rel="noreferrer">developer.spotify.com/dashboard <ExternalLink size={12} /></a> and create an app (Web API).</li>
			<li>Add this <b>Redirect URI</b> to the app:<code class="copy">{info?.spotify.redirect_uri}</code></li>
			<li>Paste the app's <b>Client ID</b> below. No secret is needed (PKCE).</li>
		</ol>
		<p class="hint">Since 2026 Spotify only lets development apps work when the app owner has Premium, and only for up to 5 users you add under "User Management".</p>
		<div class="grid2" style="margin-top: 10px">
			<label class="field">Client ID<input class="mono" bind:value={setup.spotify_client_id} /></label>
			<label class="field">Public URL (optional)<input class="mono" bind:value={setup.public_url} placeholder="https://music.example.com" /></label>
		</div>
		<p class="hint">Set the public URL when Tubekeep runs behind a reverse proxy, so the redirect URI uses your domain. Spotify accepts http only for 127.0.0.1.</p>
	{:else if connectProvider === 'apple'}
		<ol class="steps">
			<li>In <a href="https://developer.apple.com/account/resources/authkeys/list" target="_blank" rel="noreferrer">Certificates, IDs &amp; Profiles → Keys <ExternalLink size={12} /></a> create a key with <b>MusicKit</b> enabled (a Media ID may be required first) and download the .p8 file.</li>
			<li>Fill in your Team ID, the Key ID and the content of the .p8 file.</li>
			<li>Click connect and sign in with your Apple ID in the popup.</li>
		</ol>
		<div class="grid2" style="margin-top: 10px">
			<label class="field">Team ID<input class="mono" bind:value={setup.apple_team_id} /></label>
			<label class="field">Key ID<input class="mono" bind:value={setup.apple_key_id} /></label>
		</div>
		<label class="field" style="margin-top: 10px">
			Private key (.p8) {#if setup.apple_key_set}<span class="pill ok">stored</span>{/if}
			<textarea class="mono" rows="4" bind:value={setup.apple_private_key} placeholder={setup.apple_key_set ? 'Leave empty to keep the stored key' : '-----BEGIN PRIVATE KEY-----'}></textarea>
		</label>
		<p class="hint">The Apple Music API can add songs to playlists but cannot remove them. Removals that should go to Apple Music are listed so you can do them by hand.</p>
	{:else if connectProvider === 'tidal'}
		<p class="muted">TIDAL uses a device login: a TIDAL page opens, you sign in and confirm the code.</p>
		{#if tidalFlow}
			<div class="tidal card pad">
				<div>Code: <b class="mono" style="font-size: 18px">{tidalFlow.code}</b></div>
				<a href={tidalFlow.url} target="_blank" rel="noreferrer" style="color: var(--accent)">{tidalFlow.url} <ExternalLink size={12} /></a>
				<div class="row small muted"><Loader size={14} class="spin" /> Waiting for you to confirm on TIDAL…</div>
			</div>
		{/if}
	{/if}
	{#snippet footer()}
		<button onclick={() => (connectOpen = false)}>Cancel</button>
		{#if connectProvider === 'youtube'}
			<button class="primary" onclick={connectYouTube} disabled={busy || !ytAccount}>{#if busy}<Loader size={15} class="spin" />{/if} Connect</button>
		{:else if connectProvider === 'spotify'}
			<button class="primary" onclick={connectSpotify} disabled={busy || !setup.spotify_client_id}>{#if busy}<Loader size={15} class="spin" />{/if} Sign in with Spotify</button>
		{:else if connectProvider === 'apple'}
			<button class="primary" onclick={connectApple} disabled={busy || !setup.apple_team_id || !setup.apple_key_id || (!setup.apple_private_key && !setup.apple_key_set)}>{#if busy}<Loader size={15} class="spin" />{/if} Sign in with Apple</button>
		{:else}
			<button class="primary" onclick={connectTidal} disabled={busy}>{#if busy}<Loader size={15} class="spin" />{/if} Open TIDAL login</button>
		{/if}
	{/snippet}
</Modal>

<!-- ------------------------------------------------------------ wizard -->
<Modal bind:open={wizOpen} title="New transfer · step {step} of 3" width="640px">
	{#if step === 1}
		<h3>From</h3>
		<label class="field" style="margin: 8px 0 10px">
			Service
			<select bind:value={w.a_conn_id} onchange={() => (w.src = { id: null, name: null })}>
				{#each conns as c (c.id)}<option value={c.id}>{c.provider_label} · {c.label}</option>{/each}
			</select>
		</label>
		{#key w.a_conn_id}<PlaylistPicker connId={w.a_conn_id} bind:value={w.src} />{/key}
	{:else if step === 2}
		<h3>To</h3>
		<label class="field" style="margin: 8px 0 10px">
			Service
			<select bind:value={w.b_conn_id} onchange={() => (w.dst = { id: null, name: null })}>
				{#each conns as c (c.id)}<option value={c.id}>{c.provider_label} · {c.label}</option>{/each}
			</select>
		</label>
		{#key w.b_conn_id}<PlaylistPicker connId={w.b_conn_id} bind:value={w.dst} allowNew onlyEditable />{/key}
	{:else}
		<div class="summary">
			<ProviderBadge provider={connOf(w.a_conn_id)?.provider} label={w.src.name ?? w.src.id} />
			<span class="arrow">{#if w.keep_synced && w.two_way}<ArrowLeftRight size={18} />{:else}<ArrowRight size={18} />{/if}</span>
			<ProviderBadge provider={connOf(w.b_conn_id)?.provider} label={w.dst.id ? (w.dst.name ?? w.dst.id) : `new: ${w.dst.name || w.src.name}`} />
		</div>
		<div class="opts">
			<label class="opt"><input type="checkbox" bind:checked={w.keep_synced} /><span><b>Keep in sync</b><span class="hint">Run again on a schedule and bring over new songs.</span></span></label>
			{#if w.keep_synced}
				<div class="indent col">
					<label class="field">Check every<SchedulePicker bind:value={w.schedule} /></label>
					<label class="opt"><input type="checkbox" bind:checked={w.two_way} /><span><b>Both directions</b><span class="hint">Songs added on either side are added to the other.</span></span></label>
					<label class="opt"><input type="checkbox" bind:checked={w.sync_removals} /><span><b>Sync removals</b><span class="hint">A song removed on {w.two_way ? 'one side' : 'the source'} is removed on the other. Off: removals are never copied.</span></span></label>
					{#if w.sync_removals && targetNoRemove}<p class="pill warn">Apple Music cannot remove songs through its API, those removals are only listed.</p>{/if}
				</div>
			{/if}
			<label class="opt"><input type="checkbox" bind:checked={w.add_uncertain} /><span><b>Also add uncertain matches</b><span class="hint">Off: songs that only roughly match wait for your review.</span></span></label>
			{#if involvesYouTube}
				<label class="opt"><input type="checkbox" bind:checked={w.download} /><span><b>Download the YouTube playlist with Tubekeep</b><span class="hint">Keeps an audio copy on disk, like the other playlists.</span></span></label>
			{/if}
			<label class="field">Name (optional)<input bind:value={w.name} placeholder="{w.src.name ?? 'Playlist'} → …" /></label>
		</div>
	{/if}
	{#snippet footer()}
		{#if step > 1}<button onclick={() => step--}>Back</button>{/if}
		<span class="spacer"></span>
		{#if step === 1}
			<button class="primary" disabled={!w.src.id} onclick={() => step++}>Next</button>
		{:else if step === 2}
			<button class="primary" disabled={!w.b_conn_id || (w.a_conn_id === w.b_conn_id && w.dst.id === w.src.id)} onclick={() => step++}>Next</button>
		{:else}
			<button class="primary" onclick={createTransfer} disabled={busy}>{#if busy}<Loader size={15} class="spin" />{/if} Start transfer</button>
		{/if}
	{/snippet}
</Modal>

<style>
	.conns {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
		gap: 12px;
	}
	.conn {
		padding: 14px;
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.conn.add {
		border-style: dashed;
		box-shadow: none;
	}
	.addgrid {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 6px;
	}
	.addgrid button {
		justify-content: flex-start;
	}
	.tlist {
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.tr {
		padding: 14px 16px;
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.tr:hover {
		border-color: var(--line-2);
	}
	.flow,
	.summary {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
		gap: 14px;
		align-items: center;
	}
	.side {
		display: flex;
		flex-direction: column;
		gap: 4px;
		min-width: 0;
	}
	.arrow {
		color: var(--text-3);
		display: grid;
		place-items: center;
	}
	.errtext {
		color: var(--err);
	}
	.steps {
		padding-left: 20px;
		display: flex;
		flex-direction: column;
		gap: 8px;
		color: var(--text-2);
		margin: 0 0 10px;
	}
	.steps a {
		color: var(--accent);
	}
	.copy {
		display: block;
		margin-top: 4px;
		padding: 6px 8px;
		background: var(--bg-3);
		border-radius: 6px;
		user-select: all;
		word-break: break-all;
	}
	.tidal {
		margin-top: 12px;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.summary {
		padding: 12px;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		background: var(--bg);
		margin-bottom: 14px;
	}
	.opts {
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.opt {
		display: flex;
		gap: 10px;
		align-items: flex-start;
		cursor: pointer;
	}
	.opt input {
		margin-top: 2px;
	}
	.opt > span {
		display: flex;
		flex-direction: column;
	}
	.indent {
		padding-left: 26px;
		gap: 12px;
	}
	@media (max-width: 560px) {
		.flow,
		.summary {
			grid-template-columns: 1fr;
		}
		.arrow {
			justify-content: start;
		}
	}
</style>
