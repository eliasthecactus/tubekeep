<script lang="ts">
	import { Search, ListX, Trash2, EyeOff, ExternalLink, Music, Loader, TriangleAlert, CopyMinus, Undo2, Sparkles } from '@lucide/svelte';
	import { SvelteSet } from 'svelte/reactivity';
	import { get, post, qs } from '#lib/api.ts';
	import { bytes, duration } from '#lib/format.ts';
	import { errorToast, toast } from '#lib/state.svelte.ts';
	import Modal from '#lib/components/Modal.svelte';
	import StatusBadge from '#lib/components/StatusBadge.svelte';

	type DupItem = {
		id: number;
		playlist_id: number;
		playlist_title: string;
		video_id: string;
		title: string;
		artist: string | null;
		video_title: string | null;
		channel: string | null;
		duration: number | null;
		thumbnail: string | null;
		status: string;
		file_size: number | null;
		file_name: string | null;
		shared_file: boolean;
		copies: number;
	};
	type Group = { key: string; kind: 'video' | 'song' | 'repeat'; repeat: boolean; title: string; artist: string | null; keep_id: number; items: DupItem[]; extra: number; wasted_bytes: number };
	type PlInfo = { title: string; writable: boolean | null; reason: string | null; files_follow: boolean; syncing: boolean };
	type Summary = { groups: number; extra: number; wasted_bytes: number; dismissed: number; kinds: Record<string, number> };
	type Action = 'youtube' | 'file' | 'ignore';

	const KINDS: { key: string; label: string }[] = [
		{ key: '', label: 'All' },
		{ key: 'video', label: 'Same video' },
		{ key: 'song', label: 'Same song' },
		{ key: 'repeat', label: 'Twice in one playlist' }
	];
	const KIND_LABEL: Record<string, string> = { video: 'Same video', song: 'Same song', repeat: 'Twice in one playlist' };
	const KIND_HINT: Record<string, string> = {
		video: 'The same YouTube video is in several of your playlists',
		song: 'Different videos of the same song, for example the official video and the audio upload',
		repeat: 'This video was added more than once to the same YouTube playlist'
	};
	const PAGE = 60;

	let groups = $state<Group[]>([]);
	let playlists = $state<Record<string, PlInfo>>({});
	let summary = $state<Summary | null>(null);
	let allPlaylists = $state<{ id: number; title: string | null; url: string }[]>([]);
	let loading = $state(true);
	let kind = $state('');
	let pl = $state<number | ''>('');
	let q = $state('');
	let qDebounced = $state('');
	let showDismissed = $state(false);
	let shown = $state(PAGE);
	const keep = $state<Record<string, number>>({});
	const selected = new SvelteSet<string>();

	get('/playlists').then((p) => (allPlaylists = p));

	let t: ReturnType<typeof setTimeout>;
	$effect(() => {
		const v = q;
		clearTimeout(t);
		t = setTimeout(() => (qDebounced = v), 250);
	});

	async function load() {
		loading = true;
		try {
			const r = await get<{ summary: Summary; groups: Group[]; playlists: Record<string, PlInfo> }>(
				'/duplicates' + qs({ kind: kind || undefined, playlist_id: pl || undefined, q: qDebounced || undefined, dismissed: showDismissed || undefined })
			);
			groups = r.groups;
			playlists = r.playlists;
			summary = r.summary;
			for (const k of [...selected]) if (!groups.some((g) => g.key === k)) selected.delete(k);
			checkAccess();
		} catch (e) {
			errorToast(e);
		} finally {
			loading = false;
		}
	}
	$effect(() => {
		void [kind, pl, qDebounced, showDismissed];
		shown = PAGE;
		load();
	});

	// playlists never checked: ask once in the background so the YouTube buttons know if they can work
	let checking = false;
	async function checkAccess() {
		if (checking) return;
		checking = true;
		try {
			for (const [id, p] of Object.entries(playlists)) {
				if (p.writable !== null) continue;
				try {
					const a = await get<{ writable: boolean; reason: string | null }>(`/playlists/${id}/access`);
					if (playlists[id]) playlists[id] = { ...playlists[id], ...a };
				} catch {
					/* stays unknown, the action itself reports it */
				}
			}
		} finally {
			checking = false;
		}
	}

	const keepOf = (g: Group) => keep[g.key] ?? g.keep_id;
	const canYouTube = (it: DupItem) => playlists[it.playlist_id]?.writable !== false;
	const ytReason = (it: DupItem) => playlists[it.playlist_id]?.reason ?? "This playlist can't be changed on YouTube";

	// ---------------------------------------------------------------- applying
	type Plan = { title: string; groups: number; item_ids: number[]; extra_copy_ids: number[]; items: DupItem[]; fixed?: Action };
	let plan = $state<Plan | null>(null);
	let action = $state<Action>('youtube');
	let deleteFiles = $state(true);
	let applying = $state(false);

	/** keep one copy of each group, act on the others */
	function planGroups(gs: Group[]) {
		const item_ids: number[] = [];
		const extra_copy_ids: number[] = [];
		const items: DupItem[] = [];
		for (const g of gs) {
			const k = keepOf(g);
			for (const it of g.items) {
				if (it.id !== k) {
					item_ids.push(it.id);
					items.push(it);
				} else if (it.copies > 1) extra_copy_ids.push(it.id);
			}
		}
		const n = gs.length;
		plan = { title: n === 1 ? `Clean up "${gs[0].title}"` : `Clean up ${n} groups`, groups: n, item_ids, extra_copy_ids, items };
		action = 'youtube';
		deleteFiles = true;
	}
	function planOne(it: DupItem, a: Action | 'extra') {
		if (a === 'extra') {
			plan = { title: 'Remove the extra entries on YouTube?', groups: 1, item_ids: [], extra_copy_ids: [it.id], items: [it], fixed: 'youtube' };
			action = 'youtube';
		} else {
			const titles: Record<Action, string> = { youtube: 'Remove this copy from YouTube?', file: 'Delete the file of this copy?', ignore: 'Ignore this copy?' };
			plan = { title: titles[a], groups: 1, item_ids: [it.id], extra_copy_ids: [], items: [it], fixed: a };
			action = a;
		}
		deleteFiles = true;
	}

	const planReadOnly = $derived(plan ? plan.items.filter((it) => !canYouTube(it)).length + plan.extra_copy_ids.filter((id) => {
		const it = groups.flatMap((g) => g.items).find((x) => x.id === id);
		return it && !canYouTube(it);
	}).length : 0);
	const planFollow = $derived(plan ? plan.items.some((it) => playlists[it.playlist_id]?.files_follow) : false);
	const planFiles = $derived(plan ? plan.items.filter((it) => it.file_name).length : 0);
	const planBytes = $derived(plan ? plan.items.reduce((n, it) => n + (it.shared_file ? 0 : it.file_size || 0), 0) : 0);
	const planCount = $derived(plan ? plan.item_ids.length + (action === 'youtube' ? plan.extra_copy_ids.length : 0) : 0);

	async function apply() {
		if (!plan) return;
		applying = true;
		try {
			const body = {
				item_ids: plan.item_ids,
				extra_copy_ids: action === 'youtube' ? plan.extra_copy_ids : [],
				action,
				delete_files: deleteFiles
			};
			if (!body.item_ids.length && !body.extra_copy_ids.length) {
				toast('Nothing to do: tracks added twice to one playlist can only be fixed on YouTube', 'error');
				return;
			}
			const r = await post('/duplicates/apply', body);
			const parts: string[] = [];
			if (action === 'youtube') parts.push(`Removed ${r.removed} from YouTube`);
			else if (r.ignored) parts.push(`${r.ignored} ignored`);
			if (r.still_listed) parts.push(`${r.still_listed} still listed on YouTube, ${r.still_listed === 1 ? 'it stays' : 'they stay'} in Tubekeep until a sync shows ${r.still_listed === 1 ? 'it' : 'them'} gone`);
			if (r.deleted_files) parts.push(`${r.deleted_files} file${r.deleted_files === 1 ? '' : 's'} deleted${r.freed_bytes ? ` (${bytes(r.freed_bytes)})` : ''}`);
			const skipped = r.skipped.reduce((n: number, x: any) => n + x.tracks, 0);
			if (skipped) parts.push(`${skipped} skipped (${r.skipped.map((x: any) => `${x.title}: ${x.reason}`).join('; ')})`);
			toast(parts.join(', '), skipped || r.still_listed ? 'error' : 'ok');
			plan = null;
			selected.clear();
			load();
		} catch (e) {
			errorToast(e);
		} finally {
			applying = false;
		}
	}

	async function dismiss(g: Group, undo = false) {
		try {
			await post('/duplicates/dismiss', { key: g.key, undo });
			groups = groups.filter((x) => x.key !== g.key);
			selected.delete(g.key);
			if (summary) summary.dismissed += undo ? -1 : 1;
			toast(undo ? 'Shown as duplicate again' : 'Marked as not a duplicate', 'ok');
		} catch (e) {
			errorToast(e);
		}
	}

	const allSelected = $derived(groups.length > 0 && groups.every((g) => selected.has(g.key)));
	function toggleAll() {
		if (allSelected) selected.clear();
		else groups.forEach((g) => selected.add(g.key));
	}
	const kindCount = (k: string) => (summary ? (k ? summary.kinds[k] : summary.groups) : null);
