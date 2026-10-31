<script lang="ts">
	import { page } from '$app/state';
	import { ScrollText, Trash2 } from '@lucide/svelte';
	import { del, get, post, qs } from '#lib/api.ts';
	import { datetime, ago } from '#lib/format.ts';
	import { errorToast, live, on, toast } from '#lib/state.svelte.ts';
	import JobCard from '#lib/components/JobCard.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import StatusBadge from '#lib/components/StatusBadge.svelte';

	let jobs = $state<any[]>([]);
	let total = $state(0);
	let status = $state('');
	let type = $state('');
	let logId = $state<number | null>(null);
	let logText = $state('');
	let logOpen = $state(false);
	let logEl: HTMLPreElement | undefined = $state();
	let follow = $state(true);

	async function load() {
		try {
			const r = await get('/jobs' + qs({ status, type, limit: 100 }));
			jobs = r.jobs;
			total = r.total;
		} catch (e) {
			errorToast(e);
		}
	}
	$effect(() => {
		void [status, type];
		load();
	});
	$effect(() => on('job', (j) => ['done', 'failed', 'cancelled', 'queued'].includes(j.status) && load()));

	async function openLog(id: number) {
		logId = id;
		logText = '';
		logOpen = true;
		try {
			logText = await get(`/jobs/${id}/log`);
		} catch (e) {
			errorToast(e);
		}
		scroll();
	}
	function scroll() {
		if (follow) requestAnimationFrame(() => logEl && (logEl.scrollTop = logEl.scrollHeight));
	}
	$effect(() =>
		on('log', (d) => {
			if (logOpen && d.job_id === logId) {
				logText += (logText ? '\n' : '') + d.line;
				scroll();
			}
		})
	);
	$effect(() => {
		const j = page.url.searchParams.get('job');
		if (j) openLog(Number(j));
	});

	async function clear() {
		try {
			await del('/jobs');
			load();
		} catch (e) {
			errorToast(e);
		}
	}
	function dur(j: any) {
		if (!j.started_at) return '';
		const s = Math.round((j.finished_at ?? Date.now() / 1000) - j.started_at);
		return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
	}
	function summary(s: Record<string, number>) {
		const parts = [];
		if (s.total != null) parts.push(`${s.total} videos`);
		if (s.new) parts.push(`${s.new} new`);
		if (s.downloaded) parts.push(`${s.downloaded} downloaded`);
		if (s.adopted) parts.push(`${s.adopted} linked`);
		if (s.missing) parts.push(`${s.missing} missing`);
		if (s.failed) parts.push(`${s.failed} failed`);
		if (s.removed) parts.push(`${s.removed} removed`);
		return parts.join(' · ');
	}
	const running = $derived(Object.values(live.jobs).filter((j) => j.status !== 'queued'));
	const waiting = $derived(Object.values(live.jobs).filter((j) => j.status === 'queued').sort((a, b) => a.id - b.id));
	async function cancelJob(id: number) {
		await post(`/jobs/${id}/cancel`).catch(errorToast);
	}
	async function cancelWaiting() {
		try {
			const r = await post('/jobs/cancel-waiting');
			toast(`Cancelled ${r.cancelled} waiting jobs`);
		} catch (e) {
			errorToast(e);
		}
	}
</script>

<div class="page-head"><h1>Activity</h1></div>

<section style="margin-bottom: 22px">
	<div class="section-title"><h2>Running</h2><span class="pill">{running.length}</span></div>
	{#each running as j (j.id)}
		<JobCard job={j} onlog={openLog} />
	{:else}
		<div class="card empty small">No jobs running</div>
	{/each}
</section>

{#if waiting.length}
	<section style="margin-bottom: 22px">
		<div class="section-title">
			<h2>Waiting</h2><span class="pill">{waiting.length}</span>
			<span class="spacer"></span>
			<button class="sm" onclick={cancelWaiting}>Cancel all waiting</button>
		</div>
		<div class="card waitlist">
			{#each waiting as j, i (j.id)}
				<div class="wrow">
					<span class="faint small">{i + 1}</span>
					<span class="grow ellipsis">{j.title}</span>
					<button class="ghost sm" onclick={() => cancelJob(j.id)}>Cancel</button>
				</div>
			{/each}
		</div>
	</section>
{/if}

<section>
	<div class="section-title">
		<h2>History</h2>
		<span class="pill">{total}</span>
		<span class="spacer"></span>
		<select bind:value={type} style="width: 140px">
			<option value="">All types</option>
			<option value="sync">Sync</option>
			<option value="download">Download</option>
			<option value="verify">Verify</option>
			<option value="transfer">Transfer</option>
		</select>
		<select bind:value={status} style="width: 140px">
			<option value="">All results</option>
			<option value="done">Done</option>
			<option value="failed">Failed</option>
			<option value="cancelled">Cancelled</option>
		</select>
		<button class="sm" onclick={clear}><Trash2 size={14} /> Clear history</button>
	</div>
	<div class="card table-wrap">
		<table class="tbl">
			<thead><tr><th>Job</th><th>Result</th><th class="hide-sm">Summary</th><th>When</th><th class="hide-sm">Took</th><th></th></tr></thead>
			<tbody>
				{#each jobs as j (j.id)}
					<tr>
						<td class="ellipsis" style="max-width: 320px">
							<b>{j.title}</b>
							<div class="faint small">{j.type} · {j.trigger}</div>
						</td>
						<td><StatusBadge status={j.status === 'done' ? 'ok' : j.status} title={j.error ?? ''} />{#if j.error}<div class="small errtext ellipsis" style="max-width: 240px" title={j.error}>{j.error}</div>{/if}</td>
						<td class="small muted hide-sm">{summary(j.stats)}</td>
						<td class="small muted" title={datetime(j.created_at)}>{ago(j.created_at)}</td>
						<td class="small muted hide-sm">{dur(j)}</td>
						<td><button class="ghost icon sm" onclick={() => openLog(j.id)} title="Log"><ScrollText size={15} /></button></td>
					</tr>
				{:else}
					<tr><td colspan="6" class="empty">No jobs yet</td></tr>
				{/each}
			</tbody>
		</table>
	</div>
</section>

<Modal bind:open={logOpen} title="Log of job #{logId}" width="980px">
	<pre class="log" bind:this={logEl}>{logText || 'Empty log'}</pre>
	{#snippet footer()}
		<label class="check small"><input type="checkbox" bind:checked={follow} /> Follow new lines</label>
		<span class="spacer"></span>
		<button onclick={() => (logOpen = false)}>Close</button>
	{/snippet}
</Modal>

<style>
	.log {
		margin: 0;
		background: #0c0d0f;
		color: #d6d6dc;
		padding: 12px;
		border-radius: 8px;
		font-family: var(--mono);
		font-size: 12px;
		line-height: 1.5;
		height: 60vh;
		overflow: auto;
		white-space: pre-wrap;
		word-break: break-word;
	}
	.errtext {
		color: var(--err);
	}
	.waitlist {
		max-height: 340px;
		overflow: auto;
	}
	.wrow {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 4px 6px 4px 14px;
		border-bottom: 1px solid var(--line);
	}
	.wrow:last-child {
		border-bottom: none;
	}
	@media (max-width: 720px) {
		.hide-sm {
			display: none;
		}
	}
</style>
