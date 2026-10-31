<script lang="ts">
	import { Search, Loader, Plus, Heart } from '@lucide/svelte';
	import { get } from '../api';
	import { errorToast } from '../state.svelte';

	let {
		connId,
		value = $bindable<{ id: string | null; name: string | null }>({ id: null, name: null }),
		allowNew = false,
		onlyEditable = false
	}: { connId: number | null; value?: { id: string | null; name: string | null }; allowNew?: boolean; onlyEditable?: boolean } = $props();

	let lists = $state<any[] | null>(null);
	let loading = $state(false);
	let q = $state('');
	let url = $state('');
	let mode = $state<'list' | 'new' | 'url'>(allowNew ? 'new' : 'list');
	let newName = $state('');

	$effect(() => {
		const id = connId;
		lists = null;
		if (!id) return;
		loading = true;
		get(`/connections/${id}/playlists`)
			.then((r) => (lists = r))
			.catch((e) => {
				errorToast(e);
				lists = [];
			})
			.finally(() => (loading = false));
	});
	$effect(() => {
		if (mode === 'new') value = { id: null, name: newName || null };
		if (mode === 'url') value = { id: url.trim() || null, name: null };
	});
	const shown = $derived((lists ?? []).filter((p) => (!onlyEditable || p.editable) && (!q || (p.name ?? '').toLowerCase().includes(q.toLowerCase()))));
</script>

<div class="tabs">
	{#if allowNew}<button class:active={mode === 'new'} onclick={() => (mode = 'new')}><Plus size={14} /> New playlist</button>{/if}
	<button class:active={mode === 'list'} onclick={() => (mode = 'list')}>{allowNew ? 'Existing playlist' : 'Your playlists'}</button>
	<button class:active={mode === 'url'} onclick={() => (mode = 'url')}>Link / ID</button>
</div>

{#if mode === 'new'}
	<label class="field">Name of the new playlist<input bind:value={newName} placeholder="Same name as the source" /></label>
{:else if mode === 'url'}
	<label class="field">Playlist link or ID<input class="mono" bind:value={url} placeholder="https://…" /></label>
{:else}
	<div class="search"><Search size={15} /><input type="search" bind:value={q} placeholder="Filter playlists" /></div>
	<div class="list">
		{#if loading}
			<div class="empty small"><Loader size={16} class="spin" /> Loading playlists…</div>
		{:else}
			{#each shown as p (p.id)}
				<button class="pl" class:sel={value.id === p.id} onclick={() => (value = { id: p.id, name: p.name })}>
					{#if p.image}<img src={p.image} alt="" />{:else}<span class="ph">{#if p.id === '__liked__'}<Heart size={14} />{/if}</span>{/if}
					<span class="grow ellipsis">{p.name}</span>
					{#if p.count != null}<span class="faint small">{p.count}</span>{/if}
				</button>
			{:else}
				<div class="empty small">No playlists</div>
			{/each}
		{/if}
	</div>
{/if}

<style>
	.tabs {
		display: flex;
		gap: 4px;
		margin-bottom: 8px;
		flex-wrap: wrap;
	}
	.tabs button {
		height: 28px;
		font-size: 13px;
		border-radius: 99px;
	}
	.tabs button.active {
		background: var(--text);
		color: var(--bg);
		border-color: var(--text);
	}
	.search {
		position: relative;
		display: flex;
		align-items: center;
		margin-bottom: 6px;
	}
	.search :global(svg) {
		position: absolute;
		left: 10px;
		color: var(--text-3);
	}
	.search input {
		padding-left: 32px;
	}
	.list {
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		max-height: 260px;
		overflow: auto;
	}
	.pl {
		width: 100%;
		justify-content: flex-start;
		border: none;
		border-radius: 0;
		height: 42px;
		gap: 10px;
		font-weight: 450;
		border-bottom: 1px solid var(--line);
		background: transparent;
	}
	.pl.sel {
		background: var(--accent-soft);
		color: var(--accent);
		font-weight: 600;
	}
	.pl img,
	.ph {
		width: 28px;
		height: 28px;
		border-radius: 5px;
		object-fit: cover;
		background: var(--bg-3);
		display: grid;
		place-items: center;
		flex-shrink: 0;
	}
</style>
