<script lang="ts">
	import FolderPicker from './FolderPicker.svelte';
	import SchedulePicker from './SchedulePicker.svelte';

	let {
		v = $bindable(),
		mode = 'add',
		accounts = [],
		proxies = [],
		originalFolder = ''
	}: {
		v: Record<string, any>;
		mode?: 'add' | 'edit';
		accounts?: { id: number; label: string; status: string }[];
		proxies?: { id: number; label: string; url: string }[];
		originalFolder?: string;
	} = $props();

	let advanced = $state(false);
	const urlCount = $derived(((v.urlsText as string) ?? '').split('\n').filter((l) => l.trim()).length);

	function boolOpt(key: string) {
		const x = v.options?.[key];
		return x === true ? 'yes' : x === false ? 'no' : '';
	}
	function setBool(key: string, val: string) {
		v.options = { ...v.options };
		if (val === '') delete v.options[key];
		else v.options[key] = val === 'yes';
	}
	function setOpt(key: string, val: string) {
		v.options = { ...v.options };
		if (!val) delete v.options[key];
		else v.options[key] = val;
	}
</script>

<div class="col" style="gap: 14px">
	{#if mode === 'add'}
		<label class="field">
			Playlist URLs
			<textarea class="mono" rows="4" bind:value={v.urlsText} placeholder="https://www.youtube.com/playlist?list=PL…&#10;One per line, also works with your old playlists.txt"></textarea>
		</label>
	{:else}
		<label class="field">Playlist URL<input class="mono" bind:value={v.url} /></label>
		<label class="field">Display name<input bind:value={v.title} /></label>
	{/if}

	<label class="field">
		Download folder
		<FolderPicker bind:value={v.folder} placeholder={mode === 'add' ? 'Default: <downloads>/<Playlist_Name>' : ''} />
		{#if mode === 'add'}
			<span class="hint">{urlCount > 1 ? 'With several URLs every playlist gets its own folder below the default root.' : 'Leave empty to use the default root plus the playlist name (same as the old script). Existing files in the folder are picked up, not downloaded again.'}</span>
		{/if}
	</label>
	{#if mode === 'edit' && v.folder && v.folder !== originalFolder}
		<label class="check"><input type="checkbox" bind:checked={v.move_files} /> Move already downloaded files to the new folder</label>
	{/if}

	<div class="grid2">
		<label class="field">
			YouTube account
			<select bind:value={v.account_id}>
				<option value={null}>None (public playlist)</option>
				{#each accounts as a (a.id)}<option value={a.id}>{a.label}{a.status === 'expired' ? ' (expired)' : ''}</option>{/each}
			</select>
			<span class="hint">Needed for private playlists, Liked videos and Watch later.</span>
		</label>
		<label class="field">
			Sync schedule
			<SchedulePicker bind:value={v.schedule} />
		</label>
	</div>

	<div class="grid2">
		<label class="field">
			Proxy
			<select bind:value={v.proxy_mode}>
				<option value="default">Default (global strategy)</option>
				<option value="pool">Always use proxy pool</option>
				<option value="fixed">One fixed proxy</option>
				<option value="none">Never use a proxy</option>
			</select>
			{#if v.account_id}<span class="hint">With an account, the account's pinned proxy is used instead.</span>{/if}
		</label>
		{#if v.proxy_mode === 'fixed'}
			<label class="field">
				Fixed proxy
				<select bind:value={v.proxy_id}>
					<option value={null}>Choose…</option>
					{#each proxies as p (p.id)}<option value={p.id}>{p.label} ({p.url})</option>{/each}
				</select>
			</label>
		{:else}
			<label class="field">
				When a video is removed from the playlist
				<select bind:value={v.on_removed}>
					<option value="keep">Keep the file</option>
					<option value="delete">Delete the file</option>
				</select>
			</label>
		{/if}
	</div>
	{#if v.proxy_mode === 'fixed'}
		<label class="field" style="max-width: 50%">
			When a video is removed from the playlist
			<select bind:value={v.on_removed}>
				<option value="keep">Keep the file</option>
				<option value="delete">Delete the file</option>
			</select>
		</label>
	{/if}

	<div class="row wrap" style="gap: 18px">
		<label class="check"><input type="checkbox" bind:checked={v.redownload_missing} /> Download again when a file is missing on disk</label>
		<label class="check"><input type="checkbox" bind:checked={v.enabled} /> Scheduled syncs enabled</label>
		{#if mode === 'add'}<label class="check"><input type="checkbox" bind:checked={v.sync_now} /> Sync right away</label>{/if}
	</div>

	<button class="ghost sm" style="align-self: flex-start; padding: 0" onclick={() => (advanced = !advanced)}>
		{advanced ? '▾' : '▸'} Download options for this playlist
	</button>
	{#if advanced}
		<div class="adv">
			<p class="hint">Empty means the global setting is used.</p>
			<div class="grid3">
				<label class="field">
					Audio format
					<select value={v.options?.audio_format ?? ''} onchange={(e) => setOpt('audio_format', e.currentTarget.value)}>
						<option value="">Default</option>
						<option value="mp3">mp3</option>
						<option value="m4a">m4a</option>
						<option value="opus">opus</option>
						<option value="flac">flac</option>
						<option value="best">Original (no conversion)</option>
					</select>
				</label>
				<label class="field">
					Quality
					<input value={v.options?.audio_quality ?? ''} oninput={(e) => setOpt('audio_quality', e.currentTarget.value)} placeholder="0 = best, or 320K" />
				</label>
				<label class="field">
					Square cover
					<select value={boolOpt('square_cover')} onchange={(e) => setBool('square_cover', e.currentTarget.value)}>
						<option value="">Default</option><option value="yes">Yes</option><option value="no">No</option>
					</select>
				</label>
			</div>
			<label class="field">
				File name template
				<input class="mono" value={v.options?.filename_template ?? ''} oninput={(e) => setOpt('filename_template', e.currentTarget.value)} placeholder="%(channel)s - %(title)s" />
			</label>
			<div class="grid3">
				<label class="field">
					Embed cover
					<select value={boolOpt('embed_thumbnail')} onchange={(e) => setBool('embed_thumbnail', e.currentTarget.value)}>
						<option value="">Default</option><option value="yes">Yes</option><option value="no">No</option>
					</select>
				</label>
				<label class="field">
					Artist from channel
					<select value={boolOpt('artist_from_channel')} onchange={(e) => setBool('artist_from_channel', e.currentTarget.value)}>
						<option value="">Default</option><option value="yes">Yes</option><option value="no">No</option>
					</select>
				</label>
				<label class="field">
					Cut non-music parts
					<select value={boolOpt('sponsorblock_music_offtopic')} onchange={(e) => setBool('sponsorblock_music_offtopic', e.currentTarget.value)}>
						<option value="">Default</option><option value="yes">Yes (SponsorBlock)</option><option value="no">No</option>
					</select>
				</label>
			</div>
			<label class="field">
				Extra yt-dlp arguments
				<input class="mono" value={v.options?.extra_args ?? ''} oninput={(e) => setOpt('extra_args', e.currentTarget.value)} placeholder="--match-filter 'duration < 900'" />
			</label>
		</div>
	{/if}
</div>

<style>
	.adv {
		display: flex;
		flex-direction: column;
		gap: 12px;
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		padding: 12px;
		background: var(--bg);
	}
</style>
