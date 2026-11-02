<script lang="ts">
	import { ChevronRight, FileAudio, Trash2, Loader } from '@lucide/svelte';
	import { SvelteSet } from 'svelte/reactivity';
	import { get, post } from '../api';
	import { ago, bytes } from '../format';
	import { errorToast, on, toast } from '../state.svelte';
	import Modal from './Modal.svelte';

	type Extra = {
		path: string;
		name: string;
		size: number;
		mtime: number;
		title: string;
		artist: string | null;
		item_id: number | null;
		removed_at: number | null;
		video_id: string | null;
	};

	let { playlistId }: { playlistId: number } = $props();

	let files = $state<Extra[]>([]);
	let open = $state(false);
	let confirmDelete = $state(false);
	let deleting = $state(false);
	const selected = new SvelteSet<string>();
	const total = $derived(files.reduce((n, f) => n + f.size, 0));
	const allSelected = $derived(files.length > 0 && files.every((f) => selected.has(f.path)));

	export async function reload() {
		try {
			files = await get<Extra[]>(`/playlists/${playlistId}/extra-files`);
			for (const p of [...selected]) if (!files.some((f) => f.path === p)) selected.delete(p);
		} catch (e) {
			errorToast(e);
		}
	}
	$effect(() => {
		void playlistId;
		selected.clear();
		reload();
	});
	$effect(() => on('playlist', (d) => d.id === playlistId && reload()));

	function toggleAll() {
		if (allSelected) selected.clear();
		else files.forEach((f) => selected.add(f.path));
	}
	async function remove() {
		deleting = true;
		try {
			const r = await post(`/playlists/${playlistId}/extra-files/delete`, { paths: [...selected] });
			toast(`Deleted ${r.deleted} file${r.deleted === 1 ? '' : 's'}`, 'ok');
			confirmDelete = false;
			selected.clear();
			reload();
		} catch (e) {
			errorToast(e);
		} finally {
			deleting = false;
		}
	}
</script>

{#if files.length}
	<div class="card extras">
		<button class="head" onclick={() => (open = !open)} aria-expanded={open}>
			<ChevronRight size={16} class={open ? 'rot' : ''} />
			<span class="grow">In the folder, not on YouTube</span>
			<span class="small muted">{files.length} file{files.length === 1 ? '' : 's'} · {bytes(total)}</span>
		</button>
		{#if open}
			<p class="small muted note">Audio files in this playlist's folder that no track of the YouTube playlist uses: tracks that left the playlist and were kept, and files Tubekeep didn't download.</p>
			{#if selected.size}
				<div class="bar">
					<b class="small">{selected.size} selected</b>
					<span class="spacer"></span>
					<button class="sm danger" onclick={() => (confirmDelete = true)}><Trash2 size={14} /> Delete files</button>
				</div>
			{/if}
			<div class="table-wrap">
				<table class="tbl">
					<thead>
						<tr>
							<th style="width: 36px"><input type="checkbox" checked={allSelected} onchange={toggleAll} /></th>
							<th>File</th>
							<th>Why it's here</th>
							<th class="hide-sm" style="text-align: right">Size</th>
						</tr>
					</thead>
					<tbody>
						{#each files as f (f.path)}
							<tr class:sel={selected.has(f.path)}>
								<td><input type="checkbox" checked={selected.has(f.path)} onchange={() => (selected.has(f.path) ? selected.delete(f.path) : selected.add(f.path))} /></td>
								<td class="track">
									<span class="t ellipsis" title={f.title}><FileAudio size={13} /> {f.title}{#if f.artist}<span class="muted a"> · {f.artist}</span>{/if}</span>
									<span class="ellipsis faint small mono" title={f.path}>{f.name}</span>
								</td>
								<td class="small muted">
									{#if f.removed_at}Removed from the playlist {ago(f.removed_at)}{:else}Not from Tubekeep{#if f.video_id} <a href="https://www.youtube.com/watch?v={f.video_id}" target="_blank" rel="noreferrer">(video)</a>{/if}{/if}
								</td>
								<td class="hide-sm small muted" style="text-align: right">{bytes(f.size)}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	</div>
{/if}

<Modal bind:open={confirmDelete} title="Delete {selected.size} file{selected.size === 1 ? '' : 's'}?" width="440px">
	<p>The files are deleted from disk. Removed tracks whose file is deleted disappear from Tubekeep. The YouTube playlist isn't changed.</p>
	{#snippet footer()}
		<button onclick={() => (confirmDelete = false)}>Cancel</button>
		<button class="danger solid" onclick={remove} disabled={deleting}>{#if deleting}<Loader size={14} class="spin" />{/if} Delete files</button>
	{/snippet}
</Modal>

<style>
	.extras {
		margin-top: 16px;
		overflow: hidden;
	}
	.head {
		all: unset;
		box-sizing: border-box;
		display: flex;
		align-items: center;
		gap: 8px;
		width: 100%;
		padding: 12px 14px;
		cursor: pointer;
		font-weight: 560;
	}
	.head:hover {
		background: var(--bg-3);
	}
	.head :global(svg) {
		transition: transform 0.15s;
		color: var(--text-3);
	}
	.head :global(svg.rot) {
		transform: rotate(90deg);
	}
	.note {
		margin: 0;
		padding: 0 14px 10px;
	}
	.bar {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 6px 14px;
		border-top: 1px solid var(--line);
	}
	.table-wrap {
		border-top: 1px solid var(--line);
	}
	tr.sel td {
		background: color-mix(in srgb, var(--accent-soft) 60%, transparent);
	}
	.track {
		max-width: 0;
		width: 60%;
	}
	.track > span {
		display: block;
	}
	.t {
		font-weight: 560;
	}
	.a {
		font-weight: 400;
	}
	.t :global(svg) {
		vertical-align: -2px;
		color: var(--text-3);
	}
	@media (max-width: 720px) {
		.hide-sm {
			display: none;
		}
	}
</style>
