<script lang="ts">
	import { CircleCheck, Clock, CircleAlert, FileWarning, Ban, HardDrive, RefreshCw, TriangleAlert } from '@lucide/svelte';
	import { get, post } from '#lib/api.ts';
	import { ago, bytes, until } from '#lib/format.ts';
	import { errorToast, live, on, toast } from '#lib/state.svelte.ts';
	import JobCard from '#lib/components/JobCard.svelte';
	import StorageAlert from '#lib/components/StorageAlert.svelte';

	let d = $state<any>(null);
	let sys = $state<any>(null);

	async function load() {
		try {
			[d, sys] = await Promise.all([get('/dashboard'), get('/system')]);
		} catch (e) {
			errorToast(e);
		}
	}
	load();
	$effect(() => on('job', (j) => ['done', 'failed', 'cancelled'].includes(j.status) && load()));
	let soon: ReturnType<typeof setTimeout>;
	$effect(() =>
		on('playlist', () => {
			clearTimeout(soon);
			soon = setTimeout(load, 800);
		})
	);

	async function syncAll() {
		try {
			const r = await post('/playlists/sync-all');
			toast(`Started ${r.jobs.length} syncs`);
		} catch (e) {
			errorToast(e);
		}
	}

	const jobs = $derived(Object.values(live.jobs).filter((j) => j.status !== 'queued'));
	const waiting = $derived(Object.values(live.jobs).filter((j) => j.status === 'queued').length);
	const c = $derived(d?.counts ?? {});
	const todo = $derived((c.pending || 0) + (c.queued || 0) + (c.downloading || 0));
</script>

<div class="page-head">
	<h1>Dashboard</h1>
	<span class="spacer"></span>
	<button class="primary" onclick={syncAll}><RefreshCw size={15} /> Sync all playlists</button>
</div>

