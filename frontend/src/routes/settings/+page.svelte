<script lang="ts">
	import { Save, RefreshCw, Loader, Trash2, Plus } from '@lucide/svelte';
	import { del, get, post, put } from '#lib/api.ts';
	import { ago } from '#lib/format.ts';
	import { errorToast, session, toast } from '#lib/state.svelte.ts';
	import FolderPicker from '#lib/components/FolderPicker.svelte';
	import SchedulePicker from '#lib/components/SchedulePicker.svelte';

	let s = $state<any>(null);
	let orig = $state('');
	let sys = $state<any>(null);
	let accounts = $state<any[]>([]);
	let users = $state<any[]>([]);
	let models = $state<any[] | null>(null);
	let modelQuery = $state('');
	let newKey = $state('');
	let busy = $state(false);
	let updating = $state(false);
	let pw = $state({ current: '', new: '', repeat: '' });
	let newUser = $state({ username: '', password: '' });

	async function load() {
		try {
			[s, sys, accounts, users] = await Promise.all([get('/settings'), get('/system'), get('/accounts'), get('/users')]);
			orig = JSON.stringify(s);
		} catch (e) {
			errorToast(e);
		}
	}
	load();

	const dirty = $derived(s && (JSON.stringify(s) !== orig || newKey !== ''));

	async function save() {
		busy = true;
		try {
			const body: any = { ...s };
			if (newKey) body.openrouter_key = newKey;
			for (const k of ['concurrency', 'max_attempts', 'sleep_interval', 'max_sleep_interval', 'sleep_requests', 'ytdlp_check_hours', 'account_check_hours', 'ai_batch_size'])
				body[k] = Number(body[k]) || 0;
			s = await put('/settings', body);
			orig = JSON.stringify(s);
			newKey = '';
			toast('Settings saved', 'ok');
		} catch (e) {
			errorToast(e);
		} finally {
			busy = false;
		}
	}
	async function clearKey() {
		try {
			s = await put('/settings', { openrouter_key: '' });
			orig = JSON.stringify(s);
			toast('API key removed');
		} catch (e) {
			errorToast(e);
		}
	}
	async function updateYtdlp(force = false) {
		updating = true;
		try {
			const r = await post(`/system/ytdlp/update?force=${force}`);
			toast(`yt-dlp: ${r.last_result}`, 'ok');
			sys = await get('/system');
		} catch (e) {
			errorToast(e);
		} finally {
			updating = false;
		}
	}
	async function loadModels() {
		try {
			models = await get('/ai/models');
		} catch (e) {
			errorToast(e);
		}
	}
	const filteredModels = $derived(
		(models ?? []).filter((m) => !modelQuery || m.id.toLowerCase().includes(modelQuery.toLowerCase()) || m.name.toLowerCase().includes(modelQuery.toLowerCase())).slice(0, 60)
	);
	async function changePw(e: Event) {
		e.preventDefault();
		if (pw.new !== pw.repeat) return toast('New passwords do not match', 'error');
		try {
			await post('/auth/password', { current: pw.current, new: pw.new });
			pw = { current: '', new: '', repeat: '' };
			toast('Password changed', 'ok');
		} catch (err) {
			errorToast(err);
		}
	}
	async function addUser(e: Event) {
		e.preventDefault();
		try {
			await post('/users', newUser);
			newUser = { username: '', password: '' };
			users = await get('/users');
		} catch (err) {
			errorToast(err);
		}
	}
	async function removeUser(u: any) {
		if (!confirm(`Delete user ${u.username}?`)) return;
		try {
			await del(`/users/${u.id}`);
			users = await get('/users');
		} catch (err) {
			errorToast(err);
		}
	}
</script>

