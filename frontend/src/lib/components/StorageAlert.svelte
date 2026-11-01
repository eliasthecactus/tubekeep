<script lang="ts">
	import { HardDrive, RefreshCw, Download, FileX, Loader } from '@lucide/svelte';
	import { post } from '../api';
	import { errorToast, toast } from '../state.svelte';
	import Modal from './Modal.svelte';

	type Pl = { id: number; title: string | null; url: string; folder: string | null; storage_offline_at: number | null };
	let { playlists, onchanged }: { playlists: Pl[]; onchanged?: () => void } = $props();

	const single = $derived(playlists.length === 1);
	let busy = $state('');
	let confirm = $state<'' | 'redownload' | 'keep_missing'>('');

	async function run(action: 'check' | 'redownload' | 'keep_missing') {
		busy = action;
		confirm = '';
		try {
			const r = single
				? { results: [await post(`/playlists/${playlists[0].id}/storage`, { action })] }
				: await post('/storage/offline', { action, playlist_ids: playlists.map((p) => p.id) });
			const still = r.results.filter((x: any) => x.offline).length;
			if (action === 'check') {
				toast(still ? `Still missing: ${still} folder${still === 1 ? '' : 's'}` : 'Folder found, syncing again', still ? 'error' : 'ok');
			} else {
				toast(action === 'redownload' ? 'Downloading the missing files again' : 'Missing files are kept as missing', 'ok');
			}
			onchanged?.();
		} catch (e) {
			errorToast(e);
		} finally {
			busy = '';
		}
	}
	const what = $derived(single ? 'this playlist' : `these ${playlists.length} playlists`);
</script>

<div class="alert">
	<div class="head">
		<HardDrive size={18} />
		<div class="grow col" style="gap: 4px; min-width: 0">
			{#if single}
				<b>The download folder is missing</b>
				<span class="small">Tubekeep can't find its marker file in <span class="mono">{playlists[0].folder}</span>. Usually the drive isn't mounted. Syncs of this playlist are paused, nothing was downloaded and nothing was marked as missing.</span>
			{:else}
				<b>{playlists.length} playlists can't find their download folder</b>
				<span class="small">Tubekeep can't find its marker file in their folders. Usually the drive isn't mounted. Their syncs are paused, nothing was downloaded and nothing was marked as missing.</span>
				<span class="small names">
					{#each playlists as p, i (p.id)}<a href="/playlists/{p.id}">{p.title ?? p.url}</a>{i < playlists.length - 1 ? ', ' : ''}{/each}
				</span>
			{/if}
		</div>
	</div>
	<div class="choices">
		<button class="primary sm" onclick={() => run('check')} disabled={!!busy} title="Mount the drive first, then check again. Syncing continues by itself once the folder is back.">
			{#if busy === 'check'}<Loader size={14} class="spin" />{:else}<RefreshCw size={14} />{/if} I mounted it, check again
		</button>
		<button class="sm" onclick={() => (confirm = 'redownload')} disabled={!!busy}><Download size={14} /> Download everything again here</button>
		<button class="sm" onclick={() => (confirm = 'keep_missing')} disabled={!!busy}><FileX size={14} /> Keep the files as missing</button>
	</div>
	<span class="small faint">Doing nothing is fine too: Tubekeep checks again at every scheduled sync and continues as soon as the folder is back.</span>
</div>

<Modal open={!!confirm} onclose={() => (confirm = '')} title={confirm === 'redownload' ? 'Download everything again?' : 'Keep the files as missing?'} width="480px">
	{#if confirm === 'redownload'}
		<p>Tubekeep creates the folder{single ? '' : 's'} again where {single ? 'it was' : 'they were'} and downloads every missing track of {what} again.</p>
		<p class="small muted" style="margin-top: 8px">If the drive is only unmounted, the files land on the disk below the mount point instead of on the drive.</p>
	{:else}
		<p>Tubekeep accepts the new, empty folder{single ? '' : 's'}. The tracks of {what} are marked as missing and are <b>not</b> downloaded again. You can still download single tracks later.</p>
	{/if}
	{#snippet footer()}
		<button onclick={() => (confirm = '')}>Cancel</button>
		<button class="primary" onclick={() => confirm && run(confirm)}>{confirm === 'redownload' ? 'Download again' : 'Keep as missing'}</button>
	{/snippet}
</Modal>

<style>
	.alert {
		display: flex;
		flex-direction: column;
		gap: 10px;
		background: var(--warn-soft);
		border: 1px solid color-mix(in srgb, var(--warn) 35%, transparent);
		border-radius: var(--radius);
		padding: 12px 14px;
		margin-bottom: 16px;
	}
	.head {
		display: flex;
		gap: 10px;
		align-items: flex-start;
	}
	.head > :global(svg) {
		color: var(--warn);
		flex-shrink: 0;
		margin-top: 2px;
	}
	.mono {
		word-break: break-all;
	}
	.names a {
		text-decoration: underline;
		text-decoration-color: color-mix(in srgb, currentColor 40%, transparent);
	}
	.choices {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}
</style>