{#if d}
	{#if d.storage_offline?.length}<StorageAlert playlists={d.storage_offline} onchanged={load} />{/if}
	{#each d.accounts_expired as a (a.id)}
		<a href="/accounts" class="banner"><TriangleAlert size={16} /> Account <b>{a.label}</b> needs fresh cookies. Its playlists are skipped by the schedule.</a>
	{/each}

	<div class="stats">
		<div class="stat card"><CircleCheck size={18} class="ok" /><div><b>{c.done ?? 0}</b><span>Downloaded</span></div></div>
		<div class="stat card"><Clock size={18} class="info" /><div><b>{todo}</b><span>Not done yet</span></div></div>
		<div class="stat card"><CircleAlert size={18} class="err" /><div><b>{c.failed ?? 0}</b><span>Failed</span></div></div>
		<div class="stat card"><FileWarning size={18} class="warn" /><div><b>{c.missing ?? 0}</b><span>Files missing</span></div></div>
		<div class="stat card"><Ban size={18} class="faint" /><div><b>{c.unavailable ?? 0}</b><span>Unavailable</span></div></div>
		<div class="stat card"><HardDrive size={18} class="faint" /><div><b>{bytes(d.bytes)}</b><span>{d.playlists} playlists</span></div></div>
	</div>

	<div class="cols">
		<div class="col" style="gap: 16px">
			<section>
				<div class="section-title"><h2>Running now</h2><span class="spacer"></span><a class="small muted" href="/activity">Activity →</a></div>
				{#each jobs as j (j.id)}
					<JobCard job={j} />
				{:else}
					<div class="card empty small">{waiting ? 'Starting…' : `Nothing running. ${d.next_runs[0] ? `Next sync ${until(d.next_runs[0].next_run_at)}.` : ''}`}</div>
				{/each}
				{#if waiting}<a class="small muted" href="/activity">+ {waiting} waiting in the queue →</a>{/if}
			</section>

			<section>
				<div class="section-title"><h2>Recently downloaded</h2></div>
				<div class="card">
					{#each d.recent_downloads as r (r.id)}
						<a class="li" href="/playlists/{r.playlist_id}">
							<span class="grow ellipsis"><b>{r.title}</b> <span class="faint">· {r.channel}</span></span>
							<span class="small faint">{ago(r.downloaded_at)}</span>
						</a>
					{:else}
						<div class="empty small">No downloads yet</div>
					{/each}
				</div>
			</section>

			{#if d.recent_failures.length}
				<section>
					<div class="section-title"><h2>Recent failures</h2></div>
					<div class="card">
						{#each d.recent_failures as r (r.id)}
							<a class="li" href="/playlists/{r.playlist_id}">
								<span class="col grow" style="gap: 2px; min-width: 0">
									<span class="ellipsis"><b>{r.title}</b> <span class="faint small">· {r.playlist_title}</span></span>
									<span class="small errtext ellipsis" title={r.error}>{r.error_kind}: {r.error?.split('\n').pop()}</span>
								</span>
								<span class="small faint">{ago(r.updated_at)}</span>
							</a>
						{/each}
					</div>
				</section>
			{/if}
		</div>

		<div class="col" style="gap: 16px">
			<section class="card pad">
				<h3>Next scheduled syncs</h3>
				{#each d.next_runs as r (r.id)}
					<a class="kv" href="/playlists/{r.id}"><span class="ellipsis">{r.title ?? r.url}</span><span class="faint small">{until(r.next_run_at)}</span></a>
				{:else}
					<p class="faint small">No schedules</p>
				{/each}
			</section>
			<section class="card pad">
				<h3>System</h3>
				<div class="kv"><span>yt-dlp</span><code>{d.ytdlp.version ?? '…'}</code></div>
				{#if d.ytdlp.latest && d.ytdlp.latest !== d.ytdlp.version}<div class="kv"><span>latest</span><code>{d.ytdlp.latest}</code></div>{/if}
				<div class="kv"><span>Last update check</span><span class="small faint">{ago(d.ytdlp.last_check)}</span></div>
				<div class="kv"><span>Proxies usable</span><span>{d.proxies.usable} / {d.proxies.total}</span></div>
				{#each sys?.disks ?? [] as disk (disk.path)}
					<div class="disk">
						<div class="kv"><code class="ellipsis" title={disk.path}>{disk.path}</code><span class="small faint nowrap">{bytes(disk.free)} free</span></div>
						<div class="dbar"><span style:width="{(100 * disk.used) / disk.total}%"></span></div>
					</div>
				{/each}
			</section>
			<section class="card pad">
				<h3>Last jobs</h3>
				{#each d.last_jobs as j (j.id)}
					<a class="kv" href="/activity?job={j.id}">
						<span class="ellipsis">{j.title}</span>
						<span class="pill {j.status === 'done' ? 'ok' : j.status === 'failed' ? 'err' : ''}">{j.status}</span>
					</a>
				{:else}
					<p class="faint small">No jobs yet</p>
				{/each}
			</section>
		</div>
	</div>
{/if}

<style>
	.banner {
		display: flex;
		align-items: center;
		gap: 8px;
		background: var(--err-soft);
		color: var(--err);
		padding: 10px 14px;
		border-radius: var(--radius);
		margin-bottom: 14px;
	}
	.stats {
		display: grid;
		grid-template-columns: repeat(6, minmax(0, 1fr));
		gap: 12px;
		margin-bottom: 20px;
	}
	.stat {
		display: flex;
		gap: 12px;
		align-items: flex-start;
		padding: 14px;
	}
	.stat div {
		display: flex;
		flex-direction: column;
	}
	.stat b {
		font-size: 22px;
		font-weight: 680;
		letter-spacing: -0.02em;
		font-variant-numeric: tabular-nums;
	}
	.stat span {
		font-size: 12.5px;
		color: var(--text-2);
	}
	.stat :global(.ok) {
		color: var(--ok);
	}
	.stat :global(.err) {
		color: var(--err);
	}
	.stat :global(.warn) {
		color: var(--warn);
	}
	.stat :global(.info) {
		color: var(--info);
	}
	.stat :global(.faint) {
		color: var(--text-3);
	}
	.cols {
		display: grid;
		grid-template-columns: minmax(0, 1fr) 320px;
		gap: 16px;
		align-items: start;
	}
	.li {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 10px 14px;
		border-bottom: 1px solid var(--line);
	}
	.li:last-child {
		border-bottom: none;
	}
	.li:hover {
		background: var(--bg-hover);
	}
	.errtext {
		color: var(--err);
	}
	.kv {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 10px;
		padding: 6px 0;
		min-width: 0;
	}
	h3 {
		margin-bottom: 6px;
	}
	.nowrap {
		white-space: nowrap;
	}
	.disk {
		margin-top: 4px;
	}
	.dbar {
		height: 5px;
		background: var(--bg-3);
		border-radius: 9px;
		overflow: hidden;
	}
	.dbar span {
		display: block;
		height: 100%;
		background: var(--text-3);
	}
	@media (max-width: 1100px) {
		.stats {
			grid-template-columns: repeat(3, minmax(0, 1fr));
		}
		.cols {
			grid-template-columns: minmax(0, 1fr);
		}
	}
	@media (max-width: 520px) {
		.stats {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}
</style>
