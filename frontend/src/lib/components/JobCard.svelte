<script lang="ts">
	import { Square, Loader, ScrollText } from '@lucide/svelte';
	import { post } from '../api';
	import { speed } from '../format';
	import { errorToast, type LiveJob } from '../state.svelte';

	let { job, onlog }: { job: LiveJob; onlog?: (id: number) => void } = $props();
	const s = $derived(job.stats ?? {});
	const pct = $derived(
		s.to_download ? Math.round((100 * ((s.downloaded || 0) + (s.failed || 0) + (s.unavailable || 0))) / s.to_download) : null
	);

	async function cancel() {
		try {
			await post(`/jobs/${job.id}/cancel`);
		} catch (e) {
			errorToast(e);
		}
	}
</script>

<div class="job card">
	<div class="row">
		<Loader size={15} class={job.status === 'queued' ? '' : 'spin'} />
		<a class="grow ellipsis" href={job.playlist_id ? `/playlists/${job.playlist_id}` : (job as any).transfer_id ? `/transfers/${(job as any).transfer_id}` : '/activity'}><b>{job.title}</b></a>
		<span class="pill info">{job.status === 'queued' ? 'waiting' : job.phase}</span>
		{#if onlog}<button class="ghost icon sm" onclick={() => onlog(job.id)} title="Log"><ScrollText size={15} /></button>{/if}
		<button class="ghost icon sm" onclick={cancel} title="Cancel"><Square size={14} /></button>
	</div>
	{#if s.to_download}
		<div class="row small muted" style="margin-top: 6px">
			<span>{s.downloaded || 0} of {s.to_download} downloaded</span>
			{#if s.failed}<span class="err">· {s.failed} failed</span>{/if}
			{#if s.unavailable}<span>· {s.unavailable} unavailable</span>{/if}
			<span class="spacer"></span><span>{pct}%</span>
		</div>
		<div class="bar"><span style:width="{pct}%"></span></div>
	{:else if s.a_total != null}
		<div class="small muted" style="margin-top: 6px">
			{s.a_total} and {s.b_total} songs{s.progress ? ` · matched ${s.progress}` : ''}{s.added_to_b ? ` · ${s.added_to_b} added` : ''}{s.added_to_a ? ` · ${s.added_to_a} added back` : ''}
		</div>
	{:else if s.total}
		<div class="small muted" style="margin-top: 6px">{s.total} videos · {s.new ?? 0} new · {s.removed ?? 0} removed</div>
	{/if}
	{#each job.items ?? [] as it (it.item_id)}
		<div class="item">
			<span class="ellipsis grow small">{it.title}</span>
			<span class="small faint">{it.stage === 'downloading' ? `${Math.round(it.pct)}% ${speed(it.speed)}` : it.stage}</span>
			<div class="ibar"><span style:width="{it.pct || 0}%"></span></div>
		</div>
	{/each}
</div>

<style>
	.job {
		padding: 12px 14px;
		margin-bottom: 8px;
	}
	.bar,
	.ibar {
		height: 5px;
		background: var(--bg-3);
		border-radius: 9px;
		overflow: hidden;
		margin-top: 6px;
	}
	.bar span,
	.ibar span {
		display: block;
		height: 100%;
		background: var(--info);
		transition: width 0.4s;
	}
	.bar span {
		background: var(--ok);
	}
	.item {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto;
		column-gap: 10px;
		margin-top: 8px;
	}
	.ibar {
		grid-column: 1 / -1;
		margin-top: 3px;
		height: 3px;
	}
	.err {
		color: var(--err);
	}
</style>