</script>

<div class="page-head">
	<div class="col" style="gap: 2px">
		<h1>Duplicates</h1>
		<p class="muted small">Tracks that are in your library more than once. Keep one copy and remove the others from YouTube, from disk or from Tubekeep.</p>
	</div>
</div>

{#if summary && !showDismissed}
	<div class="card stats">
		<span><b>{summary.groups}</b> group{summary.groups === 1 ? '' : 's'}</span>
		<span><b>{summary.extra}</b> extra cop{summary.extra === 1 ? 'y' : 'ies'}</span>
		<span><b>{bytes(summary.wasted_bytes)}</b> on disk for extra copies</span>
	</div>
{/if}

<div class="toolbar">
	<div class="chips">
		{#each KINDS as k (k.key)}
			{@const c = kindCount(k.key)}
			<button class="chip" class:active={kind === k.key && !showDismissed} onclick={() => { kind = k.key; showDismissed = false; }}>
				{k.label}
				{#if c != null}<span class="cnt">{c}</span>{/if}
			</button>
		{/each}
		<button class="chip" class:active={showDismissed} onclick={() => (showDismissed = !showDismissed)} title="Groups you marked as not a duplicate">
			Not duplicates
			{#if summary}<span class="cnt">{summary.dismissed}</span>{/if}
		</button>
	</div>
	<div class="row wrap">
		<div class="search grow">
			<Search size={15} />
			<input type="search" bind:value={q} placeholder="Search title, artist…" />
		</div>
		<select bind:value={pl} style="width: 200px">
			<option value="">All playlists</option>
			{#each allPlaylists as p (p.id)}<option value={p.id}>{p.title ?? p.url}</option>{/each}
		</select>
	</div>
</div>

{#if groups.length && !showDismissed}
	<div class="bulk card" class:on={selected.size}>
		<label class="check small"><input type="checkbox" checked={allSelected} onchange={toggleAll} /> {selected.size ? `${selected.size} selected` : 'Select all'}</label>
		<span class="spacer"></span>
		{#if selected.size}
			<button class="sm primary" onclick={() => planGroups(groups.filter((g) => selected.has(g.key)))}><Sparkles size={14} /> Clean up {selected.size} group{selected.size === 1 ? '' : 's'}</button>
		{:else}
			<span class="small muted">Select groups to clean them up at once, keeping the marked copy of each.</span>
		{/if}
	</div>
{/if}

{#if loading && !groups.length}
	<div class="empty"><Loader size={18} class="spin" /></div>
{:else if !groups.length}
	<div class="card empty">
		{#if showDismissed}Nothing marked as not a duplicate.{:else if kind || pl || qDebounced}No duplicates match these filters.{:else}No duplicates. Every track is in your library once.{/if}
	</div>
{/if}

<div class="groups">
	{#each groups.slice(0, shown) as g (g.key)}
		{@const k = keepOf(g)}
		<section class="card group" class:sel={selected.has(g.key)}>
			<header>
				{#if !showDismissed}
					<input type="checkbox" checked={selected.has(g.key)} onchange={() => (selected.has(g.key) ? selected.delete(g.key) : selected.add(g.key))} />
				{/if}
				<div class="grow col gtitle">
					<b class="ellipsis">{g.title}</b>
					<span class="small muted ellipsis">{g.artist ?? ''}</span>
				</div>
				<span class="pill {g.kind === 'song' ? 'ok' : g.kind === 'repeat' ? 'warn' : 'info'}" title={KIND_HINT[g.kind]}>{KIND_LABEL[g.kind]}</span>
				{#if g.repeat && g.kind !== 'repeat'}<span class="pill warn hide-sm" title={KIND_HINT.repeat}>Twice in one playlist</span>{/if}
				{#if g.wasted_bytes}<span class="small muted hide-sm" title="Disk space of the copies you don't keep">{bytes(g.wasted_bytes)} extra</span>{/if}
				{#if showDismissed}
					<button class="sm" onclick={() => dismiss(g, true)}><Undo2 size={14} /> Show again</button>
				{:else}
					<button class="ghost sm" onclick={() => dismiss(g)} title="Hide this group. It shows up again when another copy joins it.">Not a duplicate</button>
					<button class="sm" onclick={() => planGroups([g])}><Sparkles size={14} /> Clean up</button>
				{/if}
			</header>
			<ul>
				{#each g.items as it (it.id)}
					<li class:keep={it.id === k}>
						{#if !showDismissed}
							<label class="keepbox" title="Keep this copy">
								<input type="radio" name="keep-{g.key}" checked={it.id === k} onchange={() => (keep[g.key] = it.id)} />
								<span class="small">{it.id === k ? 'Keep' : ''}</span>
							</label>
						{/if}
						<div class="thumb">{#if it.thumbnail}<img src={it.thumbnail} alt="" loading="lazy" />{:else}<Music size={15} />{/if}</div>
						<div class="grow col" style="min-width: 0">
							<span class="ellipsis" title={it.video_title ?? ''}>{it.video_title || it.title}</span>
							<span class="small faint ellipsis">{[it.channel, duration(it.duration)].filter(Boolean).join(' · ')}</span>
						</div>
						<div class="col meta">
							<span class="small ellipsis" title={it.playlist_title}>{it.playlist_title}</span>
							<span class="row" style="gap: 4px">
								<StatusBadge status={it.status} />
								{#if it.copies > 1}<span class="pill warn" title="This video is {it.copies} times in this playlist on YouTube">{it.copies}× on YouTube</span>{/if}
								{#if it.shared_file}<span class="pill" title="Another copy uses the same file, it is only deleted with the last copy">shared file</span>{/if}
								{#if it.file_size}<span class="small faint" title={it.file_name ?? ''}>{bytes(it.file_size)}</span>{/if}
							</span>
						</div>
						<div class="acts">
							<a class="ghost icon sm btnlike" href="https://www.youtube.com/watch?v={it.video_id}" target="_blank" rel="noreferrer" title="Open on YouTube"><ExternalLink size={14} /></a>
							{#if !showDismissed}
								{#if it.copies > 1}
									<button class="ghost icon sm" disabled={!canYouTube(it)} onclick={() => planOne(it, 'extra')} title={canYouTube(it) ? 'Remove the extra entries on YouTube, keep one' : ytReason(it)}><CopyMinus size={14} /></button>
								{/if}
								<button class="ghost icon sm" disabled={!canYouTube(it)} onclick={() => planOne(it, 'youtube')} title={canYouTube(it) ? 'Remove this copy from its YouTube playlist' : ytReason(it)}><ListX size={14} /></button>
								<button class="ghost icon sm" disabled={!it.file_name} onclick={() => planOne(it, 'file')} title={it.file_name ? 'Delete the file, keep it on YouTube' : 'No file on disk'}><Trash2 size={14} /></button>
								<button class="ghost icon sm" onclick={() => planOne(it, 'ignore')} title="Ignore in Tubekeep, change nothing on YouTube or disk"><EyeOff size={14} /></button>
							{/if}
						</div>
					</li>
				{/each}
			</ul>
		</section>
	{/each}
</div>
{#if groups.length > shown}
	<div class="row" style="justify-content: center; margin-top: 12px">
		<button onclick={() => (shown += PAGE)}>Show more ({groups.length - shown} left)</button>
	</div>
{/if}

<Modal open={!!plan} onclose={() => (plan = null)} title={plan?.title ?? ''} width="520px">
	{#if plan}
		{#if !plan.fixed}
			<p class="small">Keeps the marked copy of {plan.groups === 1 ? 'the group' : `each of the ${plan.groups} groups`}. What should happen to the other {plan.item_ids.length} cop{plan.item_ids.length === 1 ? 'y' : 'ies'}?</p>
			<div class="choices">
				<label class="choice" class:on={action === 'youtube'}>
					<input type="radio" bind:group={action} value="youtube" />
					<span class="col"><b>Remove from YouTube</b><span class="small muted">Removes them from their YouTube playlists. A video added twice to one playlist keeps its first entry.</span></span>
				</label>
				<label class="choice" class:on={action === 'file'}>
					<input type="radio" bind:group={action} value="file" />
					<span class="col"><b>Delete the files only</b><span class="small muted">They stay on YouTube. Tubekeep ignores them, so they don't download again.</span></span>
				</label>
				<label class="choice" class:on={action === 'ignore'}>
					<input type="radio" bind:group={action} value="ignore" />
					<span class="col"><b>Ignore in Tubekeep</b><span class="small muted">YouTube and the files stay as they are. Tubekeep stops tracking them.</span></span>
				</label>
			</div>
		{:else if action === 'file'}
			<p>The file is deleted from disk. The track stays on YouTube and Tubekeep ignores it, so it doesn't download again.</p>
		{:else if action === 'ignore'}
			<p>Tubekeep stops tracking this copy. It stays on YouTube and its file stays on disk.</p>
		{/if}

		{#if action === 'youtube'}
			<div class="warnbox" style="margin-top: 12px">
				<TriangleAlert size={16} />
				<span>This changes the <b>real playlists on YouTube</b>, not only Tubekeep. Tubekeep can't undo it.</span>
			</div>
			{#if plan.item_ids.length && planFiles}
				<label class="check" style="margin-top: 12px"><input type="checkbox" bind:checked={deleteFiles} /> Also delete their files{planBytes ? ` (${bytes(planBytes)})` : ''}</label>
				{#if planFollow && !deleteFiles}<p class="small muted">Playlists that delete files of removed tracks always delete them.</p>{/if}
			{/if}
			{#if planReadOnly}
				<p class="small muted" style="margin-top: 8px">{planReadOnly} cop{planReadOnly === 1 ? 'y is' : 'ies are'} in playlists that can't be changed on YouTube and will be skipped.</p>
			{/if}
		{:else if action === 'file' && !plan.fixed}
			<p class="small muted" style="margin-top: 12px">{planFiles} file{planFiles === 1 ? '' : 's'}{planBytes ? `, ${bytes(planBytes)}` : ''}. A file another copy still uses is kept.</p>
		{/if}
		{#if plan.extra_copy_ids.length && action !== 'youtube'}
			<p class="small muted" style="margin-top: 8px">Videos added twice to one playlist can only be fixed on YouTube.</p>
		{/if}
	{/if}
	{#snippet footer()}
		<button onclick={() => (plan = null)}>Cancel</button>
		<button class="solid" class:danger={action !== 'ignore'} class:primary={action === 'ignore'} onclick={apply} disabled={applying || !planCount}>
			{#if applying}<Loader size={14} class="spin" />{/if}
			{action === 'youtube' ? 'Remove from YouTube' : action === 'file' ? 'Delete files' : 'Ignore'}
		</button>
	{/snippet}
</Modal>

<style>
	.stats {
		display: flex;
		flex-wrap: wrap;
		gap: 6px 22px;
		padding: 10px 14px;
		margin-bottom: 12px;
		font-size: 13.5px;
		color: var(--text-2);
	}
	.stats b {
		color: var(--text);
	}
	.toolbar {
		display: flex;
		flex-direction: column;
		gap: 10px;
		margin-bottom: 12px;
	}
	.chips {
		display: flex;
		gap: 6px;
		flex-wrap: wrap;
	}
	.chip {
		height: 30px;
		border-radius: 999px;
		padding: 0 12px;
		font-size: 13px;
		color: var(--text-2);
	}
	.chip.active {
		background: var(--text);
		color: var(--bg);
		border-color: var(--text);
	}
	.cnt {
		font-size: 11.5px;
		opacity: 0.7;
		font-variant-numeric: tabular-nums;
	}
	.search {
		position: relative;
		display: flex;
		align-items: center;
		min-width: 200px;
	}
	.search :global(svg) {
		position: absolute;
		left: 10px;
		color: var(--text-3);
		pointer-events: none;
	}
	.search input {
		padding-left: 32px;
	}
	.bulk {
		position: sticky;
		top: 8px;
		z-index: 5;
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 8px;
		padding: 8px 12px;
		margin-bottom: 10px;
	}
	.bulk.on {
		border-color: color-mix(in srgb, var(--accent) 40%, var(--line));
	}
	.groups {
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.group {
		padding: 0;
		overflow: hidden;
	}
	.group.sel {
		border-color: color-mix(in srgb, var(--accent) 45%, var(--line));
	}
	header {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 10px 12px;
		border-bottom: 1px solid var(--line);
		min-width: 0;
	}
	ul {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	li {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 8px 12px;
		min-width: 0;
	}
	li + li {
		border-top: 1px solid var(--line);
	}
	li.keep {
		background: color-mix(in srgb, var(--ok-soft) 60%, transparent);
	}
	.keepbox {
		display: flex;
		align-items: center;
		gap: 4px;
		width: 56px;
		flex-shrink: 0;
		cursor: pointer;
		color: var(--text-2);
	}
	.thumb {
		width: 40px;
		height: 40px;
		border-radius: 6px;
		overflow: hidden;
		background: var(--bg-3);
		color: var(--text-3);
		display: grid;
		place-items: center;
		flex-shrink: 0;
	}
	.thumb img {
		width: 100%;
		height: 100%;
		object-fit: cover;
	}
	.meta {
		width: 240px;
		flex-shrink: 0;
		gap: 3px;
		min-width: 0;
	}
	.acts {
		display: flex;
		gap: 2px;
		flex-shrink: 0;
	}
	.btnlike {
		display: inline-grid;
		place-items: center;
		width: 28px;
		height: 28px;
		border-radius: 6px;
		color: var(--text-2);
	}
	.btnlike:hover {
		background: var(--bg-hover);
	}
	.choices {
		display: flex;
		flex-direction: column;
		gap: 8px;
		margin-top: 12px;
	}
	.choice {
		display: flex;
		gap: 10px;
		align-items: flex-start;
		padding: 10px 12px;
		border: 1px solid var(--line);
		border-radius: 8px;
		cursor: pointer;
	}
	.choice.on {
		border-color: var(--accent);
		background: color-mix(in srgb, var(--accent) 6%, transparent);
	}
	.choice input {
		margin-top: 3px;
	}
	.warnbox {
		display: flex;
		gap: 8px;
		align-items: flex-start;
		background: var(--warn-soft);
		border-radius: 8px;
		padding: 10px 12px;
	}
	.warnbox :global(svg) {
		color: var(--warn);
		flex-shrink: 0;
		margin-top: 2px;
	}
	.gtitle {
		min-width: 0;
	}
	@media (max-width: 720px) {
		.hide-sm {
			display: none;
		}
		header {
			flex-wrap: wrap;
		}
		.gtitle {
			flex: 1 1 calc(100% - 40px);
		}
		header .pill {
			margin-right: auto;
		}
		li {
			flex-wrap: wrap;
		}
		.meta {
			width: auto;
			flex: 1 1 100%;
			padding-left: 66px;
		}
		.acts {
			margin-left: auto;
		}
	}
</style>