<div class="page-head">
	<h1>Settings</h1>
	<span class="spacer"></span>
	{#if s}<button class="primary" onclick={save} disabled={!dirty || busy}><Save size={15} /> Save changes</button>{/if}
</div>

{#if s}
	<div class="sections">
		<section class="card pad">
			<h2>Downloads</h2>
			<p class="hint">Defaults for every playlist. A playlist can override them in its own settings.</p>
			<div class="grid3">
				<label class="field">
					Audio format
					<select bind:value={s.audio_format}>
						<option value="mp3">mp3</option><option value="m4a">m4a</option><option value="opus">opus</option><option value="flac">flac</option><option value="best">Original (no conversion)</option>
					</select>
				</label>
				<label class="field">Quality<input bind:value={s.audio_quality} placeholder="0 = best VBR, or 320K" /></label>
				<label class="field">Parallel downloads<input type="number" min="1" max="16" bind:value={s.concurrency} /></label>
			</div>
			<label class="field">
				File name template (yt-dlp syntax)
				<input class="mono" bind:value={s.filename_template} />
				<span class="hint">Default <code>%(channel)s - %(title)s</code> matches the old script. Subfolders work, e.g. <code>%(channel)s/%(title)s</code>.</span>
			</label>
			<div class="checks">
				<label class="check"><input type="checkbox" bind:checked={s.embed_thumbnail} /> Embed the thumbnail as cover</label>
				<label class="check"><input type="checkbox" bind:checked={s.square_cover} /> Crop covers to a square</label>
				<label class="check"><input type="checkbox" bind:checked={s.artist_from_channel} /> Artist tag = YouTube Music artist, else channel name</label>
				<label class="check"><input type="checkbox" bind:checked={s.sponsorblock_music_offtopic} /> Cut non-music parts (SponsorBlock)</label>
			</div>
			<label class="field">Extra yt-dlp arguments<input class="mono" bind:value={s.extra_args} placeholder="e.g. --match-filter &quot;duration < 900&quot;" /></label>
		</section>

		<section class="card pad">
			<h2>Library and schedule</h2>
			<div class="grid2">
				<label class="field">
					Default download root
					<FolderPicker bind:value={s.default_folder_root} placeholder={sys?.downloads_dir} />
					<span class="hint">New playlists go to <code>&lt;root&gt;/&lt;Playlist_Name&gt;</code>. Empty uses {sys?.downloads_dir}.</span>
				</label>
				<label class="field">Default schedule for new playlists<SchedulePicker bind:value={s.default_schedule} /></label>
			</div>
			<div class="grid3">
				<label class="field">Attempts per track and sync<input type="number" min="1" max="10" bind:value={s.max_attempts} /></label>
				<label class="field">
					Default account for new playlists
					<select bind:value={s.default_account_id}>
						<option value={null}>None</option>
						{#each accounts as a (a.id)}<option value={a.id}>{a.label}</option>{/each}
					</select>
				</label>
				<label class="field">Check accounts every (h)<input type="number" min="0" bind:value={s.account_check_hours} /></label>
			</div>
		</section>

		<section class="card pad">
			<h2>Throttling</h2>
			<p class="hint">Slower downloads look less like a bot. Combine with proxies for big libraries.</p>
			<div class="grid2">
				<label class="field">Wait before each download, min (s)<input type="number" min="0" bind:value={s.sleep_interval} /></label>
				<label class="field">Wait before each download, max (s)<input type="number" min="0" bind:value={s.max_sleep_interval} /></label>
				<label class="field">Wait between requests (s)<input type="number" min="0" step="0.5" bind:value={s.sleep_requests} /></label>
				<label class="field">Bandwidth limit<input bind:value={s.limit_rate} placeholder="e.g. 2M, empty = unlimited" /></label>
			</div>
		</section>

		<section class="card pad">
			<div class="row"><h2 class="grow">yt-dlp</h2>
				<button onclick={() => updateYtdlp(false)} disabled={updating}>{#if updating}<Loader size={15} class="spin" />{:else}<RefreshCw size={15} />{/if} Check for update</button>
			</div>
			{#if sys}
				<div class="kvs">
					<div><span>Installed</span><code>{sys.ytdlp.version ?? '—'}</code></div>
					<div><span>Latest ({s.ytdlp_channel})</span><code>{sys.ytdlp.latest ?? 'unknown'}</code></div>
					<div><span>Last check</span><span>{ago(sys.ytdlp.last_check)}{sys.ytdlp.last_result ? ` · ${sys.ytdlp.last_result}` : ''}</span></div>
					<div><span>ffmpeg</span><code class="ellipsis">{sys.ffmpeg ?? 'not found'}</code></div>
					<div><span>Deno (JS runtime)</span><code class="ellipsis">{sys.deno ?? 'not found'}</code></div>
				</div>
			{/if}
			<div class="grid3">
				<label class="field">
					Channel
					<select bind:value={s.ytdlp_channel}><option value="stable">Stable</option><option value="nightly">Nightly (fixes land faster)</option></select>
				</label>
				<label class="field">Check every (h)<input type="number" min="1" bind:value={s.ytdlp_check_hours} /></label>
				<label class="check" style="margin-top: 22px"><input type="checkbox" bind:checked={s.ytdlp_auto_update} /> Update automatically</label>
			</div>
			<p class="hint">Updates install next to the running version, so downloads in progress are not interrupted. The binary lives in the config volume and survives container restarts.</p>
		</section>

		<section class="card pad">
			<h2>AI metadata (OpenRouter)</h2>
			<div class="grid2">
				<label class="field">
					API key
					{#if s.openrouter_key_set && !newKey}
						<div class="row"><span class="pill ok">stored encrypted</span><span class="spacer"></span><button class="sm" onclick={clearKey}>Remove</button></div>
						<input type="password" bind:value={newKey} placeholder="Paste a new key to replace it" />
					{:else}
						<input type="password" bind:value={newKey} placeholder="sk-or-…" />
					{/if}
					<span class="hint">Create one at openrouter.ai/keys.</span>
				</label>
				<label class="field">
					Model
					<input class="mono" bind:value={s.openrouter_model} />
					<span class="hint">Cheap and fast works well, e.g. anthropic/claude-haiku-5.5. <button class="ghost sm" style="padding: 0; height: auto" onclick={loadModels}>Browse models</button></span>
				</label>
			</div>
			{#if models}
				<input type="search" bind:value={modelQuery} placeholder="Filter models…" style="margin-top: 10px" />
				<div class="models">
					{#each filteredModels as m (m.id)}
						<button class="ghost model" class:sel={m.id === s.openrouter_model} onclick={() => (s.openrouter_model = m.id)}>
							<span class="mono ellipsis grow">{m.id}</span>
							<span class="faint small">{m.prompt_per_m != null ? `$${m.prompt_per_m.toFixed(2)}/M in` : ''}</span>
						</button>
					{/each}
				</div>
			{/if}
			<label class="field" style="max-width: 240px; margin-top: 12px">Tracks per AI request<input type="number" min="1" max="100" bind:value={s.ai_batch_size} /></label>
		</section>

		<section class="card pad">
			<h2>Notifications</h2>
			<label class="field">
				Webhook URL
				<input class="mono" bind:value={s.notify_webhook_url} placeholder="https://ntfy.sh/my-topic or any JSON webhook" />
				<span class="hint">Called when a scheduled sync fails or an account needs fresh cookies. ntfy URLs get a plain text message, others a JSON body with title and message.</span>
			</label>
		</section>

		<section class="card pad">
			<h2>Users</h2>
			<div class="grid2">
				<form class="col" onsubmit={changePw}>
					<h3>Change your password ({session.user?.username})</h3>
					<input type="password" bind:value={pw.current} placeholder="Current password" autocomplete="current-password" required />
					<input type="password" bind:value={pw.new} placeholder="New password (min. 8)" autocomplete="new-password" required minlength="8" />
					<input type="password" bind:value={pw.repeat} placeholder="Repeat new password" autocomplete="new-password" required />
					<button type="submit" style="align-self: flex-start">Change password</button>
				</form>
				<div class="col">
					<h3>Local users</h3>
					{#each users as u (u.id)}
						<div class="row"><span class="grow">{u.username}</span>{#if u.id !== session.user?.id}<button class="ghost icon sm danger" onclick={() => removeUser(u)}><Trash2 size={14} /></button>{:else}<span class="pill">you</span>{/if}</div>
					{/each}
					<form class="row" onsubmit={addUser}>
						<input bind:value={newUser.username} placeholder="Username" required />
						<input type="password" bind:value={newUser.password} placeholder="Password" required minlength="8" />
						<button type="submit" class="icon" title="Add user"><Plus size={15} /></button>
					</form>
				</div>
			</div>
		</section>
	</div>

	{#if dirty}
		<div class="savebar card">
			<span class="grow">You have unsaved changes</span>
			<button onclick={() => { s = JSON.parse(orig); newKey = ''; }}>Discard</button>
			<button class="primary" onclick={save} disabled={busy}><Save size={15} /> Save</button>
		</div>
	{/if}
{/if}

<style>
	.sections {
		display: flex;
		flex-direction: column;
		gap: 16px;
		padding-bottom: 60px;
	}
	section {
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.checks {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 8px 16px;
	}
	.kvs {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
		gap: 6px 16px;
		background: var(--bg);
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		padding: 10px 12px;
	}
	.kvs div {
		display: flex;
		justify-content: space-between;
		gap: 10px;
		min-width: 0;
	}
	.kvs div span:first-child {
		color: var(--text-3);
	}
	.models {
		max-height: 240px;
		overflow: auto;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		margin-top: 6px;
	}
	.model {
		width: 100%;
		justify-content: flex-start;
		border-radius: 0;
		height: 30px;
		font-weight: 400;
	}
	.model.sel {
		background: var(--accent-soft);
	}
	.savebar {
		position: fixed;
		bottom: 16px;
		left: 50%;
		transform: translateX(-50%);
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 10px 14px;
		width: min(560px, calc(100vw - 32px));
		box-shadow: var(--shadow-lg);
		z-index: 30;
	}
	@media (max-width: 720px) {
		.checks {
			grid-template-columns: 1fr;
		}
	}
</style>
