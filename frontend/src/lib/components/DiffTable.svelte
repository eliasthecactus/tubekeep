<script lang="ts">
	import { ArrowRight } from '@lucide/svelte';
	import { FIELD_LABELS } from '../format';
	import type { Proposal } from '../types';


	let { proposals = $bindable([]) }: { proposals: Proposal[] } = $props();
	let showUnchanged = $state(false);

	const changedRows = $derived(proposals.filter((p) => p.changed.length || p.rename_to));
	const rows = $derived(showUnchanged ? proposals : changedRows);
	const includedCount = $derived(changedRows.filter((p) => p.include !== false).length);

	function recompute(p: Proposal) {
		p.changed = Object.keys(p.after).filter((k) => (p.after[k] ?? '') !== (p.before[k] ?? ''));
	}
	function toggleAll(v: boolean) {
		for (const p of changedRows) p.include = v;
	}
</script>

<div class="row head">
	<label class="check">
		<input type="checkbox" checked={includedCount === changedRows.length && changedRows.length > 0} onchange={(e) => toggleAll(e.currentTarget.checked)} />
		<span><b>{includedCount}</b> of {changedRows.length} tracks with changes selected</span>
	</label>
	<span class="spacer"></span>
	<label class="check small muted"><input type="checkbox" bind:checked={showUnchanged} /> show unchanged ({proposals.length - changedRows.length})</label>
</div>

<div class="list">
	{#each rows as p (p.item_id)}
		<div class="prop" class:off={p.include === false}>
			<div class="row">
				<input type="checkbox" checked={p.include !== false} onchange={(e) => (p.include = e.currentTarget.checked)} disabled={!p.changed.length && !p.rename_to} />
				<b class="ellipsis grow" title={p.title}>{p.title}</b>
				{#if !p.changed.length && !p.rename_to}<span class="pill">no change</span>{/if}
			</div>
			{#each p.changed as f (f)}
				<div class="chg">
					<span class="lbl">{FIELD_LABELS[f] ?? f}</span>
					<span class="before ellipsis" title={p.before[f]}>{p.before[f] || '—'}</span>
					<ArrowRight size={13} class="faint" />
					<input class="after" bind:value={p.after[f]} oninput={() => recompute(p)} />
				</div>
			{/each}
			{#if p.rename_to}
				<div class="chg">
					<span class="lbl">File name</span>
					<span class="before ellipsis" title={p.file ?? ''}>{p.file?.split('/').pop()}</span>
					<ArrowRight size={13} class="faint" />
					<input class="after mono" bind:value={p.rename_to} />
				</div>
			{/if}
		</div>
	{:else}
		<div class="empty">No changes. Adjust the operations and preview again.</div>
	{/each}
</div>

<style>
	.head {
		margin-bottom: 8px;
	}
	.list {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.prop {
		border: 1px solid var(--line);
		border-radius: var(--radius-sm);
		padding: 8px 10px;
		display: flex;
		flex-direction: column;
		gap: 5px;
	}
	.prop.off {
		opacity: 0.5;
	}
	.chg {
		display: grid;
		grid-template-columns: 90px minmax(0, 1fr) 16px minmax(0, 1.3fr);
		align-items: center;
		gap: 8px;
		padding-left: 24px;
	}
	.lbl {
		font-size: 12px;
		color: var(--text-3);
		font-weight: 600;
	}
	.before {
		color: var(--text-3);
		text-decoration: line-through;
		text-decoration-color: color-mix(in srgb, var(--err) 50%, transparent);
	}
	.after {
		height: 28px;
		border-color: color-mix(in srgb, var(--ok) 45%, var(--line-2));
		background: color-mix(in srgb, var(--ok-soft) 50%, var(--bg-2));
	}
	@media (max-width: 640px) {
		.chg {
			grid-template-columns: 1fr;
			padding-left: 0;
		}
		.chg :global(svg) {
			display: none;
		}
	}
</style>
